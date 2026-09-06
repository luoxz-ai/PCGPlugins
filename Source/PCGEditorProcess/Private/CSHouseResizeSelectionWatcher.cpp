#include "CSHouseResizeSelectionWatcher.h"

// 判定改成走 attach 归属之后，这里不再需要认识抓手这个类型 —— 抓手只是"挂在房下"的一种。
#include "CSHouseActor.h"
#include "Editor.h"
#include "Engine/Selection.h"
#include "TimerManager.h"   // FTimerManager / FTimerDelegate —— 下一 tick 才判失选

FCSHouseResizeSelectionWatcher::~FCSHouseResizeSelectionWatcher()
{
	Stop();
}

void FCSHouseResizeSelectionWatcher::Start()
{
	if (ResizeModeHandle.IsValid()) return;
	ResizeModeHandle = ACSHouseActor::OnResizeModeChanged.AddRaw(
		this, &FCSHouseResizeSelectionWatcher::HandleResizeModeChanged);
}

void FCSHouseResizeSelectionWatcher::Stop()
{
	if (ResizeModeHandle.IsValid())
	{
		ACSHouseActor::OnResizeModeChanged.Remove(ResizeModeHandle);
		ResizeModeHandle.Reset();
	}
	if (SelectionChangedHandle.IsValid())
	{
		USelection::SelectionChangedEvent.Remove(SelectionChangedHandle);
		SelectionChangedHandle.Reset();
	}
	// 排着的判定必须撤掉：定时器持的是 raw 指针，本对象随模块卸载先死，回调会打到野指针上。
	if (PendingEvaluationHandle.IsValid())
	{
		if (GEditor) GEditor->GetTimerManager()->ClearTimer(PendingEvaluationHandle);
		PendingEvaluationHandle.Invalidate();
	}
	ActiveHouses.Reset();
}

bool FCSHouseResizeSelectionWatcher::IsStillEditing(const ACSHouseActor* House, const TSet<const AActor*>& SelectedActors)
{
	if (!House) return false;

	for (const AActor* Actor : SelectedActors)
	{
		if (!Actor) continue;
		// `IsAttachedTo` 走的是整条 attach 父链，不只是直接父级 —— 抓手、窗标记，以及将来
		// 任何挂在房下的编辑设施都一次覆盖。选另一栋房必然两条都不成立，于是正常退出。
		if (Actor == House || Actor->IsAttachedTo(House)) return true;
	}
	return false;
}

void FCSHouseResizeSelectionWatcher::HandleResizeModeChanged(ACSHouseActor* House, bool bEntered)
{
	if (!House) return;

	if (bEntered) ActiveHouses.Add(House);
	else          ActiveHouses.Remove(House);

	UpdateSelectionBinding();
}

void FCSHouseResizeSelectionWatcher::UpdateSelectionBinding()
{
	// 顺带清掉已经失效的弱引用：房子在模式里被删时 `Destroyed` 会广播退出，但流送卸载
	// 这类路径不保证走到，留着的话集合永远非空、事件永远挂着。
	for (auto It = ActiveHouses.CreateIterator(); It; ++It)
	{
		if (!It->IsValid()) It.RemoveCurrent();
	}

	const bool bWant = ActiveHouses.Num() > 0 && GEditor != nullptr;
	if (bWant == SelectionChangedHandle.IsValid()) return;

	if (bWant)
	{
		SelectionChangedHandle = USelection::SelectionChangedEvent.AddRaw(
			this, &FCSHouseResizeSelectionWatcher::HandleSelectionChanged);
	}
	else
	{
		USelection::SelectionChangedEvent.Remove(SelectionChangedHandle);
		SelectionChangedHandle.Reset();
	}
}

void FCSHouseResizeSelectionWatcher::HandleSelectionChanged(UObject* /*NewSelection*/)
{
	if (ActiveHouses.Num() == 0 || !GEditor) return;

	// 只排队，不判定 —— 理由见头文件那段。一次点击会广播好几回（清空一次、选中一次），
	// 句柄有效就说明这一帧已经排过了，重复排只会让同一份判定跑好几遍。
	if (PendingEvaluationHandle.IsValid()) return;

	PendingEvaluationHandle = GEditor->GetTimerManager()->SetTimerForNextTick(
		FTimerDelegate::CreateRaw(this, &FCSHouseResizeSelectionWatcher::EvaluateSelection));
}

void FCSHouseResizeSelectionWatcher::EvaluateSelection()
{
	PendingEvaluationHandle.Invalidate();

	if (ActiveHouses.Num() == 0 || !GEditor) return;

	USelection* Selection = GEditor->GetSelectedActors();
	if (!Selection) return;

	TSet<const AActor*> Selected;
	for (FSelectionIterator It(*Selection); It; ++It)
	{
		if (const AActor* Actor = Cast<AActor>(*It)) Selected.Add(Actor);
	}

	// ⚠️ 迭代**副本**：`ExitResizeMode` 会同步广播 `OnResizeModeChanged(false)`，
	// 那条回调就在本类里、就要改 `ActiveHouses` —— 边迭代边改是当场崩溃。
	TArray<TWeakObjectPtr<ACSHouseActor>> Snapshot = ActiveHouses.Array();
	for (const TWeakObjectPtr<ACSHouseActor>& Weak : Snapshot)
	{
		ACSHouseActor* House = Weak.Get();
		if (!House) continue;

		if (!IsStillEditing(House, Selected)) House->ExitResizeMode();
	}

	UpdateSelectionBinding();
}
