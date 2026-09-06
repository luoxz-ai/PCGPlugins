# -*- coding: utf-8 -*-
"""门扇（D6 的 2026-09-04 新增一档）的资产接线：建材质 + 挂到 `BP_TinyGladeHouse` 的 CDO。

门扇网格用 TG 自己的 `decorators/door.glb`（本仓库已提取为
`/PCGPlugins/HouseTest/TinyGladeAsset/Meshes/door`）。⚠️ **它带 COLOR_0、没有 UV** ——
和 TG 那批砖/瓦一路，观感靠顶点色而不是贴图（`M_TG_*` 那 176 个 MI 是看图材质，不是候选母材质）。
所以这里建一张自己的 `M_TinyGladeDoor`：`VertexColor × Tint → BaseColor`，`MSM_DefaultLit`。

⚠️ **与 TG 的一处有意不同构**：TG 的门扇是 `construct_gates` 按 `segment_length` 逐 quad
**现搭**的（`DoorMeshInProgress::add_quad`），资产里根本没有门扇网格，只有把手
（`wooden_gate/door_handle_circle.glb`）。本项目用现成网格**按洞宽缩放**，代价是宽度差得多时
板条比例会被拉伸 —— `ACSHouseActor::DoorLeafMaxStretch` 就是夹这个的（超过倍数只缩不拉）。

⚠️ **只写 BP 的 CDO，不存关卡**：`DoorLeafMesh` 是新属性，关卡里已摆的 actor 没有它的
per-instance 覆盖，因此会直接继承 CDO 的值。顺带避开"后台进程存关卡会和用户开着的编辑器
抢同一个包"那条（同 `TinyGladeMakeWallMaterials.py` 文件头）。

用法::

    UnrealEditor.exe <project> -ExecutePythonScript="<本文件>"
"""
import unreal

PKG = "/PCGPlugins/HouseTest"
# TG 的门是**按尺寸分档**的（`DecoratorSubtype` 里的 rank）：120 / 150 / 180 cm 宽 × 262.5 高，
# 1002 / 1242 / 1716 顶点，带 COLOR_0 + UV。代码按 native 宽度挑档，顺序无所谓。
#
# ⚠️ **不要用 `Meshes/door`**：那是 `decorators/door.glb`，36 顶点、120×250×75、**没有 UV**
# —— 交互/碰撞代理盒，不是可见门扇（2026-09-04 第一版错用了它，画面是一块纯色板）。
# 同族的 `*_collision` / `*_interaction` / `*_outline` / `decorator_test_door` 同理。
MESH_PATHS = ["%s/TinyGladeAsset/Meshes/balcony_door_rank1" % PKG,
              "%s/TinyGladeAsset/Meshes/balcony_door_rank2" % PKG,
              "%s/TinyGladeAsset/Meshes/balcony_door_rank3" % PKG]
MEL = unreal.MaterialEditingLibrary
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()


def make_or_load(name):
    """已存在就清空表达式原地重建 —— 不要 delete_asset（关卡与 BP 引用的是资产路径）。

    ⚠️ `delete_all_material_expressions` 一次只删一趟（2026-09-03 实测），少了这个循环
    重跑一次就把两代节点叠在一张图里，参数表变成两代的并集且不报任何错。
    """
    path = "%s/%s" % (PKG, name)
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        mat = unreal.EditorAssetLibrary.load_asset(path)
        for _ in range(16):
            if MEL.get_num_material_expressions(mat) == 0:
                break
            MEL.delete_all_material_expressions(mat)
        return mat
    return TOOLS.create_asset(name, PKG, unreal.Material, unreal.MaterialFactoryNew())


