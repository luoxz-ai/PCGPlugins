# -*- coding: utf-8 -*-
"""
石阶材质 —— 照 TG 的石阶 PS 重做（**UE 编辑器里跑**，幂等可重跑）。

## 为什么重做

石阶此前借用 `M_TinyGladeStone`（`TinyGladeMakeStoneMaterial.py` 建的）：**常数暖灰 + stone_floor
的法线/粗糙度三平面，没有 albedo 贴图**。那个选择当时的依据是「TG 的 stone_floor 系列没有 albedo」
—— 这条对**墙砖 / 地板**成立，对**石阶不成立**。

出处：`D:/MyProject/Tiny Glade/tmp/shaders/_rocky_terrain_stairs_stairs_indirect.raster.hlsl_*.ps_main.glsl`
（104 行，逐行读过）。TG 的石阶归在 **`rocky_terrain`** 那一族里（文件名前缀就是），采的是
**和岩壳同一张 `rocky_terrain_texture`**，不是 stone_floor。所以画面上石阶读成"塑料灰块"、
和周围岩石不同材质，是**配错了贴图**，不是灯光或法线的问题。

## TG 原式（逐行对照，本脚本照抄）

```glsl
vec3 _92  = cross(dFdx(P), -dFdy(P));            // 面法线（屏幕导数），逐面 ⇒ 硬边
vec3 _95  = P * 0.08 + vec3(C1);                 // 三平面域 = 世界(米) × 0.08 + 逐实例相位
vec4 _102 = texture(rocky_terrain, _95.zy);      // 法线 X 的那面
vec4 _106 = texture(rocky_terrain, _95.zx);      // 法线 Y
vec4 _110 = texture(rocky_terrain, _95.xy);      // 法线 Z
vec3 _113 = pow(abs(_92), vec3(1.0));            // 权重 = |面法线|，**一次方 = 不锐化**
vec3 _126 = (加权平均) * 1.2;                     // 混合后整体提亮 1.2
_133 = is_pebble ? _126 * 0.6 : _126;            // 石子压暗到 0.6
_138 = normalize(mix(面法线, 顶点法线, is_pebble));// 石阶写面法线、石子写顶点法线
```

四条落到 UE：

1. **周期 12.5 m**：`0.08` 是 TG 的米制口径 ⇒ 1/0.08 = 12.5 m。UE 是厘米，所以除 1250。
   ⚠️ 比岩壳那张细一倍多（岩壳 PS 是 `× 0.0333` = 30 m 周期）—— TG 有意让小构件用更密的
   平铺，照抄，别去对齐岩壳。
2. **权重不锐化**：常见三平面会 `pow(abs(N), 4~8)` 收窄过渡带，TG 是一次方。照抄。
3. **逐实例相位**：TG 把 `C1` 加在域上 ⇒ 每一级台阶采到贴图的不同位置，这是石阶之间颜色
   差异的**唯一**来源。所以本材质**不再做 `M_TinyGladeStone` 那种明度抖动** —— 那是在没有
   albedo 贴图时的替代品，有了贴图就该用相位。
   ⚠️ 相位读的是 `PerInstanceRandom + VertexColor.A` 而不是裸的 `PerInstanceRandom`：烘成
   StaticMesh 之后没有实例了，`PerInstanceRandom` 恒为 0，整片石阶会塌成同一个相位。
   两条路互斥地为零，相加即可。同 `M_TinyGladeStone` 的裁决六 ③。
4. **法线不接**：`stairs_step` 是硬边立方体，顶点法线本来就等于面法线，与 TG 写面法线等价。
   石子那一支 TG 用顶点法线，这边同样不接 ⇒ 两支共用一份图。

## 粗糙度

TG 这条 pass 的 G-buffer 只打包 albedo + normal（`out uvec2`），**粗糙度不在里面**，所以没有
可抄的值。取常数 0.85，与 `M_TG_Texture` 翻 lit 时定的那档一致。当前阶段材质验收面只有
「法线正确 + 颜色贴图正确」两条，粗糙度不追。

## 产物

- `M_TinyGladeStairs`：母材质，勾 `used_with_instanced_static_meshes`（GPU 实例路径必须）。
- `MI_TinyGladeStairStep`：`AlbedoMult = 1.2`（TG 石阶）。
- `MI_TinyGladeStairPebble`：`AlbedoMult = 0.72`（= 1.2 × 0.6，TG 石子那一档）。
- 挂到 `BP_TinyGladeGround` 的 CDO **与**关卡实例两处 —— 只改 CDO 的话，关卡里已经存在的
  实例内存中还揣着构造时拷走的旧值，看不出变化（2026-09-06 在草的坡度阈值上刚踩过）。

日志自诊断：最后一行是 `STAIRSMAT DONE ok=<bool>`。
"""
import unreal

