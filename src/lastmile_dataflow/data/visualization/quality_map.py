"""Base-quality map figures: discrete cells, heading arrows, per-grasp panels and an offline viewer.

Plotting only reads saved map arrays; it never computes IK or scores. Red is high and
blue low (jet) on one shared [0, max valid quality] scale. A position's cell is the
mean over its valid headings; every heading is also drawn as an arrow. Cells are
never smoothed: evaluated-without-solution, geometrically filtered and
unevaluated cells are drawn differently, and IK validity rate has its own figure.
"""
import base64
import io
import json
import math
from pathlib import Path

import numpy as np

from ...utils.io import write_json

CMAP = 'jet'
NO_SOLUTION, FILTERED, OBSTACLE = '#8f8f8f', '#e4e4e4', '#c9c9c9'
FILTER_CODES = (1, 2, 3, 4)  # no_heading, no_floor, base_collision, distance_filtered
NO_SOLUTION_CODE, VALID_CODE = 5, 6


def cell_categories(status_k):
    """(Ny, Nx) per grasp: 0 unevaluated, 1 filtered, 2 evaluated without solution, 3 valid."""
    valid = (status_k == VALID_CODE).any(axis=-1)
    tried = (status_k == NO_SOLUTION_CODE).any(axis=-1)
    filtered = np.isin(status_k, FILTER_CODES).any(axis=-1)
    return np.select([valid, tried, filtered], [3, 2, 1], 0)


def _extent(x, y, spacing):
    return [x[0] - spacing / 2, x[-1] + spacing / 2, y[0] - spacing / 2, y[-1] + spacing / 2]


def _background(ax, data):
    free = data['background_free']
    origin, resolution = data['background_origin'], float(data['background_resolution_m'])
    ny, nx = free.shape
    extent = [origin[0] - resolution / 2, origin[0] + (nx - .5) * resolution,
              origin[1] - resolution / 2, origin[1] + (ny - .5) * resolution]
    from matplotlib.colors import ListedColormap
    ax.imshow(np.where(free, np.nan, 1.), origin='lower', extent=extent, cmap=ListedColormap([OBSTACLE]),
              vmin=0, vmax=1, interpolation='nearest', zorder=0)


def _status_layer(ax, categories, extent):
    from matplotlib.colors import ListedColormap
    image = np.where(categories == 1, 0., np.where(categories == 2, 1., np.nan))
    ax.imshow(image, origin='lower', extent=extent, cmap=ListedColormap([FILTERED, NO_SOLUTION]),
              vmin=0, vmax=1, interpolation='nearest', zorder=1, alpha=.9)


def _decorate(ax, scene, extent, grasp=None):
    center = scene['object_center_world']
    ax.scatter(*center[:2], marker='*', s=160, c='black', zorder=6, label='Cup centre')
    if grasp is not None:
        tcp = np.asarray(scene['grasps'][grasp]['tcp_world'])
        ax.scatter(*tcp[:2, 3], marker='o', s=36, facecolors='none', edgecolors='black', zorder=6, label='grasp TCP')
    ax.set_aspect('equal')
    ax.set_xlim(extent[0], extent[1])  # the occupancy raster is a larger disk; show only evaluated ground
    ax.set_ylim(extent[2], extent[3])
    ax.set_xlabel('world X (m)')
    ax.set_ylabel('world Y (m)')


