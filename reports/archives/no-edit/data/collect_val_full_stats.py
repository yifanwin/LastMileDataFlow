"""只读聚合某个 no-edit 全量 run 的统计。

用法：
    python3 collect_val_full_stats.py [run_dir] [out_json]

run_dir 默认是 no-edit-val-full-20261009 的输出目录。
不启动仿真、不修改被统计的 run 文件。
"""
import json,os,glob,collections,statistics,datetime,sys
DEFAULT="/data0/wenyifan/MoMaTrajGen/LastMileDataFlow/.worktrees/no-edit-lastmile-val/outputs/no_edit/no-edit-val-full-20261009"
B=sys.argv[1] if len(sys.argv)>1 else DEFAULT
OUT=sys.argv[2] if len(sys.argv)>2 else "/home/wenyifan/.claude/jobs/2903cb52/tmp/stats.json"
out={}
out["run_dir"]=B
out["summary"]=json.load(open(f"{B}/summary.json"))
fc=json.load(open(f"{B}/frozen_config.json"))
out["code_sha256"]=fc["code_sha256"]; out["schema_version"]=fc["schema_version"]
out["protocol"]=fc["collection"]
out["gpu_selection"]=json.load(open(f"{B}/gpu_selection.json"))
out["scene_index_status"]=collections.Counter(e["status"] for e in json.load(open(f"{B}/scene_index.json")))

scenes={}
for s in range(4):
    ti=json.load(open(f"{B}/scenes/val_{s}/task_index.json"))
    tr=json.load(open(f"{B}/scenes/val_{s}/task_results.json"))
    atts=os.listdir(f"{B}/scenes/val_{s}/attempts")
    terms=collections.Counter(); ex=0; suc=0
    for a in atts:
        d=json.load(open(f"{B}/scenes/val_{s}/attempts/{a}/attempt.json"))
        terms[d["termination_reason"]]+=1
        if (d.get("executed_steps") or 0)>0: ex+=1
        if d["status"]=="success": suc+=1
    scenes[f"val_{s}"]={"summary":json.load(open(f"{B}/scenes/val_{s}/summary.json")),
        "enumerated_tasks":len(ti),"task_dirs":len(os.listdir(f"{B}/scenes/val_{s}/tasks")),
        "task_results":len(tr),"attempts":len(atts),"executed":ex,"success":suc,
        "termination":dict(terms),
        "ops_enumerated":dict(collections.Counter(t.get("operation") for t in ti)),
        "statuses":dict(collections.Counter(t["status"] for t in tr))}
out["scenes"]=scenes

allrows=[]
for s in range(4):
    for a in os.listdir(f"{B}/scenes/val_{s}/attempts"):
        d=json.load(open(f"{B}/scenes/val_{s}/attempts/{a}/attempt.json")); d["_s"]=s; d["_op"]=a.split("-")[0]; allrows.append(d)
out["totals"]={"attempts":len(allrows),
  "status":dict(collections.Counter(r["status"] for r in allrows)),
  "termination":dict(collections.Counter(r["termination_reason"] for r in allrows).most_common()),
  "executed_gt0":sum(1 for r in allrows if (r.get("executed_steps") or 0)>0),
  "max_executed_steps":max(r.get("executed_steps") or 0 for r in allrows)}
byop={}
for op in ("pick","open"):
    rs=[r for r in allrows if r["_op"]==op]
    byop[op]={"attempts":len(rs),"executed":sum(1 for r in rs if (r.get("executed_steps") or 0)>0),
              "success":sum(1 for r in rs if r["status"]=="success"),
              "termination":dict(collections.Counter(r["termination_reason"] for r in rs).most_common())}
out["by_operation"]=byop

cats=collections.Counter(); phases=collections.Counter(); navs=collections.Counter()
for f in glob.glob(f"{B}/scenes/*/attempts/*/outcome.json"):
    d=json.load(open(f)); cats[(d.get("attribution") or {}).get("category")]+=1
    navs[(d.get("navigation") or {}).get("status")]+=1
