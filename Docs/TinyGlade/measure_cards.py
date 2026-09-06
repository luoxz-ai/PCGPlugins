# -*- coding: utf-8 -*-
"""用与资产完全相同的口径，量一遍我生成的那棵树的卡片朝向。"""
import hou, math

geo = hou.node("/obj").createNode("geo", "measure_tmp")
f = geo.createNode("file")
f.parm("file").set("D:/MyProject/Houdini/TinyGlade/out/tinyglade_tree_generated.bgeo.sc")
g = f.geometry()

cd = g.findPointAttrib("Cd")
ap = g.findPointAttrib("appear_pos")

def v3(t):
    return hou.Vector3(t[0], t[1], t[2])

cards = []
hub = hou.Vector3(0, 0, 0)
ncan = 0
for prim in g.prims():
    pts = list(prim.points())
    if len(pts) != 4:
        continue
    if pts[0].attribValue(cd)[2] > 0.5:
        continue
    ring = {}
    ok = True
    for pt in pts:
        c = pt.attribValue(cd)
        k = (int(round(c[0])), int(round(c[1])))
        ring[k] = pt
    if len(ring) != 4:
        continue
    p00, p10, p01 = ring[(0, 0)].position(), ring[(1, 0)].position(), ring[(0, 1)].position()
    ctr = sum((pt.position() for pt in pts), hou.Vector3(0, 0, 0)) / 4.0
    n = (p10 - p00).cross(p01 - p00).normalized()
    cards.append((ctr, n, v3(ring[(0, 0)].attribValue(ap)),
                  (p10 - p00).normalized(), (p01 - p00).normalized()))
    hub += ctr
    ncan += 1
hub /= ncan

def stat(name, dots):
    ang = sorted(math.degrees(math.acos(max(-1.0, min(1.0, d)))) for d in dots)
    med = ang[len(ang) // 2]
    under30 = sum(1 for a in ang if a < 30)
    print("  %-34s 夹角中位 %5.1f deg   <30deg: %3d/%d" % (name, med, under30, len(ang)))

UP = hou.Vector3(0, 1, 0)
print("生成树：%d 张卡片" % ncan)
print("== 卡面法线 n 指向什么 ==")
stat("背离整冠中心", [n.dot((c - hub).normalized()) for c, n, a, u, v in cards])
stat("背离自己的 appear_pos", [n.dot((c - a).normalized()) if (c - a).length() > 1e-6 else 1.0
                              for c, n, a, u, v in cards])
stat("世界 +Y", [abs(n.dot(UP)) for c, n, a, u, v in cards])
print("== 卡片局部轴 ==")
stat("v 轴 vs 世界 +Y", [abs(v.dot(UP)) for c, n, a, u, v in cards])
stat("u 轴 vs 世界 +Y", [abs(u.dot(UP)) for c, n, a, u, v in cards])

# 平面度 / 歪斜（资产实测：平面偏差/对角线 中位 0.099，|e2-e3| 最大 2.2）
devs, skews = [], []
for prim in g.prims():
    pts = list(prim.points())
    if len(pts) != 4 or pts[0].attribValue(cd)[2] > 0.5:
        continue
    ring = {}
    for pt in pts:
        c = pt.attribValue(cd)
        ring[(int(round(c[0])), int(round(c[1])))] = pt.position()
    if len(ring) != 4:
        continue
    a, b, cc, d = ring[(0, 0)], ring[(1, 0)], ring[(1, 1)], ring[(0, 1)]
    n = (b - a).cross(d - a).normalized()
    diag = (cc - a).length()
    devs.append(abs((cc - a).dot(n)) / diag if diag > 1e-6 else 0.0)
    skews.append(((d - a) - (cc - b)).length())
devs.sort(); skews.sort()
print("== 四边形本身 ==")
print("  平面偏差/对角线 中位 %.3f  最大 %.3f     (资产 0.099 / 0.751)" % (devs[len(devs)//2], devs[-1]))
print("  歪斜 |e2-e3|    中位 %.3f  最大 %.3f     (资产 中位?/最大 2.20)" % (skews[len(skews)//2], skews[-1]))

geo.destroy()
