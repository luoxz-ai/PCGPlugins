# -*- coding: utf-8 -*-
"""
在 Houdini 里程序化生成一棵 Tiny Glade 式的完整树。
产出与 assets/meshes/*.json 同一套编码：
  Vertex_Color.xy = 叶簇卡片角码 (0,0)(1,0)(1,1)(0,1)
  Vertex_Color.z  = 部件标签 (0=树冠卡片, 1=树干)   <- extract_canopy / extract_trunk 的判据
  prim_center / prim_normals = 每卡 4 角共享，由 recalculate_canopy_quad_attribs 重算
  appear_pos = 该卡所属枝梢（生长动画起点）
  age        = 生长错峰
尺寸对齐实测的 branchy_tree_v1：树高 16m，冠底 ~7.4m，312 张卡。
"""
import hou, os

HIP = "D:/MyProject/Houdini/TinyGlade/Tree.hip"
hou.hipFile.load(HIP, ignore_load_warnings=True)

def P(*a):
    print(" ".join(str(x) for x in a))

def set_menu(node, parm, want):
    """按 token / label 子串设置菜单参数，避免猜索引。"""
    p = node.parm(parm)
    items, labels = list(p.menuItems()), list(p.menuLabels())
    for w in want:
        if w in items:
            p.set(w); return
    for i, l in enumerate(labels):
        for w in want:
            if w.lower() in l.lower():
                p.set(items[i]); return
    raise RuntimeError("menu miss %s.%s want=%s items=%s labels=%s"
                       % (node.name(), parm, want, items, labels))

def pt_wrangle(parent, name, snippet, inputs=()):
    n = parent.createNode("attribwrangle", name)
    set_menu(n, "class", ["point"])          # 注意：class 索引 0 是 Detail，不是 Point
    n.parm("snippet").set(snippet)
    for i, src in enumerate(inputs):
        n.setInput(i, src)
    return n

obj = hou.node("/obj")
old = obj.node("tinyglade_tree_gen")
if old:
    old.destroy()
geo = obj.createNode("geo", "tinyglade_tree_gen")

# ================================================================= 1. 枝干骨架
skel = geo.createNode("python", "branch_skeleton")
skel.parm("python").set(r'''# 递归枝干生成器：主干 + 4 级分叉，每级带向上的趋光修正（tropism），
# 保证枝条越分越细、越分越短，但始终往上长。枝梢入 tips 组 -> 叶簇卡片的 appear_pos 源。
import hou, math, random

node = hou.pwd()
geo = node.geometry()
geo.clear()

rng = random.Random(11)
geo.addAttrib(hou.attribType.Point, "width", 0.0)
geo.addAttrib(hou.attribType.Point, "blobr", 0.0)   # 冠体积种子半径
tips = geo.createPointGroup("tips")                 # 末端枝梢 -> 卡片的 appear_pos 源
seeds = geo.createPointGroup("canopy_seeds")        # 全部分叉节点 -> 吹出树冠体积

TRUNK_H = 4.6          # 归一化后约占树高 46%，对齐实测（冠底 7.42 / 树高 16.3）
UP = hou.Vector3(0, 1, 0)

def mkpt(pos, width):
    p = geo.createPoint()
    p.setPosition(pos)
    p.setAttribValue("width", width)
    return p

def seg(a, b):
    poly = geo.createPolygon()
    poly.setIsClosed(False)
    poly.addVertex(a)
    poly.addVertex(b)

# --- 主干：分 5 段，带轻微弯曲，不是一根直杆 ---
prev = mkpt(hou.Vector3(0, 0, 0), 1.0)
NSEG = 5
for i in range(1, NSEG + 1):
    t = float(i) / NSEG
    pos = hou.Vector3(math.sin(t * 2.2) * 0.26, TRUNK_H * t, math.cos(t * 1.7) * 0.20)
    cur = mkpt(pos, 1.0 - 0.46 * t)
    seg(prev, cur)
    prev = cur
trunk_top = prev

def rot(v, axis, deg):
    return v * hou.hmath.buildRotateAboutAxis(axis, deg)

MAXD = 4

def branch(p0, dirv, length, width, depth):
    pos1 = p0.position() + dirv * length
    p1 = mkpt(pos1, width)
    seg(p0, p1)
    # 每个分叉节点都当冠球种子：越接近梢部球越大 -> 冠体积裹住整个分叉区，
    # 而不是只在顶上扣一顶帽子（实测资产树冠 bbox 从 7.4m 起，几乎与分叉区同高）
    p1.setAttribValue("blobr", float(MAXD - depth) / MAXD)   # 先存深度比例，归一化后再换算成世界半径
    seeds.add(p1)
    if depth <= 0:
        tips.add(p1)
        return
    n = 3 if depth >= 3 else 2
    ref = UP if abs(dirv.dot(UP)) < 0.9 else hou.Vector3(1, 0, 0)
    t1 = dirv.cross(ref).normalized()
    t2 = dirv.cross(t1)
    for i in range(n):
        roll = (2.0 * math.pi * i) / n + rng.uniform(-0.35, 0.35)
        axis = (t1 * math.cos(roll) + t2 * math.sin(roll)).normalized()
        nd = rot(dirv, axis, rng.uniform(30.0, 46.0)).normalized()
        nd = (nd * 0.78 + UP * 0.22).normalized()      # 趋光：拉回竖直，枝条不下垂
        branch(p1, nd, length * rng.uniform(0.62, 0.78), width * 0.66, depth - 1)

trunk_top.setAttribValue("blobr", 0.0)
seeds.add(trunk_top)
branch(trunk_top, UP, 1.7, 0.54, MAXD)                  # 3*3*2*2 = 36 个枝梢

# 归一化：树高 16m、根部贴地（对齐实测 branchy_tree_v1）。
# 在这里做而不是用 Transform SOP —— 后者是绕包围盒质心缩放的，会把整棵树平移。
bb = geo.boundingBox()
h = bb.sizevec()[1]
sc = 16.0 / h if h > 1e-6 else 1.0
y0 = bb.minvec()[1]
for pt in geo.points():
    q = pt.position()
    pt.setPosition(hou.Vector3(q[0] * sc, (q[1] - y0) * sc, q[2] * sc))
    # 冠球半径直接按世界单位给：分叉处 0.95m -> 梢部 1.85m，
    # 让冠体积贴着枝条走，而不是在树顶悬一个大球
    t = pt.attribValue("blobr")
    pt.setAttribValue("blobr", 0.95 + 0.90 * t)
''')

