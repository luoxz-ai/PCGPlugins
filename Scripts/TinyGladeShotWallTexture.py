# -*- coding: utf-8 -*-
"""墙面「灰泥剥落 + 凸出砖」材质（`M_TinyGladeWall`，2026-09-03 建）的**在引擎里**出图与判据。

建这张材质那一轮只跑到 numpy 逐像素**仿真**为止（`wall_preview.png`），从没在引擎里看过一眼。
仿真对不上引擎的地方至少有三处，全都只有真渲染能答：

  · 投影 UV 走的是 `Parameters.AbsoluteWorldPosition` 与 `VertexNormalWS`，仿真里是一张平面网格；
    真墙有四个面 + 转角，`TGWallProjUV` 的三选一在转角处换轴，**会不会在角上撕开**只有图能看。
  · 卷边法线加在切空间里（`Parameters.TangentToWorld`），仿真用的是固定 T/B。房体是
    `AddPanel` 拼出来的，切空间由 `UCSMesh` 给，**方向对不对**仿真答不了。
  · 洞的 `OpacityMask` 与剥落是两条独立链路，仿真只跑了剥落。两条一起跑会不会互相踩，得看图。

--- 判据（不是"看着还行"，是逐像素）-----------------------------------------------
同机位拍**两趟**，只切材质参数：

  A `peel` —— `MI_TinyGladeWall` 原样。
  B `flat` —— 同一张母材质的 MID，只把 `PeelAmount` 压到 -5、`ProtrudeLevel` 抬到 5
     ⇒ 侵蚀场恒负、砖高恒不过液面 ⇒ **整面墙纯灰泥**。其余（洞、投影、贴图、粗糙度）全同。

于是：
  ① A 与 B 的差异像素率 > 0 ⇒ 剥落/凸出那两项**真的走到了像素**（而不是"材质挂上去了"）；
  ② 差异像素在 A 里的均色应当**偏暖且不是中性灰** —— 引擎把不支持的母材质静默换成默认材质
     时是中性灰（R≈G≈B），砖色卡是暖调，这一条把"没编译成功"从"确实是砖"里分出来；
  ③ B 那趟本身必须**不是纯色** —— 灰泥贴图 + 法线还在，纯色说明贴图根本没采到。
判据在**图外**算（PNG 拿到本地跑 numpy），脚本这边只负责产出两趟同机位的 PNG。

用法::

    set TG_SHOT_TAG=v1
    UnrealEditor.exe <project> -ExecCmds="py .../TinyGladeShotWallTexture.py"

产物：`Saved/TinyGladeShots/walltex_<tag>_<pass>_<cam>.png`，日志里 `WALLTEX` 打机位与进度。

--- 踩过的坑（沿用 `TinyGladeShotWallRoofSeams.py` 那张表，逐条仍然成立）-------------
 ① `unreal.Rotator` 的参数序是 **(roll, pitch, yaw)** —— 一律关键字参数。
 ② 必须 `UnrealEditor.exe` + `-ExecCmds="py <脚本>"`；`-ExecutePythonScript` 跑完即退，
    tick 回调没机会触发。
 ③ 离屏 `SceneCapture2D`，**不要** `HighResShot`（本工程起来是四分屏）。
 ④ `create_render_target2d` 必须显式 `RTF_RGBA8`，否则导出的 png 是 HDR 内容。
 ⑤ 离屏捕获被引擎写死关掉 Lumen，只能在**捕获组件自己**的 `post_process_settings` 里翻回来。
 ⑥ 准备阶段抛异常 ⇒ 编辑器永不退出，所以整段包在 try 里、出错自己 `quit_editor`。
 ⑦ **换材质要走 `set_editor_property`**：`ACSHouseActor::PostEditChangeProperty` 认这四个
    材质属性名并只重绑不重建（材质不进 `BodyDescHash`，走 `ReevaluateSite` 会因哈希不变直接跳过，
    画面零变化）。这里额外再调一次 `RebuildHouse` 兜底，代价只是一次重建。
 ⑧ **`always_persist_rendering_state = True` 必须开**：`USceneCaptureComponent` 默认没有
    ViewState，关卡 PPV 里的曝光一条都不生效。v1 那次没开，五张全是一片近黑
    （全图均色 0.003~0.03），差异率被压到 0.00% —— 判据不是"没差异"，是**根本没曝光**。
 ⑨ 修了 ⑧ 才会触发 ⑨：有 ViewState 之后 Lumen 才真跑，而它靠**帧间历史** ⇒
    `capture_scene()` 只打一次的话间接光恒为零。必须**一帧一次**预热到 `WARMUP`（128）。
 ⑩ 控制组用 `MaterialInstanceDynamic`（不落盘），别在 `Content/` 里造对照资产 ——
    `ACSHouseActor` 对墙材质有 `BLEND_Masked` 硬判据，MID 继承父级的 BlendMode，能过。
"""
import math
import os
import traceback

