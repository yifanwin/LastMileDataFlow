"""Reference-style fourth video: real observer + synchronized wrist + measured facts."""
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from ..visualization.fonts import font

PHASE_LABELS = {'navigation': 'A* 路线 · 底盘闭环', 'arrival': '到站稳定 · 无重置',
                'dry_pregrasp': '候选抓取规划', 'torso_adjust': '躯干调整', 'pregrasp': '预抓取',
                'approach': '接近目标', 'close': '闭合夹爪', 'lift': '真实抬升',
                'lift_attached': '持物抬升', 'hold': '稳定保持', 'open': '沿真实关节开启',
                'retry_release': '操作重试 · 松爪', 'initialize': '初始化'}


@dataclass(frozen=True)
class MapPanel:
    """Square top-down inset of a disc of `radius` around `center`."""
    draw: ImageDraw.ImageDraw
    left: int
    top: int
    size: int
    center: np.ndarray
    radius: float

    def pixel(self, xy):
        v = (np.asarray(xy) - (self.center - self.radius)) / (2 * self.radius)
        return (self.left + int(v[0] * self.size), self.top + self.size - int(v[1] * self.size))

    def dot(self, xy, color, label):
        x, y = self.pixel(xy)
        self.draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=color)
        self.draw.text((x + 6, y - 10), label, font=font(20), fill=color)

    def footprint(self, xy, color, label, radius_m):
        x, y = self.pixel(xy)
        r = radius_m * self.size / (2 * self.radius)
        self.draw.ellipse((x - r, y - r, x + r, y + r), outline=color, width=2)
        self.draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)
        self.draw.text((x + r + 6, y - 10), label, font=font(20), fill=color)


class AnalysisVideo:
    width, height = 1280, 720

    def __init__(self, task, station, *, path=None, goal=None, radius_m=2., footprint_radius_m=None):
        self.task, self.station, self.path, self.goal = task, station, path, goal
        self.radius = radius_m
        self.history = []
        if footprint_radius_m is not None and (not np.isfinite(footprint_radius_m) or footprint_radius_m <= 0):
            raise ValueError('footprint_radius_m must be finite and positive')
        self.footprint_radius_m = footprint_radius_m

    def frame(self, frames, *, phase, time_s, base_xy, target_height, initial_height, facts=None, side=None,
              collisions=0, wrist_camera=None):
        self.history.append(list(base_xy))
        canvas = Image.new('RGB', (self.width, self.height), '#101820')
        draw = ImageDraw.Draw(canvas)
        self._draw_views(canvas, draw, frames, side, wrist_camera)
        self._draw_facts(draw, phase, target_height - initial_height, facts, collisions)
        self._draw_map(draw, base_xy)
        draw.text((18, 678), f'仿真时间 {time_s:.3f} s | 1× | 连续物理执行，无重置 | 原场景遮挡保留',
                  font=font(20), fill='white')
        draw.text((973, 678), '蓝:实走 灰:规划', font=font(20), fill='#72d8ea')
        return np.asarray(canvas)

    def _draw_views(self, canvas, draw, frames, side, wrist_camera):
        scene = ImageOps.contain(Image.fromarray(frames['third_person_camera']), (960, 594))
        canvas.paste(scene, ((960 - scene.width) // 2, 74 + (594 - scene.height) // 2))
        heading = 'S0 → A* → S1 → cuRobo 操作' if self.path else 'S0 独立操作评测'
        draw.text((18, 8), heading, font=font(26), fill='white')
        draw.text((18, 43), self.task.get('instruction', '')[:60], font=font(20), fill='#72d8ea')
        draw.text((973, 10), '夹爪近景 · 同一物理时刻', font=font(20), fill='white')
        wrist = wrist_camera or ('wrist_camera_r' if side == 'right' else 'wrist_camera_l')
        if wrist in frames:
            inset = ImageOps.contain(Image.fromarray(frames[wrist]), (308, 230))
            canvas.paste(inset, (968 + (308 - inset.width) // 2, 76))

    def _draw_facts(self, draw, phase, lift_m, facts, collisions):
        contacts = '--' if facts is None else str(len(facts.get('finger_forces_n', {}))) + ' / 2'
        support = '--' if facts is None else '有' if facts.get('support_contact') else '无'
        draw.text((973, 315), '阶段  ' + PHASE_LABELS.get(phase, phase), font=font(20), fill='#72d8ea')
        draw.text((973, 347), f'目标抬升 {lift_m * 100:.1f} cm', font=font(20), fill='white')
        draw.text((973, 379), f'承力手指 {contacts}    支撑 {support}', font=font(20), fill='white')
        draw.text((973, 411), f'碰撞记录 {collisions}', font=font(20), fill='white')

    def _station(self, panel, xy, color, label):
        if self.footprint_radius_m is None:
            panel.dot(xy, color, label)
        else:
            panel.footprint(xy, color, label, self.footprint_radius_m)

    def _draw_map(self, draw, base_xy):
        left, top, size = 980, 452, 206
        center = np.asarray(self.task.get('anchor_world', base_xy)[:2], float)
        panel = MapPanel(draw, left, top, size, center, self.radius)
        draw.rectangle((left, top, left + size, top + size), outline='#506575')
        draw.ellipse((left, top, left + size, top + size), outline='#506575')
        for obstacle in self.task.get('support_obstacles', []):
            x0, y1 = panel.pixel(obstacle['min'][:2])
            x1, y0 = panel.pixel(obstacle['max'][:2])
            x0, x1 = max(left, x0), min(left + size, x1)
            y0, y1 = max(top, y0), min(top + size, y1)
            if x0 <= x1 and y0 <= y1:
                draw.rectangle((x0, y0, x1, y1), fill='#493d2d', outline='#b99a62')
        if self.path:
            planned = [panel.pixel(p) for p in self.path['xy']]
            if len(planned) > 1:
                draw.line(planned, fill='#687785', width=2)
        actual = [panel.pixel(p) for p in self.history]
        if len(actual) > 1:
            draw.line(actual, fill='#72d8ea', width=3)
        self._station(panel, self.station['xy'], '#ed6b64', 'S0')
        if self.goal:
            self._station(panel, self.goal['xy'], '#66d68c', 'S1')
        panel.dot(center, '#ffc55b', 'T')
        panel.dot(base_xy, 'white', '')
