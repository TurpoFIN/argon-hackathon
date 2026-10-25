import json, sys
import numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LightSource, ListedColormap
R = "/home/ubuntu/work/argonhack"
sys.path.insert(0, R)
OUT = R + "/web/public/slide/img/"
BLUE = "#2f6fb5"
d = np.load(R + "/data/grid/city.npz")
meta = json.load(open(R + "/data/grid/meta.json"))
x0, y0, dx, nx, ny = meta["x0"], meta["y0"], meta["dx"], meta["nx"], meta["ny"]
ext = [x0, x0 + nx * dx, y0, y0 + ny * dx]
z, bh, cover, f100 = d["z"], d["bldg_h"], d["cover"], d["flood100"]
bay = cover == 3
# light basemap: land, buildings slightly darker, bay white
base = np.full((ny, nx, 3), 0.955)
base[bh > 0] = 0.895
base[bay] = 1.0

def fig():
    f = plt.figure(figsize=(4, 4), dpi=100)
    ax = f.add_axes([0, 0, 1, 1]); ax.set_axis_off()
    ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3]); ax.set_aspect("equal")
    # centre the portrait tile in a square canvas
    w = ext[1] - ext[0]; h = ext[3] - ext[2]
    pad = (h - w) / 2
    ax.set_xlim(ext[0] - pad, ext[1] + pad)
    return f, ax

def save(f, name):
    f.savefig(OUT + name, facecolor="white"); plt.close(f)

def basemap(ax):
    ax.imshow(base, origin="lower", extent=ext, interpolation="nearest")

# 1 terrain hillshade
f, ax = fig()
zz = np.where(bay, np.nan, z)
ls = LightSource(azdeg=315, altdeg=40)
hs = ls.hillshade(np.nan_to_num(z, nan=-2.0), vert_exag=6, dx=dx, dy=dx)
rgb = np.dstack([0.30 + 0.70 * hs] * 3)
rgb[bay] = 1.0
ax.imshow(rgb, origin="lower", extent=ext, interpolation="bilinear")
save(f, "1_terrain.png")

# 2 buildings by height
f, ax = fig()
img = np.full((ny, nx, 3), 0.96); img[bay] = 1.0
hn = np.clip(bh / 60.0, 0, 1)
c = np.array([0.18, 0.44, 0.71])
for k in range(3):
    ch = img[..., k]
    ch[bh > 0] = (0.80 * (1 - hn) + c[k] * hn)[bh > 0]
ax.imshow(img, origin="lower", extent=ext, interpolation="nearest")
save(f, "2_buildings.png")

# 3 trees
t = json.load(open(R + "/data/grid/trees.json"))
f, ax = fig(); basemap(ax)
ax.scatter([a["x"] for a in t], [a["y"] for a in t], s=0.6, c=BLUE, linewidths=0, alpha=0.9)
save(f, "3_trees.png")

# 4 inlets
il = json.load(open(R + "/data/grid/inlets.json"))
f, ax = fig(); basemap(ax)
ax.scatter([a["x"] for a in il], [a["y"] for a in il], s=2.2, c=BLUE, linewidths=0)
save(f, "4_inlets.png")

# 5 flood100
f, ax = fig(); basemap(ax)
m = np.ma.masked_where(f100 == 0, f100)
ax.imshow(m, origin="lower", extent=ext, cmap=ListedColormap([BLUE]), interpolation="nearest", alpha=0.85)
save(f, "5_flood100.png")

# 6 311 reports
v = json.load(open(R + "/data/grid/validation.json"))["311_2022_12_31"]
f, ax = fig(); basemap(ax)
ax.scatter([a["x"] for a in v], [a["y"] for a in v], s=22, c=BLUE, linewidths=0.5, edgecolors="white")
save(f, "6_311.png")

# 7 storm: rain bars + tide line
from sim.storm import load_storm
s = load_storm("dec31_2022")
th = np.asarray(s.t) / 3600.0
f = plt.figure(figsize=(4, 4), dpi=100)
ax = f.add_axes([0.19, 0.16, 0.62, 0.76])
ax.bar(th, s.rain_mm_h, width=(th[1] - th[0]) * 0.9, color=BLUE, align="edge")
ax.set_ylabel("rain mm/h", fontsize=17, color="#444")
ax2 = ax.twinx()
ax2.plot(th, s.tide_m, color="#333", lw=2.5)
ax.set_xticks([0,2,4,6]); ax.locator_params(axis="y", nbins=4); ax2.locator_params(axis="y", nbins=4)
ax2.set_ylabel("tide m NAVD88", fontsize=17, color="#444")
ax.set_xlabel("hours", fontsize=17, color="#444")
for a in (ax, ax2):
    a.tick_params(labelsize=15, colors="#555", length=3)
    for sp in a.spines.values(): sp.set_color("#bbb")
ax.spines["top"].set_visible(False); ax2.spines["top"].set_visible(False)
f.savefig(OUT + "7_storm.png", facecolor="white"); plt.close(f)
print("ok", len(t), len(il), len(v), float(np.max(s.rain_mm_h)))
