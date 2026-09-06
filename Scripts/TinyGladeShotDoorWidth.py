# -*- coding: utf-8 -*-
"""门宽跟不跟路：**画不同宽度的路，量门宽**（D6 的 2026-09-04 重做的验收）。

这是整条改动的判据。用户在 TG 里的实测是"路只擦过墙一点点时，拱非常窄"，
PDB 侧的对位物是 `ArchSegment (*)(WallPathSegment)`（拱由墙路径的一段变成，段自带 `length_ws`）
⇒ **拱宽 = 路在墙上截出的弦长**。旧口径做不到：门宽取自等分槽宽 × 离地收窄，与路无关，
路只能投"这一格开不开"的票。

--- 怎么量 -------------------------------------------------------------------
在同一面墙上**逐档加宽**地画一条横穿的路（`ACSGroundActor::BrushRadius` 从小到大），
每档 `ReevaluateSite()` 之后读 `ACSHouseActor` 当前的洞表，记下那面墙上的门宽与门心。

判据（全部落在数字上，不靠看图）：
  ① **单调**：笔刷半径每加大一档，门宽只增不减。
  ② **连续**：相邻两档的门宽增量必须是**厘米量级**，不能出现"一跳一个采样格"
     （`DoorSampleStep` 默认 25 cm）。旧口径在这条上必然失败 —— 它的门宽只有一个值。
  ③ **跟着路心走**：把路平移半米，门心必须跟着挪同样的距离；旧口径的门心恒在槽心。
  ④ **窄路 → 窄门**：最小那一档的门宽必须显著小于最大那一档（"擦过一点点就很窄"）。
  ⑤ 门扇跟着洞走：`GetDoorLeafCount()` 与门洞数一致，且 `GetDoorLeafUndrawableReason()` 为空。

同时每档出一张图，供肉眼复核门扇有没有穿帮。

用法::

    set TG_SHOT_TAG=d1
    UnrealEditor.exe <project> -ExecCmds="py .../TinyGladeShotDoorWidth.py"

产物：`Saved/TinyGladeShots/doorwidth_<tag>_r<半径>.png`，判据打在日志的 `DOORWIDTH` 行。

--- 坑（沿用出图脚本那张表）--------------------------------------------------
 ① `unreal.Rotator` 是 (roll, pitch, yaw)，一律关键字参数。
 ② 必须 `-ExecCmds="py <脚本>"`；`-ExecutePythonScript` 跑完即退，tick 回调没机会触发。
 ③ 离屏 `SceneCapture2D`，不要 `HighResShot`。④ `create_render_target2d` 显式 `RTF_RGBA8`。
 ⑤ 捕获组件自己那份 PP 里翻回 Lumen。⑧ `always_persist_rendering_state` 必须开，否则一片近黑。
 ⑨ 一帧一次 `capture_scene()` 预热。⑥ 准备段整个包在 try 里，出错自己 quit。
 ⑪ **画完路要 `FlushPaintToGpu(True)`**：落笔只写镜像，不推 GPU 的话镜像（判据读的那份）
    是对的、画面却停在上一帧 —— 数字全绿而图是旧的，最难发现的一种假绿。
"""
import math
import os
import traceback

import unreal

TAG = os.environ.get("TG_SHOT_TAG", "d1")

PKG = "/PCGPlugins/HouseTest"
OUT_DIR = unreal.Paths.project_saved_dir() + "TinyGladeShots"
W, H_PIX = 1600, 900
FIRST_TICK = 45
SETTLE = 10
WARMUP = int(os.environ.get("TG_SHOT_WARMUP", "96"))