import unreal

TAG = os.environ.get("TG_SHOT_TAG", "v1")

PKG = "/PCGPlugins/HouseTest"
OUT_DIR = unreal.Paths.project_saved_dir() + "TinyGladeShots"
W, H_PIX = 1600, 900

FIRST_TICK = 45      # 天光实时捕获 / surface cache 的第一轮
SETTLE = 8           # 换材质 → 重建 + 组件重注册，留几帧
# 坑 ⑨ 的执行面，口径抄 `TinyGladeShotSkirt.py`：**必须一帧一次**，同一帧连打 128 次
# 不推进帧间历史，等于没预热。128 不是保守取整（那边 2026-08-31 标定过：32 次仍差 0.54%）。
WARMUP = int(os.environ.get("TG_SHOT_WARMUP", "128"))


def make_pp():
    """见坑 ⑤：捕获组件自己那份 PP 覆盖，Lumen 才回到出图里。

    ⚠️ **这里不碰曝光**：关卡里的 `TG_PostProcess` 已经把曝光钉死（D14 那一轮），
    捕获组件只要开了 ViewState（坑 ⑧）就会吃到它；在这儿再覆盖一次只会和它打架。
    """
    pp = unreal.PostProcessSettings()
    pp.set_editor_property("override_dynamic_global_illumination_method", True)
    pp.set_editor_property("dynamic_global_illumination_method", unreal.DynamicGlobalIlluminationMethod.LUMEN)
    pp.set_editor_property("override_reflection_method", True)
    pp.set_editor_property("reflection_method", unreal.ReflectionMethod.LUMEN)
    pp.set_editor_property("override_lumen_surface_cache_resolution", True)
    pp.set_editor_property("lumen_surface_cache_resolution", 1.0)
    return pp


