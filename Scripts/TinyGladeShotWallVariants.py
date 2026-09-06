# -*- coding: utf-8 -*-
"""墙面材质**默认值**的候选扫描：同机位、同光照，一次编辑器启动拍完 N 组参数。

为什么需要它：`M_TinyGladeWall` 的默认值是 2026-09-03 对着 numpy 仿真调出来的，而仿真
**没有光照、没有 tonemap、没有天光**。2026-09-04 第一次在引擎里看到实景（`walltex_v2_*`）
发现两者差得很远 —— 仿真里的三文鱼粉灰泥在关卡光照下读成暗红褐，砖读成橄榄绿鳞片，
剥落覆盖了大半面墙。这些都不是仿真能预告的，只能一组组拍出来比。

比什么：`Docs/TinyGlade/img/tiny-glade-ref-corner-pillar.jpg` 是判据 —— TG 的抹灰墙是
**高调奶白**、剥落是**小块稀疏**、砖是**淡砂黄大块 + 细暗缝**。整幅低对比。

⚠️ `VARIANTS` 这张表**每轮扫描都要改**（它就是这轮要比的东西），别当成定值。
⚠️ 这个脚本**不改任何资产**：所有候选都是 `MI_TinyGladeWall` 的 MID 覆盖，跑完就没。
   选定之后要落到默认值，改的是 `TinyGladeMakeWallMaterials.py` 再重跑那一个。

用法::

    set TG_SHOT_TAG=p1
    UnrealEditor.exe <project> -ExecCmds="py .../TinyGladeShotWallVariants.py"

产物：`Saved/TinyGladeShots/wallvar_<tag>_<变体名>_<机位>.png`。

坑与 `TinyGladeShotWallTexture.py` 同一张表（尤其 ⑧ ViewState + ⑨ 一帧一次预热 128）。
"""
import math
import os
import traceback

import unreal

TAG = os.environ.get("TG_SHOT_TAG", "p1")

PKG = "/PCGPlugins/HouseTest"
TEX = "/PCGPlugins/HouseTest/TinyGladeAsset/Textures"
OUT_DIR = unreal.Paths.project_saved_dir() + "TinyGladeShots"
W, H_PIX = 1600, 900

FIRST_TICK = 45
SETTLE = 6
WARMUP = int(os.environ.get("TG_SHOT_WARMUP", "128"))

# 候选表。`s` = 标量覆盖，`t` = 贴图覆盖（贴图名在 TinyGladeAsset/Textures 下）。
# `cur` 是今天的默认值，必须留着当基准 —— 没有基准的对照图读不出"改好了多少"。
#
# 色卡说明（`plaster_colors_layer00..07` / `brick_colors_layer00..04` 是 TG 的**逐房配色选项**，
# 不是"那张灰泥贴图"）：P00 三文鱼粉 / P01 赭黄 / P02 灰蓝 / P03 炭灰 / P04 淡灰绿奶白 /
# P05 鼠尾草绿 / P06 浅褐 / P07 藕粉；B00 米黄带红纹 / B01 淡奶黄 / B02 墨橄榄 / B03 橄榄黄 /
# B04 灰藕。参照图里那面墙是 P04 一档，拱石是 B01 一档。
VARIANTS = [
    ("new", {}, {}),
    # 凸出砖的密度：`ProtrudeLevel` 是灰泥"液面"，砖高（归一化到 ~1）过线才顶出来。
    # 0.88 在引擎里读成一地碎屑而不是"偶尔几块石头"，这两档用来定档。
    ("prot93", {"ProtrudeLevel": 0.93}, {}),
    ("prot83", {"ProtrudeLevel": 0.83}, {}),
    # 配色档（新的剥落量下重看一遍，2026-09-03 那版是在旧剥落量下选的）。
    ("P00", {}, {"PlasterColor": "plaster_colors_layer00"}),
    ("P06", {}, {"PlasterColor": "plaster_colors_layer06"}),
    ("P02", {}, {"PlasterColor": "plaster_colors_layer02"}),
]


def make_pp():
    """坑 ⑤。不碰曝光：关卡 PPV 已经把曝光钉死，开了 ViewState 就会吃到。"""
    pp = unreal.PostProcessSettings()
    pp.set_editor_property("override_dynamic_global_illumination_method", True)
    pp.set_editor_property("dynamic_global_illumination_method", unreal.DynamicGlobalIlluminationMethod.LUMEN)
    pp.set_editor_property("override_reflection_method", True)
    pp.set_editor_property("reflection_method", unreal.ReflectionMethod.LUMEN)
    pp.set_editor_property("override_lumen_surface_cache_resolution", True)
    pp.set_editor_property("lumen_surface_cache_resolution", 1.0)
    return pp


def look_at(frm, to):
    """⚠️ `unreal.Rotator` 是 (roll, pitch, yaw)，只用关键字参数。"""
    dx, dy, dz = to.x - frm.x, to.y - frm.y, to.z - frm.z
    flat = math.hypot(dx, dy)
    return unreal.Rotator(roll=0.0,
                          pitch=math.degrees(math.atan2(dz, max(flat, 1e-3))),
                          yaw=math.degrees(math.atan2(dy, dx)))


