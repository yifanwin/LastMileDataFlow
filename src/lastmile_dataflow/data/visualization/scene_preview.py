"""Read-only scene contact sheets: paired observers and final station coverage."""
from contextlib import ExitStack, closing
from html import escape
import logging
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ...config import RobotConfig
from ...runtime.simulation import Simulation
from ...scenes.geometry import descendants, geom_points
from ...utils.logging_config import Progress
from ...utils.io import read_json, rows
from ..rendering.camera_calibration import rendered_camera
from ..rendering.edit_views import CameraRig, ViewConfig

logger = logging.getLogger(__name__)
WIDTH, HEIGHT = 640, 480
TARGET, ADDED, STATION = '#ef4444', '#edaa36', '#38bdf8'


def font(size):
    paths = ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
             '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    path = next((p for p in paths if Path(p).exists()), None)
    return ImageFont.truetype(path, size) if path else ImageFont.load_default()


def project(points, calibration):
    """World XYZ to actual renderer pixels; points behind the camera are omitted."""
    transform = np.asarray(calibration['T_world_camera_optical'])
    xyz = (np.asarray(points) - transform[:3, 3]) @ transform[:3, :3]
    xyz = xyz[xyz[:, 2] > calibration['near_m']]
    if not len(xyz):
        return np.empty((0, 2))
    if calibration['projection_type'] != 'orthographic':
        xyz = xyz / xyz[:, 2:3]
    return (xyz @ np.asarray(calibration['intrinsics']['K']).T)[:, :2]


def object_points(sim, name):
    bodies = descendants(sim.model, sim.model.body(name).id)
    points = [geom_points(sim, g) for g in range(sim.model.ngeom)
              if int(sim.model.geom_bodyid[g]) in bodies
              and sim.model.geom_type[g] != mujoco.mjtGeom.mjGEOM_PLANE]
    return np.concatenate(points)


def outline(draw, pixels, label, color, *, dashed=False):
    if not len(pixels):
        return
    lo = np.maximum(pixels.min(axis=0) - 4, [2, 2])
    hi = np.minimum(pixels.max(axis=0) + 4, [WIDTH - 3, HEIGHT - 3])
    if np.any(hi <= lo):
        return
    x0, y0 = lo; x1, y1 = hi
    if dashed:
        for x in np.arange(x0, x1, 12):
            draw.line((x, y0, min(x + 6, x1), y0), fill=color, width=2)
            draw.line((x, y1, min(x + 6, x1), y1), fill=color, width=2)
        for y in np.arange(y0, y1, 12):
            draw.line((x0, y, x0, min(y + 6, y1)), fill=color, width=2)
            draw.line((x1, y, x1, min(y + 6, y1)), fill=color, width=2)
    else:
        draw.rectangle((*lo, *hi), outline=color, width=3)
    xy = (x0, max(0, y0 - 23))
    bounds = draw.textbbox(xy, label, font=font(16))
    draw.rectangle(bounds, fill='#102030')
    draw.text(xy, label, fill=color, font=font(16))


def observer(sim, rig, index, target, additions):
    """Hide only architecture/robot in the render scene, never in model or physics."""
    with closing(mujoco.Renderer(sim.model, height=HEIGHT, width=WIDTH)) as renderer:
        renderer.update_scene(sim.data, camera=rig.camera(index))
        for geom in renderer.scene.geoms[:renderer.scene.ngeom]:
            if geom.objtype != mujoco.mjtObj.mjOBJ_GEOM:
                continue
            gid = geom.objid
            names = (sim.model.geom(gid).name or '',
                     sim.model.body(int(sim.model.geom_bodyid[gid])).name or '')
            if any(n.lower().startswith(('wall', 'ceiling', 'roof'))
                   or n.startswith(sim.robot.config.namespace) for n in names):
                geom.rgba[3] = 0
        calibration = rendered_camera(renderer, WIDTH, HEIGHT)
        image = Image.fromarray(renderer.render().copy())
        renderer.enable_segmentation_rendering()
        try:
            segmentation = renderer.render().copy()
        except IndexError:
            # 与 head_frame 相同：segid 映射表越界时退回目标投影框。
            segmentation = None
        renderer.disable_segmentation_rendering()
    draw = ImageDraw.Draw(image)
    for i, name in enumerate(additions):
        outline(draw, project(object_points(sim, name), calibration), f'+{i + 1}', ADDED)
    bodies = descendants(sim.model, sim.model.body(target).id)
    geoms = np.flatnonzero(np.isin(sim.model.geom_bodyid, list(bodies)))
    if segmentation is None:
        mask = np.zeros((HEIGHT, WIDTH), bool)
    else:
        mask = (segmentation[:, :, 1] == int(mujoco.mjtObj.mjOBJ_GEOM)) & np.isin(segmentation[:, :, 0], geoms)
    ys, xs = np.nonzero(mask)
    pixels = np.column_stack((xs, ys)) if len(xs) else project(object_points(sim, target), calibration)
    outline(draw, pixels, '目标' if len(xs) else '目标（遮挡，投影框）', TARGET, dashed=not len(xs))
    calibration['target_visible_pixels'] = int(mask.sum())
    return image, calibration


