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
