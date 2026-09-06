#include "CSHouseResizeHandleActor.h"

#include "CSHouseActor.h"
#include "CSHouseProfile.h"   // CSHouse_GetEdge —— 墙在哪儿只有这一个真源
#include "CSHouseResize.h"    // CSHouseResize_EdgeOuterLocal / _EdgeOuterWorld
#include "Components/StaticMeshComponent.h"
#include "Components/SceneComponent.h"
#include "Engine/StaticMesh.h"
#include "Materials/MaterialInterface.h"
#include "UObject/ConstructorHelpers.h"

ACSHouseResizeHandleActor::ACSHouseResizeHandleActor()
{
	// 根组件、不 tick、Movable 都由基类构造好了。这边只加自己的示意锥。
	// （宿主是生成时就钉死的，不像特征标记要在拖拽期逐帧重解析，所以照旧不 tick。）

	// 示意锥：编辑器里"抓这儿推墙"的提示，游戏里不存在。与地形塑形物的示意圆柱同一路数。
	ArrowComponent = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Arrow"));
	ArrowComponent->SetupAttachment(RootComponent);
	MakeEditorGizmoProp(ArrowComponent);

	static ConstructorHelpers::FObjectFinderOptional<UStaticMesh> ConeMesh(TEXT("/Engine/BasicShapes/Cone.Cone"));
	if (UStaticMesh* Mesh = ConeMesh.Get())
	{
		ArrowComponent->SetStaticMesh(Mesh);
		ArrowComponent->SetRelativeScale3D(FVector(0.35, 0.35, 0.5));
	}
}

void ACSHouseResizeHandleActor::InitializeHandle(ACSHouseActor* InHost, int32 InEdgeIndex)
{
	SetHost(InHost);
	EdgeIndex = InEdgeIndex & 3;

	// 锥子指向房外：把它的 +Z 转到**局部**外法线上。用局部量而不是世界量，房子整体旋转时
	// attach 会自动带着走 —— 存世界朝向的话每次转房子都得回来补一次。
	if (ArrowComponent)
	{
		const FVector2D N = CSHouseResize_EdgeOuterLocal(EdgeIndex);
		ArrowComponent->SetRelativeRotation(FRotationMatrix::MakeFromZ(FVector(N.X, N.Y, 0.0)).Rotator());

		ApplyHighlightMaterial(ArrowComponent);
	}

	SnapToCanonical();
}

FVector ACSHouseResizeHandleActor::GetOuterNormalWorld() const
{
	const ACSHouseActor* H = Host.Get();
	if (!H) return FVector::ZeroVector;
	return CSHouseResize_EdgeOuterWorld(EdgeIndex, float(H->GetActorRotation().Yaw));
}

FVector ACSHouseResizeHandleActor::ComputeCanonicalWorldLocation() const
{
	const ACSHouseActor* H = Host.Get();
	// 宿主没了就原地不动：跳回世界原点会让"房子删了但抓手还在"这一瞬间变成"抓手飞走了",
	// 而真正该发生的是自毁（`PostRegisterAllComponents` / 下一次事件里做）。
	if (!H) return GetActorLocation();

	// 墙外皮中心：`CSHouse_GetEdge` 的线段中点沿外法线推 HandleOffset。**不另起一套口径** ——
	// 墙板、门框砖、藤蔓、摆件全都问这一个函数，抓手再抄一份的症状是改了 WallThickness
	// 之后抓手悬在离墙半个墙厚的空中。
	const FCSHouseEdgeFrame F = CSHouse_GetEdge(EdgeIndex, H->FootprintSize, H->WallThickness);
	const FVector2D MidLocal = F.Start + F.U * (F.Len * 0.5f);
	const FVector2D OuterLocal = CSHouseResize_EdgeOuterLocal(EdgeIndex);
	const FVector2D OutLocal = MidLocal + OuterLocal * HandleOffset;

	const FVector Local(OutLocal.X, OutLocal.Y, H->WallHeight * HandleHeightFraction);
	return H->GetActorTransform().TransformPosition(Local);
}

void ACSHouseResizeHandleActor::SnapToCanonical()
{
	const FVector Canonical = ComputeCanonicalWorldLocation();
	SetActorLocation(Canonical);

	// 朝向也一并归位：用户拿 gizmo 转过抓手的话，锥子会指歪。我们从不读 actor 的旋转，
	// 所以转它无害 —— 但看起来像坏了。
	if (const ACSHouseActor* H = Host.Get())
	{
		SetActorRotation(H->GetActorRotation());
	}

	// 记账量与摆位**必须一起更新**：只摆位不重置，下一次 PostEditMove 会把程序刚制造的
	// 这段位移当成用户拖的，墙会自己跳一下（而且跳的量恰好是上一次的残差，极难归因）。
	LastConsumedWorld = GetActorLocation();
}

float ACSHouseResizeHandleActor::ConsumeDragToHost(bool bFinished)
{
	// 走基类那条唯一执行面 —— 无宿主自毁、最终裁决的时机判定都在那儿，两族抓手一份。
	LastAppliedOffset = 0.0f;
	HandleDrag(bFinished);
	return LastAppliedOffset;
}

bool ACSHouseResizeHandleActor::OnHandleDrag(bool bFinished)
{
	ACSHouseActor* H = Host.Get();
	// 返回 false ⇒ 基类在 `bFinished` 时按 `bDestroyWhenHostless` 自毁（计划 D5 的最后一条
	// 销毁时机）。这里不自己调 `Destroy()`：拖拽途中判自毁是两族共同的坑，归基类统一挡。
	if (!H) return false;

	const FVector Outer = GetOuterNormalWorld();
	const float Offset = float(FVector::DotProduct(GetActorLocation() - LastConsumedWorld, Outer));

	// 侧向 / 竖向分量直接忽略：抓手只沿墙的外法线有意义，用户把它拖歪不该改变尺寸。
	// 歪掉的那部分在 bFinished 的统一回位里被清掉。
	const float Applied = H->PushEdge(EdgeIndex, Offset, bFinished);

	// 全部抓手重摆，包括正在被拖的这一个（纪律 ②）。`SnapToCanonical` 末尾会把
	// `LastConsumedWorld` 设成规范位置，于是下一次 gizmo 事件读到的差仍然是纯增量 δ ——
	// "拖 1 m 墙走 1 m"不受影响。
	//
	// ⚠️ 不重摆自己的话，锥子每次事件比规范位置多走 0.5δ（gizmo 移 δ + attach 又带走 δ/2，
	// 而规范位置只走 δ），越拖越跑到光标前面去。
	H->SnapResizeHandles();

	LastAppliedOffset = Applied;
	return true;
}

void ACSHouseResizeHandleActor::OnDetachFromHost()
{
	// 房子那张表要把这一格清掉，否则 `IsInResizeMode()` 会一直答 true，编辑器侧的失选监听
	// 就永远等不到退出。基类保证 `Destroyed` 与 `EndPlay` 两条路都会走到这里，且幂等
	// （`NotifyResizeHandleDestroyed` 找不到就什么都不做）。
	if (ACSHouseActor* H = Host.Get())
	{
		H->NotifyResizeHandleDestroyed(this);
	}
}
