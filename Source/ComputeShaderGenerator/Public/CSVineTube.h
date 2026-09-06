#pragma once

#include "CoreMinimal.h"

#include "Containers/ArrayView.h"
#include "Math/IntVector.h"

class UCSMesh;

/**
 * 把一组**已经排好的折线**扫掠成管子，写进一个 `UCSMesh`。
 *
 * 这是 vinegenerator（`AVineContainer` / VisVineGPU）那条管线的**对外入口**：它内部本来就
 * 分成"谁产折线"与"折线怎么变成管子"两半，而 `BuildVVVoxelCS`（Pass C）吃的是**通用折线缓冲**
 * ——`PathPointMeta` 的语义是 `.x/.y` = prev/next、`.z` = 该线起点下标、`.w` = 该线点数——
 * **与折线是谁写的无关**。所以"用 vinegenerator"与"不跑空间殖民"并不冲突：换掉上游即可。
 *
 * 墙面藤（TinyGladeHouse D13，2026-09-06 裁决）走的正是这条：折线由 `CSHouseVine::BuildPlan`
 * 的墙面游走给出（不分叉、活在墙面参数坐标里），空间殖民与表面吸附一概不参与。
 *
 * ⚠️ **别拿它当"通用建管子"接口用在会动的东西上**：产物是 `UCSMesh` 的常驻流，几何**在世界
 * 空间**，宿主组件必须钉在**恒等世界变换**上（`UCSMeshRenderComponent` 的构造函数已经把变换
 * 标成绝对，正是为此）。两者必须一起动，否则网格会画在别的地方。
 */
namespace CSVineTube
{
/** 扫掠参数。只留真正会影响管子的那几个，别把 `FVV` 上那一堆吸附相关的旋钮抄过来。 */
struct FParams
{
	/**
	 * 截面环的周向段数。3 = 三棱柱（TG 的 `ivy_branch` 原始形态），越大越接近圆。
	 * 与 `AVineContainer::FVV::VisVineGPUTubeSegments` 是同一个量。
	 */
	uint32 ProfileCount = 8;

	/**
	 * 环半径系数。**必须与喂给 `CSHouseVine::PackTubePath` 的那个值逐位相同** ——
	 * Pass C 的环半径 = `10 * CircleScale * PathPoints[i].w`，而 `PackTubePath` 正是按这个
	 * 公式反解出 `.w` 的。两处取不同值的症状是"管子粗细整体差一个常数倍"，而两边各自都自洽。
	 */
	float CircleScale = 0.2f;

	/** 贴图沿长度方向的缩放（Pass C 的 `UVLengthScale`）。 */
	float UVLengthScale = 1.0f;
};

/**
 * 录一趟"折线 → 管子"的构建，写进 `Target` 的常驻流。**异步**：录完即返回。
 *
 * 四条折线缓冲的格式见 `CSHouseVine::FTubePath` 的字段注释；`Axes` 必须显式清零
 * （`BuildRawVoxelVineFrame` 的回退判据是 `dot(Axis,Axis) > 1e-8`，池子里的旧内容会让它
 * 误以为有轴可用）。
 *
 * 返回是否**已递交**，不是"已建好"。`Target` 上有编辑在途时直接返回 false ——
 * 调用方应当把这次的输入挂起，在完成回调里补发（同 `ACSHouseActor::SubmitPillarMesh`）。
 * `OnBuilt` 在游戏线程的完成回调里跑，参数是"pass 是否真的录进去了"。
 */
COMPUTESHADERGENERATOR_API bool BuildTubeIntoMesh(
	UCSMesh* Target,
	const TArray<FVector4f>& PathPoints,
	const TArray<FVector4f>& PathPointAxes,
	const TArray<FIntVector4>& PathPointMeta,
	const TArray<FIntVector4>& SegmentMeta,
	/** 逐点 `(SpawnTime 秒, 弧长 cm)`。空数组 = 不做生长动画（UV 只留一组）。 */
	const TArray<FVector2f>& PathPointGrowth,
	const FParams& Params,
	TFunction<void(bool)> OnBuilt);
}