def export_quality_maps(data, scene, summary, figures):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    figures = Path(figures)
    figures.mkdir(parents=True, exist_ok=True)
    x, y = data['x'], data['y']
    spacing = scene['grid']['spacing_m']
    extent = _extent(x, y, spacing)
    quality, status, yaw = data['quality'], data['status'], data['yaw']
    K = quality.shape[-1]
    offsets = np.rad2deg(data['heading_offsets_rad'])
    vmax = summary['quality_range'][1] if summary.get('quality_range') else 1.
    norm = matplotlib.colors.Normalize(0., vmax)
    legend = [Patch(color=FILTERED, label='filtered (floor / base / reach)'),
              Patch(color=NO_SOLUTION, label='evaluated, no valid IK'),
              Patch(color=OBSTACLE, label='obstacle footprint (< 1.5 m)')]
    written = []

    position = data['position_quality']
    for k in range(K):
        row = scene['grasps'][k]['source_row']
        fig, ax = plt.subplots(figsize=(10, 9), layout='constrained')
        _background(ax, data)
        _status_layer(ax, cell_categories(status[..., k]), extent)
        im = ax.imshow(position[..., k], origin='lower', extent=extent, cmap=CMAP, norm=norm,
                       interpolation='nearest', zorder=2)
        _decorate(ax, scene, extent, k)
        ax.set_title(f"{scene['scene_id']} / {scene['asset_id']} grasp g{k} (row {row}): "
                     'Q(x, y) = mean over valid headings')
        fig.colorbar(im, ax=ax, label='extended manipulability (higher = better)', shrink=.8)
        ax.legend(handles=[*legend, *ax.get_legend_handles_labels()[0]], loc='upper right', fontsize=8)
        path = figures / f'base-quality-g{k}.png'
        fig.savefig(path, dpi=160)
        plt.close(fig)
        written.append(path)

        # Every valid heading as one arrow from its position; the arrows of a position form a fan.
        fig, ax = plt.subplots(figsize=(10, 9), layout='constrained')
        iy, ix, mi = np.nonzero(status[..., k] == VALID_CODE)
        arrows = ax.quiver(x[ix], y[iy], np.cos(yaw[iy, ix, mi]), np.sin(yaw[iy, ix, mi]), quality[iy, ix, mi, k],
                           cmap=CMAP, norm=norm, angles='xy', scale_units='xy', scale=1 / (.48 * spacing),
                           width=.0022, headwidth=2.6, headlength=3, headaxislength=2.6, zorder=3)
        _decorate(ax, scene, extent, k)
        ax.set_title(f'grasp g{k} (row {row}): every valid heading, coloured by its quality')
        fig.colorbar(arrows, ax=ax, label='extended manipulability (higher = better)', shrink=.8)
        path = figures / f'heading-quality-g{k}.png'
        fig.savefig(path, dpi=160)
        plt.close(fig)
        written.append(path)

    written.append(_ik_rate_figure(plt, data, scene, extent, figures))
    written.append(_grasp_figure(plt, scene, figures))
    written.append(_viewer(figures / 'base-quality.html', data, scene, vmax))
    write_json(figures / 'figures.json', {'color_scale': [0., vmax], 'colormap': CMAP,
               'scale_scope': 'all valid configurations of this map', 'files': [p.name for p in written],
               'ik_valid_rate': 'separate figure; never colours quality'})
    return figures


def _ik_rate_figure(plt, data, scene, extent, figures):
    executed = data['executed_seeds'].sum(axis=2).astype(float)
    rate = np.where(executed > 0, data['valid_seeds'].sum(axis=2) / np.maximum(executed, 1), np.nan)
    K = rate.shape[-1]
    fig, axes = plt.subplots(1, K, figsize=(4.4 * K, 4.4), layout='constrained', squeeze=False)
    for k, ax in enumerate(axes.flat):
        _background(ax, data)
        im = ax.imshow(rate[..., k], origin='lower', extent=extent, cmap='viridis', vmin=0, vmax=1,
                       interpolation='nearest', zorder=2)
        _decorate(ax, scene, extent, k)
        ax.set_title(f'g{k}', fontsize=10)
    fig.colorbar(im, ax=axes, label='IK valid seeds / executed seeds (all headings)', shrink=.8)
    fig.suptitle('IK validity rate: solver statistic only, not part of quality')
    out = figures / 'ik-valid-rate.png'
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def _grasp_figure(plt, scene, figures):
    center = np.asarray(scene['object_center_world'])
    fig, axes = plt.subplots(1, 2, figsize=(11, 5), layout='constrained')
    colors = plt.get_cmap('tab10')
    for axis_pair, ax, title in (((0, 1), axes[0], 'top view (X-Y)'), ((0, 2), axes[1], 'side view (X-Z)')):
        for k, grasp in enumerate(scene['grasps']):
            pose = np.asarray(grasp['tcp_world'])
            p = pose[:3, 3] - center
            approach = pose[:3, 2] * .06
            ax.quiver(p[axis_pair[0]] - approach[axis_pair[0]], p[axis_pair[1]] - approach[axis_pair[1]],
                      approach[axis_pair[0]], approach[axis_pair[1]], angles='xy', scale_units='xy', scale=1,
                      color=colors(k), width=.006)
            ax.scatter(p[axis_pair[0]], p[axis_pair[1]], color=colors(k), s=30, zorder=3)
            ax.annotate(f"g{k} (row {grasp['source_row']})", (p[axis_pair[0]], p[axis_pair[1]]), xytext=(4, 4),
                        textcoords='offset points', fontsize=8, color=colors(k))
        ax.add_patch(plt.Circle((0, 0), .05, fill=False, ls='--', color='#888888'))
        ax.set_aspect('equal')
        ax.set_xlim(-.2, .2)
        ax.set_ylim(-.2, .2)
        ax.set_title(title)
        ax.set_xlabel(f"{'XYZ'[axis_pair[0]]} relative to cup centre (m)")
        ax.set_ylabel(f"{'XYZ'[axis_pair[1]]} (m)")
    fig.suptitle(f"{len(scene['grasps'])} farthest-point grasps of {scene['grasp_candidates']} "
                 'candidates; arrow = approach direction into the TCP')
    out = figures / 'grasp-poses.png'
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def _png_data_url(free):
    import matplotlib.pyplot as plt
    buffer = io.BytesIO()
    plt.imsave(buffer, np.where(free[::-1], 1., 0.), cmap='gray', vmin=0, vmax=1, format='png')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()