def station_panel(top, calibration, stations):
    image = top.copy()
    draw = ImageDraw.Draw(image)
    labels = []
    for station in stations:
        x, y = station['xy']
        yaw = station.get('base', [0, 0, 0])[2]
        points = project([[x, y, .04], [x + .12 * np.cos(yaw), y + .12 * np.sin(yaw), .04]], calibration)
        if len(points) != 2:
            continue
        start, end = points
        if not (0 <= start[0] < WIDTH and 0 <= start[1] < HEIGHT):
            continue
        draw.line((*start, *end), fill=STATION, width=2)
        direction = end - start
        normal = np.array([-direction[1], direction[0]])
        arrow = [tuple(end), tuple(end - .35 * direction + .25 * normal),
                 tuple(end - .35 * direction - .25 * normal)]
        draw.polygon(arrow, fill=STATION)
        px, py = start
        draw.ellipse((px - 3, py - 3, px + 3, py + 3), fill=STATION, outline='#102030')
        labels.append((py, px, station['station_id']))
    # Keep IDs readable instead of stacking text over a dense station cluster.
    labels.sort()
    if len(labels) > 20:
        labels = [labels[i] for i in np.linspace(0, len(labels) - 1, 20, dtype=int)]
    if labels:
        start_y = float(np.clip(np.mean([p[0] for p in labels]) - 9 * len(labels), 10,
                                HEIGHT - 18 * len(labels) - 10))
        for i, (py, px, label) in enumerate(labels):
            y = start_y + 18 * i
            draw.line((78, y + 8, px, py), fill=STATION, width=1)
            draw.rectangle((5, y, 76, y + 17), fill='#102030')
            draw.text((8, y), label, fill=STATION, font=font(12))
    return image


