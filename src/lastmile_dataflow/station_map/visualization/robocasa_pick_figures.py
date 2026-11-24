"""Contact outlines and same-station native RGB for the pick-scene selection sheet."""

from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.spatial import ConvexHull

INK = "#182b37"
MUTED = "#50636b"
PAPER = "#f3f5f4"
TEAL = "#168275"
AMBER = "#b17735"
BLUE = "#295caa"
OBJECT = "#965333"
CAMERA_KEYS = ("robot0_agentview_left", "robot0_agentview_right")
FONT_ROOT = Path("/usr/share/fonts/truetype/dejavu")


@lru_cache
def _font(size, bold=False):
    file = FONT_ROOT / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    return ImageFont.truetype(str(file), size) if file.exists() else ImageFont.load_default(size=size)


def _text(draw, xy, value, size=16, *, color=INK, bold=False):
    draw.text(xy, str(value), fill=color, font=_font(size, bold))


def _paste(image, path, xy, size):
    with Image.open(path) as frame:
        image.paste(frame.resize(size, Image.Resampling.LANCZOS), xy)


def _map(path, states, reps, geometry, evidence, *, close_up):
    size = (336, 238)
    card = Image.new("RGB", size, "#ffffff")
    draw = ImageDraw.Draw(card)
    # Keep the frame and scale identical within each plot type.
    support = np.array(list(geometry["support_rectangles_xy_m"].values())).reshape(-1, 2)
    lo, hi = support.min(axis=0), support.max(axis=0)
    scale, x0, y0 = 98, 168, 117
    if close_up:
        scale = min(310 / (hi[0] - lo[0]), 176 / (hi[1] - lo[1]))
        center = (lo + hi) / 2
        x0, y0 = 168 - scale * center[0], 117 + scale * center[1]

    def xy(point):
        return x0 + scale * point[0], y0 - scale * point[1]

    for i in range(-9, 10):
        x, y = xy([i / 10, i / 10])
        draw.line((x, 23, x, 211), fill="#f0f2f2")
        if not close_up:
            draw.line((9, y, 327, y), fill="#f0f2f2")
    a = xy(lo)
    b = xy(hi)
    draw.rectangle((max(8, a[0]), max(23, b[1]), min(327, b[0]), min(211, a[1])),
                   fill="#e9e3da", outline="#a69785")
    origin, axes = np.array(geometry["origin_world"]), np.array(geometry["axes_world"])
    rep_labels = {station: label[0] for label, station in reps.items()}
    if not close_up:
        for row in sorted(states, key=lambda q: q["valid"]):
            pos = (np.array([row["x"], row["y"], origin[2]]) - origin) @ axes
            x, y = xy(pos)
            if not (8 <= x <= 327 and 23 <= y <= 211):
                continue
            color = TEAL if row["pi05_visible"] else AMBER if row["valid"] else "#d3d9db"
            r = 3 if row["valid"] else 2
            draw.ellipse((x-r, y-r, x+r, y+r), fill=color)
            if row["station_id"] in rep_labels:
                draw.ellipse((x-7, y-7, x+7, y+7), outline=INK, width=1)
                _text(draw, (x-4, y+7), rep_labels[row["station_id"]], 11, bold=True)

    for key, polys, color, fill in (
        (geometry.get("distractor_label", "Apple"),
         geometry.get("distractor_outline_xy_m", geometry.get("apple_outline_xy_m")), "#607049", "#b5c28f"),
        ("Cup", geometry["cup_outline_xy_m"], BLUE, "#a5c0eb"),
        ("Obstacle", evidence.get("obstacle_outline_xy_m"), OBJECT, "#edb590"),
    ):
        if not polys:
            continue
        points = np.concatenate(polys)
        loop = points[ConvexHull(points).vertices]
        polygon = [xy(point) for point in loop]
        if all(8 <= x <= 327 and 23 <= y <= 211 for x, y in polygon):
            draw.polygon(polygon, fill=fill, outline=color)
            if close_up:
                for poly in polys:
                    vertices = [xy(point) for point in poly]
                    draw.line(vertices + vertices[:1], fill=color)
                x, y = xy(points.mean(axis=0))
                _text(draw, (x-11, y+16), key, 10, color=color, bold=True)
    end = 18 + scale * (0.10 if close_up else 0.20)
    draw.line((18, 219, end, 219), fill=INK, width=2)
    _text(draw, (end+5, 211), "10 cm" if close_up else "20 cm", 12)
    _text(draw, (181, 214), "Left  /  Robot side  /  Right", 10, color=MUTED)
    _text(draw, (12, 5), "Counter geometry" if close_up else "Shared station grid", 13, bold=True)
    card.save(path)
    return card


def _gap(summary):
    if summary["horizontal_gap_m"] is None:
        return "Original kitchen"
    return f"{summary['actual_center_distance_m']*100:.1f} cm center  /  {summary['horizontal_gap_m']*1000:.1f} mm XY gap"


