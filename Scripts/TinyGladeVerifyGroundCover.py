# -*- coding: utf-8 -*-
"""
地被验收：**画一笔路，草必须当场退掉**，而且交互期不许出现实例源交接。

两条判据，缺一条都不算过：

  A. **遮罩生效**：沿地面中线画一条路，草的 GPU 实例数必须下降，且降幅与
     "被涂到的面积 × 密度"同一个量级。只看"跑通了"是不够的 —— 遮罩通道读错、
     阈值配反、色流没刷新，三种情况下实例数都照样是个大数，画面上却完全没有路。

  B. **交互期零阻塞**：落笔那一段**不许**打 "实例源交接" 这行日志。打了就说明某个
     "没变"的判据其实每笔都在变（容量或包围盒），而 `SetInstanceSourceGPU` 是阻塞的
     —— 那正是交互期掉帧的来源。判据在引擎日志里，本脚本只负责打出落笔的分界线。

⚠️ **落笔坐标必须自己从地面矩形算**，不能想当然写 (0,0) 附近：
   `GetWorldRect2D()` 的 **Min = actor 位置**，不是中心 —— 地面从 actor 那一角往 +X/+Y 铺开。
   第一版就是栽在这里：在 (0,0) 周围画了 9 笔，其中大部分落在地面**外面**，
   草只掉了 152 株（而不是该有的两千多），差点被误判成"阈值配松了"。
   `GetWorldRect2D()` 不是 UFUNCTION，所以这里按同一条公式复算：
   `Min = actor 位置`，`Max = Min + (NumCells × CellSize)`。

⚠️ 本脚本**会改地面的顶点色**（画一条路）但**不存盘**，跑完关卡是脏的。
"""
import unreal

PKG = "/PCGPlugins/HouseTest"
unreal.EditorLoadingAndSavingUtils.load_map("%s/L_HouseGroundDemo" % PKG)
A = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

ground = next((a for a in A.get_all_level_actors()
               if "Ground" in a.get_class().get_name() and "Shaper" not in a.get_class().get_name()), None)
if ground is None:
    unreal.log_error("COVERCHK FAILED: 关卡里没有地面 actor")
    raise SystemExit


def counts(tag):
    c = [ground.call_method("DebugReadGroundCoverCountGpuSync", (i,)) for i in range(3)]
    unreal.log("COVERCHK %-10s 草=%s 花1=%s 花2=%s" % (tag, c[0], c[1], c[2]))
    return c


reason = ground.call_method("GetGroundCoverUndrawableReason")
unreal.log("COVERCHK drawable=%s" % ("OK" if not reason else reason))

loc = ground.get_actor_location()
cell = ground.get_editor_property("CellSize")
nx = ground.get_editor_property("NumCellsX")
ny = ground.get_editor_property("NumCellsY")
radius = ground.get_editor_property("BrushRadius")
span_x, span_y = nx * cell, ny * cell
unreal.log("COVERCHK 地面 (%.0f, %.0f) → (%.0f, %.0f)  格 %dx%d @ %.0f cm  笔刷 R=%.0f cm"
           % (loc.x, loc.y, loc.x + span_x, loc.y + span_y, nx, ny, cell, radius))

before = counts("落笔前")

# 沿地面中线（Y 居中）从 20% 走到 80%，步长取 **笔刷直径的一半**，让相邻两笔真正连成一条带。
step = radius
x0, x1 = loc.x + 0.2 * span_x, loc.x + 0.8 * span_x
cy = loc.y + 0.5 * span_y
dabs = int((x1 - x0) / step) + 1

unreal.log("COVERCHK ---- 落笔开始，%d 笔（下面这一段里不许出现『实例源交接』）----" % dabs)
ground.call_method("BeginPaintStroke")
for i in range(dabs):
    ground.call_method("ApplyPaintStroke", (unreal.Vector(x0 + i * step, cy, loc.z),))
ground.call_method("EndPaintStroke")
unreal.log("COVERCHK ---- 落笔结束 ----")

after = counts("落笔后")

drop = before[0] - after[0]
# 涂到的面积 ≈ 长 (x1−x0) × 宽 2R 的带 + 两端各半个圆。笔刷单笔就把 R 打到 1.0
# （PROBE 实测：中心 1.000、50 cm 处 0.686），而 MaskEnd = 0.45 ⇒ 带内基本全拒。
import math
band = ((x1 - x0) * 2.0 * radius + math.pi * radius * radius) / 10000.0   # m²
density = ground.get_editor_property("Grass").get_editor_property("DensityPerSqM")
# 实际密度可能被 MaxInstances 钳过，用"落笔前实例数 / 地面面积"反算才是真的那个数。
actual = before[0] / (span_x * span_y / 10000.0)
unreal.log("COVERCHK 草减少 %d 株；涂到约 %.0f m²，实际密度 %.1f 株/m² ⇒ 期望 %.0f 株"
           % (drop, band, actual, band * actual))

if drop <= 0:
    unreal.log_error("COVERCHK FAILED: 画了路草一株都没少 —— 遮罩没接上")
elif drop < 0.6 * band * actual:
    unreal.log_error("COVERCHK FAILED: 降幅 %d 只有期望 %.0f 的 %.0f%% —— 阈值或采样口径有问题"
                     % (drop, band * actual, 100.0 * drop / max(band * actual, 1.0)))
else:
    unreal.log("COVERCHK PASS: 遮罩生效（降幅 %.0f%% 期望值）" % (100.0 * drop / max(band * actual, 1.0)))

unreal.log("COVERCHK DONE")