PKG = "/PCGPlugins/HouseTest"
MEL = unreal.MaterialEditingLibrary
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()
EAL = unreal.EditorAssetLibrary

MAT_NAME = "M_TinyGladeStairs"
MAT_PATH = "%s/%s" % (PKG, MAT_NAME)
MI_STEP = "%s/MI_TinyGladeStairStep" % PKG
MI_PEBBLE = "%s/MI_TinyGladeStairPebble" % PKG
ROCKY_TEX = "%s/TinyGladeAsset/Textures/rocky_terrain" % PKG

TILE_CM = 1250.0        # TG 的 0.08 米制口径换算：1 / 0.08 = 12.5 m
MULT_STEP = 1.2         # TG PS 的 `* 1.2`
MULT_PEBBLE = 1.2 * 0.6  # TG PS 的 `is_pebble ? * 0.6`
ROUGHNESS = 0.85


def _log(msg):
    unreal.log("STAIRSMAT %s" % msg)


def _custom_input(name):
    i = unreal.CustomInput()
    i.set_editor_property("input_name", name)
    return i


def _scalar(mat, name, default, x, y, group="Stairs"):
    p = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, x, y)
    p.set_editor_property("parameter_name", name)
    p.set_editor_property("default_value", default)
    p.set_editor_property("group", group)
    return p


def build_master():
    tex = EAL.load_asset(ROCKY_TEX)
    if not tex:
        unreal.log_error("STAIRSMAT 找不到 %s —— TG 的岩地贴图是这份材质的全部内容，中止" % ROCKY_TEX)
        return None

    if EAL.does_asset_exist(MAT_PATH):
        EAL.delete_asset(MAT_PATH)
    mat = TOOLS.create_asset(MAT_NAME, PKG, unreal.Material, unreal.MaterialFactoryNew())
    # GPU 实例化路径必须勾；没勾的材质在实例路径上会被引擎**静默换成默认材质**，
    # 画面一片灰而所有 readback 断言照绿（与裙边摆件同一条陷阱）。
    mat.set_editor_property("used_with_instanced_static_meshes", True)

    wp = MEL.create_material_expression(mat, unreal.MaterialExpressionWorldPosition, -900, 0)
    vn = MEL.create_material_expression(mat, unreal.MaterialExpressionVertexNormalWS, -900, 160)

    # 逐实例相位。裸 PerInstanceRandom 烘完恒 0，必须与 VertexColor.A 相加（见文件头 ③）。
    rnd = MEL.create_material_expression(mat, unreal.MaterialExpressionPerInstanceRandom, -1150, 320)
    vcol = MEL.create_material_expression(mat, unreal.MaterialExpressionVertexColor, -1150, 440)
    phase = MEL.create_material_expression(mat, unreal.MaterialExpressionAdd, -900, 340)
    MEL.connect_material_expressions(rnd, "", phase, "A")
    MEL.connect_material_expressions(vcol, "A", phase, "B")

    texobj = MEL.create_material_expression(mat, unreal.MaterialExpressionTextureObjectParameter, -900, 560)
    texobj.set_editor_property("parameter_name", "RockyTerrain")
    texobj.set_editor_property("texture", tex)
    texobj.set_editor_property("group", "Stairs")

    p_tile = _scalar(mat, "StairsTilePeriodCm", TILE_CM, -900, 720)
    p_mult = _scalar(mat, "AlbedoMult", MULT_STEP, -900, 800)

    tri = MEL.create_material_expression(mat, unreal.MaterialExpressionCustom, -450, 200)
    tri.set_editor_property("description", "StairsTriplanarTG")
    tri.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT3)
    tri.set_editor_property("inputs", [
        _custom_input("WP"), _custom_input("N"), _custom_input("Phase"),
        _custom_input("Tex"), _custom_input("TileCm"), _custom_input("Mult")])
    # 平面配对与 TG 逐字对齐：法线 X 的那面采 (z,y)，Y 采 (z,x)，Z 采 (x,y)。
    # 这个配对与坐标系手性无关（TG 是 Y-up、UE 是 Z-up，但"用另外两轴"这件事一样），
    # 所以直接用 UE 自己的 xyz，不做轴变换。
    tri.set_editor_property("code", """float3 p = WP / max(TileCm, 1.0f) + Phase;
// 权重 = |法线|，**一次方**：TG 的 pow(abs(n), 1.0) 就是不锐化（常见三平面会 pow 4~8）。
float3 n = abs(N);
float  s = max(n.x + n.y + n.z, 1e-4f);
float3 c = Texture2DSample(Tex, TexSampler, p.zy).rgb * n.x
         + Texture2DSample(Tex, TexSampler, p.zx).rgb * n.y
         + Texture2DSample(Tex, TexSampler, p.xy).rgb * n.z;
return (c / s) * Mult;""")
    for src, out_pin, pin in ((wp, "", "WP"), (vn, "", "N"), (phase, "", "Phase"),
                              (texobj, "", "Tex"), (p_tile, "", "TileCm"), (p_mult, "", "Mult")):
        MEL.connect_material_expressions(src, out_pin, tri, pin)
    MEL.connect_material_property(tri, "", unreal.MaterialProperty.MP_BASE_COLOR)

    rough = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -450, 620)
    rough.set_editor_property("r", ROUGHNESS)
    MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)

    # Normal 有意不接：stairs_step 是硬边立方体，顶点法线 = 面法线，与 TG 写面法线等价。
    MEL.recompile_material(mat)
    EAL.save_loaded_asset(mat)
    _log("master built %s" % MAT_PATH)
    return mat


