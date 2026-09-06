# -*- coding: utf-8 -*-
"""
地被可调性探针：证明**在细节面板改完立刻生效**，而不是只证明"属性上写了 EditAnywhere"。

四条各自独立，任一条不过都是真 bug（而且都是那种"改了没反应"的静默失效，
没有任何断言看得见 —— 计划 D14 开篇 FrameMaterial 那条的同型）：

  ① 草的密度：50 → 20，实例数应按 20/50 成比例掉。
  ② 花的密度：逐种可调，1.2 → 6，实例数应涨 ~5 倍。
  ③ 材质（含 MI）：换掉物种的 `Material`，组件的 `InstanceMaterial` 必须跟着换。
  ④ 网格：换掉物种的 `Mesh`，组件的基础网格必须跟着换 —— 这条最容易漏，因为组件数没变，
     只看数量的实现会得到"换了资产但什么都没发生"（裙边摆件踩过）。

⚠️ **两条路径不一样，别把探针的结果当成细节面板的结果**（2026-09-04 查清，verbose 日志实证）：
   · 改**单个结构体**属性（`Grass`）：`set_editor_property` 会发通知 ⇒ 走
     `PostEditChangeProperty` ⇒ 自动重散。日志里能看到 `prop=DensityPerSqM member=Grass`。
   · 改 **TArray 属性**（`Flowers`）：`set_editor_property` **一行通知都不发** ⇒ 不会自动重散。
     所以脚本里改完数组**必须自己补一句 `RebuildGroundCover()`**（幂等哈希会吃掉多余的调用）。
   · **细节面板不受这条影响**：UI 改数组元素走 `PostEditChangeChainProperty`，而
     `UObject::PostEditChangeChainProperty` 结尾会转发 `PostEditChangeProperty`
     （`CoreUObject/Private/UObject/Obj.cpp:643`）⇒ 手改是自动生效的。
⚠️ **不存盘**，跑完关卡是脏的。
"""
import unreal

PKG = "/PCGPlugins/HouseTest"
ASSET = "%s/TinyGladeAsset" % PKG
unreal.EditorLoadingAndSavingUtils.load_map("%s/L_HouseGroundDemo" % PKG)
A = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
g = next(a for a in A.get_all_level_actors()
         if "Ground" in a.get_class().get_name() and "Shaper" not in a.get_class().get_name())


def cover_components():
    """按下标取地被组件：0 = 草，1.. = 花。组件是 Transient 的，按创建序附在根上。"""
    out = []
    for c in g.get_components_by_class(unreal.ActorComponent):
        if c.get_class().get_name() == "CSGpuInstancedMeshComponent":
            out.append(c)
    return out


def count(i):
    return g.call_method("DebugReadGroundCoverCountGpuSync", (i,))


def report(tag):
    comps = cover_components()
    # 石阶/石子的组件也是同一个类，所以只认地被那几个：地被是最后建的，取末尾 N 个。
    n = 1 + len(g.get_editor_property("Flowers"))
    cover = comps[-n:] if len(comps) >= n else comps
    for i, c in enumerate(cover):
        mesh = c.get_editor_property("BaseMesh") if "BaseMesh" in dir(c) else None
        unreal.log("TWEAK %-14s 物种%d 数=%-7s 材质=%s"
                   % (tag, i, count(i),
                      c.get_editor_property("InstanceMaterial").get_name()
                      if c.get_editor_property("InstanceMaterial") else "<none>"))
    return cover


unreal.log("TWEAK ===== 基线 =====")
report("baseline")
base_grass, base_f1 = count(0), count(1)

# ---- ① 草的密度 ----
grass = g.get_editor_property("Grass")
grass.set_editor_property("DensityPerSqM", 20.0)
g.set_editor_property("Grass", grass)
after = count(0)
ratio = after / float(max(base_grass, 1))
unreal.log("TWEAK ① 草密度 50→20：%d → %d（比 %.3f，期望 0.40）%s"
           % (base_grass, after, ratio, "PASS" if 0.34 < ratio < 0.46 else "**FAIL**"))

