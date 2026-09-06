#pragma once

#include "CoreMinimal.h"
#include "CSMesh.h"
#include "RenderGraphResources.h"

/**
 * 地被（草 + 花）：100% GPU 散布 + 零回读。形状照 Tiny Glade 的 `_generate_grass.cs` 那条链
 * （对照见 `Docs/TinyGlade/`）。
 *
 * -----------------------------------------------------------------------------
 * 归属：和石阶 / 岩壳 / 裙边摆件同一条理由 —— 归**地面**
 * -----------------------------------------------------------------------------
 * 草长在哪儿由两样东西决定：**合成后**的高度场（坡太陡不长）与地面的**顶点色遮罩**
 * （画了路的地方不长）。两样都只有 `ACSGroundActor` 手上有 —— 塑形物只看得见自己那一座，
 * 而遮罩本来就是地面的笔刷双写出来的。这与裙边摆件那三条依据逐字同形，不再重复。
 *
 * -----------------------------------------------------------------------------
 * 一株一个 instance，不是"一簇一个"
 * -----------------------------------------------------------------------------
 * TG 的草是**每叶一个 instance**（`_grass.raster` 用 `gl_InstanceIndex` 取叶参数、
 * `gl_VertexIndex` 取叶内顶点），没有"簇"这个概念；落点用低差异序列在 tile 内分层散布，
 * 本来就是为了不扎堆。本模块用**抖动网格**（一格一株、落点由格身份哈希抖动）拿到同一条性质，
 * 省掉 TG 那一整套 tile/bundle 前缀和 —— 它需要那套是因为它每帧重算并做 tile 级 HZB 剔除，
 * 本项目是"地面变了才重扫"，剔除交给 `UCSGpuInstancedMeshComponent` 自己的 pass。
 *
 * -----------------------------------------------------------------------------
 * ⚠️ 三条纪律（都是别处踩过的坑，照抄不要重新发明）
 * -----------------------------------------------------------------------------
 * 1. **随机源是格身份，不是 `InterlockedAdd` 的槽位**。槽位由线程组完成顺序决定，同一份世界
 *    状态两次 dispatch 能把同一株草放进不同的槽；而本 pass 每一笔落笔都重扫，症状是"画一笔路
 *    整片草原地重掷"，且不会有任何断言报红（石阶 S1 栽过一次）。
 * 2. **容量一次性定死、永不重算**。CPU 不知道长了几株（只有 GPU 知道），所以既不能按实例数
 *    算容量，也不能按实例数算包围盒 —— 后者会让 `bNeedHandover` 每一笔都成立，把阻塞的
 *    `SetInstanceSourceGPU` 拖进交互热路径。
 * 3. **遮罩读 GPU 色流，不读 CPU 镜像的拷贝**。色流就是笔刷双写出来的那份权威投影；上传一份
 *    拷贝是第三条路，迟早与另外两条错开一帧，症状（草比路慢一笔）只在快速涂抹时才看得见。
 */
namespace CSGroundCover
{
/**
 * 一个物种（草，或某一种花）的固定容量实例源，直接喂
 * `UCSGpuInstancedMeshComponent::SetInstanceSourceGPU`。
 *
 * 一个物种一套，不共享一块 buffer：一个实例组件只绑一张基础网格 ⇒ 一张网格一个组件 ⇒
 * 一个组件一套实例源。`FCSGpuInstanceSourceGPU` 里也没有偏移量字段，塞进同一块 buffer 的
 * 后半段是画不出来的（石子那条已经把这个结论写死过一次）。
 */
struct FCoverBuffers
{
	TRefCountPtr<FRDGPooledBuffer> PackedInstances;   // Buffer<float4>，5 个 / 实例
	TRefCountPtr<FRDGPooledBuffer> Counter;           // Buffer<uint>，[0] = 活跃数（只有 GPU 知道）
	uint32 Capacity = 0;

	bool IsValid() const { return PackedInstances.IsValid() && Counter.IsValid() && Capacity > 0; }
	void Reset() { *this = FCoverBuffers(); }
};

/** 一个物种散布一趟的全部标量参数。命名与 `CSGroundCover.usf` 的 uniform 一一对应。 */
struct FScatterParams
{
	FMatrix44f WorldToComponent = FMatrix44f::Identity;

	/** 散布格：原点是 (0,0) 格的角。`CellSize` = 1/√密度（换算在 `MakeGridForDensity`）。 */
	FVector2f GridOriginXY = FVector2f::ZeroVector;
	float CellSize = 20.0f;
	FIntPoint GridDims = FIntPoint(0, 0);