# 逐档加宽的笔刷半径 cm。最小那一档要真的"只擦过一点点"。
RADII = [60.0, 90.0, 120.0, 150.0, 180.0, 220.0]
# 判据 ③ 用：把路沿墙平移这么多，看门心跟不跟。
#
# ⚠️ **必须在最窄那一档量**（2026-09-04 踩过）：宽档的门已经顶满整面墙的可用跨度
# （edge 1 的可用长 = 400 − 2×24 − 2×60 = 232 cm），端点被护角夹死，门心物理上挪不动，
# 判据会假红。窄档的门只有 66 cm，跨度里还剩 160 多 cm 的余量，平移才量得出来。
SHIFT = 60.0
SHIFT_RADIUS = 60.0


def make_pp():
    pp = unreal.PostProcessSettings()
    pp.set_editor_property("override_dynamic_global_illumination_method", True)
    pp.set_editor_property("dynamic_global_illumination_method", unreal.DynamicGlobalIlluminationMethod.LUMEN)
    pp.set_editor_property("override_reflection_method", True)
    pp.set_editor_property("reflection_method", unreal.ReflectionMethod.LUMEN)
    return pp


def look_at(frm, to):
    """⚠️ 坑 ①。"""
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
    ground = next((a for a in ACTORS.get_all_level_actors() if a.get_actor_label() == "Ground_Demo"), None)
    if not house or not ground:
        raise RuntimeError("House_Road / Ground_Demo missing")

    loc = house.get_actor_location()
    fp = house.get_editor_property("footprint_size")
    wall_h = float(house.get_editor_property("wall_height"))
    hx, hy = float(fp.x) * 0.5, float(fp.y) * 0.5
    unreal.log("DOORWIDTH house loc=(%.0f, %.0f, %.0f) fp=(%.0f, %.0f)" % (loc.x, loc.y, loc.z, fp.x, fp.y))

    # 先把地面的道路通道清干净，否则演示关卡上原有的路会和这里画的叠在一起，
    # 判据 ③（门心跟着路心走）会被旧路拖住。
    ground.call_method("ResetPaint")

    # 挑 +X 那面墙（edge 1，沿 +Y）。路沿 X 横穿它，笔刷中心落在墙外一点点，
    # 这样"路擦过墙"的程度完全由笔刷半径决定。
    wall_x = loc.x + hx
    base_y = loc.y

    def paint_corner(radius):
        """斜穿 +X/−Y 那个转角画一条路：验"四条边接成环"之后转角能不能开门。"""
        cx, cy = loc.x + hx, loc.y - hy       # 该角的世界坐标
        ground.set_editor_property("BrushRadius", radius)
        ground.call_method("BeginPaintStroke")
        for k in range(11):
            t = -260.0 + k * 52.0             # 沿角平分线（+X, −Y）方向穿过去
            ground.call_method("ApplyPaintStroke", (unreal.Vector(cx + t * 0.707, cy - t * 0.707, loc.z),))
        ground.call_method("EndPaintStroke")
        ground.call_method("FlushPaintToGpu", (True,))

    def paint_road(radius, shift):
        ground.set_editor_property("BrushRadius", radius)
        ground.call_method("BeginPaintStroke")
        # 沿 X 从墙外画到墙内一点点：一条真正横穿墙面的路。
        for k in range(9):
            x = wall_x - 220.0 + k * 55.0
            ground.call_method("ApplyPaintStroke", (unreal.Vector(x, base_y + shift, loc.z),))
        ground.call_method("EndPaintStroke")
        ground.call_method("FlushPaintToGpu", (True,))   # 坑 ⑪

    def measure():
        """读这一档的门：只看 +X 那面墙（edge 1）上的门。

        ⚠️ 洞类型**用枚举对象比**，不要 `str(...).endswith("DOOR")`：UE Python 的枚举
        stringify 口径随版本变（2026-09-04 实测这条匹配不上任何一项，而门明明存在 ——
        `GetDoorLeafCount()` 同时报 1~3）。字符串匹配失败是**静默**的，症状是"一档都没量到"。
        """
        house.call_method("RebuildHouse")
        openings = house.call_method("GetCurrentOpenings")
        door_type = unreal.CSOpeningType.DOOR
        out = []
        for o in openings:
            kind = o.get_editor_property("type")
            if kind != door_type:
                continue
            out.append((int(o.get_editor_property("edge_index")),
                        float(o.get_editor_property("center_s")),
                        float(o.get_editor_property("width"))))
        if openings and not state.get("dumped"):
            state["dumped"] = True
            first = openings[0]
            unreal.log("DOORWIDTH probe n=%d type=%r (== DOOR: %s) edge=%r width=%r"
                       % (len(openings), first.get_editor_property("type"),
                          first.get_editor_property("type") == door_type,
                          first.get_editor_property("edge_index"),
                          first.get_editor_property("width")))
        out.sort()
        return out

    state = {"ticks": 0, "step": 0, "phase": 0, "handle": None, "rows": []}

    # ⚠️ **先把别的房子藏掉**：演示关卡上 `House_Pillar` 正压在这条视线上，退得越远它挡得越满
    # （2026-09-04 连拍两版都拍到了它的墙 —— 数字全绿而图上是隔壁房子，与卷零坑表里
    # "山墙机位拍进了隔壁房子的白墙"是同一个坑）。判据只关心 House_Road。
    hidden = []
    for other in ACTORS.get_all_level_actors():
        if other == house or "House" not in other.get_class().get_name():
            continue
        # ⚠️ 不是 `set_editor_property("hidden_in_game", ...)` —— actor 上没有这个属性名
        # （2026-09-04 实测抛 "Failed to find property"）。编辑器世界里生效的是下面这条，
        # 离屏 SceneCapture 跟着它走；`set_actor_hidden_in_game` 只影响 PIE，一并写上无害。
        other.set_actor_hidden_in_game(True)
        other.set_is_temporarily_hidden_in_editor(True)
        hidden.append(other)
    unreal.log("DOORWIDTH hid %d other house actor(s)" % len(hidden))

    # 机位：退到 1400 cm、抬到墙高，40° 俯看，整面墙 + 门洞 + 门扇 + 地上的路一起进画。
    cam = unreal.Vector(loc.x + hx + 1400.0, loc.y, wall_h * 1.0)
    rt = unreal.RenderingLibrary.create_render_target2d(
        world, W, H_PIX, unreal.TextureRenderTargetFormat.RTF_RGBA8, unreal.LinearColor(0, 0, 0, 1), False)
    cap = ACTORS.spawn_actor_from_class(unreal.SceneCapture2D, cam, look_at(cam, unreal.Vector(loc.x + hx, loc.y, wall_h * 0.35)))
    comp = cap.get_component_by_class(unreal.SceneCaptureComponent2D)
    comp.set_editor_property("texture_target", rt)
    comp.set_editor_property("capture_source", unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
    comp.set_editor_property("fov_angle", 40.0)
    comp.set_editor_property("capture_every_frame", False)
    comp.set_editor_property("capture_on_movement", False)
    comp.set_editor_property("post_process_settings", make_pp())
    comp.set_editor_property("post_process_blend_weight", 1.0)
    comp.set_editor_property("always_persist_rendering_state", True)   # 坑 ⑧
    spawned.append(cap)

    # (标签, 笔刷半径, 沿墙平移)
    plan = [("r%03d" % int(r), r, 0.0) for r in RADII]
    plan.append(("shift", SHIFT_RADIUS, SHIFT))
    plan.append(("corner", 130.0, None))       # None = 走 paint_corner

    def finish():
        unreal.unregister_slate_post_tick_callback(state["handle"])
        rows = state["rows"]
        for label, radius, shift, doors, leaves, reason in rows:
            # ⚠️ `shift` 对转角那一行是 None（走 paint_corner），`%4.0f` 收不了 —— 格式化在
            # tick 回调里抛异常时**编辑器不会退**，只会一直刷错误行（2026-09-04 踩过）。
            shift_text = "corner" if shift is None else "%.0f" % shift
            unreal.log("DOORWIDTH %-6s radius=%5.0f shift=%-6s doors=%s leaves=%d reason='%s'"
                       % (label, radius, shift_text, doors, leaves, reason))

        # ---- 判据 ----
        # ⚠️ 量的是**总开口宽**（这面墙上所有拱的宽度之和），不是单个拱的宽度。
        # 路一宽过 `DoorMaxWidth`，一个拱会裂成一排（"连续石拱门"），单拱宽度**必然下降**
        # ——2026-09-04 用单拱宽当判据时正是在这里报了假红。总宽才是"路有多宽"的度量。
        widths = []        # 总开口宽
        counts = []        # 这面墙上的拱数
        centers = []       # 单拱时的洞心（判据 ③ 用，只在窄档有意义）
        for label, radius, shift, doors, leaves, reason in rows:
            if shift is None or shift != 0.0:
                continue
            edge1 = [d for d in doors if d[0] == 1]
            widths.append(sum(d[2] for d in edge1))
            counts.append(len(edge1))
            centers.append(edge1[0][1] if len(edge1) == 1 else 0.0)

        ok = True
        # ⚠️ **空数据必须红**：下面每一条判据在 widths 为空时都会真空通过 ——
        # 那正是项目坑表里"故意破坏世界侧的对照"要防的假绿（脚本跑完什么都没量到也报 OK）。
        if len(widths) < len(RADII):
            unreal.log_error("DOORWIDTH FAIL ⓪ 只量到 %d/%d 档，判据无效" % (len(widths), len(RADII)))
            ok = False
        if not any(w > 0 for w in widths):
            unreal.log_error("DOORWIDTH FAIL ⓪ 一档都没开出门，判据无效")
            ok = False
        for i in range(1, len(widths)):
            if widths[i] + 0.51 < widths[i - 1]:
                unreal.log_error("DOORWIDTH FAIL ① 总开口宽不单调: %.1f -> %.1f" % (widths[i - 1], widths[i]))
                ok = False
        jumps = [widths[i] - widths[i - 1] for i in range(1, len(widths)) if widths[i - 1] > 0]
        if jumps and max(jumps) <= 0.0:
            unreal.log_error("DOORWIDTH FAIL ② 宽度对笔刷半径完全不响应（旧口径的症状）")
            ok = False
        positive = [w for w in widths if w > 0]
        if len(positive) >= 2 and positive[-1] <= positive[0] * 1.5:
            unreal.log_error("DOORWIDTH FAIL ④ 最宽档没有显著宽于最窄档: %.1f vs %.1f" % (positive[0], positive[-1]))
            ok = False
        # ⑤ 过宽的路必须读成**一排拱**（用户 2026-09-04 的诉求，也是 TG 的观感）：
        #    最宽那一档这面墙上应当不止一个拱，且每个都不超过 `DoorMaxWidth`。
        if counts and max(counts) < 2:
            unreal.log_error("DOORWIDTH FAIL ⑤ 路再宽也只有一个拱，没切成拱廊 counts=%s" % counts)
            ok = False
        for label, radius, shift, doors, leaves, reason in rows:
            for d in [x for x in doors if x[0] == 1]:
                if d[2] > 162.0:      # DoorMaxWidth 160 + 一个量化步长的余量
                    unreal.log_error("DOORWIDTH FAIL ⑤ 单拱 %.1f 超过 DoorMaxWidth" % d[2])
                    ok = False
        unreal.log("DOORWIDTH ⑤ 逐档拱数 counts=%s" % counts)

        # ---- ⑦ 拱廊不装门：用户实测"两道门并排时只有拱没有木门"。单拱那几档恰好 1 扇门，
        #      这面墙裂成多拱的档位（其它墙同步也是多拱）一扇都不许有。
        leaf_rows = [(r[0], r[4]) for r in rows if r[2] is not None and r[2] == 0.0]
        for (label, leaves), count in zip(leaf_rows, counts):
            want = 1 if count == 1 else 0
            if leaves != want:
                unreal.log_error("DOORWIDTH FAIL ⑦ %s: 拱数 %d 时门扇应为 %d，实际 %d" % (label, count, want, leaves))
                ok = False
        unreal.log("DOORWIDTH ⑦ 逐档门扇数 leaves=%s" % [lv for _, lv in leaf_rows])

        # ---- ⑥ 转角：一条斜穿转角的路必须让**相邻两条边各开一个洞**，且两侧都不装门扇
        #      （实拍 `img/tiny-glade-ref-corner-arch-passage.png`：两道拱共用一根角柱、没有木门）。
        corner_row = [r for r in rows if r[2] is None]
        if corner_row:
            _, _, _, doors, leaves, _ = corner_row[0]
            edges = sorted(set(d[0] for d in doors))
            unreal.log("DOORWIDTH ⑥ 转角: doors=%s edges=%s leaves=%d" % (doors, edges, leaves))
            if len(edges) < 2:
                unreal.log_error("DOORWIDTH FAIL ⑥ 斜穿转角只开出 %d 条边的洞（环形边没生效）" % len(edges))
                ok = False
            if leaves != 0:
                unreal.log_error("DOORWIDTH FAIL ⑥ 转角处装了 %d 扇门（TG 那边转角是敞开过道）" % leaves)
                ok = False

        shift_row = [r for r in rows if r[2] is not None and r[2] != 0.0]
        base_index = RADII.index(SHIFT_RADIUS) if SHIFT_RADIUS in RADII else 0
        if shift_row and len(centers) > base_index:
            edge1 = [d for d in shift_row[0][3] if d[0] == 1]
            if edge1:
                moved = edge1[0][1] - centers[base_index]
                unreal.log("DOORWIDTH ③ 路平移 %.0f ⇒ 门心移动 %.1f" % (SHIFT, moved))
                if abs(moved - SHIFT) > 30.0:
                    unreal.log_error("DOORWIDTH FAIL ③ 门心没跟着路心走（旧口径恒在槽心）")
                    ok = False
        unreal.log("DOORWIDTH 总开口宽 widths=%s" % ["%.0f" % w for w in widths])
        unreal.log("DOORWIDTH %s" % ("OK" if ok else "FAILED"))

        for a in hidden:
            if a:
                a.set_is_temporarily_hidden_in_editor(False)
        for a in spawned:
            ACTORS.destroy_actor(a)
        unreal.SystemLibrary.quit_editor()

    def tick(delta):
        state["ticks"] += 1
        if state["ticks"] < FIRST_TICK:
            return
        if state["step"] >= len(plan):
            finish()
            return

        label, radius, shift = plan[state["step"]]
        phase = state["phase"]
        state["phase"] += 1

        if phase == 0:
            ground.call_method("ResetPaint")
            if shift is None:
                paint_corner(radius)
            else:
                paint_road(radius, shift)
            return
        if phase < SETTLE:
            return
        if phase == SETTLE:
            doors = measure()
            leaves = int(house.call_method("GetDoorLeafCount"))
            reason = str(house.call_method("GetDoorLeafUndrawableReason") or "")
            state["rows"].append((label, radius, shift, doors, leaves, reason))
            unreal.EditorLevelLibrary.pilot_level_actor(cap)
            unreal.EditorLevelLibrary.editor_invalidate_viewports()
            return

        comp.capture_scene()          # 坑 ⑨：一帧一次
        if phase >= SETTLE + WARMUP:
            unreal.RenderingLibrary.export_render_target(world, rt, OUT_DIR, "doorwidth_%s_%s.png" % (TAG, label))
            state["step"] += 1
            state["phase"] = 0

    state["handle"] = unreal.register_slate_post_tick_callback(tick)


try:
    setup()
except Exception:                     # 坑 ⑥
    unreal.log_error("DOORWIDTH FAILED\n" + traceback.format_exc())
    unreal.SystemLibrary.quit_editor()