def _viewer(path, data, scene, vmax):
    """Single offline page: grasp/heading switch, every arrow, click for numbers and the winning q."""
    status, quality = data['status'], data['quality']
    valid = status == VALID_CODE
    free = data['background_free']
    origin, resolution = data['background_origin'], float(data['background_resolution_m'])
    payload = {
        'scene': scene['scene_id'], 'asset': scene['asset_id'], 'vmax': vmax,
        'x': np.round(data['x'], 4).tolist(), 'y': np.round(data['y'], 4).tolist(),
        'spacing': scene['grid']['spacing_m'], 'offsets': np.round(np.rad2deg(data['heading_offsets_rad']), 2).tolist(),
        'yaw': np.round(np.nan_to_num(data['yaw'], nan=999), 4).tolist(),
        'status': status.tolist(), 'statusNames': data['status_names'].tolist(),
        'quality': np.round(np.where(valid, quality, -1), 4).tolist(),
        'valid': data['valid_seeds'].tolist(), 'executed': data['executed_seeds'].tolist(),
        'distinct': data['distinct_valid'].tolist(), 'warm': data['warm_start_seeds'].tolist(),
        'bestQ': np.round(np.nan_to_num(data['best_q'], nan=0.), 3).tolist(),
        'grasps': [{'row': g['source_row'], 'xy': np.asarray(g['tcp_world'])[:2, 3].round(4).tolist()} for g in scene['grasps']],
        'center': np.round(scene['object_center_world'], 4).tolist(),
        'background': {'url': _png_data_url(free), 'x0': float(origin[0] - resolution / 2),
                       'y0': float(origin[1] - resolution / 2), 'w': free.shape[1] * resolution, 'h': free.shape[0] * resolution}}
    html = VIEWER.replace('__DATA__', json.dumps(payload, separators=(',', ':')))
    path.write_text(html, encoding='utf-8')
    return path