def build_instance(mat, path, mult):
    name = path.rsplit("/", 1)[1]
    if EAL.does_asset_exist(path):
        EAL.delete_asset(path)
    mi = TOOLS.create_asset(name, PKG, unreal.MaterialInstanceConstant,
                            unreal.MaterialInstanceConstantFactoryNew())
    MEL.set_material_instance_parent(mi, mat)
    MEL.set_material_instance_scalar_parameter_value(mi, "AlbedoMult", mult)
    MEL.update_material_instance(mi)
    EAL.save_loaded_asset(mi)
    _log("%s AlbedoMult=%.3f" % (path, mult))
    return mi


def assign(step_mi, pebble_mi):
    """CDO 与关卡实例两处都写。只改 CDO 的话关卡里已存在的实例不会变（实测踩过）。"""
    ok = True
    bp = EAL.load_asset("%s/BP_TinyGladeGround" % PKG)
    if bp:
        cdo = unreal.get_default_object(bp.generated_class())
        cdo.set_editor_property("StairMaterial", step_mi)
        cdo.set_editor_property("StairPebbleMaterial", pebble_mi)
        EAL.save_loaded_asset(bp)
        _log("CDO assigned")
    else:
        unreal.log_error("STAIRSMAT 找不到 BP_TinyGladeGround")
        ok = False

    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    n = 0
    for a in sub.get_all_level_actors():
        if isinstance(a, unreal.CSGroundActor):
            a.set_editor_property("StairMaterial", step_mi)
            a.set_editor_property("StairPebbleMaterial", pebble_mi)
            n += 1
    _log("level actors assigned: %d" % n)
    return ok


if not globals().get("STAIRSMAT_AS_LIBRARY"):
    _mat = build_master()
    _ok = False
    if _mat:
        _step = build_instance(_mat, MI_STEP, MULT_STEP)
        _pebble = build_instance(_mat, MI_PEBBLE, MULT_PEBBLE)
        _ok = assign(_step, _pebble)
    _log("DONE ok=%s" % _ok)
