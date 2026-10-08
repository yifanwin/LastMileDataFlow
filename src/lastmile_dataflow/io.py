"""确定性摘要和原子元数据写入；不读取环境变量或密钥。

【为什么需要 canonical()】工程里到处用“摘要”来判断两个东西是不是同一个（配置、场景版本、
检查点、观察包……）。要保证同样的内容永远算出同样的摘要，必须先把 JSON 规范化：
  - sort_keys     键顺序固定
  - separators    去空格，避免格式差异
  - allow_nan=False 禁止 NaN/Inf 混进摘要（否则不可复现）
  - default=jsonable 统一处理 numpy 数组和 Path

【为什么写文件要这么啰嗦】write_json 先写临时文件、fsync、再 os.replace 原子替换。
这样即使写到一半断电/崩溃，磁盘上也只会是“旧的完整文件”或“新的完整文件”，
绝不会出现半截 JSON——对“证据不可损坏”的要求来说这很关键。
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def jsonable(value):
    """把 numpy 数组 / Path 这类对象转成可 JSON 化的形式。"""
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Unsupported JSON type: {type(value).__name__}")


def canonical(value):
    """规范化 JSON 字节串；摘要计算和原子写入都以它为准。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      default=jsonable, separators=(",", ":")).encode("utf-8")


def digest(value):
    """对“值”求稳定摘要（内部先 canonical）。用于配置摘要、身份摘要等。"""
    return hashlib.sha256(canonical(value)).hexdigest()


def file_digest(path):
    """对“文件内容”求摘要，分块读取以免大文件占内存。用于快照/产物完整性校验。"""
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    """原子写 JSON：临时文件 → 落盘 → 替换，绝不产生半截文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload + b"\n")
            stream.flush()
            os.fsync(stream.fileno())   # 确保真正落盘再做替换
        os.replace(tmp, path)           # 原子替换
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def validate_case_task(record):
    """Factory v1 task envelope. Existing attempt/scene serialization is unchanged."""
    required={'schema_version','task_id','case_type','requested_case_type','construction_branch','edits',
              'move_type','attribution','improvement_level','construction_validity','case_condition','task_success',
              'S0','S1','station_map','comparison_evidence','review','manual_review','scene_dir','scene_checksums',
              'spec_digest','delivery_status','navigation_success','record_digest'}
    if not isinstance(record,dict) or set(record)!=required or record['schema_version']!='case-task-v1':
        raise ValueError('invalid task envelope')
    raw=dict(record); identity=raw.pop('record_digest')
    if digest(raw)!=identity: raise ValueError('task record digest mismatch')
    branch=record['construction_branch']
    if branch not in ('unedited','edited') or not isinstance(record['edits'],list) or (branch=='unedited' and record['edits']) or (branch=='edited' and not record['edits']):
        raise ValueError('construction branch/edit mismatch')
    if record['case_type'] not in ('case1','case1.5','case1-S','case2','case3') or record['requested_case_type'] not in ('case1','case1.5','case1-S','case2','case3'):
        raise ValueError('unknown case type')
    if record['move_type'] not in ('same_edge','switch_edge') or (record['case_type'] in ('case1','case1.5') and record['move_type']!='switch_edge') or (record['case_type']=='case1-S' and record['move_type']!='same_edge'):
        raise ValueError('case/move mismatch')
    if record['improvement_level'] not in ('L1','L2') or record['construction_validity']!='pass' or record['case_condition']!='pass':
        raise ValueError('only measured L1/L2 comparisons can be frozen')
    if record['task_success'] != ('success' if record['improvement_level']=='L2' else 'unknown'):
        raise ValueError('planning is not execution evidence')
    if record['navigation_success']!='unknown': raise ValueError('v1 does not certify navigation')
    if type(record['manual_review']) is not bool or (record['review']['conclusion']=='unknown' and not record['manual_review']):
        raise ValueError('unknown review must enter manual queue')
    if record['review']['conclusion']=='implausible': raise ValueError('rejected review cannot be frozen')
    from .validation.comparison import validate_station
    fingerprint=validate_station(record['S0']); validate_station(record['S1'],fingerprint)
    return record