wire = geo.createNode("polywire", "trunk_tubes")
wire.setInput(0, skel)
# radius 默认是 if(multipscale && haspointattrib(pscale), @pscale, 1)*multi 的表达式，
# 骨架上没有 pscale，即便走不到那一支 Houdini 仍会报未定义局部变量 -> 直接压成常量。
wire.parm("multipscale").set(0)
wire.parm("radius").deleteAllKeyframes()
wire.parm("radius").set(0.50)   # 骨架已归一化到 16m，这就是世界单位下的根部半径
set_menu(wire, "usescaleattrib", ["attrib"])
wire.parm("scaleattrib").set("width")
wire.parm("div").set(5)          # 低面数五边形截面，贴合手绘风
wire.parm("segs").set(1)
wire.parm("jointcorrect").set(1)

trunk_tag = geo.createNode("attribwrangle", "trunk_attribs")
trunk_tag.setInput(0, wire)
set_menu(trunk_tag, "class", ["point"])
trunk_tag.parm("snippet").set('''// 树干部件属性。Cd.z = 1 —— 对应资产里 extract_trunk 的判据。
vector bb = relbbox(0, v@P);
v@Cd = set(fit01(rand(@ptnum * 3 + 1), 0.32, 0.92), v@P.y, 1.0);
f@age = clamp(bb.y, 0.0, 1.0);
v@appear_pos = v@P;
v@prim_center = 0;
v@prim_normals = 0;''')

trunk_normal = geo.createNode("normal", "trunk_normals")
trunk_normal.setInput(0, trunk_tag)

trunk_clean = geo.createNode("attribdelete", "trunk_drop_width")
trunk_clean.setInput(0, trunk_normal)
trunk_clean.parm("ptdel").set("width blobr")

# ================================================================= 2. 枝梢 -> 树冠壳
tips = geo.createNode("blast", "branch_tips")
tips.setInput(0, skel)
tips.parm("group").set("tips")
set_menu(tips, "grouptype", ["points"])
tips.parm("negate").set(1)       # 保留选中

seeds = geo.createNode("blast", "canopy_seeds")
seeds.setInput(0, skel)
seeds.parm("group").set("canopy_seeds")
set_menu(seeds, "grouptype", ["points"])
seeds.parm("negate").set(1)

