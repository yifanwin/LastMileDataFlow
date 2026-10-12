"""Real RGB browser, paired contact sheet and reproducible diagnostic map."""
from pathlib import Path
import html
import numpy as np

from ..io import write_json


def save_grid(path, grid):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, origin=grid.origin, resolution_m=grid.resolution, free=grid.free)


def region_plot(path, task, stations, regions, after=None, placements=()):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    grid = after or regions['navigation_grid']
    h, w = grid.free.shape
    extent = [grid.origin[0] - grid.resolution / 2, grid.origin[0] + (w - .5) * grid.resolution,
              grid.origin[1] - grid.resolution / 2, grid.origin[1] + (h - .5) * grid.resolution]
    fig, ax = plt.subplots(figsize=(8, 7), layout='constrained')
    ax.imshow(grid.free, origin='lower', extent=extent, cmap='Greys_r', vmin=0, vmax=1)
    if after is None:
        mask = np.ma.masked_where(~regions['corridor'], regions['corridor'])
        ax.imshow(mask, origin='lower', extent=extent, cmap='Blues', alpha=.25, vmin=0, vmax=1)
    for obstacle in grid.support_obstacles:
        lo, hi = np.asarray(obstacle['min']), np.asarray(obstacle['max'])
        added = obstacle['kind'] == 'added_ground_obstacle'
        ax.add_patch(Rectangle(lo[:2], *(hi - lo)[:2], facecolor='#d55e0044' if added else '#e7cf9c88',
                               edgecolor='#d55e00' if added else '#856b43', lw=1.5))
    for kind, color, marker in (('station', '#0072b2', 'o'), ('arithmetic_midpoint', '#009e73', '+')):
        points = [p['xy'] for p in regions['points'] if any(s['kind'] == kind for s in p['sources'])]
        if points:
            points = np.asarray(points)
            ax.scatter(points[:, 0], points[:, 1], s=28, c=color, marker=marker, label=kind)
    for p in placements:
        ax.scatter(*p['point_xy'], s=120, marker='X', c='#d55e00', label=p['asset_id'])
    ax.scatter(*task['anchor_world'][:2], s=130, marker='*', c='#cc79a7', label='Cup_30')
    ax.set(xlabel='World X (m)', ylabel='World Y (m)', title='After: settled obstacle / planning grid' if after else 'Before: stations, midpoints and corridor band')
    ax.set_aspect('equal')
    ax.legend(fontsize=8)
    for suffix in ('png', 'svg'):
        fig.savefig(Path(path).with_suffix('.' + suffix), dpi=140)
    plt.close(fig)


def export_browser(path, summary):
    from PIL import Image, ImageDraw
    path = Path(path)
    success = [v for v in summary['variants'] if v['status'] == 'accepted']
    sheet = Image.new('RGB', (1280, max(1, len(success)) * 510), 'white')
    draw = ImageDraw.Draw(sheet)
    cards = []
    for i, variant in enumerate(summary['variants']):
        ident = variant['variant_id']
        name = html.escape(ident)
        if variant['status'] != 'accepted':
            cards.append(f'<section><h2>{name}: {html.escape(variant["status"])}</h2><p>{html.escape(variant.get("reason", ""))}</p></section>')
            continue
        folder = Path('variants') / ident
        pairs = ''.join(f'<h3>{view}</h3><div class="pair"><img src="{folder}/rgb/before_{view}.png"><img src="{folder}/rgb/after_{view}.png"></div>'
                        for view in ('top', 'oblique_a', 'oblique_b'))
        label = ', '.join(p['asset_id'] for p in variant['placements'])
        cards.append(f'<section><h2>{name} / seed {variant["seed"]} / {html.escape(label)}</h2><p>Left: before; Right: after real MuJoCo settle. Physical checks passed; navigation/task execution not tested.</p>{pairs}<div class="pair"><img src="baseline/regions.png"><img src="{folder}/navigation/regions.png"></div><a href="{folder}/checks.json">Checks</a> · <a href="{folder}/navigation/comparison.json">Navigation changes</a></section>')
    for row, variant in enumerate(success):
        folder = path / 'variants' / variant['variant_id'] / 'rgb'
        for col, stage in enumerate(('before', 'after')):
            with Image.open(folder / f'{stage}_oblique_a.png') as im:
                sheet.paste(im.convert('RGB').resize((640, 480)), (640 * col, 510 * row + 30))
        draw.text((8, 510 * row + 8), f'{variant["variant_id"]} / seed {variant["seed"]} / BEFORE', fill='black')
        draw.text((648, 510 * row + 8), 'AFTER / ' + ', '.join(p['asset_id'] for p in variant['placements']), fill='black')
    sheet.save(path / 'before_after_overview.jpg', quality=94)
    content = f'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>Procedural obstacles / Cup_30</title><style>body{{font:16px sans-serif;max-width:1400px;margin:24px auto;padding:0 16px}}section{{border-top:1px solid #ccc;margin-top:24px}}.pair{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}img{{width:100%;height:auto}}a{{color:#0072b2}}</style><h1>Cup_30: procedural obstacle augmentation</h1><p>Accepted {len(success)} / {len(summary["variants"])}. Pure programmatic construction, real assets, real MuJoCo RGB. No agent calls. Left before / right after.</p><p><a href="summary.json">Summary</a> · <a href="before_after_overview.jpg">Five-row overview</a></p>{''.join(cards)}</html>'''
    (path / 'index.html').write_text(content, encoding='utf-8')
    write_json(path / 'media_manifest.json', {'raw_rgb_count': len(success) * 6,
               'browser': 'index.html', 'overview': 'before_after_overview.jpg',
               'source': 'real_MuJoCo_renderer_not_generated_images'})