def build_door_material():
    mat = make_or_load("M_TinyGladeDoor")
    mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_OPAQUE)

    vcol = MEL.create_material_expression(mat, unreal.MaterialExpressionVertexColor, -600, 0)
    tint = MEL.create_material_expression(mat, unreal.MaterialExpressionVectorParameter, -600, 160)
    tint.set_editor_property("parameter_name", "Tint")
    # 木门那一档：比墙暗、比屋面暖。顶点色若是全白，画面上就是这个色本身。
    tint.set_editor_property("default_value", unreal.LinearColor(0.34, 0.21, 0.13, 1.0))

    # ⚠️ **顶点色默认不参与**（2026-09-04 出图实测：门扇渲染成纯黑）。
    # `door.glb` 带 COLOR_0，但导入后这张 mesh 的顶点色是 0 —— 直接 `VertexColor × Tint`
    # 得到的是黑板子，而且**不报任何错**（材质编译通过、组件也画得出来，只是全黑）。
    # 所以改成 `Tint × lerp(1, VertexColor, UseVertexColor)`：默认 0 = 纯 Tint，
    # 哪天换一张真带顶点色的门扇资产，把这个参数调到 1 就接回去。
    one = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -600, 300)
    one.set_editor_property("r", 1.0)
    use_vc = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -600, 380)
    use_vc.set_editor_property("parameter_name", "UseVertexColor")
    # `balcony_door_rank*` 带真顶点色（门板/门框/五金分色），所以默认接回去。
    # ⚠️ 之前那张 `door` 代理盒的顶点色是 0 ⇒ 乘出来全黑，那是"用错资产"的症状不是材质的错。
    use_vc.set_editor_property("default_value", 1.0)

    pick = MEL.create_material_expression(mat, unreal.MaterialExpressionLinearInterpolate, -430, 200)
    MEL.connect_material_expressions(one, "", pick, "A")
    MEL.connect_material_expressions(vcol, "RGB", pick, "B")
    MEL.connect_material_expressions(use_vc, "", pick, "Alpha")

    mul = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -320, 40)
    MEL.connect_material_expressions(pick, "", mul, "A")
    MEL.connect_material_expressions(tint, "", mul, "B")
    MEL.connect_material_property(mul, "", unreal.MaterialProperty.MP_BASE_COLOR)

    rough = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -320, 220)
    rough.set_editor_property("parameter_name", "Roughness")
    rough.set_editor_property("default_value", 0.82)
    MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)

    # ⚠️ **用途标记要在资产上预先备好**（2026-09-04 崩过一次）：门扇网格若带 Nanite 数据，
    # 组件注册时会走 `AuditMaterials` → `CheckMaterialUsage(MATUSAGE_Nanite)`，材质缺这一位就
    # 当场重编译 + `FlushRenderingCommands()`，在 post-tick 组件更新阶段里直接断言炸编辑器。
    # C++ 侧已经给门扇组件关了 Nanite（`bDisallowNanite`），这里再补一道 —— 换别的用法时不至于
    # 重踩。与"母材质没勾 `bUsedWithInstancedStaticMeshes` 被静默换成默认材质"同一族。
    for flag in ("used_with_nanite", "used_with_static_lighting"):
        try:
            mat.set_editor_property(flag, True)
        except Exception as exc:
            unreal.log_warning("DOORLEAF could not set %s: %s" % (flag, exc))

    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
    return mat


meshes = []
for path in MESH_PATHS:
    mesh = unreal.EditorAssetLibrary.load_asset(path)
    if mesh is None:
        raise RuntimeError("door mesh missing: %s" % path)
    # 实测尺寸打进日志：`RebuildDoorLeaves` 的挑档与竖直/宽度轴都是从包围盒自判的，
    # 这几行是那条判定的输入，出问题时第一个要看的就是它。
    extent = mesh.get_bounds().box_extent
    unreal.log("DOORLEAF rank mesh=%-22s size=(%.1f, %.1f, %.1f) cm  verts=%d"
               % (mesh.get_name(), extent.x * 2, extent.y * 2, extent.z * 2, mesh.get_num_vertices(0)))
    meshes.append(mesh)

door_mat = build_door_material()

bp = unreal.EditorAssetLibrary.load_asset("%s/BP_TinyGladeHouse" % PKG)
if bp:
    cdo = unreal.get_default_object(bp.generated_class())
    cdo.set_editor_property("DoorLeafMeshes", meshes)
    cdo.set_editor_property("DoorLeafMaterial", door_mat)
    unreal.EditorAssetLibrary.save_loaded_asset(bp)
    unreal.log("DOORLEAF assigned to BP_TinyGladeHouse CDO")

unreal.log("DOORLEAF DONE")
