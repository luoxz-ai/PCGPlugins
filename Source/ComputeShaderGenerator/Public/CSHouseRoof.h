#pragma once

#include "CoreMinimal.h"
#include "CSHouseRoof.generated.h"

/**
 * 屋面的共享求值器（TinyGladeHouse_Plan.md D4「屋面：抽一个共享求值器，脊向要显式」）。
 *
 * 为什么必须共享：Tiny Glade 的屋面是被瓦、梁、尖顶、雪、老虎窗**共同引用的单一求值器**
 * （逆向报告 §3.3【确凿】：支撑梁沿 circle_normal 按 roof_profile 平移并施加与瓦片**完全
 * 相同**的屋面凹陷噪声；雪 mesh 加与瓦片一致的抖动噪声保证贴合）。不共享就会脱开 —— 屋面
 * 方程一旦散在各自的生成函数里，铺瓦/铺梁/落窗谓词就会各写一份，彼此差一点点就穿帮。
 *
 * -----------------------------------------------------------------------------
 * 2026-08-31：双坡 + 山墙 → **四坡（hip）**，屋面本体交给瓦片
 * -----------------------------------------------------------------------------
 * 实拍俯视（用户提供）：TG 的屋顶是**四个坡面** + 四条角斜脊 + 中间一条短脊，且整面**全由瓦
 * 铺成**。逆向侧对得上的是 `roof_shape::ridge_length_01_from_rectangle_ratio` —— 脊长是矩形
 * 长宽比的连续函数，越接近正方形脊越短，正方形处连续退化成金字塔。
 *
 * 据此删掉的三样（都不是"暂时不做"，是**在四坡下不存在**）：
 *  · **山墙**：四坡的四面墙全是檐墙，墙顶一律平在 `EaveZ`。山墙棱柱、藤爬山墙剖面、
 *    `ECSHousePart::Gable` 一并作废。
 *  · **翻轴事件**：脊向由长轴连续导出，正方形处两轴对称、脊长为 0，「脊朝哪」这个问题根本
 *    不出现。脊向滞回（`ChooseRidgeAxis` / `RidgeSwitchRatio`）与尺寸禁带
 *    （`FootprintBandFraction`，2026-08-30 裁决四）唯一的存在理由就是遮双坡那次 90° 原地
 *    跳变，随之删除。
 *  · **实体屋面板**：屋顶是瓦片实例，房体三角汤里一片屋面都不产。于是"墙顶该砌到哪"那一整套
 *    （`SlabVerticalThickness` / `SoffitBite` / `SoffitTopZ`、檐口封口楔形、咬入量）全部作废
 *    —— 没有板底可咬，墙顶就是平的 `EaveZ`。TG 侧那条墙顶与屋面之间的漏光缝是**照抄的**，
 *    不是缺陷（室内实拍可见）。
 *
 * 四坡的高度场 = 矩形**内距**乘坡度，一行写完：
 *
 *     Z(x, y) = EaveZ + tan(pitch) · min(HalfX − |x|, HalfY − |y|)
 *
 * 四面同坡度 ⇒ 角斜脊自然落在 45° 对角线上、脊线自然缩到 |X − Y|。**脊长与角斜脊都是推论，
 * 不是独立参数**，别再给它们加旋钮。footprint 边界处按构造等于 `EaveZ`（= 墙顶），外挑段继续
 * 往下走，所以檐口高度与屋脊高度天然自洽。
 *
 * 坐标口径：脊向坐标系 (AlongRidge, AcrossRidge, Z)，原点在 footprint 中心、Z=0 是房底；
 * `RidgeToLocal()` 是它到 actor 局部 XY 的唯一映射，屋面上一切摆位都过这一个函数。
 *
 * 全部是无 GPU 依赖的纯函数，可直接进 automation 测试。
 */

/** 一座四坡屋面的完整描述。房屋 actor 每次生成时现组，不序列化（脊向也不再是状态）。 */
USTRUCT()
struct COMPUTESHADERGENERATOR_API FCSRoofDesc
{
	GENERATED_BODY()

	/** 底面尺寸 cm（局部 X/Y）。 */
	UPROPERTY() FVector2D Footprint = FVector2D(600.0, 400.0);

