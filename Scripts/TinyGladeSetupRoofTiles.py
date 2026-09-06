# -*- coding: utf-8 -*-
"""把 TG 提取的屋面瓦接到演示房子上（`CSHouseTile` 的资产侧收尾）。

C++ 侧（`CSHouseTile.h` / `ACSHouseActor::RebuildRoofTiles`）已经齐了、单测全绿，但
**`RoofTileMesh` 在关卡与 BP CDO 里都是空的**，而"留空 = 不铺瓦" ⇒ 四坡屋顶一片瓦都没有，
且**没有任何断言会红**（房体三角汤里本来就不含屋面）。这正是仓库记过的那一枪：
「`StairMesh` 那样在两张演示关卡里一直是空的，而所有断言照绿」。本脚本补上这一步。

判据（日志断言，按 `ROOFTILE SETUP OK` / `FAILED` 判，不看退出码）：
  · 网格与材质资产真的存在
  · **母材质勾了 `bUsedWithInstancedStaticMeshes`** —— 没勾的话引擎**静默换成默认材质**，
    症状与"没绑材质"逐像素相同（藤蔓那轮栽过，见 `TinyGladeMakeIvyMaterials.py` 抬头）
  · 母材质不是 `MSM_Unlit`（2026-08-30 裁决六：交付路径上的材质一律不许 unlit）
  · 接完之后 `GetRoofTileCount() > 0` 且 `GetRoofTileUndrawableReason()` 是空串

⚠️ **CDO 与关卡实例都要写**：CDO 的默认值不传播到已经存在的实例（实测陷阱，全仓通用）。
只写 CDO 的症状是"脚本说成功了，打开关卡还是没有瓦"。

跑法（编辑器不能开着）：
  UnrealEditor-Cmd.exe <uproject> -ExecutePythonScript="<本文件>" -unattended -nosplash -stdout -AbsLog=<log>
"""
import unreal

PKG = "/PCGPlugins/HouseTest"
TILE_MESH = "/PCGPlugins/HouseTest/TinyGladeAsset/Meshes/roof_tile"
TILE_MAT = "/PCGPlugins/HouseTest/TinyGladeAsset/Materials/MI_roof_tile_normal"
SPIRE_MESH = "/PCGPlugins/HouseTest/TinyGladeAsset/Meshes/roof_spire"
# ⚠️ **尖顶必须用三平面材质**：`roof_spire.json` 的属性只有 Position / Normal / roof_profile_mult，
# **没有 UV** —— 任何跟 UV 走的贴图（`MI_roof_tile_normal` 就是）在它身上采的都是垃圾。
# `M_TinyGladeStone` 是石阶那轮建的三平面石头材质，正好不吃 UV。
SPIRE_MAT = "/PCGPlugins/HouseTest/M_TinyGladeStone"
HOUSE_BP = "/PCGPlugins/HouseTest/BP_TinyGladeHouse.BP_TinyGladeHouse_C"

FAILS = []


def log(msg):
    unreal.log("[RoofTile] %s" % msg)


def check(label, ok, detail=""):
    if ok:
        log("[PASS] %s %s" % (label, detail))
    else:
        FAILS.append(label)
        unreal.log_error("[FAIL] %s %s" % (label, detail))


def actors():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()


def houses():
    out = []
    for a in actors():
        if a and a.get_class().get_name().startswith("BP_TinyGladeHouse"):
            out.append(a)
    return out


def vet_material(mat):
    """母材质的两条执行面判据。`MaterialInstance` 要一路走到根母材质才问得到。"""
    root = mat
    for _ in range(8):
        parent = None
        try:
            parent = root.get_editor_property("parent")
        except Exception:
            parent = None
        if parent is None:
            break
        root = parent
    log("材质 %s -> 母材质 %s" % (mat.get_name(), root.get_name()))

    instanced = None
    try:
        instanced = root.get_editor_property("used_with_instanced_static_meshes")
    except Exception as exc:
        log("读 used_with_instanced_static_meshes 失败：%s" % exc)
    # ⚠️ 没勾 = 引擎**静默换默认材质**，症状与"没绑材质"逐像素相同。这条比"材质非空"重要得多。
    check("母材质勾了 bUsedWithInstancedStaticMeshes", instanced is True,
          "used_with_instanced_static_meshes=%s（%s）" % (instanced, root.get_name()))

    shading = None
    try:
        shading = root.get_editor_property("shading_model")
    except Exception:
        pass
    check("母材质不是 Unlit（裁决六）",
          shading != unreal.MaterialShadingModel.MSM_UNLIT, "shading_model=%s" % shading)
    return root


def wire_cdo(mesh, mat, spire, spire_mat):
    """写 BP 的 CDO —— 供用户手动拖放新房子时自带默认值。"""
    bp_class = unreal.load_class(None, HOUSE_BP)
    if bp_class is None:
        check("加载得到 BP_TinyGladeHouse_C", False, HOUSE_BP)
        return
    cdo = unreal.get_default_object(bp_class)
    if cdo is None:
        check("拿得到 BP 的 CDO", False)
        return
    cdo.set_editor_property("RoofTileMesh", mesh)
    cdo.set_editor_property("RoofMaterial", mat)
    cdo.set_editor_property("bRoofTilesEnabled", True)
    if spire is not None:
        cdo.set_editor_property("RoofFinialMesh", spire)
    if spire_mat is not None:
        cdo.set_editor_property("RoofFinialMaterial", spire_mat)
    # CDO 改完要存 BP 资产，否则重开编辑器就回到空值。
    unreal.EditorAssetLibrary.save_asset(HOUSE_BP.split(".")[0])
    log("CDO 已写：RoofTileMesh=%s RoofMaterial=%s RoofFinialMesh=%s" % (
        mesh.get_name(), mat.get_name(), spire.get_name() if spire else None))