def render_scene_preview(root, record, destination):
    """One PNG per accepted scene; legacy missing baselines are explicitly blank."""
    root = Path(root)
    task = record['task']
    edited = bool(record.get('placements'))
    with ExitStack() as stack:
        def load(location):
            path = root / location
            from ...config import robot_config
            robot = robot_config(read_json(path / 'config.json')['robot'])
            return stack.enter_context(closing(Simulation.from_snapshot(path, robot, target=task['target_body'])))

        after = load(record['scene'])
        before = load(record['before_scene']) if edited and record.get('before_scene') else None
        additions = [f'procedural_obstacle_{i}' for i in range(len(record.get('placements', [])))]
        names = [task['target_body']] + additions
        context = [object_points(after, name) for name in names]
        for support in task.get('support_obstacles', []):
            context.append(object_points(after, support['body']))
        if before is not None:
            context.append(object_points(before, task['target_body']))
        # Paired top cameras cover the union of geometry AND both station sets.
        station_points = [[*s['xy'], .04] for s in record['stations'] + record.get('before_stations', [])]
        if station_points:
            context.append(np.asarray(station_points))
        top = CameraRig.fit(np.concatenate(context), ViewConfig(fovy_rad=np.deg2rad(float(after.model.vis.global_.fovy))))
        target = np.asarray(task['anchor_world'])
        # Local view includes support height, while avoiding a house-wide framing.
        local = np.concatenate([object_points(after, name) for name in names] +
                               [np.array([target + [-.5, -.5, -.6], target + [.5, .5, .3]])])
        side = CameraRig.fit(local, ViewConfig(fovy_rad=np.deg2rad(float(after.model.vis.global_.fovy))))
        # Pick one fixed paired angle by target visibility, without moving any objects.
        best = None
        for azimuth in (0., 90., 180., 270.):
            candidate = CameraRig(side.lookat, side.distance_m, side.fovy_rad, WIDTH, HEIGHT,
                                  ((np.deg2rad(azimuth), np.deg2rad(-18.)),))
            _, calibration = observer(after, candidate, 0, task['target_body'], [])
            score = calibration['target_visible_pixels']
            if best is None or score > best[0]:
                best = (score, candidate)
        side = best[1]
        count = 2 if edited else 1
        header, label_height, footer = 114, 42, 70
        canvas = Image.new('RGB', (WIDTH * 2, header + count * (HEIGHT + label_height) + footer), '#102030')
        draw = ImageDraw.Draw(canvas)
        draw.text((18, 10), record['scene_id'], fill='white', font=font(25))
        draw.text((18, 49), task.get('instruction', task['operation']), fill='#e2e8f0', font=font(20))
        draw.text((18, 82), f"{task.get('asset_id', task['category'])} | {record.get('case', '')} | 新增物体 {len(additions)}", fill=ADDED, font=font(17))
        stages = [(before, record.get('before_stations'), '编辑前（同步静置对照）'),
                  (after, record['stations'], '编辑后')] if edited else [(after, record['stations'], '未编辑')]
        for row, (sim, stations, stage) in enumerate(stages):
            y = header + row * (HEIGHT + label_height)
            for col, title in enumerate((f'俯视站位分布（{len(stations)}）' if stations is not None else '俯视站位分布（记录缺失）', '目标局部侧视图')):
                draw.text((col * WIDTH + 14, y + 7), f'{stage} · {title}', fill='white', font=font(19))
            if sim is None:
                draw.text((24, y + 150), '原始编辑前快照未保存；不重建、不推测。', fill=ADDED, font=font(24))
                continue
            added = additions if sim is after else []
            top_image, calibration = observer(sim, top, 0, task['target_body'], added)
            side_image, _ = observer(sim, side, 0, task['target_body'], added)
            panels = [station_panel(top_image, calibration, stations or []), side_image]
            for col, panel in enumerate(panels):
                canvas.paste(panel, (col * WIDTH, y + label_height))
        y = canvas.height - footer
        draw.text((18, y + 6), '红框：可见目标；红虚框：遮挡目标投影；橙框：新增物体投影；蓝点/箭头：站位/朝向。', fill='#e2e8f0', font=font(17))
        draw.text((18, y + 35), '仅展示：墙/顶/机器人隐藏，物理场景不变；站位编号最多标20个；站位有效不代表操作成功。', fill='#e2e8f0', font=font(17))
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        canvas.save(destination)


def export_scene_previews(dataset):
    """Append human-readable assets only; never rewrite scene records or snapshots."""
    root = Path(dataset)
    cards = []
    records = rows(root / 'scenes.jsonl')
    progress = Progress(logger)
    rendered = failed = 0
    logger.info('开始导出场景预览 记录数=%s 输出=%s', len(records), root / 'index.html', extra={'progress': True})
    for index, record in enumerate(records):
        progress.update('场景预览 已处理记录=%s/%s 已生成=%s 失败=%s 当前=%s',
                        index, len(records), rendered, failed, record['scene_id'])
        title = escape(record['scene_id'])
        if record['status'] != 'accepted':
            cards.append(f'<article><h2>{title} · rejected</h2><p>{escape(record.get("reason", "unknown"))}</p></article>')
            continue
        relative = Path('previews') / (record['scene_id'] + '.png')
        try:
            render_scene_preview(root, record, root / relative)
            rendered += 1
            cards.append(f'<article><h2>{title} · accepted</h2><a href="{relative.as_posix()}"><img src="{relative.as_posix()}" alt="{title}"></a></article>')
        except Exception as exc:
            failed += 1
            logger.exception('场景预览失败：%s（构建结论不变）', record['scene_id'])
            cards.append(f'<article><h2>{title} · accepted</h2><p>预览失败：{escape(str(exc))}</p></article>')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>场景库预览</title>'
    page += '<style>body{font-family:sans-serif;background:#102030;color:#e2e8f0;margin:24px}article{margin:28px 0}img{width:100%;max-width:1280px}a{color:#38bdf8}</style>'
    page += '<h1>场景库预览</h1><p>点击组合图查看原图。仅供场景与采样检查，不代表机器人任务成功。</p>'
    page += ''.join(cards) + '</html>'
    (root / 'index.html').write_text(page, encoding='utf-8')
    progress.update('场景预览收尾 已处理记录=%s/%s 已生成=%s 失败=%s 输出=%s',
                    len(records), len(records), rendered, failed, root / 'index.html', force=True)
    return root / 'index.html'