for f in glob.glob(f"{B}/scenes/*/attempts/*/result.json"):
    phases[json.load(open(f)).get("extra",{}).get("phase")]+=1
out["attribution_category"]=dict(cats); out["phase"]=dict(phases.most_common()); out["navigation_status"]=dict(navs)

tasks=[]; 
for s in range(4):
    for t in json.load(open(f"{B}/scenes/val_{s}/task_results.json")): t["_s"]=s; tasks.append(t)
out["tasks"]={"total":len(tasks),"status":dict(collections.Counter(t["status"] for t in tasks)),
  "with_success_rate":sum(1 for t in tasks if t["rate"].get("success_rate") is not None),
  "success_rate_gt0":sum(1 for t in tasks if (t["rate"].get("successes") or 0)>0),
  "terminal_trials_sum":sum(t["rate"].get("terminal_trials",0) for t in tasks),
  "successes_sum":sum(t["rate"].get("successes",0) for t in tasks),
  "dataset_eligible":dict(collections.Counter(t.get("dataset_eligible") for t in tasks)),
  "successful":sorted([{"task_id":t["task_id"],"scene":t["_s"],"op":t["operation"],
       "instruction":t["instruction"],"successes":t["rate"]["successes"],
       "trials":t["rate"]["terminal_trials"],"rate":t["rate"]["success_rate"]}
       for t in tasks if (t["rate"].get("successes") or 0)>0],key=lambda x:-x["rate"])}
sk=collections.Counter()
for f in glob.glob(f"{B}/scenes/*/skipped_targets.json"):
    for e in json.load(open(f)): sk[(e.get("operation"),e.get("reason"))]+=1
out["skipped_targets"]={f"{k[0]}|{k[1]}":v for k,v in sk.most_common()}
out["artifacts"]={"mp4":len(glob.glob(f"{B}/scenes/*/attempts/*/*.mp4")),
  "videos_json":len(glob.glob(f"{B}/scenes/*/attempts/*/videos.json")),
  "videos_enabled":sum(1 for f in glob.glob(f"{B}/scenes/*/attempts/*/videos.json") if json.load(open(f)).get("enabled")),
  "events_nonempty":len([f for f in glob.glob(f"{B}/scenes/*/attempts/*/events.jsonl") if os.path.getsize(f)>0]),
  "trajectory_nonempty":len([f for f in glob.glob(f"{B}/scenes/*/attempts/*/trajectory.jsonl") if os.path.getsize(f)>0]),
  "planning_nonempty":len([f for f in glob.glob(f"{B}/scenes/*/attempts/*/planning.json") if os.path.getsize(f)>3]),
  "physics_gz":len(glob.glob(f"{B}/scenes/*/attempts/*/physics.jsonl.gz")),
  "heatmaps":len(glob.glob(f"{B}/scenes/*/tasks/*/maps/success_heatmap.png"))}
ts=[]
for f in glob.glob(f"{B}/scenes/*/attempts/*/attempt.json"):
    d=json.load(open(f)); ts.append((d["created_at_utc"],d["finished_at_utc"]))
ts.sort()
dur=[(datetime.datetime.fromisoformat(b)-datetime.datetime.fromisoformat(a)).total_seconds() for a,b in ts]
out["timing"]={"first_attempt":ts[0][0],"last_finish":max(x[1] for x in ts),
  "dur_median_s":statistics.median(dur),"dur_mean_s":round(statistics.mean(dur),3),
  "dur_p99_s":sorted(dur)[int(len(dur)*.99)],"dur_max_s":max(dur)}
json.dump(out,open(OUT,"w"),ensure_ascii=False,indent=1,default=str)
print(json.dumps(out,ensure_ascii=False,indent=1,default=str)[:6000])