	/** 檐口高 = 墙高（局部 Z）。屋面在 footprint 边界处恰好等于它。 */
	UPROPERTY() float EaveZ = 300.0f;

	/** 坡度（度）。**四个坡面同一个坡度** —— 脊长与角斜脊都是它的推论。 */
	UPROPERTY() float Pitch = 35.0f;

	/** 屋檐外挑 cm（四面都挑同样多）。 */
	UPROPERTY() float Overhang = 25.0f;

	float TanPitch() const { return FMath::Tan(FMath::DegreesToRadians(FMath::Clamp(Pitch, 0.0f, 89.0f))); }

	/** cos(pitch)。铺瓦时"沿坡量的长度 → 竖直/水平分量"都按它换算，别在生成器里再写一遍三角函数。 */
	float CosPitch() const { return FMath::Cos(FMath::DegreesToRadians(FMath::Clamp(Pitch, 0.0f, 89.0f))); }

	float SinPitch() const { return FMath::Sin(FMath::DegreesToRadians(FMath::Clamp(Pitch, 0.0f, 89.0f))); }

	/** 局部 XY 半尺寸。 */
	FVector2D HalfSize() const { return Footprint * 0.5; }

	/**
	 * 脊沿哪根局部轴走。**由长轴导出，不是状态**：等坡度四坡的脊必然落在长轴上。
	 *
	 * ⚠️ **平局（正方形）归 X，是用户裁决（2026-08-31）不是随手写的 `>=`。** 别改成 `>`。
	 *
	 * ⚠️ **也别改成"无条件朝 X"** —— 几何上不成立：四坡的脊只能落在长轴上，`Y > X` 时强行
	 * 朝 X 会让 `SpanLength` 取到长边、`HalfSpan` 超过短半轴，两侧坡面越过顶点继续上升，
	 * 屋面直接翻掉。想要的"退化成金字塔"由 `RidgeLength()` 的 `max(…, 0)` 自然给出：
	 * 长宽比走到正方形时脊长连续收到 0，脊向那个布尔翻不翻都看不出来
	 * （断言在 `House.TilePyramid`：4 cm 一步扫过正方形，瓦数跳变必须 < 80）。
	 *
	 * 这条同时**取代了裁决四**（离散脊向 + `RidgeSwitchRatio` 滞回 + 尺寸禁带）：那三样唯一的
	 * 用途是挡双坡屋顶在 X 穿过 Y 时那次 90° 原地翻面，四坡下这个事件不存在了。
	 */
	bool bRidgeAlongX() const { return Footprint.X >= Footprint.Y; }

	/** 沿脊方向的底面长（长边）。⚠️ 不是脊线长，见 `RidgeLength()`。 */
	float AlongLength() const { return float(bRidgeAlongX() ? Footprint.X : Footprint.Y); }

	/** 跨度方向的底面长（短边，两坡各占一半）。 */
	float SpanLength() const { return float(bRidgeAlongX() ? Footprint.Y : Footprint.X); }

	float HalfSpan() const { return SpanLength() * 0.5f; }

	/** **脊线本身**的长度 = 长边 − 短边（正方形为 0 ⇒ 金字塔）。等坡度四坡的推论，不是参数。 */
	float RidgeLength() const { return FMath::Max(AlongLength() - SpanLength(), 0.0f); }

	float RidgeHalfLength() const { return RidgeLength() * 0.5f; }

	/** 檐口外沿的跨度坐标（含外挑）。 */
	float EaveOuterAcross() const { return HalfSpan() + Overhang; }

	/** 檐口外沿的沿脊坐标（含外挑）。四坡两端也是坡面，这一条同样是真檐口。 */
	float EaveOuterAlong() const { return AlongLength() * 0.5f + Overhang; }

	/** (沿脊, 跨度, Z) → actor 局部 (x, y, z)。线性映射，方向向量同样可以过它。 */
	FVector RidgeToLocal(double AlongRidge, double AcrossRidge, double Z) const
	{
		return bRidgeAlongX() ? FVector(AlongRidge, AcrossRidge, Z) : FVector(AcrossRidge, AlongRidge, Z);
	}

	/** actor 局部 XY → 跨度坐标（带符号，脊线上为 0）。 */
	double LocalToAcross(const FVector2D& LocalXY) const
	{
		return bRidgeAlongX() ? LocalXY.Y : LocalXY.X;
	}

