"""Label fonts for PIL images; CJK when the host has one."""
from functools import cache
from pathlib import Path

from PIL import ImageFont

CJK_FONT_PATHS = (
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
)
FALLBACK_FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'


@cache
def _cjk_path():
    return next((p for p in CJK_FONT_PATHS if Path(p).exists()), None)


def cjk_available():
    return _cjk_path() is not None


@cache
def font(size):
    path = _cjk_path() or FALLBACK_FONT
    return ImageFont.truetype(path, size) if Path(path).exists() else ImageFont.load_default()


def label(chinese, english):
    """Keep images readable when the host has no CJK font."""
    return chinese if cjk_available() else english