VIEWER = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>底盘可达质量图</title><style>
body{margin:0;font-family:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif;background:#faf9f9;color:#1f2328}
header{padding:10px 14px;display:flex;flex-wrap:wrap;gap:10px;align-items:center;border-bottom:1px solid #e3e5e8;background:#fff}
select,label{font-size:14px} .hint{color:#6b7280;font-size:12px} main{display:flex;flex-wrap:wrap;gap:12px;padding:12px}
#wrap{flex:1 1 560px;min-width:300px;background:#fff;border:1px solid #e3e5e8;border-radius:8px;padding:6px}
canvas{width:100%;display:block;touch-action:none} #info{flex:1 1 320px;min-width:280px;background:#fff;border:1px solid #e3e5e8;border-radius:8px;padding:10px;font-size:13px}
table{border-collapse:collapse;width:100%} td,th{border-bottom:1px solid #e3e5e8;padding:3px 4px;text-align:right} th{color:#6b7280;font-weight:normal}
.bar{height:10px;border-radius:3px;background:linear-gradient(90deg,#00008f,#0000ff,#00ffff,#ffff00,#ff0000,#800000)}
</style></head><body><header><b id="title"></b>
<label>grasp <select id="grasp"></select></label>
<label>显示 <select id="view"><option value="-1">每格有效朝向平均</option></select></label>
<label><input type="checkbox" id="arrows" checked> 朝向箭头</label>
<span class="hint">滚轮/双指缩放，拖动平移，点格子看数值</span>
<div style="width:160px"><div class="bar"></div><div class="hint" id="scale"></div></div></header>
<main><div id="wrap"><canvas id="c"></canvas></div><div id="info"><div class="hint">点击地图上的一个格子。灰色：评价过但无有效 IK；浅灰：几何过滤（无地面/底盘碰撞/臂展外）；空白：未评价。</div></div></main>
<script>
const D=__DATA__;const C=document.getElementById('c'),X=C.getContext('2d');const bg=new Image();bg.src=D.background.url;
const stops=[[0,0,143],[0,0,255],[0,255,255],[255,255,0],[255,0,0],[128,0,0]];
function col(v){const t=Math.max(0,Math.min(1,v/D.vmax))*(stops.length-1),i=Math.min(stops.length-2,Math.floor(t)),f=t-i,a=stops[i],b=stops[i+1];return `rgb(${a.map((x,j)=>Math.round(x+(b[j]-x)*f)).join(',')})`}
document.getElementById('title').textContent=`${D.scene} / ${D.asset}`;document.getElementById('scale').textContent=`0 — ${D.vmax.toFixed(3)}`;
const gs=document.getElementById('grasp'),vs=document.getElementById('view'),ar=document.getElementById('arrows');
D.grasps.forEach((g,k)=>gs.add(new Option(`g${k} (row ${g.row})`,k)));D.offsets.forEach((o,m)=>vs.add(new Option(`偏角 ${o>0?'+':''}${o}°`,m)));
let view={cx:D.center[0],cy:D.center[1],s:1},sel=null;const ny=D.y.length,nx=D.x.length,M=D.offsets.length;
function fit(){const w=C.clientWidth,h=Math.max(320,Math.min(w,window.innerHeight-120));const r=devicePixelRatio||1;C.width=w*r;C.height=h*r;C.style.height=h+'px';
const span=Math.max(D.x[nx-1]-D.x[0],D.y[ny-1]-D.y[0])+D.spacing;view.s=Math.min(w,h)/span*r;view.cx=(D.x[0]+D.x[nx-1])/2;view.cy=(D.y[0]+D.y[ny-1])/2;draw()}
const P=(x,y)=>[C.width/2+(x-view.cx)*view.s,C.height/2-(y-view.cy)*view.s];
function cellBest(k,iy,ix){let sum=0,n=0,tried=false,filt=false;for(let m=0;m<M;m++){const s=D.status[iy][ix][m][k];if(s==6){n++;sum+=D.quality[iy][ix][m][k]}else if(s==5)tried=true;else if(s>=1&&s<=4)filt=true}return{best:n?sum/n:-1,any:n>0,tried,filt}}
function draw(){const k=+gs.value,v=+vs.value;X.fillStyle='#fff';X.fillRect(0,0,C.width,C.height);
const b=D.background;const [bx,by]=P(b.x0,b.y0+b.h);X.globalAlpha=.25;X.imageSmoothingEnabled=false;X.drawImage(bg,bx,by,b.w*view.s,b.h*view.s);X.globalAlpha=ar.checked?.35:1;
const h=D.spacing*view.s;for(let iy=0;iy<ny;iy++)for(let ix=0;ix<nx;ix++){const [px,py]=P(D.x[ix],D.y[iy]);let fill=null;
if(v<0){const c=cellBest(k,iy,ix);fill=c.any?col(c.best):c.tried?'#8f8f8f':c.filt?'#e4e4e4':null}else{const s=D.status[iy][ix][v][k];fill=s==6?col(D.quality[iy][ix][v][k]):s==5?'#8f8f8f':(s>=1&&s<=4)?'#e4e4e4':null}
if(fill){X.fillStyle=fill;X.fillRect(px-h/2,py-h/2,h*0.96,h*0.96)}}
X.globalAlpha=1;if(ar.checked){X.lineWidth=Math.max(1,h/40);for(let iy=0;iy<ny;iy++)for(let ix=0;ix<nx;ix++)for(let m=0;m<M;m++){if(v>=0&&m!=v)continue;if(D.status[iy][ix][m][k]!=6)continue;
const [px,py]=P(D.x[ix],D.y[iy]),a=D.yaw[iy][ix][m],L=h*0.45;X.strokeStyle=col(D.quality[iy][ix][m][k]);X.beginPath();X.moveTo(px,py);X.lineTo(px+L*Math.cos(a),py-L*Math.sin(a));X.stroke()}}
const [ox,oy]=P(D.center[0],D.center[1]);X.fillStyle='#1f2328';X.beginPath();X.arc(ox,oy,Math.max(4,h/5),0,7);X.fill();
const g=D.grasps[k].xy,[gx,gy]=P(g[0],g[1]);X.strokeStyle='#3b915d';X.lineWidth=2;X.beginPath();X.arc(gx,gy,Math.max(5,h/4),0,7);X.stroke();
if(sel){const [sx,sy]=P(D.x[sel[1]],D.y[sel[0]]);X.strokeStyle='#1f2328';X.lineWidth=2;X.strokeRect(sx-h/2,sy-h/2,h,h)}}
function info(iy,ix){const k=+gs.value;const c=cellBest(k,iy,ix);let r=`<b>X ${D.x[ix].toFixed(2)} m, Y ${D.y[iy].toFixed(2)} m</b> · g${k} · 有效朝向平均 ${c.any?c.best.toFixed(4):'-'}<table><tr><th>偏角</th><th>世界yaw</th><th>状态</th><th>质量</th><th>有效/执行 seed</th><th>不同解</th><th>热启动</th></tr>`;
for(let m=0;m<M;m++){const s=D.status[iy][ix][m][k],q=D.quality[iy][ix][m][k],y=D.yaw[iy][ix][m];r+=`<tr><td>${D.offsets[m]}°</td><td>${y>99?'-':(y*180/Math.PI).toFixed(1)+'°'}</td><td>${D.statusNames[s]}</td><td>${s==6?q.toFixed(4):'-'}</td><td>${D.executed[iy][ix][m][k]?D.valid[iy][ix][m][k]+'/'+D.executed[iy][ix][m][k]:'-'}</td><td>${s==6?D.distinct[iy][ix][m][k]:'-'}</td><td>${D.executed[iy][ix][m][k]?D.warm[iy][ix][m][k]:'-'}</td></tr>`}
r+='</table><div class="hint" style="margin-top:6px">获胜构型 q（rad，Panda joint1–7）</div>';for(let m=0;m<M;m++)if(D.status[iy][ix][m][k]==6)r+=`<div>${D.offsets[m]}°: [${D.bestQ[iy][ix][m][k].join(', ')}]</div>`;
r+='<div class="hint" style="margin-top:6px">IK 有效率只是求解统计，不进入质量和颜色。</div>';document.getElementById('info').innerHTML=r}
let drag=null,moved=false;C.addEventListener('pointerdown',e=>{drag=[e.clientX,e.clientY];moved=false;C.setPointerCapture(e.pointerId)});
C.addEventListener('pointermove',e=>{if(!drag)return;const r=devicePixelRatio||1,dx=e.clientX-drag[0],dy=e.clientY-drag[1];if(Math.abs(dx)+Math.abs(dy)>3)moved=true;view.cx-=dx*r/view.s;view.cy+=dy*r/view.s;drag=[e.clientX,e.clientY];draw()});
C.addEventListener('pointerup',e=>{drag=null;if(moved)return;const r=devicePixelRatio||1,b=C.getBoundingClientRect(),px=(e.clientX-b.left)*r,py=(e.clientY-b.top)*r;
const x=view.cx+(px-C.width/2)/view.s,y=view.cy-(py-C.height/2)/view.s,ix=Math.round((x-D.x[0])/D.spacing),iy=Math.round((y-D.y[0])/D.spacing);if(ix>=0&&ix<nx&&iy>=0&&iy<ny){sel=[iy,ix];info(iy,ix);draw()}});
C.addEventListener('wheel',e=>{e.preventDefault();view.s*=e.deltaY<0?1.15:1/1.15;draw()},{passive:false});
[gs,vs,ar].forEach(el=>el.addEventListener('change',()=>{draw();if(sel)info(...sel)}));bg.onload=draw;window.addEventListener('resize',fit);fit();
</script></body></html>"""


def _sight(sim, lookat, azimuth, elevation, distance):
    """Free distance between a free camera and its look-at point, checked from both ends.

    The outward ray starts 0.5 m from the look-at point so the robot itself does
    not count; it catches cameras placed inside furniture, which a ray cast from
    inside a mesh cannot see.
    """
    import mujoco
    a, e = math.radians(azimuth), math.radians(elevation)
    # MuJoCo places a free camera at lookat - forward * distance.
    outward = -np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
    camera = np.asarray(lookat) + outward * distance
    geom = np.zeros(1, np.int32)
    inward = mujoco.mj_ray(sim.model, sim.data, camera, -outward, None, 1, -1, geom)
    back = mujoco.mj_ray(sim.model, sim.data, np.asarray(lookat) + outward * .5, outward, None, 1, -1, geom)
    clear = [distance if inward < 0 else min(inward, distance), distance if back < 0 else min(back + .5, distance)]
    return min(clear)


def export_examples(scene, data, output, size=(480, 400)):
    """Side renders of the winning configuration at the best, median and lowest valid query.

    `scene` is the MapScene the map was built from; MuJoCo only places the robot
    (no stepping). Needs a working offscreen GL (e.g. MUJOCO_GL=egl).
    """
    import mujoco
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from copy import deepcopy

    status, quality, best_q, yaw, x, y = (data[key] for key in ('status', 'quality', 'best_q', 'yaw', 'x', 'y'))
    cells = np.argwhere(status == VALID_CODE)
    values = quality[tuple(cells.T)]
    order = np.argsort(values)
    picks = {'highest': order[-1], 'median': order[len(order) // 2], 'lowest': order[0]}
    sim = scene.open()
    renderer = mujoco.Renderer(sim.model, size[1], size[0])
    initial = deepcopy(sim.robot.config.initial)
    target = sim.data.xpos[sim.target_id].copy()
    fig, axes = plt.subplots(1, len(picks), figsize=(4.2 * len(picks), 4.2), layout='constrained')
    rows = []
    for ax, (label, index) in zip(axes, picks.items()):
        iy, ix, mi, k = cells[index]
        base = [float(x[ix]), float(y[iy]), float(yaw[iy, ix, mi])]
        initial.update(base_xyyaw=base, arm_q={'right': best_q[iy, ix, mi, k].tolist()})
        sim.robot.initialize(initial)
        camera = mujoco.MjvCamera()
        camera.lookat[:] = (np.r_[base[:2], target[2]] + target) / 2
        heading = math.degrees(math.atan2(target[1] - base[1], target[0] - base[0]))
        camera.distance = 2.2
        # Side view from whichever direction has the clearest line of sight to the scene centre;
        # a camera inside furniture sees nothing, so a raised view is also offered.
        camera.azimuth, camera.elevation = max(
            ((heading + a, e) for e in (-20, -45) for a in (90, -90, 135, -135, 180)),
            key=lambda view: (round(_sight(sim, camera.lookat, *view, 2.2), 1), view[1]))
        renderer.update_scene(sim.data, camera)
        ax.imshow(renderer.render())
        ax.axis('off')
        ax.set_title(f'{label}: Q={values[index]:.3f}, g{k}, view {camera.elevation:.0f} deg\nbase ({base[0]:.2f}, {base[1]:.2f}) m, '
                     f'yaw {math.degrees(base[2]):.0f} deg', fontsize=9)
        rows.append({'label': label, 'quality': float(values[index]), 'grasp': int(k), 'base_xyyaw': base,
                     'q': best_q[iy, ix, mi, k].tolist()})
    renderer.close()
    sim.close()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    fig.savefig(output / 'examples.png', dpi=140)
    plt.close(fig)
    write_json(output / 'examples.json', {'view': 'free camera 2.2 m, side or raised, clearest line of sight', 'picks': rows,
               'note': 'static placements of saved winning configurations; no physics stepping'})
    return output / 'examples.png'
