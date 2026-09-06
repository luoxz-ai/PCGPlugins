# -*- coding: utf-8 -*-
"""
拉尺寸抓手（D5）那四根"窗框"条子的**高亮自发光材质**。

--- 为什么不用引擎自带的 gizmo 材质 ------------------------------------------------
`/Engine/EditorMaterials/TransformGizmoMaterial*` 全是 unlit 的，而本工程 2026-08-30 裁决六
钉死「交付路径上不许 unlit」（`M_TG_Texture` 当时就是被这条翻成 `MSM_DefaultLit` 的）。
抓手虽然只在编辑器里出现，但没有理由为它开一个 unlit 的例外 —— **Lit + 强自发光**在观感上
与 unlit 几乎不可分（自发光项不受光照影响），却不需要动着色模型。

另一条更实际的理由：引擎那几张材质没有可调的颜色参数，想让四条边分色或做悬停高亮就没有抓手。

--- 参数 ---------------------------------------------------------------------------
`HighlightColor`（Vector，默认青绿）× `Intensity`（Scalar，默认 6）→ Emissive Color。
`BaseColor` 压到近黑：条子应该读成"一根发光的线"，而不是"一根被打亮的塑料棒"。
Emissive 超过 1 才会在默认曝光下真的亮起来；6 是同机位试出来的，低于 3 在白天场景里发灰。

材质本体做成 `Material` 而不是 `MaterialInstance`：只有一个消费者，多一层实例没有收益。
C++ 侧是**惰性加载**（`CSHouseResizeHandleActor.cpp` 的 `GHandleMaterialPath`），
所以这份脚本跑完不需要重启编辑器 —— 下一次进拉尺寸模式就会用上。

用法::

    UnrealEditor-Cmd.exe <uproject> -ExecutePythonScript=".../TinyGladeMakeHandleMaterial.py" \\
        -unattended -nopause -abslog=<独立日志>

日志自诊断：最后一行是 `HANDLE MATERIAL OK` 或 `HANDLE MATERIAL FAILED`。
"""
import unreal

PKG = "/PCGPlugins/HouseTest"
NAME = "M_CSHandleHighlight"

MEL = unreal.MaterialEditingLibrary
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()

_failed = []


def log(msg):
    unreal.log("[HandleMaterial] %s" % msg)


def fail(msg):
    _failed.append(msg)
    unreal.log_error("[HandleMaterial] %s" % msg)


def build():
    path = "%s/%s" % (PKG, NAME)
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)

    mat = TOOLS.create_asset(NAME, PKG, unreal.Material, unreal.MaterialFactoryNew())
    if not mat:
        fail("建不出材质 %s" % path)
        return None

    # 裁决六：交付路径上不许 unlit。自发光在 Lit 下照样不受光照影响，观感等价。
    mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
    # 条子很细，侧面看过去容易只剩一条背面 —— 两面画一次性堵掉。
    mat.set_editor_property("two_sided", True)

    # ---- Emissive = HighlightColor × Intensity ----
    color = MEL.create_material_expression(mat, unreal.MaterialExpressionVectorParameter, -600, 0)
    color.set_editor_property("parameter_name", "HighlightColor")
    # 青绿：在本工程的砖红/土黄场景里对比最强，且不与门框砖、藤蔓撞色。
    color.set_editor_property("default_value", unreal.LinearColor(0.12, 0.95, 0.85, 1.0))

    intensity = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -600, 180)
    intensity.set_editor_property("parameter_name", "Intensity")
    # 低于 3 在白天场景里发灰；6 是同机位试出来的。
    intensity.set_editor_property("default_value", 6.0)

    emissive = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -300, 60)
    MEL.connect_material_expressions(color, "", emissive, "A")
    MEL.connect_material_expressions(intensity, "", emissive, "B")
    MEL.connect_material_property(emissive, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)

    # ---- BaseColor 近黑 + 粗糙：读成"发光的线"而不是"被打亮的棒" ----
    base = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -300, 300)
    base.set_editor_property("constant", unreal.LinearColor(0.02, 0.02, 0.02, 1.0))
    MEL.connect_material_property(base, "", unreal.MaterialProperty.MP_BASE_COLOR)

    rough = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -300, 420)
    rough.set_editor_property("r", 0.9)
    MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)

    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
    log("built %s" % path)
    return mat


mat = build()

# 自诊断：建完当场再读一遍，确认资产真的落盘了（`create_asset` 成功但保存失败是常见形态）。
if mat and unreal.EditorAssetLibrary.does_asset_exist("%s/%s" % (PKG, NAME)):
    log("HANDLE MATERIAL OK")
else:
    fail("材质没能落盘")
    log("HANDLE MATERIAL FAILED")
