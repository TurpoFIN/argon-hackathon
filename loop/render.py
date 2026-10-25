"""Render a simulated storm result the way a model can watch it: a north-up peak-flood map with the
plan drawn on it, a four-step timeline, and a short MP4 of the whole storm."""
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from matplotlib import colormaps
from PIL import Image, ImageDraw, ImageFont

SCALE = 3
WATER = colormaps["Blues"]
OCEAN = np.array([11, 31, 51], np.uint8)
BUILDING = np.array([70, 70, 74], np.uint8)
FONT = ImageFont.load_default(size=13)
SMALL = ImageFont.load_default(size=11)
TITLE = ImageFont.load_default(size=16)


def _base(world):
    z = np.clip((world.z - 0.0) / 30.0, 0, 1)
    gray = (60 + 120 * z).astype(np.uint8)
    rgb = np.repeat(gray[..., None], 3, axis=2)
    rgb[world.walls] = BUILDING
    rgb[world.ocean] = OCEAN
    return rgb


def depth_image(world, depth, scale=SCALE):
    rgb = _base(world).astype(np.float32)
    land = ~world.walls & ~world.ocean
    wet = land & (depth >= 0.03)
    level = np.clip(np.log1p(depth / 0.05) / np.log1p(1.5 / 0.05), 0.15, 1.0)
    water = WATER(level)[..., :3] * 255
    alpha = np.where(wet, 0.88, 0.0)[..., None]
    rgb = rgb * (1 - alpha) + water * alpha
    excluded = world.blocked & land
    rgb[excluded] = rgb[excluded] * 0.55 + np.array([255, 77, 77]) * 0.45
    image = Image.fromarray(rgb.astype(np.uint8)).resize((depth.shape[1] * scale, depth.shape[0] * scale), Image.NEAREST)
    return image


def _xy(cell, scale=SCALE):
    return ((cell[1] + 0.5) * scale, (cell[0] + 0.5) * scale)


def graticule(draw, world, step=0.002):
    """Longitude/latitude lines every `step` degrees, labelled, so a model can place vertices."""
    rows, cols = world.lon.shape
    mid_r, mid_c = rows // 2, cols // 2
    for value in np.arange(np.ceil(world.lon.min() / step) * step, world.lon.max(), step):
        c = int(np.argmin(np.abs(world.lon[mid_r] - value)))
        x = (c + 0.5) * SCALE
        draw.line([x, 0, x, rows * SCALE], fill=(255, 255, 255, 60), width=1)
        draw.text((x + 2, 2), f"{value:.3f}", fill=(220, 220, 220), font=SMALL, stroke_width=2, stroke_fill=(0, 0, 0))
    for value in np.arange(np.ceil(world.lat.min() / step) * step, world.lat.max(), step):
        r = int(np.argmin(np.abs(world.lat[:, mid_c] - value)))
        y = (r + 0.5) * SCALE
        draw.line([0, y, cols * SCALE, y], fill=(255, 255, 255, 60), width=1)
        draw.text((2, y + 2), f"{value:.3f}", fill=(220, 220, 220), font=SMALL, stroke_width=2, stroke_fill=(0, 0, 0))


