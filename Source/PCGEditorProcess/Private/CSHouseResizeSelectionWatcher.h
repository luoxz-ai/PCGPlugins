#pragma once

#include "CoreMinimal.h"
#include "Engine/TimerHandle.h"
#include "UObject/WeakObjectPtr.h"

class ACSHouseActor;

/**
 * 拉尺寸模式的失选监听（计划 D5）。
 *
 * 分工照抄笔刷那条既有的线：**runtime 请求、editor 应答**。`ACSHouseActor` 只管广播
 * `OnResizeModeChanged`，怎么"失选即退出"归编辑器模块 —— 房子那一侧因此零编辑器依赖，
 * 无头测试可以完整走完整条模式而不需要 Slate。
 *
 * ## 为什么绑定是**惰性**的
 *
 * `USelection::SelectionChangedEvent` 在编辑器里是高频事件（每次点选都发）。没有任何房子
 * 处于拉尺寸模式时挂在上面，等于给全项目每一次点选加一段无用回调。所以集合空 ⇒ 解绑，
 * 集合非空 ⇒ 绑定，`UpdateSelectionBinding()` 是这条规则的唯一执行处。
 */
class FCSHouseResizeSelectionWatcher
{
public:
	~FCSHouseResizeSelectionWatcher();

	/** 绑 `ACSHouseActor::OnResizeModeChanged`。模块 Startup 里调，不需要 GEditor。 */
	void Start();

	/** 解绑两条委托。模块 Shutdown 里调，幂等。 */
	void Stop();

	/**
	 * 这次选中是否还算「仍在编辑这栋房」—— 失选判定的**唯一**真源（用户裁决 2026-09-05）。
	 *
	 * 判据是**归属**而不是"是不是抓手"：选中集里只要有宿主房本身，或者**任何挂在它 attach 链
	 * 下的 actor**，就算仍在编辑。抓手因此自动被覆盖（`EnterResizeMode` 把它们 attach 在房子
	 * 下），窗标记之类同样挂在房下的编辑设施也一并覆盖 —— 调窗的时候抓手不该无故消失。
	 *
	 * 反过来：**选中另一栋房子就该退出**，哪怕那栋房也在拉尺寸模式里（它有它自己的一套抓手）。
	 *
	 * 抽成静态纯谓词是为了能单测 —— 判定本身不碰 `GEditor`、不碰定时器。
	 */
	static bool IsStillEditing(const ACSHouseActor* House, const TSet<const AActor*>& SelectedActors);

private:
	void HandleResizeModeChanged(ACSHouseActor* House, bool bEntered);

	/**
	 * 选择事件的接收端。**它只负责排队，不做判定** —— 判定在 `EvaluateSelection`，下一 tick 才跑。
	 *
	 * ⚠️ **同步判定是错的**（2026-09-05 实测：点抓手 → 四个抓手当场全没）。引擎换一次选中
	 * 走的是"先清空、再选中"两步，中间那次 `ESyncType::Cleared` 会**带着空选中集**广播一次
	 * `SelectionChangedEvent`（`Selection.cpp::OnElementListSyncEvent`）。同步响应那一帧就会
	 * 读到"既没选房也没选抓手" ⇒ 判定编辑结束 ⇒ 销毁全部抓手；紧接着的"选中抓手"于是落在
	 * 一个已经死掉的 actor 上。空选中集在这里是**中间态**，不是终态，必须等它落定再判。
	 */
	void HandleSelectionChanged(UObject* NewSelection);

	/** 真正的失选判定。排到下一 tick 跑，此时一次点击引发的所有选择变动都已落定。 */
	void EvaluateSelection();

	/** 集合空/非空决定要不要挂在选择事件上。 */
	void UpdateSelectionBinding();

	/** 处于拉尺寸模式的房屋集。弱引用：房子可能在模式里被直接删掉。 */
	TSet<TWeakObjectPtr<ACSHouseActor>> ActiveHouses;

	FDelegateHandle ResizeModeHandle;
	FDelegateHandle SelectionChangedHandle;
	/** 已排队但还没跑的判定。有效 = 这一帧已经排过，别重复排（一次点击会广播好几回）。 */
	FTimerHandle PendingEvaluationHandle;
};