tipr = geo.createNode("attribwrangle", "seed_blob_radius")
tipr.setInput(0, seeds)
set_menu(tipr, "class", ["point"])
tipr.parm("snippet").set('f@pscale = f@blobr * fit01(rand(@ptnum * 11 + 5), 0.88, 1.14);')

vdb = geo.createNode("vdbfromparticles", "canopy_volume")
vdb.setInput(0, tipr)
vdb.parm("voxelsize").set(0.17)
vdb.parm("builddistance").set(1)
vdb.parm("radiusscale").set(1.0)

shell = geo.createNode("convertvdb", "canopy_shell")
shell.setInput(0, vdb)
shell.parm("conversion").set("poly")
shell.parm("adaptivity").set(0.55)

# convertvdb 默认不输出 N -> scatter 就没有法线可传，copytopoints 会退化成纯平移。
shell_n = geo.createNode("normal", "canopy_shell_normals")
shell_n.setInput(0, shell)

# ================================================================= 3. 叶簇卡片
scat = geo.createNode("scatter", "card_centers")
scat.setInput(0, shell_n)
scat.parm("generateby").set("bydensity")
scat.parm("forcetotal").set(1)
scat.parm("npts").set(312)       # 与 branchy_tree_v1 实测卡片数一致
scat.parm("seed").set(3)
scat.parm("relaxpoints").set(1)
scat.parm("relaxiterations").set(30)
scat.parm("usegeometricnormals").set(1)
scat.parm("pointattribs").set("N")   # 把冠壳法线带到撒出的点上

frame = geo.createNode("attribwrangle", "card_frame")
frame.setInput(0, scat)
frame.setInput(1, tips)
set_menu(frame, "class", ["point"])
frame.parm("snippet").set('''// 每张卡的朝向 / 尺寸 / 生长起点
// 卡面法线 = 冠壳外法线（与资产里 prim_normals 背离冠心的性质一致）
vector nrm = normalize(v@N);          // 冠壳外法线，只作为起点
// 实测：资产里卡面法线与"背离冠心"方向的夹角中位 41 度、仅 113/312 在 30 度内，
// 也就是说卡片并不贴着冠壳躺平，而是美术摆得很散。这里按同样的散度抖开。
vector t0 = normalize(cross(nrm, (abs(nrm.y) < 0.9) ? set(0,1,0) : set(1,0,0)));
vector t1 = cross(nrm, t0);
float ra = rand(@ptnum * 19 + 3) * 2.0 * M_PI;
vector ax = t0 * cos(ra) + t1 * sin(ra);
matrix3 m = ident();
rotate(m, radians(fit01(rand(@ptnum * 17 + 4), 0.0, 85.0)), ax);
nrm = normalize(nrm * m);
v@N = nrm;
// 绕新法线随机滚转（资产实测局部 v 轴与世界 +Y 中位 89 度 —— 与世界上方向无关）
vector t = normalize(cross(nrm, (abs(nrm.y) < 0.9) ? set(0,1,0) : set(1,0,0)));
vector b = cross(nrm, t);
float roll = rand(@ptnum * 5 + 2) * 2.0 * M_PI;
v@up = normalize(t * cos(roll) + b * sin(roll));
// 卡片尺寸：实测 branchy_tree_v1 中位数 0.90 x 0.94 m
f@pscale = fit01(rand(@ptnum * 7 + 1), 0.78, 1.36);
// appear_pos = 最近的枝梢。资产里 312 张卡共用 46 个 appear_pos —— 同理成簇冒出
int tp = nearpoint(1, v@P);
v@appear_pos = point(1, "P", tp);
// age：高处后长
vector bb = relbbox(0, v@P);
f@age = clamp(fit(bb.y, 0.0, 1.0, 0.15, 1.0), 0.0, 1.0);''')

tmpl = geo.createNode("grid", "card_template")
tmpl.parm("type").set("poly")
tmpl.parm("orient").set("xy")
tmpl.parm("sizex").set(1.0)
tmpl.parm("sizey").set(1.0)
tmpl.parm("rows").set(2)
tmpl.parm("cols").set(2)

