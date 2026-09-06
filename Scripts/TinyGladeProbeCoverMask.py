# -*- coding: utf-8 -*-
"""地被遮罩探针：量出"一笔刷到底把 R 画到多少"，据此判断阈值配得对不对。

`TinyGladeVerifyGroundCover.py` 只证明了遮罩接上了（草会掉），没证明**降幅该有多大**。
两种情况下降幅都会偏小，而它们的修法完全相反：
  ① 笔刷单笔就画得淡（PaintStrength / Falloff）⇒ 阈值没问题，多描几笔就是了；
  ② 阈值（MaskStart/MaskEnd）配得比笔刷能达到的 R 还高 ⇒ 阈值要往下调。
分辨它们只需要一个数：**同一点反复落笔后 R 的上限**。
"""
import unreal

PKG = "/PCGPlugins/HouseTest"
unreal.EditorLoadingAndSavingUtils.load_map("%s/L_HouseGroundDemo" % PKG)
A = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
g = next(a for a in A.get_all_level_actors()
         if "Ground" in a.get_class().get_name() and "Shaper" not in a.get_class().get_name())

for p in ("BrushRadius", "BrushFalloff", "BrushStrength", "PaintStrength", "PaintFalloff"):
    try:
        unreal.log("PROBE %-16s = %s" % (p, g.get_editor_property(p)))
    except Exception:
        pass

grass = g.get_editor_property("Grass")
unreal.log("PROBE MaskStart=%.2f MaskEnd=%.2f MaskShorten=%.2f"
           % (grass.get_editor_property("MaskStart"),
              grass.get_editor_property("MaskEnd"),
              grass.get_editor_property("MaskShorten")))

C = unreal.Vector(0.0, 0.0, 0.0)
unreal.log("PROBE 落笔前 R(0,0) = %.3f  草=%s"
           % (g.call_method("SampleRoadWeight", (unreal.Vector2D(0.0, 0.0),)),
              g.call_method("DebugReadGroundCoverCountGpuSync", (0,))))

for n in (1, 2, 4, 8, 16):
    g.call_method("BeginPaintStroke")
    for _ in range(n):
        g.call_method("ApplyPaintStroke", (C,))
    g.call_method("EndPaintStroke")
    total = 1 + 2 + 4 + 8 + 16
    unreal.log("PROBE 累计 %2d 笔后 R(0,0) = %.3f   R(50cm) = %.3f   草=%s"
               % (n, g.call_method("SampleRoadWeight", (unreal.Vector2D(0.0, 0.0),)),
                  g.call_method("SampleRoadWeight", (unreal.Vector2D(50.0, 0.0),)),
                  g.call_method("DebugReadGroundCoverCountGpuSync", (0,))))

unreal.log("PROBE DONE")