# ---- ② 花的密度（逐种） ----
# ⚠️ 两个 Python 侧的坑叠在一起，第一版探针因此报了两个**假 FAIL**，差点被读成 C++ 有 bug：
#    ① `array[i]` 取出来的是**结构体副本**，改副本要 `flowers[0] = f0` 写回数组；
#    ② TArray 的 `set_editor_property` 不发变更通知，所以还得自己补 `RebuildGroundCover()`。
flowers = g.get_editor_property("Flowers")
f0 = flowers[0]
f0.set_editor_property("DensityPerSqM", 6.0)
flowers[0] = f0
g.set_editor_property("Flowers", flowers)
# 读回：把"属性根本没写进去"和"写进去了但没重散"分开 —— 两者的修法完全不同。
rb = g.get_editor_property("Flowers")
unreal.log("TWEAK ②诊断 写回后 actor 上的 Flowers[0].DensityPerSqM = %s（数组长 %d）"
           % (rb[0].get_editor_property("DensityPerSqM"), len(rb)))
g.call_method("RebuildGroundCover")     # TArray 的 set 不发通知，脚本里必须自己补这一句
after_f1 = count(1)
ratio = after_f1 / float(max(base_f1, 1))
unreal.log("TWEAK ② 花1密度 1.2→6.0：%d → %d（比 %.2f，期望 5.0）%s"
           % (base_f1, after_f1, ratio, "PASS" if 4.3 < ratio < 5.7 else "**FAIL**"))

# ---- ③ 材质（换成一个 MI） ----
mi = unreal.EditorAssetLibrary.load_asset("%s/Materials/MI_TG_Grass" % ASSET)
alt = unreal.EditorAssetLibrary.load_asset("%s/Materials/M_TG_VertexColor" % ASSET)
before_mat = cover_components()[-3].get_editor_property("InstanceMaterial")
grass = g.get_editor_property("Grass")
grass.set_editor_property("Material", alt)
g.set_editor_property("Grass", grass)
now_mat = cover_components()[-3].get_editor_property("InstanceMaterial")
unreal.log("TWEAK ③ 草材质 %s → %s：组件上现在是 %s  %s"
           % (before_mat.get_name() if before_mat else "<none>", alt.get_name(),
              now_mat.get_name() if now_mat else "<none>",
              "PASS" if now_mat == alt else "**FAIL**"))
# 换回 MI，顺带证明 MI 也吃得下
grass.set_editor_property("Material", mi)
g.set_editor_property("Grass", grass)
back = cover_components()[-3].get_editor_property("InstanceMaterial")
unreal.log("TWEAK ③b 换回 MI：%s  %s"
           % (back.get_name() if back else "<none>", "PASS" if back == mi else "**FAIL**"))

# ---- ④ 网格 ----
alt_mesh = unreal.EditorAssetLibrary.load_asset("%s/Meshes/clutter_reed" % ASSET) \
        or unreal.EditorAssetLibrary.load_asset("%s/Meshes/lowpoly_flower" % ASSET)
flowers = g.get_editor_property("Flowers")
f0 = flowers[0]
old_name = f0.get_editor_property("Mesh").get_name()
f0.set_editor_property("Mesh", alt_mesh)
flowers[0] = f0                       # 同上：不写回数组等于什么都没改
g.set_editor_property("Flowers", flowers)
rb = g.get_editor_property("Flowers")
unreal.log("TWEAK ④诊断 写回后 actor 上的 Flowers[0].Mesh = %s"
           % (rb[0].get_editor_property("Mesh").get_name() if rb[0].get_editor_property("Mesh") else "<none>"))
g.call_method("RebuildGroundCover")
comp = cover_components()[-2]
now_mesh = comp.get_editor_property("BaseMesh")
unreal.log("TWEAK ④ 花1网格 %s → %s：组件上现在是 %s  %s"
           % (old_name, alt_mesh.get_name(),
              now_mesh.get_name() if now_mesh else "<none>",
              "PASS" if now_mesh == alt_mesh else "**FAIL**"))

unreal.log("TWEAK DONE")
