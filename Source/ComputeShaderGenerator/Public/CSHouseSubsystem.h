#pragma once

#include "CoreMinimal.h"
#include "Subsystems/WorldSubsystem.h"
#include "CSHouseProfile.h"   // FCSWallHit —— PickHouse 的出参
#include "CSHouseSubsystem.generated.h"

class ACSHouseActor;
class ACSHouseFeatureMarker;

/**
 * 房屋注册表 + 低频兜底变换快扫（TinyGladeHouse_Plan.md D10）。
 *
 * **已按计划瘦身**：接触检测、接缝生死、角柱合并都下放给房子与接缝 actor 自管理，地面 →
 * 房屋的唤醒也仍走房子自己订阅的 ACSGroundActor::OnGroundChanged 直推（那条已经跑通，
 * 搬进来只会多一层且不改语义）。这里只剩两件本 subsystem 独有、别处做不了的事：
 *
 *  ① **兜底快扫**：捕捉那些**不发任何通知**的改动 —— 蓝图直设 transform、物理、Python
 *     脚本改属性。编辑器里 PostEditMove 覆盖了鼠标拖动，但它对上面三条一律沉默，而
 *     "房子动了就该重求值"是架构不变量，不能依赖某一条委托的完备性。
 *  ② **回写哈希基线（顺序是承重的）**：ReevaluateSite() 会在落座时改 Z，所以扫完必须用
 *     **落座之后**的变换重算基线存回去。漏了这步，落座改的那点 Z 会在下一帧被当成"外部
 *     移动"再次唤醒，形成永不停止的 1 帧空转 —— 重求值本身幂等，所以不会出错，只会白烧
 *     CPU 并掩盖真实的抖动问题。
 *
 * **必须在编辑器 world 里 tick**（P2 验收门）：本项目的创作全在编辑器里发生，只在 PIE 里
 * 转的 subsystem 等于没有。因此 DoesSupportWorldType 放行 Editor，IsTickableInEditor 返回 true。
 */
UCLASS()
class COMPUTESHADERGENERATOR_API UCSHouseSubsystem : public UTickableWorldSubsystem
{
	GENERATED_BODY()

public:
	/** 幂等：重复登记只是刷新基线。房子在 PostRegisterAllComponents 调。 */
	void RegisterHouse(ACSHouseActor* House);
	void UnregisterHouse(ACSHouseActor* House);

	/** 外部（将来的接缝 / 特征标记）显式标脏，下一次 tick 重求值。 */
	void MarkHouseDirty(ACSHouseActor* House);

	/** 当前登记的房屋数（调试统计 / 无头断言）。 */
	UFUNCTION(BlueprintPure, Category = "CS House")
	int32 GetTrackedHouseCount() const { return Tracked.Num(); }

	/**
	 * 只读枚举当前登记的房子，**按 GUID 升序**（D7 接缝：一栋房要拿邻居的 footprint 自己算缝）。
	 *
	 * ⚠️ **排序不是洁癖**：`TMap` 的迭代序取决于插入历史与桶分布，同一个关卡换个加载顺序就能
	 * 换一个次序 —— 接缝砖的序号跟着它走，于是"同一份世界状态两次给出不同砖列"，而且不会有
	 * 任何断言报红。GUID 升序与摆位、加载顺序、以及谁先注册全都无关。
	 *
	 * 这是**只读**接口，故意不给任何写口：接缝是纯函数，subsystem 在它这条路上只是一本花名册。
	 */
	void GetTrackedHouses(TArray<ACSHouseActor*>& Out) const;

	/**
	 * 射线找宿主（D8 特征标记）。返回最近命中的房子，`OutHit` 是**该房局部空间**的墙面命中。
	 *
	 * ⚠️ **解析求交，不走引擎 trace** —— 房子的 gpumesh 全线 `NoCollision`，`LineTraceSingle`
	 * 一栋都打不到（计划 D8 明写）。判据全在 `CSHouse_RayHitWall` 这个纯函数里，subsystem
	 * 只负责"遍历花名册 + 解世界变换 + 取最近"。
	 *
	 * 平局（两栋房同距）按 GUID 升序取先者 —— 与 `GetTrackedHouses` 同一条理由：TMap 的迭代序
	 * 取决于加载历史，不定序会让"同一份世界状态两次给出不同宿主"。
	 */
	ACSHouseActor* PickHouse(const FVector& WorldOrigin, const FVector& WorldDir,
		float MaxDistance, FCSWallHit& OutHit) const;