def look_at(frm, to):
    """⚠️ 见坑 ①。"""
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
    unreal.log("WALLTEX house loc=(%.0f, %.0f, %.0f) fp=(%.0f, %.0f) H=%.0f"
               % (loc.x, loc.y, loc.z, fp.x, fp.y, wall_h))

    def P(dx, dy, dz):
        return unreal.Vector(loc.x + dx, loc.y + dy, loc.z + dz)

    # 机位全部从 footprint / 墙高反算：手调的机位参数一变就废，而两趟对照最怕机位不一致。
    # 剥落集中在墙脚（`hf = smoothstep(0, HeightFade=2, uv0.y)`，uv0.y = 离墙脚高度/200cm），
    # 所以近景一张压在墙脚、一张压在半墙高（那里只剩凸出砖）。
    cam_wide = P(hx + 900.0, hy + 900.0, wall_h * 0.9)
    cam_face = P(hx + 460.0, 0.0, wall_h * 0.45)
    cam_base = P(hx + 170.0, 55.0, 70.0)
    cam_mid = P(hx + 170.0, -55.0, wall_h * 0.72)
    cam_graze = P(hx + 200.0, hy * 0.85, 95.0)

    shots = [
        # 整体：一眼看出墙面到底是"一片灰泥"还是"下半截露砖"。
        ("wide", cam_wide, look_at(cam_wide, P(0.0, 0.0, wall_h * 0.45)), 50.0),
        # 正对 +X 面：整堵墙从脚到顶，剥落的高度渐变在这一张里读。
        ("face", cam_face, look_at(cam_face, P(hx, 0.0, wall_h * 0.45)), 48.0),
        # 墙脚近景：剥落露砖 + 灰泥断面带（`PlasterThickness`）+ 卷边法线。
        ("base", cam_base, look_at(cam_base, P(hx, 20.0, 60.0)), 40.0),
        # 半墙高近景：这里侵蚀场基本不过阈值 ⇒ 画面上只该剩"凸出砖"。
        ("mid", cam_mid, look_at(cam_mid, P(hx, -20.0, wall_h * 0.72)), 40.0),
        # 转角掠射：`TGWallProjUV` 在这里换轴，看纹理有没有在角上撕开。
        ("corner", cam_graze, look_at(cam_graze, P(hx, hy, 110.0)), 42.0),
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
        comp.set_editor_property("always_persist_rendering_state", True)   # 坑 ⑧ + ⑨ 成对
        unreal.log("WALLTEX cam %-7s loc=(%.0f, %.0f, %.0f) rot=(p %.1f, y %.1f) fov=%.0f"
                   % (name, cam_loc.x, cam_loc.y, cam_loc.z, cam_rot.pitch, cam_rot.yaw, fov))
        rigs.append((name, cap, comp, rt))
        spawned.append(cap)

    wall_mi = unreal.EditorAssetLibrary.load_asset("%s/MI_TinyGladeWall" % PKG)
    if wall_mi is None:
        raise RuntimeError("MI_TinyGladeWall missing — 先跑 TinyGladeMakeWallMaterials.py")

    # 见坑 ⑩：控制组是 MID，不落盘。只关剥落与凸出，其余全同。
    flat = unreal.MaterialLibrary.create_dynamic_material_instance(world, wall_mi)
    flat.set_scalar_parameter_value("PeelAmount", -5.0)      # 侵蚀场恒负 ⇒ peelM ≡ 0
    flat.set_scalar_parameter_value("ProtrudeLevel", 5.0)    # 液面高过任何砖 ⇒ prot ≡ 0
    flat.set_scalar_parameter_value("ProtrudeVariation", 0.0)

    def use(mat):
        def act():
            house.set_editor_property("WallMaterial", mat)   # 见坑 ⑦
            house.call_method("RebuildHouse")
            unreal.log("WALLTEX wall material -> %s" % mat.get_name())
        return act

    # 拍摄计划：一趟材质 = 一次 use + 每个机位一段 WARMUP 帧的预热 + 一次导出。
    plan = []
    for mat, pass_name in ((wall_mi, "peel"), (flat, "flat")):
        plan.append(("use", use(mat), None, None))
        for i in range(len(rigs)):
            plan.append(("shoot", None, pass_name, i))

    state = {"ticks": 0, "step": 0, "phase": 0, "handle": None}

    def tick(delta):
        state["ticks"] += 1
        if state["ticks"] < FIRST_TICK:
            return

        if state["step"] >= len(plan):
            unreal.unregister_slate_post_tick_callback(state["handle"])
            house.set_editor_property("WallMaterial", wall_mi)   # 还原，免得留个 MID 在关卡上
            for a in spawned:
                ACTORS.destroy_actor(a)
            unreal.log("WALLTEX DONE tag=%s" % TAG)
            unreal.SystemLibrary.quit_editor()
            return

        kind, act, pass_name, index = plan[state["step"]]
        phase = state["phase"]
        state["phase"] += 1

        if kind == "use":
            if phase == 0:
                act()
            elif phase >= SETTLE:
                state["step"] += 1
                state["phase"] = 0
            return

        name, cap, comp, rt = rigs[index]
        if phase == 0:
            unreal.EditorLevelLibrary.pilot_level_actor(cap)
            unreal.EditorLevelLibrary.editor_invalidate_viewports()
            return
        # 坑 ⑨：一帧只打一次 capture_scene()，Lumen 的帧间历史才会真的往前走。
        comp.capture_scene()
        if phase >= WARMUP:
            fn = "walltex_%s_%s_%s.png" % (TAG, pass_name, name)
            unreal.RenderingLibrary.export_render_target(world, rt, OUT_DIR, fn)
            unreal.log("WALLTEX shot: %s（预热 %d 帧）" % (fn, WARMUP))
            state["step"] += 1
            state["phase"] = 0

    state["handle"] = unreal.register_slate_post_tick_callback(tick)


try:
    setup()
except Exception:                     # 见坑 ⑥
    unreal.log_error("WALLTEX FAILED\n" + traceback.format_exc())
    unreal.SystemLibrary.quit_editor()