def wire_level(level, mesh, mat, spire, spire_mat):
    unreal.EditorLoadingAndSavingUtils.load_map("%s/%s" % (PKG, level))
    hs = houses()
    check("%s 里有房子" % level, len(hs) > 0, "houses=%d" % len(hs))
    if not hs:
        return None

    for h in hs:
        # ⚠️ 实例上必须再写一份：CDO 的默认值不传播到已经存在的实例。
        h.set_editor_property("RoofTileMesh", mesh)
        h.set_editor_property("RoofMaterial", mat)
        h.set_editor_property("bRoofTilesEnabled", True)
        if spire is not None:
            h.set_editor_property("RoofFinialMesh", spire)
        if spire_mat is not None:
            h.set_editor_property("RoofFinialMaterial", spire_mat)
        h.call_method("ReevaluateSite")

    unreal.EditorLoadingAndSavingUtils.save_current_level()

    total = 0
    for h in hs:
        n = h.get_roof_tile_count()
        total += n
        why = str(h.get_roof_tile_undrawable_reason())
        log("%s / %s: tiles=%d reason=%r" % (level, h.get_actor_label(), n, why))
        # 空串 = 可画（C++ 侧只在失败时写原因）。别写成 bool(why) —— 判据正好反过来。
        check("%s / %s 的瓦会被画出来" % (level, h.get_actor_label()), why == "", why)
        check("%s / %s 铺出了瓦" % (level, h.get_actor_label()), n > 0, "tiles=%d" % n)

        if spire is not None:
            spires = h.get_roof_finial_count()
            why_s = str(h.get_roof_finial_undrawable_reason())
            # 矩形底面 ⇒ 脊有长度 ⇒ 两端各一根；正方形脊长为 0 ⇒ 退化成金字塔尖上的一根。
            fp = h.get_editor_property("FootprintSize")
            want = 1 if abs(float(fp.x) - float(fp.y)) < 1.0 else 2
            check("%s / %s 立起了 %d 根尖顶" % (level, h.get_actor_label(), want),
                  spires == want, "finials=%d footprint=(%.0f, %.0f)" % (spires, fp.x, fp.y))
            check("%s / %s 的尖顶会被画出来" % (level, h.get_actor_label()), why_s == "", why_s)
    log("%s 合计 %d 片瓦" % (level, total))
    return total


def main():
    mesh = unreal.EditorAssetLibrary.load_asset(TILE_MESH)
    check("瓦网格存在", mesh is not None, TILE_MESH)
    mat = unreal.EditorAssetLibrary.load_asset(TILE_MAT)
    check("瓦材质存在", mat is not None, TILE_MAT)
    if mesh is None or mat is None:
        return

    b = mesh.get_bounding_box()
    size = b.max - b.min
    # 三轴尺寸打进日志：`MakeRoofTileParams` 靠最薄的一轴认屋面法线，认错了瓦会立起来。
    log("瓦网格尺寸 cm: X=%.1f Y=%.1f Z=%.1f（最薄的一轴 = 屋面法线）" % (size.x, size.y, size.z))

    vet_material(mat)

    spire = unreal.EditorAssetLibrary.load_asset(SPIRE_MESH)
    check("尖顶网格存在", spire is not None, SPIRE_MESH)
    spire_mat = unreal.EditorAssetLibrary.load_asset(SPIRE_MAT)
    check("尖顶材质存在", spire_mat is not None, SPIRE_MAT)
    if spire is not None:
        sb = spire.get_bounding_box()
        ss = sb.max - sb.min
        # 三轴尺寸打进日志：`RebuildRoofFinials` 靠**最长**的一轴认"朝上"那根，认错了尖顶会躺倒。
        # TG 那张是 y-up 导出的（2.206 × 3.038 × 2.206 m），所以这里预期最长轴是 Y。
        log("尖顶网格尺寸 cm: X=%.1f Y=%.1f Z=%.1f（最长的一轴 = 朝上）" % (ss.x, ss.y, ss.z))
        log("尖顶枢轴相对包围盒: min=(%.1f, %.1f, %.1f) max=(%.1f, %.1f, %.1f)" % (
            sb.min.x, sb.min.y, sb.min.z, sb.max.x, sb.max.y, sb.max.z))

    wire_cdo(mesh, mat, spire, spire_mat)
    wire_level("L_HouseGroundDemo", mesh, mat, spire, spire_mat)

    unreal.log("========== ROOFTILE SETUP SUMMARY ==========")
    for f in FAILS:
        unreal.log_error("  failed: %s" % f)
    unreal.log("ROOFTILE SETUP FAILED" if FAILS else "ROOFTILE SETUP OK")


try:
    main()
except Exception:
    # 准备阶段抛异常 ⇒ 后面的 Quit 再也执行不到，任务看着像挂死（踩过的坑）。
    import traceback
    unreal.log_error("ROOFTILE SETUP EXCEPTION:\n%s" % traceback.format_exc())
    unreal.log("ROOFTILE SETUP FAILED")