def _camera_label(camera, observation):
    side = camera.rsplit("_", 1)[-1].title()
    fraction = observation["visible_fraction"]
    visible = "cup out" if fraction is None else f"{fraction:.0%} of cup"
    return f"{side} cam  |  {visible}  |  {observation['reference_pixels']} px"


def draw_details(output, summary, row, states, reps, geometry, overhead, captured):
    key = summary["id"]
    detail = output / f"{key}.png"
    image = Image.new("RGB", (1088, 956), PAPER)
    draw = ImageDraw.Draw(image)
    _text(draw, (24, 17), key, 31, bold=True)
    _text(draw, (24, 61), f"{_gap(summary)}     |     {summary['legal']} legal stations     |     {summary['visible']} cup-visible", 17)
    station_map = _map(overhead.with_name("station-map.png"), states, reps, geometry, row, close_up=False)
    close_up = _map(overhead.with_name("counter-map.png"), states, reps, geometry, row, close_up=True)
    image.paste(station_map, (24, 102))
    image.paste(close_up, (376, 102))
    draw.rectangle((728, 102, 1064, 339), fill="white")
    _text(draw, (740, 106), "Counter RGB", 13, bold=True)
    _paste(image, overhead, (797, 126), (207, 207))
    _text(draw, (26, 347), "Teal  cup-visible    Amber  legal, filtered by camera gate    Grey  invalid", 13, color=MUTED)

    by_id = {r["station_id"]: r for r in states}
    for index, (name, station) in enumerate(reps.items()):
        x = 24 + index * 352
        state = by_id[station]
        _text(draw, (x, 381), f"{name} station  /  {station}", 17, bold=True)
        for camera_index, camera in enumerate(CAMERA_KEYS):
            y = 408 + camera_index * 262
            sample = captured.get((station, camera))
            if sample is None:
                draw.rectangle((x, y, x + 335, y + 244), fill="white")
                _text(draw, (x + 14, y + 108), state["invalid_reason"].replace("_", " "), 16, color=MUTED)
                continue
            draw.rectangle((x, y, x + 335, y + 252), fill="white")
            _paste(image, sample["path"], (x + 56, y + 2), (224, 224))
            _text(draw, (x + 12, y + 230), _camera_label(camera, state["cameras"][camera]), 15)
    image.save(detail)
    return {"detail": detail, "map": overhead.with_name("station-map.png"), "overhead": overhead,
            "states": by_id, "representatives": reps, "captured": captured}


def _best_camera(state):
    return max(CAMERA_KEYS, key=lambda name: (
        state["cameras"][name]["status"] == "visible",
        state["cameras"][name]["visible_fraction"] or 0,
        state["cameras"][name]["visible_pixels"],
    ))


def draw_overview(output, kind, entries):
    entries = [(summary, figure) for summary, figure in entries if figure]
    entries.sort(key=lambda entry: (entry[0]["kind"] != "original", entry[0]["placement"] != "far",
                                    round(entry[0]["actual_center_distance_m"] or 0, 3), entry[0]["id"]))
    image = Image.new("RGB", (1290, 102 + 198 * len(entries)), PAPER)
    draw = ImageDraw.Draw(image)
    title = "Kettle001  /  Native movable obstacle" if kind == "kettle" else "Box  /  Matched collision bounds"
    _text(draw, (20, 14), title, 32, bold=True)
    _text(draw, (20, 58), "Same kitchen, cup, nominal grid and three camera stations", 17, color=MUTED)

    for index, (summary, figure) in enumerate(entries):
        y = 92 + 198 * index
        draw.rectangle((12, y, 1278, y + 185), fill="white")
        _text(draw, (21, y + 15), summary["id"], 19, bold=True)
        _text(draw, (21, y + 48), _gap(summary), 13)
        _text(draw, (21, y + 76), f"{summary['legal']} legal  /  {summary['visible']} visible", 14, color=TEAL)
        if summary["placement"] not in ("far", None) and "shared_legal_with_far" in summary:
            _text(draw, (21, y + 106), f"With far: {summary['shared_legal_with_far']} legal", 13)
            _text(draw, (21, y + 128), f"           {summary['shared_visible_with_far']} visible", 13)
        _paste(image, figure["map"], (263, y + 5), (242, 171))
        _paste(image, figure["overhead"], (511, y + 9), (162, 162))
        for col, (name, station) in enumerate(figure["representatives"].items()):
            x = 691 + col * 193
            state = figure["states"][station]
            _text(draw, (x, y + 2), f"{name}  {station}", 13, bold=True)
            if not state["valid"]:
                _text(draw, (x, y + 72), state["invalid_reason"].replace("_", " "), 13, color=AMBER)
                continue
            camera = _best_camera(state)
            sample = figure["captured"][(station, camera)]
            _paste(image, sample["path"], (x+17, y+23), (141, 141))
            fraction = state["cameras"][camera]["visible_fraction"]
            label = "out of frame" if fraction is None else f"{fraction:.0%} cup visible"
            _text(draw, (x+3, y+166), f"{camera.rsplit('_', 1)[-1].title()} cam  /  {label}", 12)
    path = output / f"{kind}-overview.png"
    image.save(path)
    return path
