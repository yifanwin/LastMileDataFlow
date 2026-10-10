"""Write COMMENT-ONLY source mirrors; preserve live src and its frozen run hash."""
from pathlib import Path
import ast,hashlib,json,re
root=Path(__file__).resolve().parents[3];out=root/'reports/no-edit/annotated-src';out.mkdir(exist_ok=True)
notes={
'runtime/no_edit_execution.py':[
("        goal=body_pose(ctx.sim,ctx.sim.model.body(candidate['body']).id)@candidate['pose_local']",'C01：假定资产抓法是 object→RBY TCP。当前代码未逐资产校验 TCP 约定；这是风险，不是已证明的坐标错误。'),
("    scored.sort(key=lambda x:-x[0]);",'C02：仅按当前底盘方位的接近方向选前32，再抽4；不是对所有抓法或移动后底盘姿态做完整搜索。'),
("            h=forced['torso_h'] if forced else cfg.torso_heights[index%len(cfg.torso_heights)]",'C03：高度绑定候选索引而非遍历高度。只有1个候选时 index 总为0，五次重复仍选同一 h。'),
("            if not forced and task['anchor_world'][2] < .8:",'C04：目标中心低于0.8m便强制h最大值；AlarmClock12中心0.7968m因此所有候选h=.738，可能引入碰撞。h是关节联动参数，不是世界高度。'),
("            contacts=robot_contacts(dry)",'C05：检查的是改变躯干后的NEW dry诊断姿态，不是过滤时的原始站位；预筛合法与这里碰撞并不矛盾。'),
("                    return {'arm':side,'torso_h':h",'C06：只要预抓取可规划就返回，未验证接近/闭爪/抬升整链；后续失败不在同一试验切换其他抓法。'),
("        pre=goal.copy(); pre[:3,3]-=goal[:3,2]*.08",'C07：预抓取沿TCP Z轴后退8cm；该TCP姿态来自完整资产抓法，不是只给位置。'),
("        goal[:3,3]+=goal[:3,2]*ctx.config.approach_offset_m",'C08：默认再向前1cm，与预抓取相差9cm。第二次MotionGen从实测预抓取态出发，可能选到不同IK分支。'),
("            if 'IK' in str(diagnostic.get('status','')).upper():",'C09：这里只做重新创建的free IK诊断。新solver/随机种子/优化状态也与MotionGen不同，不是严格只切掉碰撞的对照。'),
("                status='planning_no_solution' if not recorder.steps else 'failure';",'C10：无解后结束当前试验；站位五次是独立试验，S1额外重试仅由path分支开启。执行过预抓取时按failure保存。'),
],
'planning/curobo.py':[
("    for group in ('torso','head',idle+'_arm','left_gripper','right_gripper'):",'P01：底盘和单臂活动，但躯干、另一臂、head、夹爪均锁在该候选的姿态。放开base不等于全身自由IK。'),
("        if m.body(b).name.startswith('robot_0/') or b in target",'P02：目标整物体从规划碰撞世界排除，其他家具仍保留；目标非指碰撞由实际物理检查拒绝。'),
("            collision_activation_distance=.01",'P03：使用机器人碰撞球、5mm sphere buffer与1cm避碰代价激活距离；不等价于MuJoCo精确几何。激活距离不是简单1cm硬拒绝阈值。'),
("        self.motion=MotionGen(settings)",'P04：未显式把task seed传给MotionGen的ik_seed/trajopt_seed，本机cuRobo使用固定默认值；五次试验不保证五套IK随机种子。'),
("        settings=IKSolverConfig.load_from_robot_config(self.robot_cfg,world_model=None,",'P05：无环境且关闭自碰撞，只能做有限种子kinematic可解性诊断；true不能直接证明原失败是某个几何碰撞。'),
("        result=self.motion.plan_single(state,",'P06：MotionGen以当前实测关节作为start，还传入其内部IK seed/retract策略；内部IK_FAIL可来自优化搜索不收敛，不是不可达证书。'),
("    k['link_names']=list(dict.fromkeys",'P07：注册head及双TCP本身是FK需求，不应等价于固定其世界姿态。旧cuRobo warmup会给这些link建立额外pose目标。'),
("        self.motion.warmup(enable_graph=False,warmup_js_trajopt=False)",'P08：本机旧cuRobo warmup仍调用plan_single(link_poses=state.link_pose)。后续None不会清空缓存，head/闲置TCP的世界pose代价限制底盘运动。S0042仅抑制这些warmup目标、保留关节锁和全部避碰后，两段规划均成功。'),
],
'tasks/raw_scene.py':[
("                if result:\n                    return result",'G01：有效资产抓法直接返回，程序bbox抓法仅在无有效资产数据时才生成；有效但不适配当前RBY姿态时没有备用候选。'),
("    center = (lo+hi)/2; width_axis",'G02：bbox pinch只是几何候选，不能当实际接触/抓取成功；补充这类候选仍须完整规划与物理验收。'),
],
'stations/no_edit_sampling.py':[
("    collisions = robot_contacts(sim)",'S01：合法站位检查发生在原始initial躯干/手臂姿态。后续choose_control改躯干时必须另检，不能当作这里漏检。'),
("        penetration = -.002 if all(robot) else -.001",'S02：这是真实MuJoCo诊断姿态穿透：自碰撞2mm/环境1mm阈值，不是cuRobo的球距离标签。'),
],
'validation/no_edit.py':[
("    elif diagnostic and diagnostic.get('free_ik_success') is False:",'V01：Reachability仅表示最后记录候选+有限种子free IK无解，未遍历抓法×躯干×手臂，不证明该物体不可达。'),
("    if diagnostic:\n",'V02：diagnostic可能含dry_collisions，而顶层collisions只收实际执行记录；读取归因必须同时查看这两处。'),
]}
manifest=[]
for name,items in notes.items():
    path=root/'src/lastmile_dataflow'/name;original=path.read_text();text=original;entries=[]
    for needle,note in items:
        pattern='^'+re.escape(needle)
        match=re.search(pattern,original,re.M)
        if not match:raise ValueError('Missing annotation anchor: '+name+' '+needle)
        line=original[:match.start()].count('\n')+1;indent=needle[:len(needle)-len(needle.lstrip())];text=re.sub(pattern,lambda m:indent+'# TRACE '+note+'\n'+m.group(0),text,count=1,flags=re.M);entries.append({'original_line':line,'annotation':note})
    text='# 注释镜像：仅阅读，不是运行入口。原始源码保持不变，避免破坏正在运行任务的冻结哈希。\n'+text
    assert ast.dump(ast.parse(original),include_attributes=False)==ast.dump(ast.parse(text),include_attributes=False)
    dest=out/(name.replace('/','_').replace('.py','.annotated.py'));dest.write_text(text)
    manifest.append({'source':str(path.relative_to(root)),'annotated':str(dest.relative_to(root)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'notes':entries})
(out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
print('AST-identical comment mirrors:',len(manifest))
