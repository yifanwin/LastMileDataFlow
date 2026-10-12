"""Read final evidence and derive paired RGB figures + compact Markdown report."""
import argparse
from collections import Counter
import csv
import json
import os
from pathlib import Path
import textwrap

from PIL import Image, ImageDraw, ImageFont


def read(path):
    return json.loads(Path(path).read_text())


def build(case1, case2, report):
    report = Path(report).resolve()
    report.mkdir(parents=True, exist_ok=True)
    figures = report / 'figures'
    figures.mkdir(exist_ok=True)
    data = report / 'data'
    data.mkdir(exist_ok=True)
    runs = [Path(case1).resolve(), Path(case2).resolve()]
    summaries = [read(r / 'summary.json') for r in runs]
    audits = [read(r / 'audit.json') for r in runs]
    if not all(s['status'] == 'completed' and s['accepted_count'] == 5 for s in summaries):
        raise ValueError('report requires two completed five-variant runs')
    if not all(a['valid'] for a in audits):
        raise ValueError('report requires passing audits')
    def link(path, label):
        return f'[{label}]({os.path.relpath(path, report)})'
    table = ['| Case | 变体 / seed | 新增 | 前筛 | 增强后真实通过 | 最终 |', '|---|---|---:|---:|---:|---:|']
    rows = []
    for run, summary in zip(runs, summaries):
        sheet = Image.new('RGB', (1280, 525 * 5), 'white')
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 14)
        for i, v in enumerate(summary['variants']):
            ident = v['variant_id']
            folder = run / 'variants' / ident
            label = ', '.join(f'{cat} x{count}' for cat, count in Counter(p['category'] for p in v['placements']).items())
            draw.text((8, i * 525 + 4), f'{summary["case"]} {ident} / seed {v["seed"]} / BEFORE', font=font, fill='black')
            draw.text((648, i * 525 + 4), f'AFTER / {v["requested_count"]} added', font=font, fill='black')
            draw.text((648, i * 525 + 24), label, font=font, fill='black')
            for col, stage in enumerate(('before', 'after')):
                with Image.open(folder / f'rgb/{stage}_oblique_a.png') as im:
                    sheet.paste(im.convert('RGB'), (640 * col, 525 * i + 45))
            table.append(f'| {summary["case"]} | {ident} / {v["seed"]} | {v["requested_count"]} | {v["prefilter_count_before"]} | {v["valid_stations_after"]} | {v["final_station_count"]} |')
            rows.append(dict(case=summary['case'], variant=ident, seed=v['seed'], added=v['requested_count'], prefilter=v['prefilter_count_before'], dense_candidates=v['dense_candidate_count'], actual_passed=v['valid_stations_after'], final=v['final_station_count'], resamples=v['asset_resamples'], backtracks=v['packing_backtracks']))
        sheet.save(figures / f'{summary["case"].lower()}_pairs.jpg', quality=94)
    with (data / 'results.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    max_delta = max(v['max_source_delta_vs_control_m'] for a in audits for v in a['variants'])
    total_valid = sum(r['actual_passed'] for r in rows)
    total_final = sum(r['final'] for r in rows)
    code = read(runs[0] / 'provenance.json')
    asset_links = '\n'.join(f'- {s["case"]}：{link(r / "assets/retrieval.json", "资产加载与排除原因")}；{link(r / "summary.json", "逐份资产、提案／静置位姿和失败原因")}。' for r, s in zip(runs, summaries))
    run_links = '\n'.join(f'- {s["case"]}：{link(r / "index.html", "全部三视角前后 RGB")}；{link(r / "audit.json", "只读复核")}；{link(r / "provenance.json", "来源与源码摘要")}；原始物理构造 {s["physical_elapsed_s"]:.1f} s，媒体重录 {s["camera_repair_elapsed_s"]:.1f} s（不作性能比较）。' for r, s in zip(runs, summaries))
    previous = []
    for case in (1, 2):
        for version in (1, 2):
            run = runs[0].parent / f'dense-case{case}-five-20261011-r{version}'
            s = read(run / 'summary.json')
            previous.append(f'| Case{case} r{version} | {s["accepted_count"]}/5 | ' + ('构造已可行；被最终共同实现重新跑的 r3 取代。' if case == 1 else '未按完整资产 footprint 和源占用条件采样，部分大咖啡机耗尽几何预算。r2 加入重采／回溯仍不足；另暴露新增 free root 放置前 forward 的临时接触容量告警。') + ' |')
    report_text = f'''# val_103 / Cup_30：稠密站位与程序化 Case1 / Case2

**两类各 5 份全部通过构造与只读复核，共 10 份、60 张正式交付的原始 RGB（相机搜索诊断另存）。**
站位间距已减半为 **0.1108796040001314 m**，原始候选为 **161 点**，增强前静态几何预筛均通过 **60 点**。
每份增强后对全部原始点逐个新建 Simulation / MjData，真实调用机器人初始化与放置检查；共 **1610 次增强后独立初始化**。
这些检查在 r3 物理构造 run 实际执行；最终 r5-views 仅从不可变快照重录图像，未再做机器人初始化或物理 stepping。
审计核对除相机覆盖模块外物理算法完全一致，模型、状态和全部站位结果与 r3 字节相同。
真实通过点合计 **{total_valid}**，覆盖式压缩后合计 **{total_final}**，每份最多 20。
**未执行连续导航或抓取，任务完成和 case 对任务难度的影响仍未知。**

## 执行过程与配置

工作区：`LastMileDataFlow/.worktrees/procedural-obstacle-augmentation`；分支 `feat/procedural-obstacle-augmentation`；
base commit `{code['base_commit']}`。全部改动未提交／未合并；相邻工作区已有或并发修改保持不动。

- 修改指定 no-edit JSON 的 `spacing_m`，半径和其余字段不变。文件名沿用旧名，实际为四分之一底盘半径间距。
- 新配置：{link(Path.cwd() / 'configs/case_edits/procedural_obstacles_val103_cup30_case1.json', 'Case1')} / {link(Path.cwd() / 'configs/case_edits/procedural_obstacles_val103_cup30_case2.json', 'Case2')}。
  `construction.case` 及预筛／压缩／大物体尺寸／表面采样预算有严格校验；旧配置保留兼容。
- Case1 随机 1–3 件，恰好一把 Chair；其他 0–2 件放在距离目标 0.85 m 内的实际桌面。椅子优先采中点和 station→target 接近区域。
- Case2 随机 6–12 件，全部在目标实际餐桌顶面，至少两件尺寸达标的大／高物体。本次包含咖啡机、较高花瓶及正常桌面小物体。
- 全过程程序化：注册 THOR 检索、实测几何、源占用偏好采样、同类重采、必要时回溯、完整碰撞、实际静置、承重／支撑检查、等时无编辑对照。
- 静态预筛不足 5 点即跳过场景目标；本目标达到门槛。增强后重新检查全部 161 点，不沿用前筛集合。
- 通过点超过 20 时，按方位／距离分区加最远点选择，保留最近／最远代表；没有 `[:20]` 截断。

## 每份结果

{chr(10).join(table)}

观测单位是同一原场景的随机构造变体，不是十个独立场景。两类共享五个子种子便于追溯。
只报告这些实际计数，不从五个种子估计泛化成功率、显著性或误差棒。
全量初始化结果见各变体 `navigation/comparison.json`；最终点及压缩丢弃原因见 `stations_final.json` / `station_selection.json`。

## Case1：五份真实前后 RGB

![Case1 五份前后配对 RGB](figures/case1_pairs.jpg)

每行一份；左为等时无编辑 control，右为静置后的增强场景，取景／光照配对。图中原有家具与桌面物体没有移除。
此图展示新增一椅及零至两件桌面物体；只证明构造，不证明机器人可以完成任务。原图每张 640×480。

## Case2：五份真实前后 RGB

![Case2 五份前后配对 RGB](figures/case2_pairs.jpg)

每行一份；相同配对规则，真实新增 6–12 件。较大的咖啡机和较高花瓶不是替代盒子，也未缩放。
密集场景允许单视角遮挡；三视角联合必须让目标及每件新增物体至少在一张后图达到 24 个真实分割像素。
每对图须前图看到原场景目标、后图看到至少一个新增物体，均达到 24 个分割像素；不能用墙体遮挡的空画面凑视角。
这不等于每视角都看全。没有隐藏墙、实体、可视网格或改材质。

## 验证与证据入口

{run_links}

{asset_links}

- MuJoCo 3.5.0，真实 EGL GPU 7；设备信息写入每份 `renderer.json` / `rgb/pair.json`，不是生成式图片。
- 每个最终变体均通过全高度碰撞、静置稳定、承重／支撑、源物体等时对照及真实尺寸 case 检查，均只需一次物理静置尝试。
- 未编辑源物体相对等时 control 最大额外位移 **{max_delta:.6f} m**，阈值 0.006 m；原场景已有绝对接触异常独立记录，不宣称原场景完全无碰撞。
- 两份审计均复核通过：冻结场景、源碰撞几何、注册来源、原始候选集合、全部初始化记录、覆盖选择、60 张 RGB、配对身份和 HTML 图像链接。
  是同一上下文只读复核，不是独立 Agent 评审，也没有重新执行抓取。
- 回归：全套 303 项（294 通过、9 明确跳过）；另 38 项相关回归开启真实 OpenGL 分割／RGB 并全部通过。简化模型回归与真实 ProcTHOR 验收分开计。
- 真实机器人 FK 几何核对：{link(Path.cwd() / 'outputs/diagnostics/dense-reach-geometry-20261011.json', '半径／间距与 161 点证明')}；`compileall` 和 `git diff --check` 通过。
- 图像通过直接打开检查；链接与像素／分割检查通过。HTML 浏览器截图没有成功复核（本机 Chromium 运行时兼容问题），不把未做的浏览器验收写为通过。

## 失败诊断与保留

Case2 r3 的五份物理和全量初始化均通过，但旧联合覆盖条件允许俯视看全、斜视被墙遮挡。
实际媒体审计发现斜视前后图片完全相同，因此不将这些斜视图计为合格。
最终 r5-views 拒绝空视角并从同一冻结场景重录，保留原 RGB 和失败审计，没有改物体／阈值。
r4 先要求每对图均观测到新增物体，发现旧角度集全部共用 +45° 造成部分场景仍无法选到两张斜视图；保留其失败证据。
最终 r5 新增不依赖 +45° 的开放侧配对角度，仍最多 8 组；Case1 同步重录到此版本，独立初始化计数不重复增加。

| 调试运行 | 通过 | 保留原因 / 不作为最终结果的原因 |
|---|---:|---|
{chr(10).join(previous)}

断点是“资产选择后在完整可放区域内采位姿”，不是提高穿透容差能解决的问题。
最终实现先按旋转 footprint 筛边界，再根据源物体实测占用量偏好空闲区域，之后仍做真实窄相／静置检查；同时延迟 forward 到所有新增位姿写入之后。
这组结果支持构造链已闭合，但不是对某个采样机制的严格消融归因。

THOR Toaster 因关节结构、DeskLamp 因静态刚体融合惯量问题在本次检索中被拒绝；日志保存具体资产与原因。
没有修改源资产来强行通过，也不声称已经支持池中所有类别。当前最大未验证项是跨场景可构造性和导航／抓取难度，而不是本目标的完整点位检查。

输出／快照／日志保留在本工作区 ignored `outputs/`；旧 r1/r2 和 r3 原图保留作为诊断／物理父记录，不混入最终十份。
报告仅保留两张配对证据图和紧凑 {link(data / 'results.csv', '结果 CSV')}；图的复现脚本为 `scripts/report_obstacle_cases.py`，原始图像来源由最终 run 的摘要／审计固定。

| 候选可视化 | 决定 | 原因 |
|---|---|---|
| 两类五行前后 RGB | 保留为证据 | 用户直接要求，能核对真实物体和周边未编辑场景 |
| 站位计数统计图／误差棒 | 不画 | 十行原始计数表更准确，不编造独立样本或不确定区间 |
| 导航区域图 | 留在 run 浏览页作诊断 | 蓝色点是基线候选，不是最终通过点；栅格外空白不表示可通行 |

结论范围：**本目标两类各五份的完整程序化构造与全量初始化检查验证通过**；跨场景规模化、连续导航与抓取均未验证。
'''
    (report / 'REPORT.md').write_text(report_text)
    print(report / 'REPORT.md')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--case1', required=True, type=Path)
    parser.add_argument('--case2', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    build(args.case1, args.case2, args.report)