	/**
	 * 就近找宿主（射线落空时的退路，计划 D8 的 `csh.WindowSnapDist`）。
	 * 判据是"点到外墙面的垂距"，同样取最近、平局按 GUID 升序。
	 */
	ACSHouseActor* PickHouseNear(const FVector& WorldPoint, float MaxDistance, FCSWallHit& OutHit) const;

	/**
	 * **点击创建窗户的唯一执行面**（2026-09-06 用户裁决：改用笔刷模式，点一下就建、随即退出）。
	 *
	 * 拿一条世界射线（视口点击那条），命中墙就在那儿生成一个标记，并**直接把宿主与锚点交给它** ——
	 * 标记出生就是"几何体记录坐标"的形态，spawn 期一条射线都不用打。
	 *
	 * ⚠️ 这条替掉的是"把窗蓝图拖进视口、让 actor 自己拿 forward 去解析宿主"那条路。那条路脆在
	 * **朝向什么时候被应用**：`UEditorEngine::AddActor` 把 Rotation 一起传给 `SpawnActor`，而
	 * `EditorActorSubsystem` 那条是先放置、回调之后才 `SetActorLocationAndRotation` —— 两条 spawn
	 * 路径行为不同，回调里的 forward 未必可信（实测咬上 11 m 外的另一栋房）。视口点击给的是
	 * **相机射线 + 精确命中点**，不依赖 actor 自身朝向，所以这条从根上没有那个问题。
	 *
	 * EdMode（`FCSWindowBrushEdMode`）只是它的触发器 —— 与抓手族纪律 ⑤ 同型：无头测试直接调它，
	 * 不需要视口、不需要 Slate。
	 *
	 * @return 生成出来的标记；射线没打到任何房子时返回 nullptr（此时**什么都不生成**）。
	 */
	UFUNCTION(BlueprintCallable, Category = "CS House|Window")
	ACSHouseFeatureMarker* PlaceMarkerAlongRay(TSubclassOf<ACSHouseFeatureMarker> MarkerClass,
		const FVector& RayOrigin, const FVector& RayDir, float MaxDistance);

	/**
	 * 取某个 world 的房屋 subsystem。
	 *
	 * ⚠️ 存在的理由是**脚本侧够不着 world subsystem**：UE 的 Python 绑定里没有
	 * `SubsystemBlueprintLibrary`，而 `unreal.get_editor_subsystem` 只认 editor subsystem。
	 * 无头回归要调 `PlaceMarkerAlongRay`（点击加窗的唯一执行面），就得有这个口 ——
	 * 否则那条路只能靠在编辑器里用鼠标点，等于没有自动化判据。
	 */
	UFUNCTION(BlueprintPure, Category = "CS House", meta = (WorldContext = "WorldContextObject"))
	static UCSHouseSubsystem* GetHouseSubsystem(const UObject* WorldContextObject);

	/** 快扫累计唤醒次数（调试统计：稳态下它必须停止增长 —— 增长就是有人在自唤醒）。 */
	UFUNCTION(BlueprintPure, Category = "CS House")
	int64 GetScanWakeCount() const { return ScanWakeCount; }

	/** 兜底快扫的间隔（秒）。主通道是各自的直推 / PostEditMove，这条只是兜底，不必每帧。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "CS House", meta = (ClampMin = "0.0"))
	float ScanInterval = 0.25f;

	//~ USubsystem
	virtual bool DoesSupportWorldType(const EWorldType::Type WorldType) const override;
	virtual void Deinitialize() override;

	//~ FTickableGameObject
	virtual void Tick(float DeltaTime) override;
	virtual bool IsTickableInEditor() const override { return true; }
	virtual TStatId GetStatId() const override;

private:
	struct FCSHouseTracked
	{
		TWeakObjectPtr<ACSHouseActor> House;
		uint32 TrackingHash = 0;
	};

	/** key = 房子的稳定 GUID（D7 的接缝 key 也用它，届时无序对规范化即可）。 */
	TMap<FGuid, FCSHouseTracked> Tracked;
	TSet<FGuid> DirtyHouses;

	float ScanAccumulator = 0.0f;
	int64 ScanWakeCount = 0;
};