	/** 地面镜像格（取遮罩用；V = Y * VertsX + X）。 */
	FVector2f GroundOriginXY = FVector2f::ZeroVector;
	float GroundCellSize = 50.0f;
	FIntPoint GroundVerts = FIntPoint(0, 0);
	float GroundBaseZ = 0.0f;

	/** 遮罩通道的 one-hot（RGBA）。默认 (1,0,0,0) = R = 道路权重（见 `FCSGroundMirror::Colors`）。 */
	FVector4f MaskChannel = FVector4f(1.0f, 0.0f, 0.0f, 0.0f);
	float MaskStart = 0.15f;
	float MaskEnd = 0.45f;
	/** 遮罩 → 压高比例。TG 在路上把草高乘 (1 − path × 0.7)，路边因此是矮草过渡而不是一刀切。 */
	float MaskShorten = 0.7f;

	float Chance = 1.0f;
	float Jitter = 1.0f;
	/** 地形法线 .z 的下限。由 `ACSGroundActor` 从"最大坡度角"换算（cos）。 */
	float MinSlopeCos = 0.5f;
	float Sink = 2.0f;
	/** 坐底修正 = −基础网格局部包围盒 Min.Z（未缩放）。轴心不在底部的网格靠它落地，见 kernel 注释。 */
	float Rise = 0.0f;

	FVector2f ScaleRange = FVector2f(0.85f, 1.2f);
	float HeightJitter = 0.25f;
	float LeanMaxRad = 0.0f;
	float AlignToNormal = 0.0f;

	uint32 Seed = 0;
	/** 物种盐。**同一份 Site 里两个物种撞盐会让它们逐格完全相关** —— 花就永远长在草心里。 */
	uint32 Salt = 0;

	FVector3f BaseSphereCentre = FVector3f::ZeroVector;
	float BaseSphereRadius = 0.0f;
};

/**
 * 密度 → 格距 + 格数，并保证 `GridX * GridY ≤ MaxCells`。
 *
 * ⚠️ 钳位不是保险丝而是必需：格数 = 面积 × 密度，而地面镜像最大 1024²×50 cm = 512 m 见方，
 * 50 株/m² 就是 1300 万线程 —— 没有这个钳位，把地面拉大一档就会挂掉整个编辑器。钳住之后
 * 密度**自动退让**（格距按 √(cells/max) 放大），行为是"草变稀"而不是"卡死"。
 *
 * 返回是否发生了退让（调用方可以据此打一条日志）。
 */
COMPUTESHADERGENERATOR_API bool MakeGridForDensity(
	const FBox2D& WorldRectXY, float DensityPerSqM, int32 MaxCells,
	FVector2f& OutOriginXY, float& OutCellSize, FIntPoint& OutGridDims);

/** 把容量补到 `Capacity`（只涨不缩）。真扩容那一趟**阻塞**，所以只该在注册/加载/改配置时碰到。 */
COMPUTESHADERGENERATOR_API bool EnsureBuffers(FCoverBuffers& Buffers, uint32 Capacity);

/**
 * 散布一趟：**一张 RDG 图里把全部物种一起录完**（每个物种一次 clear + 一次 dispatch）。
 * 录完直接返回，不阻塞。
 *
 * 一张图而不是每个物种一张：`FCSMeshRenderThreadEdit` 的进出、色流 SRV、塑形物参数上传
 * 都只需要一份；分成 N 张图等于把这三样各做 N 遍，而且 N 张图之间没有任何顺序保证。
 *
 * `Buffers` 与 `Params` **按下标一一对应**，长度必须相同（不同则整趟拒绝：错位的症状是
 * "花长成草的密度"，没有任何报错）。空 palette 也要跑 —— counter 清零正是"关掉某个物种"
 * 的唯一路径。
 */
COMPUTESHADERGENERATOR_API bool Scatter(
	const FCSMeshResidentRef& GroundResident,
	const TArray<FCoverBuffers>& Buffers,
	const TArray<FScatterParams>& Params,
	const TArray<FVector4f>& ShaperParams);

COMPUTESHADERGENERATOR_API void ReleaseOnRenderThread(FCoverBuffers& Buffers);

/** 诊断 / 验收专用，**阻塞**：GPU counter（按容量钳过）。行内容按需带出。 */
COMPUTESHADERGENERATOR_API int32 DebugReadInstancesSync(
	const FCoverBuffers& Buffers, TArray<FVector>* OutOrigins, TArray<FVector4f>* OutRows);
}
