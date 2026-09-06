#pragma once

#include "CoreMinimal.h"
#include "Containers/ArrayView.h"

/**
 * 门洞的**区间求解**（D6 的 2026-09-04 重做）：沿边的道路剖面 → 洞的区间表。
 *
 * -----------------------------------------------------------------------------
 * 为什么重做：旧口径把路的连续值当场扔了
 * -----------------------------------------------------------------------------
 * 旧 `ComputeDoors` 是「等分槽 + 二值投票」：把边等分成 `round(可用长 / DoorPitchTarget)` 个
 * 固定槽，每槽沿边采若干点，`Road >= DoorOnWeight` 就记一票，票数占比过阈就开一个拱，
 * **拱宽取自槽宽与地形落差，与路无关，拱心恒在槽心**。于是画路只能决定"这一格开不开"，
 * 开出来的永远是同一个宽度、同一个位置。
 *
 * TG 不是这么做的（2026-09-04 用户实测 + PDB 泛型签名复核，详见合卷卷零
 * 「❌ 已推翻：门洞的触发规则与 TG 不同」）：
 *
 *   sample_wall_path_intersections   路径栅格 × 墙曲线 → 交集区间，写回 PublicWalls
 *   calculate_wall_path_segmentation 区间 → 沿墙一维 mask，打分求解分割点
 *   拱系统                            段 → ArchSegments，并发 ConstructGateCmd
 *   construct_arches                 ArchWalker 沿剖面摆拱圈石
 *
 * 最硬的一条是 `ArchSegment` 的构造签名 **`ArchSegment (*)(WallPathSegment)`** ——
 * 拱由「墙路径的一段」直接变成，而 `WallPathSegment` 自带 `length_ws`。
 * ⇒ **拱宽 = 段长 = 路在墙上截出的弦长**。路只擦过一点点，段就短，拱就窄。
 * 木门扇走同一个长度（`construct_gates::segment_length`、`MaterialStats::add_gate_length`），
 * 所以洞与门是**一起**变宽的。
 *
 * 本文件是那条链在本项目的对位物：本项目的墙是刚性四边、路是地面顶点色 R 的双线性场，
 * 所以「交集区间」退化成**沿边一维阈值场的连续段**，不需要 TG 那套打分求解。
 *
 * -----------------------------------------------------------------------------
 * 四条实现要点
 * -----------------------------------------------------------------------------
 * ① **端点必须亚采样插值。** 直接用"哪几个采样点过阈"定端点，宽度就只能是 `DoorSampleStep`
 *    的整数倍（默认 25 cm），画路时门会一格一格地跳 —— 那正是旧口径最刺眼的地方。
 *    这里在跨阈的相邻两点之间线性求根，宽度因此是路宽的连续函数。
 * ② **滞回挂在区间上，不挂在槽上。** 旧口径 key 里带段数 N，拉尺寸跨过 `round()` 边界那一帧
 *    整条边的 key 全部失配、滞回集体失效（合卷卷一 §3.x 记过这条）。区间没有编号，
 *    滞回靠**与上一帧区间是否交叠**来继承，拉尺寸不再有那个断点。
 * ③ **过宽的段要切开而不是截断。** 一条横穿整面墙的宽路应当读成一排连续拱（TG 的
 *    `add_splits_in_empty_range` 同向），截断成一个巨拱会同时撞上起拱线约束。
 *    切出来的相邻拱之间留 `PierWidth`，正好喂给 `ResolvePierSpans` 的墩样式。
 * ④ **碎环段要并掉，不是留着。**（2026-09-05 用户裁决）路是在这条环线上切口子，切剩下的才是墙；
 *    剩到几厘米的环段既砌不成墙也挡不住路。最刺眼的是转角：两边都有路时角上常留一点点，
 *    结果是两道拱之间夹一片没有意义的灰泥薄片。`MinWallSegment` 把这种空隙并掉，
 *    于是转角两边的路读成**一条**跨角段，按边切回去就是共用一根角柱的两道拱。
 */

/** 一个洞在边上占的区间（沿边弧长，与 `CSHouse_GetEdge` 的 S 同口径）。 */
struct FCSDoorRun
{
	float S0 = 0.0f;
	float S1 = 0.0f;

	float Width() const { return S1 - S0; }
	float Center() const { return (S0 + S1) * 0.5f; }
	/** 两个区间有没有重叠（滞回继承的判据，见要点 ②）。 */
	bool Overlaps(const FCSDoorRun& Other) const { return S0 < Other.S1 && Other.S0 < S1; }
};