	/** actor 局部 XY → 沿脊坐标。 */
	double LocalToAlong(const FVector2D& LocalXY) const
	{
		return bRidgeAlongX() ? LocalXY.X : LocalXY.Y;
	}

	/**
	 * 到 footprint 四条边的**最小内距**（边界上为 0、内部为正、外挑段为负）——四坡高度场的核心。
	 *
	 * 这就是矩形的直骨架：min 在哪条边上取到，那一点就属于哪个坡面；两条边并列取到的轨迹
	 * 正是四条 45° 角斜脊；长轴方向上两条短边并列取到的那一段就是脊线。
	 */
	double InsetDistance(const FVector2D& LocalXY) const
	{
		const FVector2D Half = HalfSize();
		return FMath::Min(Half.X - FMath::Abs(LocalXY.X), Half.Y - FMath::Abs(LocalXY.Y));
	}
};

/** 屋面在局部 XY 处的高度。不判是否落在轮廓内（那是 `CSHouseRoof_IsUnderRoof` 的事）。 */
inline float CSHouseRoof_EvalZ(const FCSRoofDesc& Desc, const FVector2D& LocalXY)
{
	return Desc.EaveZ + Desc.TanPitch() * float(Desc.InsetDistance(LocalXY));
}

/** 屋脊高（局部 Z）。四坡的脊高只由**短边**决定 —— 内距在脊线上恰好等于半跨。 */
inline float CSHouseRoof_RidgeZ(const FCSRoofDesc& Desc)
{
	return Desc.EaveZ + Desc.TanPitch() * Desc.HalfSpan();
}

/** 檐口外沿高（局部 Z）——外挑最外一圈。四面同高（同坡度、同外挑）。 */
inline float CSHouseRoof_EaveOuterZ(const FCSRoofDesc& Desc)
{
	return Desc.EaveZ - Desc.TanPitch() * Desc.Overhang;
}

/**
 * 屋面外法线（单位，朝上外）。
 *
 * 四个坡面的法线是 (±sin p, 0, cos p) / (0, ±sin p, cos p)；把**所有并列取到最小内距**的边
 * 的法线相加再归一化，角斜脊上自然给出两面的平均、金字塔尖上四面相消退化成正上方 ——
 * 不需要为脊 / 角脊 / 尖顶各写一个特例。
 */
inline FVector CSHouseRoof_EvalNormal(const FCSRoofDesc& Desc, const FVector2D& LocalXY)
{
	const FVector2D Half = Desc.HalfSize();
	const double D[4] = {                       // 到 −X / +X / −Y / +Y 四条边的内距
		Half.X + LocalXY.X, Half.X - LocalXY.X,
		Half.Y + LocalXY.Y, Half.Y - LocalXY.Y };
	const FVector2D Fall[4] = {                 // 各坡面的下降方向（水平分量）
		FVector2D(-1, 0), FVector2D(1, 0), FVector2D(0, -1), FVector2D(0, 1) };

	double MinDist = D[0];
	for (int32 I = 1; I < 4; ++I) MinDist = FMath::Min(MinDist, D[I]);

	const float SinP = Desc.SinPitch();
	FVector Sum(0, 0, 0);
	// 容差按 cm 取：并列与否是"这一点在不在角斜脊上"，那是厘米量级的事，不是 ulp 量级的。
	for (int32 I = 0; I < 4; ++I)
	{
		if (D[I] > MinDist + 0.01) continue;
		Sum += FVector(Fall[I].X * SinP, Fall[I].Y * SinP, Desc.CosPitch());
	}
	return Sum.GetSafeNormal(UE_SMALL_NUMBER, FVector::UpVector);
}

/** 该局部 XY 是否被屋面覆盖（四面外挑都算）。D8 那条"落屋顶 → 不生成窗"的谓词用它。 */
inline bool CSHouseRoof_IsUnderRoof(const FCSRoofDesc& Desc, const FVector2D& LocalXY)
{
	const FVector2D Half = Desc.HalfSize();
	return FMath::Abs(LocalXY.X) <= Half.X + Desc.Overhang
		&& FMath::Abs(LocalXY.Y) <= Half.Y + Desc.Overhang;
}