code = geo.createNode("attribwrangle", "corner_code")
code.setInput(0, tmpl)
set_menu(code, "class", ["point"])
code.parm("snippet").set('''// 角码写进 Vertex_Color.xy —— 正是 VS 里 Cd.xy*2-1 读到的 (-1,-1)(1,-1)(1,1)(-1,1)
// Cd.z = 0 标记树冠部件（对应资产里 extract_canopy 的判据）
v@Cd = set(v@P.x > 0 ? 1.0 : 0.0, v@P.y > 0 ? 1.0 : 0.0, 0.0);
// 模板面朝 +Z；copytopoints 会把它转到每张卡的实际朝向
v@N = set(0.0, 0.0, 1.0);''')

cards = geo.createNode("copytopoints", "leaf_cards")
cards.setInput(0, code)
cards.setInput(1, frame)
cards.parm("useimplicitn").set(1)
# appear_pos / age 自动从 target 点带过来；P/N/up/pscale 被用于变换、不复制

jitter = geo.createNode("attribwrangle", "card_attrib_init")
jitter.setInput(0, cards)
set_menu(jitter, "class", ["point"])
jitter.parm("snippet").set('''// 资产里的卡片不是刚性方片 —— 4 个角是美术逐个摆的，
// 平面偏差/对角线 中位 0.099、最大 0.751，歪斜 |e2-e3| 最大 2.2。这里逐角抖开。
v@P += (set(rand(@ptnum * 23 + 5), rand(@ptnum * 23 + 6), rand(@ptnum * 23 + 7)) - 0.5) * 0.17;

// 占位：让卡片这一路的属性集合与树干那一路一致（否则 merge 会告警、缺省值不确定）。
// 真正的 appear_pos / age 在 recalculate_canopy_quad_attribs 里逐卡算 —— 那里才拿得到整张卡的 4 个角。
v@appear_pos = 0;
f@age = 0;
v@prim_center = 0;
v@prim_normals = 0;''')

# ================================================================= 4. 合并 + 重算卡片属性
mrg = geo.createNode("merge", "assemble_tree")
mrg.setInput(0, trunk_clean)
mrg.setInput(1, jitter)

recalc = geo.createNode("attribwrangle", "recalculate_canopy_quad_attribs")
recalc.setInput(0, mrg)
recalc.setInput(1, tips)   # 枝梢：每张卡的 appear_pos 源
set_menu(recalc, "class", ["prim"])
recalc.parm("snippet").set('''// 与 /obj/tiny_glade_tree 里同名节点一致：每张四边形卡片重算卡心 + 卡面法线
int pts[] = primpoints(0, @primnum);
if (len(pts) != 4) return;

int i00 = -1, i10 = -1, i01 = -1;
foreach (int pt; pts) {
    vector c = point(0, "Cd", pt);
    if (c.z > 0.5) return;                 // 树干部件，跳过
    int u = int(rint(c.x)), v = int(rint(c.y));
    if (u == 0 && v == 0) i00 = pt;
    else if (u == 1 && v == 0) i10 = pt;
    else if (u == 0 && v == 1) i01 = pt;
}
if (i00 < 0 || i10 < 0 || i01 < 0) return;

vector p00 = point(0, "P", i00);
vector nml = normalize(cross(point(0, "P", i10) - p00, point(0, "P", i01) - p00));

vector ctr = 0, navg = 0;
foreach (int pt; pts) {
    ctr  += point(0, "P", pt);
    navg += point(0, "N", pt);
}
ctr /= 4.0;
if (dot(nml, normalize(navg)) < 0) nml = -nml;

v@prim_center  = ctr;
v@prim_normals = nml;

// appear_pos = 距卡心最近的枝梢。资产里 312 张卡共用 46 个 appear_pos —— 同一枝上的叶簇成簇冒出。
vector ap = point(1, "P", nearpoint(1, ctr));
// age：整树高度归一化，高处后长；同卡 4 角再各自抖一点（实测中位差 0.13）
float base_age = fit(relbbox(0, ctr).y, 0.0, 1.0, 0.15, 1.0);

foreach (int pt; pts) {
    setpointattrib(0, "prim_center",  pt, ctr);
    setpointattrib(0, "prim_normals", pt, nml);
    setpointattrib(0, "appear_pos",   pt, ap);
    setpointattrib(0, "age", pt, clamp(base_age + (rand(pt * 13 + 7) - 0.5) * 0.26, 0.0, 1.0));
}''')

out_asset = geo.createNode("null", "OUT_gen_asset")
out_asset.setInput(0, recalc)

