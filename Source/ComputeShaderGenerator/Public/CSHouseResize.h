#pragma once

#include "CoreMinimal.h"

/**
 * 拉尺寸（计划 D5）的纯函数层：单边推拉。
 *
 * **本文件不含任何交互设施**（抓手 actor / gizmo / EdMode / 命中体全部不在这一轮范围）。
 * 这里只解决「尺寸连续变化时派生物跟得住、不抖」的那半，尺寸从属性 / 蓝图 / 测试改都走同一条路。
 * 无 GPU、无 world、无编辑器依赖，可直接进 automation 测试。
 *
 * ── 为什么单边推拉要抽成纯函数（不是洁癖） ──────────────────────────────────────
 * 「对侧不动、中心随动」= `FootprintSize += Offset` 且 actor 中心沿该墙外法线移动 `Offset/2`。
 * 两个量必须**同时**改，分开写就会出现"尺寸改了、中心没跟上"的半步状态，而那个状态在画面上
 * 表现为**对侧墙也跟着走** —— 与"拖 1 m 墙走了 2 m"那个父子回路缺陷（计划 D5）逐像素相同，
 * 极易误诊。抽成一个函数 + 两条单测（Offset=0 幂等；Offset=Δ 后对侧墙世界位置逐位不变）
 * 就把这一类错误一次钉死。
 *
 * ── 尺寸禁带已删除（2026-08-31，四坡屋顶）────────────────────────────────────
 * 禁带（裁决四「房屋尺寸更换有最小距离」）与脊向滞回是一对，两者唯一的用途都是**挡住双坡
 * 屋顶在 X 穿过 Y 时那次 90° 原地翻面**。屋顶改成四坡以后脊向由长轴连续导出、正方形处脊长
 * 为 0，**翻轴这个事件不存在了**，禁带也就没有要挡的东西 —— 连同 `FCSHouseResizeBand` /
 * `CSHouseResize_ApplyBand` / `_RawMatches` / `_WouldFlipRidge` 与 `RawFootprintSize`
 * 累加器一并删除。硬下界 `MinFootprint` 保留，它与翻轴无关。
 */

/** 边号 → 被这条边推动的是哪一维（true = X，false = Y）。与 `CSHouse_GetEdge` 同号：0 南 1 东 2 北 3 西。 */
inline bool CSHouseResize_EdgeDrivesX(int32 EdgeIndex)
{
	return (EdgeIndex & 1) != 0;
}

/** 边号 → **局部**外法线（指离房子）。与 `CSHouse_GetEdge` 的 `In` 严格反号，别另写一套。 */
inline FVector2D CSHouseResize_EdgeOuterLocal(int32 EdgeIndex)
{
	switch (EdgeIndex & 3)
	{
	case 0:  return FVector2D(0, -1);
	case 1:  return FVector2D(1, 0);
	case 2:  return FVector2D(0, 1);
	default: return FVector2D(-1, 0);
	}
}

/** 边号 + yaw → **世界**外法线（Z 恒 0）。 */
inline FVector CSHouseResize_EdgeOuterWorld(int32 EdgeIndex, float YawDegrees)
{
	const FVector2D L = CSHouseResize_EdgeOuterLocal(EdgeIndex);
	return FRotator(0.0, double(YawDegrees), 0.0).RotateVector(FVector(L.X, L.Y, 0.0));
}

/**
 * 单边推拉：被推的墙沿外法线走 `Offset`，**对侧墙世界位置逐位不变**。
 *
 * 返回**实际**生效的位移（经 `MinFootprint` 下限修正之后）。调用方必须用返回值而不是传入的
 * Offset 去记账 —— 拖拽 handle 的累加器（计划 D5 的"记账量法"）一旦记成请求值而不是生效值，
 * 卡在下限上时残差就会一路累积，松手瞬间房子跳一大截。
 */
inline float CSHouse_ApplyEdgePush(FVector2D& InOutSize, FVector& InOutCenter, int32 EdgeIndex,
	float YawDegrees, float Offset, float MinFootprint)
{
	const bool bDrivesX = CSHouseResize_EdgeDrivesX(EdgeIndex);
	const double Current = bDrivesX ? InOutSize.X : InOutSize.Y;

	const double Desired = FMath::Max(Current + double(Offset), double(FMath::Max(MinFootprint, 1.0f)));
	const double Applied = Desired - Current;
	if (Applied == 0.0) return 0.0f;   // 幂等早退：Offset=0 连调 N 次不许改动任何量

	if (bDrivesX) InOutSize.X = Desired; else InOutSize.Y = Desired;
	InOutCenter += CSHouseResize_EdgeOuterWorld(EdgeIndex, YawDegrees) * (Applied * 0.5);
	return float(Applied);
}