def setup():
    ACTORS = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    unreal.EditorLoadingAndSavingUtils.load_map("%s/L_HouseGroundDemo" % PKG)
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    spawned = []

    house = next((a for a in ACTORS.get_all_level_actors() if a.get_actor_label() == "House_Road"), None)
    if not house:
        raise RuntimeError("House_Road missing")
    loc = house.get_actor_location()
    fp = house.get_editor_property("footprint_size")
    wall_h = float(house.get_editor_property("wall_height"))
    hx, hy = float(fp.x) * 0.5, float(fp.y) * 0.5

    def P(dx, dy, dz):
        return unreal.Vector(loc.x + dx, loc.y + dy, loc.z + dz)

    cam_wide = P(hx + 900.0, hy + 900.0, wall_h * 0.9)
    cam_face = P(hx + 460.0, 0.0, wall_h * 0.45)
    cam_base = P(hx + 170.0, 55.0, 70.0)
    shots = [
        ("wide", cam_wide, look_at(cam_wide, P(0.0, 0.0, wall_h * 0.45)), 50.0),
        ("face", cam_face, look_at(cam_face, P(hx, 0.0, wall_h * 0.45)), 48.0),
        ("base", cam_base, look_at(cam_base, P(hx, 20.0, 60.0)), 40.0),
    ]

    rigs = []
    for name, cam_loc, cam_rot, fov in shots:
        rt = unreal.RenderingLibrary.create_render_target2d(
            world, W, H_PIX, unreal.TextureRenderTargetFormat.RTF_RGBA8, unreal.LinearColor(0, 0, 0, 1), False)
        cap = ACTORS.spawn_actor_from_class(unreal.SceneCapture2D, cam_loc, cam_rot)
        comp = cap.get_component_by_class(unreal.SceneCaptureComponent2D)
        comp.set_editor_property("texture_target", rt)
        comp.set_editor_property("capture_source", unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
        comp.set_editor_property("fov_angle", fov)
        comp.set_editor_property("capture_every_frame", False)
        comp.set_editor_property("capture_on_movement", False)
        comp.set_editor_property("post_process_settings", make_pp())
        comp.set_editor_property("post_process_blend_weight", 1.0)
        comp.set_editor_property("always_persist_rendering_state", True)   # 坑 ⑧ + ⑨
        rigs.append((name, cap, comp, rt))
        spawned.append(cap)

    wall_mi = unreal.EditorAssetLibrary.load_asset("%s/MI_TinyGladeWall" % PKG)
    if wall_mi is None:
        raise RuntimeError("MI_TinyGladeWall missing")

    mids = []
    for label, scalars, textures in VARIANTS:
        mid = unreal.MaterialLibrary.create_dynamic_material_instance(world, wall_mi)
        for k, v in scalars.items():
            mid.set_scalar_parameter_value(k, v)
        for k, tname in textures.items():
            tex = unreal.EditorAssetLibrary.load_asset("%s/%s" % (TEX, tname))
            if tex is None:
                raise RuntimeError("texture missing: %s" % tname)
            mid.set_texture_parameter_value(k, tex)
        mids.append((label, mid))
        unreal.log("WALLVAR variant %-9s scalars=%s textures=%s" % (label, scalars, textures))

    plan = []
    for label, mid in mids:
        plan.append(("use", label, mid, None))
        for i in range(len(rigs)):
            plan.append(("shoot", label, None, i))

    state = {"ticks": 0, "step": 0, "phase": 0, "handle": None}

    def tick(delta):
        state["ticks"] += 1
        if state["ticks"] < FIRST_TICK:
            return
        if state["step"] >= len(plan):
            unreal.unregister_slate_post_tick_callback(state["handle"])
            house.set_editor_property("WallMaterial", wall_mi)
            for a in spawned:
                ACTORS.destroy_actor(a)
            unreal.log("WALLVAR DONE tag=%s" % TAG)
            unreal.SystemLibrary.quit_editor()
            return

        kind, label, mid, index = plan[state["step"]]
        phase = state["phase"]
        state["phase"] += 1

        if kind == "use":
            if phase == 0:
                house.set_editor_property("WallMaterial", mid)
                house.call_method("RebuildHouse")
            elif phase >= SETTLE:
                state["step"] += 1
                state["phase"] = 0
            return

        name, cap, comp, rt = rigs[index]
        if phase == 0:
            unreal.EditorLevelLibrary.pilot_level_actor(cap)
            unreal.EditorLevelLibrary.editor_invalidate_viewports()
            return
        comp.capture_scene()          # 坑 ⑨：一帧一次
        if phase >= WARMUP:
            fn = "wallvar_%s_%s_%s.png" % (TAG, label, name)
            unreal.RenderingLibrary.export_render_target(world, rt, OUT_DIR, fn)
            unreal.log("WALLVAR shot: %s" % fn)
            state["step"] += 1
            state["phase"] = 0

    state["handle"] = unreal.register_slate_post_tick_callback(tick)


try:
    setup()
except Exception:
    unreal.log_error("WALLVAR FAILED\n" + traceback.format_exc())
    unreal.SystemLibrary.quit_editor()