# ================================================================= 5. 预览配色（仅渲染用）
prev = geo.createNode("attribwrangle", "preview_color")
prev.setInput(0, out_asset)
set_menu(prev, "class", ["point"])
prev.parm("snippet").set('''// Cd 在资产编码里是角码，不能当颜色用。这里另建一份仅供预览的配色。
vector bb = relbbox(0, v@P);
vector col;
if (v@Cd.z < 0.5) {
    col = lerp(set(0.13, 0.26, 0.10), set(0.30, 0.47, 0.17), bb.y);   // 夏季绿
    col *= fit01(rand(@primnum * 3 + 1), 0.78, 1.18);
} else {
    col = set(0.24, 0.15, 0.09) * fit01(rand(@ptnum), 0.85, 1.35);    // 树皮暖棕
}
v@Cd = col;''')

out_prev = geo.createNode("null", "OUT_preview")
out_prev.setInput(0, prev)
out_prev.setDisplayFlag(True)
out_prev.setRenderFlag(True)

geo.layoutChildren()

# ----------------------------------------------------------------- 报告
def stats(node, label):
    g = node.geometry()
    b = g.boundingBox()
    P("%-26s points=%-6d prims=%-6d bbox=(%.2f %.2f %.2f)-(%.2f %.2f %.2f)" % (
        label, len(g.points()), len(g.prims()),
        b.minvec()[0], b.minvec()[1], b.minvec()[2],
        b.maxvec()[0], b.maxvec()[1], b.maxvec()[2]))
    return g

bad = []
for n in geo.children():
    try:
        n.cook(force=False)
    except Exception:
        pass
    if n.errors():
        bad.append((n.name(), n.errors()))
    elif n.warnings():
        bad.append((n.name(), ["W: " + w for w in n.warnings()]))
if bad:
    P("!!! 节点问题:")
    for nm, msgs in bad:
        P("   %-28s %s" % (nm, " | ".join(m.replace(chr(10), " ")[:200] for m in msgs)))

P("")
gs = stats(skel, "branch_skeleton")
tg = gs.findPointGroup("tips")
P("  枝梢 tips = %d" % (len(tg.points()) if tg else -1))
stats(wire, "trunk_tubes")
stats(shell, "canopy_shell")
stats(scat, "card_centers")
stats(jitter, "leaf_cards")
g = stats(out_asset, "OUT_gen_asset")

cd = g.findPointAttrib("Cd")
if cd is None:
    P("  !! Cd 属性丢失")
else:
    ncanopy = ntrunk = 0
    for p in g.prims():
        z = p.vertex(0).point().attribValue(cd)[2]
        if z < 0.5:
            ncanopy += 1
        else:
            ntrunk += 1
    P("  叶簇卡片 quad = %d    枝干面 = %d" % (ncanopy, ntrunk))
    pc = g.findPointAttrib("prim_center")
    if pc is None:
        P("  !! prim_center 缺失")
    else:
        nf = sum(1 for pt in g.points()
                 if pt.attribValue(cd)[2] < 0.5 and pt.attribValue(pc) != (0.0, 0.0, 0.0))
        P("  prim_center 已重算的树冠点 = %d / %d" % (nf, ncanopy * 4))
    ap = g.findPointAttrib("appear_pos")
    if ap:
        uniq = set()
        for pt in g.points():
            if pt.attribValue(cd)[2] < 0.5:
                uniq.add(tuple(round(c, 4) for c in pt.attribValue(ap)))
        P("  卡片共用的 appear_pos 数 = %d  (资产实测 46)" % len(uniq))
P("  point attribs: %s" % sorted(a.name() for a in g.pointAttribs()))
P("  prim  attribs: %s" % sorted(a.name() for a in g.primAttribs()))

# ----------------------------------------------------------------- 导出 + 存盘
outdir = "D:/MyProject/Houdini/TinyGlade/out"
if not os.path.exists(outdir):
    os.makedirs(outdir)
for ext in (".bgeo.sc", ".obj"):
    path = outdir + "/tinyglade_tree_generated" + ext
    out_asset.geometry().saveToFile(path)
    P("导出 %s  (%.0f KB)" % (path, os.path.getsize(path) / 1024.0))

oldgeo = obj.node("tiny_glade_tree")
if oldgeo:
    oldgeo.setDisplayFlag(False)
geo.setDisplayFlag(True)

hou.hipFile.save(HIP)
P("")
P("已保存 " + HIP)