struct FCSDoorRunParams
{
	/** 采样点算不算"路"的阈值。剖面在这条线上求根，端点即由它定。 */
	float OnWeight = 0.5f;
	/** 新洞的最小宽度；比这窄的段直接丢。 */
	float MinWidth = 40.0f;
	/**
	 * **已经开着的**洞的保活宽度（≤ MinWidth）。窄到这条线以下才关。
	 * 这就是滞回本身：没有它，宽度在 MinWidth 上下抖动时洞会闪。
	 */
	float KeepWidth = 32.0f;
	/** 单个洞的最大宽度；超过就按要点 ③ 切成多个。 */
	float MaxWidth = 260.0f;
	/** 切开时相邻两洞之间留的墩宽。 */
	float PierWidth = 20.0f;
	/**
	 * 两个洞之间**剩下的那段环线**短于它就并掉（两个洞合成一个）。0 = 不并。
	 *
	 * 路是把这条采样环线切成一段一段的，切剩下的才是墙。碎到几厘米的环段既砌不成墙、
	 * 也挡不住路 —— 尤其"转角两边都有路"时留在角上的那一点点，留着只会让相邻两拱之间
	 * 夹一片没有意义的灰泥薄片。
	 *
	 * ⚠️ 它比的是**环线上的空隙**，不是拱廊的墩：拱廊那 `PierWidth` 的间隔是按边切完之后
	 * 由 `CSHouse_SplitRun` 现切的（环上 `MaxWidth = 0` ⇒ 求解器根本看不到那些空隙），
	 * 所以把它调到大于 `PierWidth` 也不会把一排连续拱并成一个巨拱。
	 *
	 * 闭环模式下**首尾之间那段也算**（绕回的空隙），并出来的段用不取模的 S 表示。
	 */
	float MinWallSegment = 0.0f;
	/** 可用区间上界（下界是采样起点 Lo）。端点被夹在 [Lo, Hi] 内（`bClosed` 时不夹，见下）。 */
	float Hi = 0.0f;
	/**
	 * **闭环模式**：`Weights` 覆盖 `[Lo, Hi)` 一整圈（末尾**不重复**首点），跨过 Hi 会绕回 Lo。
	 *
	 * 房子的四条边接起来就是一条闭合周界 —— TG 那边本来就是这么建模的
	 * （`Rectangle2d::circular_slice` 是环形切片、`get_normal_at_u_non_normalized_corners`
	 * 在转角给未归一化的角平分线）。开了它，**一条压过转角的路会得到一条跨角的段**，
	 * 而不是被两条边各自的边界切成两段互不相干的洞。
	 *
	 * 绕回的那条段用**不取模的 S** 表示：`S0` 可能 < Lo，或 `S1` 可能 > Hi。调用方自己切回各边。
	 */
	bool bClosed = false;
};

/**
 * 把一条过宽的段切成一排拱，相邻之间留 `PierWidth`（要点 ③）。
 * `MaxWidth <= 0` = 不切。切不出足够宽的子段时原样返回一整条（宁可一个宽拱，不要一排碎拱）。
 */
inline void CSHouse_SplitRun(const FCSDoorRun& Run, float MaxWidth, float PierWidth, float MinWidth,
	TArray<FCSDoorRun>& OutRuns)
{
	const float Len = Run.Width();
	if (MaxWidth <= 0.0f || Len <= MaxWidth) { OutRuns.Add(Run); return; }

	const int32 Count = FMath::Max(1, FMath::CeilToInt(Len / MaxWidth));
	const float Pier = FMath::Max(PierWidth, 0.0f);
	const float SubWidth = (Len - (Count - 1) * Pier) / Count;
	if (SubWidth < MinWidth) { OutRuns.Add(Run); return; }

	for (int32 K = 0; K < Count; ++K)
	{
		FCSDoorRun Sub;
		Sub.S0 = Run.S0 + K * (SubWidth + Pier);
		Sub.S1 = Sub.S0 + SubWidth;
		OutRuns.Add(Sub);
	}
}

/**
 * 沿边道路剖面 → 洞区间表。**纯函数**（单测直接喂剖面，不起 world、不碰地面）。
 *
 * @param Weights  等距采样的道路权重，`Weights[i]` 对应 `S = Lo + i * Step`。
 *                 开区间模式覆盖 `[Lo, Hi]`（末点含）；闭环模式覆盖 `[Lo, Hi)`（末点**不含**）。
 * @param Lo       第一个采样点的 S。
 * @param Step     采样间距（> 0）。
 * @param Prev     上一帧的区间表。空表 = 冷启动，全部按 `MinWidth` 严阈判。
 * @param OutRuns  输出，按 S 升序。闭环时可能有一条段的 S0 < Lo（绕回的那条）。
 */