def plan_map(world, result, peak, ledger, exposure, candidates, title, drawn=None):
    """The main frame the model watches: peak depth, the plan's actions and the damage they left."""
    drawn = drawn or {"barriers": [], "doorways": set()}
    image = depth_image(world, peak).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    graticule(ImageDraw.Draw(overlay), world)
    image = Image.alpha_composite(image, overlay).convert("RGB")
    draw = ImageDraw.Draw(image)
    for cells in drawn["barriers"]:
        for cell in cells:
            x, y = _xy(cell)
            draw.rectangle([x - SCALE / 2 - 1, y - SCALE / 2 - 1, x + SCALE / 2 + 1, y + SCALE / 2 + 1], fill=(255, 122, 0))
    for number, cell in enumerate(drawn.get("pumps", []), start=1):
        x, y = _xy(cell)
        draw.ellipse([x - 7, y - 7, x + 7, y + 7], fill=(246, 68, 185), outline=(255, 255, 255), width=2)
        draw.text((x - 3, y - 7), "P", fill=(255, 255, 255), font=SMALL)
    usd = ledger["_per_structure_usd"]
    for row, (r, c), value in zip(exposure.rows, exposure.cell, usd):
        x, y = _xy((r, c))
        if row["fd_id"] in drawn["doorways"]:
            draw.rectangle([x - 4, y - 4, x + 4, y + 4], outline=(0, 220, 255), width=2)
        if value <= 0:
            continue
        radius = 2 + min(9, np.sqrt(value / 25_000))
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], outline=(255, 196, 0), width=2)
    cells = {d["id"]: d["cell"] for d in world.drains}
    actions = result["preparation"]["actions"]
    cleared = {a["drain_id"] for a in actions if a["kind"] == "preventive_clearing" and a.get("completed")}
    partial = {a["drain_id"] for a in actions if a["kind"] == "preventive_clearing" and not a.get("completed")}
    failed = {a["drain_id"] for a in actions if a["kind"] in ("inaccessible", "clearing_skipped_for_staging")}
    for number, candidate in enumerate(candidates, start=1):
        x, y = _xy(cells[candidate["id"]])
        if candidate["id"] in cleared:
            draw.ellipse([x - 7, y - 7, x + 7, y + 7], outline=(0, 230, 120), width=3)
        elif candidate["id"] in partial:
            draw.ellipse([x - 7, y - 7, x + 7, y + 7], outline=(255, 160, 0), width=3)
        elif candidate["id"] in failed:
            draw.line([x - 6, y - 6, x + 6, y + 6], fill=(255, 60, 60), width=3)
            draw.line([x - 6, y + 6, x + 6, y - 6], fill=(255, 60, 60), width=3)
        draw.text((x + 8, y - 8), str(number), fill=(255, 255, 255), font=SMALL, stroke_width=2, stroke_fill=(0, 0, 0))
    for crew in world.crews:
        x, y = (crew["x"] + 0.5) * SCALE, (crew["y"] + 0.5) * SCALE
        draw.polygon([(x, y - 9), (x - 8, y + 6), (x + 8, y + 6)], fill=(255, 255, 255), outline=(0, 0, 0))
    header = Image.new("RGB", (image.width, 92), (18, 18, 20))
    hd = ImageDraw.Draw(header)
    hd.text((10, 6), title, fill=(240, 240, 240), font=TITLE)
    hd.text((10, 28), f"Damage ${ledger['total_usd']:,.0f} · {ledger['structures_flooded']} flooded buildings",
            fill=(200, 200, 200), font=FONT)
    hd.text((10, 48), "blue peak depth · orange sandbag wall · cyan doorway sandbags · magenta P pump", fill=(150, 150, 150), font=SMALL)
    hd.text((10, 63), "green cleared drain · red X failed · triangle crew · yellow damaged", fill=(150, 150, 150), font=SMALL)
    hd.text((10, 78), "red tint: hazard no-go area · numbers: candidate drains", fill=(150, 150, 150), font=SMALL)
    framed = Image.new("RGB", (image.width, image.height + header.height))
    framed.paste(header, (0, 0))
    framed.paste(image, (0, header.height))
    return framed


def timeline(world, frames, minutes=(16, 32, 48, 60)):
    """Four snapshots of water depth over the storm, two by two."""
    tiles = []
    for minute in minutes:
        index = min(len(frames) - 1, round(minute * 60 / 120))
        tile = depth_image(world, frames[index], scale=1)
        tile = tile.resize((tile.width * 3 // 2, tile.height * 3 // 2), Image.NEAREST)
        ImageDraw.Draw(tile).text((6, 4), f"t = {index * 2} min", fill=(255, 255, 255), font=FONT,
                                  stroke_width=2, stroke_fill=(0, 0, 0))
        tiles.append(tile)
    w, h = tiles[0].size
    sheet = Image.new("RGB", (w * 2 + 6, h * 2 + 6), (18, 18, 20))
    for i, tile in enumerate(tiles):
        sheet.paste(tile, ((i % 2) * (w + 6), (i // 2) * (h + 6)))
    return sheet


def composite(main, sheet):
    height = max(main.height, sheet.height)
    canvas = Image.new("RGB", (main.width + sheet.width + 8, height), (18, 18, 20))
    canvas.paste(main, (0, 0))
    canvas.paste(sheet, (main.width + 8, main.height - sheet.height if main.height > sheet.height else 0))
    return canvas


def video(world, frames, destination: Path, fps=6):
    """Whole storm as an MP4 (one frame per two simulated minutes)."""
    if shutil.which("ffmpeg") is None:
        return None
    with tempfile.TemporaryDirectory() as folder:
        for i, depth in enumerate(frames):
            image = depth_image(world, depth, scale=2)
            ImageDraw.Draw(image).text((6, 4), f"t = {i * 2} min", fill=(255, 255, 255), font=FONT,
                                       stroke_width=2, stroke_fill=(0, 0, 0))
            image.save(Path(folder) / f"f{i:03d}.png")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(fps), "-i", str(Path(folder) / "f%03d.png"),
                        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(destination)],
                       check=True, timeout=120)
    return destination
