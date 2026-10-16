"""统一日志格式与终端颜色；业务模块只需获取自己的 logger。"""

import logging
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar


_TRIAL = ContextVar("logging_trial", default=None)


class Progress:
    """在实际步骤边界更新进度；快速步骤静默，不创建计时线程。"""

    def __init__(self, logger, interval=2.0):
        self.logger = logger
        self.interval = interval
        self.updated = time.monotonic()

    def update(self, message, *args, force=False):
        now = time.monotonic()
        if force or now - self.updated >= self.interval:
            self.logger.info(message, *args, extra={"progress": True})
            self.updated = now


@contextmanager
def trial_logging(label, progress):
    """给警告和阶段进度附上当前试验或变体位置。"""
    token = _TRIAL.set((label, progress))
    try:
        yield
    finally:
        _TRIAL.reset(token)


def trial_progress(message, *args):
    trial = _TRIAL.get()
    if trial is not None:
        label, progress = trial
        progress.update("%s: " + message, label, *args)


class ExperimentLogFilter(logging.Filter):
    def __init__(self, debug=False):
        super().__init__()
        self.debug = debug

    def filter(self, record):
        # 隐藏第三方内部 INFO；WARNING/ERROR 原样保留，DEBUG 模式可查看细节。
        return (self.debug or not record.name.startswith(("curobo", "OpenGL"))
                or record.levelno >= logging.WARNING)


class ColorFormatter(logging.Formatter):
    COLORS = {logging.INFO: 32, logging.WARNING: 33, logging.ERROR: 31, logging.CRITICAL: 31}

    def __init__(self, color):
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
        self.color = color

    def format(self, record):
        message = super().format(record)
        trial = _TRIAL.get()
        if trial is not None and (record.name.startswith("curobo") or record.levelno >= logging.WARNING):
            message += f" [{trial[0]}]"
        color = self.COLORS.get(record.levelno) if self.color else None
        if self.color and record.levelno == logging.INFO and getattr(record, "progress", False):
            color = 34
        return f"\033[{color}m{message}\033[0m" if color else message


def setup_logging(level=logging.INFO, *, color=None):
    """入口调用一次；默认仅在终端着色，也可显式开关颜色。"""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(ColorFormatter(sys.stderr.isatty() if color is None else color))
    handler.addFilter(ExperimentLogFilter(debug=level <= logging.DEBUG))
    # 已有日志配置时沿用调用方设置，不重复添加 handler。
    logging.basicConfig(level=level, handlers=[handler])