inline void CSHouse_SolveRoadRuns(TArrayView<const float> Weights, float Lo, float Step,
	const FCSDoorRunParams& Params, TArrayView<const FCSDoorRun> Prev, TArray<FCSDoorRun>& OutRuns)
{
	OutRuns.Reset();
	const int32 Num = Weights.Num();
	if (Num < 2 || Step <= 0.0f) return;

	const float Hi = FMath::Max(Params.Hi, Lo);
	const float On = Params.OnWeight;
	const float Period = Hi - Lo;

	auto SAt = [Lo, Step](float Index) { return Lo + Index * Step; };
	auto Root = [On](float A, float B)      // 从 A 走到 B 的插值系数
	{
		const float D = B - A;
		return FMath::IsNearlyZero(D) ? 0.5f : FMath::Clamp((On - A) / D, 0.0f, 1.0f);
	};

	TArray<FCSDoorRun> Raw;

	if (!Params.bClosed)
	{
		int32 Index = 0;
		while (Index < Num)
		{
			if (Weights[Index] < On) { ++Index; continue; }
			const int32 Begin = Index;
			while (Index < Num && Weights[Index] >= On) ++Index;
			const int32 Last = Index - 1;

			FCSDoorRun Run;
			Run.S0 = (Begin == 0) ? Lo : SAt(float(Begin - 1) + Root(Weights[Begin - 1], Weights[Begin]));
			// ⚠️ 两个端点的入参序都是「先段内、后段外」：`Root(A, B)` 求的是从 A 走到 B 的插值系数。
			//    写反会得到 `1 - t`，症状是端点在采样格里镜像跳一下 —— 剖面对称时**恰好看不出来**。
			Run.S1 = (Last + 1 >= Num) ? Hi : SAt(float(Last) + Root(Weights[Last], Weights[Last + 1]));
			Run.S0 = FMath::Clamp(Run.S0, Lo, Hi);
			Run.S1 = FMath::Clamp(Run.S1, Lo, Hi);
			if (Run.Width() > UE_KINDA_SMALL_NUMBER) Raw.Add(Run);
		}
	}
	else
	{
		// 闭环：先找一个**不是路**的采样点当扫描起点，从那儿绕一圈，段就不会被数组首尾切断。
		int32 Start = INDEX_NONE;
		for (int32 K = 0; K < Num; ++K)
		{
			if (Weights[K] < On) { Start = K; break; }
		}
		if (Start == INDEX_NONE)
		{
			// 整圈都是路：整条周界一个段（调用方会按边切回去）。
			FCSDoorRun Run;
			Run.S0 = Lo;
			Run.S1 = Hi;
			Raw.Add(Run);
		}
		else
		{
			int32 Offset = 0;
			while (Offset < Num)
			{
				const int32 Here = (Start + Offset) % Num;
				if (Weights[Here] < On) { ++Offset; continue; }
				const int32 BeginOffset = Offset;
				while (Offset < Num && Weights[(Start + Offset) % Num] >= On) ++Offset;
				const int32 LastOffset = Offset - 1;

				// **不取模的下标**：绕回的段在这里表现为 S 超过 Hi，调用方按边切时自然拆成两片。
				const int32 BeginAbs = Start + BeginOffset;
				const int32 LastAbs = Start + LastOffset;
				const float WPrev = Weights[(BeginAbs - 1 + Num) % Num];
				const float WNext = Weights[(LastAbs + 1) % Num];

				FCSDoorRun Run;
				Run.S0 = SAt(float(BeginAbs - 1) + Root(WPrev, Weights[BeginAbs % Num]));
				Run.S1 = SAt(float(LastAbs) + Root(Weights[LastAbs % Num], WNext));
				if (Run.Width() > UE_KINDA_SMALL_NUMBER && Run.Width() <= Period) Raw.Add(Run);
			}
		}
	}

	// 碎环段并掉（要点 ④）：两个洞之间剩下的环线短于 `MinWallSegment` 就把两个洞并成一个。
	// **必须排在宽度门槛之前**：转角两边各一条窄路时，两段各自都过不了 `MinWidth`，并起来才够
	// —— 先判宽就会把它们双双丢掉，画面上是"路明明穿过转角，一个洞都没开"。
	if (Params.MinWallSegment > 0.0f && Raw.Num() > 1)
	{
		for (int32 K = Raw.Num() - 1; K > 0; --K)
		{
			if (Raw[K].S0 - Raw[K - 1].S1 >= Params.MinWallSegment) continue;
			Raw[K - 1].S1 = Raw[K].S1;
			Raw.RemoveAt(K);
		}
		// 闭环还要看绕回那一段（末段尾 → 首段头，跨过 Hi）。并的时候把**首段的头往回拉一个周期**，
		// 合并结果因此和绕回段同一个口径：S0 < Lo，调用方按边切时自然拆成两片。
		if (Params.bClosed && Raw.Num() > 1
			&& (Raw[0].S0 + Period) - Raw.Last().S1 < Params.MinWallSegment)
		{
			Raw[0].S0 = Raw.Last().S0 - Period;
			Raw.RemoveAt(Raw.Num() - 1);
		}
	}

	// 宽度门槛按"上一帧有没有它"取严 / 宽两档（要点 ②），过宽的再切（要点 ③）。
	for (const FCSDoorRun& Run : Raw)
	{
		bool bWasOpen = false;
		for (const FCSDoorRun& Old : Prev)
		{
			if (Run.Overlaps(Old)) { bWasOpen = true; break; }
		}
		const float Gate = bWasOpen ? FMath::Min(Params.KeepWidth, Params.MinWidth) : Params.MinWidth;
		if (Run.Width() < Gate) continue;

		CSHouse_SplitRun(Run, Params.MaxWidth, Params.PierWidth, Gate, OutRuns);
	}
}
