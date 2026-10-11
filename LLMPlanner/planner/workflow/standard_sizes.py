"""标准钻头/螺纹尺寸表：图纸上的 "#21 DRILL"、"H" DRILL、17/64、1/4-20 UNC、M8 换算成 mm。

模型凭记忆换算英制字母钻、号码钻经常记错（H 记成 R 的 8.611mm），由工具查表。
"""

from __future__ import annotations

import re
from fractions import Fraction

from .errors import WorkflowError

INCH = 25.4

NUMBER_DRILLS_IN = {
    1: .2280, 2: .2210, 3: .2130, 4: .2090, 5: .2055, 6: .2040, 7: .2010, 8: .1990, 9: .1960, 10: .1935,
    11: .1910, 12: .1890, 13: .1850, 14: .1820, 15: .1800, 16: .1770, 17: .1730, 18: .1695, 19: .1660, 20: .1610,
    21: .1590, 22: .1570, 23: .1540, 24: .1520, 25: .1495, 26: .1470, 27: .1440, 28: .1405, 29: .1360, 30: .1285,
    31: .1200, 32: .1160, 33: .1130, 34: .1110, 35: .1100, 36: .1065, 37: .1040, 38: .1015, 39: .0995, 40: .0980,
    41: .0960, 42: .0935, 43: .0890, 44: .0860, 45: .0820, 46: .0810, 47: .0785, 48: .0760, 49: .0730, 50: .0700,
    51: .0670, 52: .0635, 53: .0595, 54: .0550, 55: .0520, 56: .0465, 57: .0430, 58: .0420, 59: .0410, 60: .0400,
    61: .0390, 62: .0380, 63: .0370, 64: .0360, 65: .0350, 66: .0330, 67: .0320, 68: .0310, 69: .0292, 70: .0280,
    71: .0260, 72: .0250, 73: .0240, 74: .0225, 75: .0210, 76: .0200, 77: .0180, 78: .0160, 79: .0145, 80: .0135,
}
LETTER_DRILLS_IN = {
    "A": .234, "B": .238, "C": .242, "D": .246, "E": .250, "F": .257, "G": .261, "H": .266, "I": .272,
    "J": .277, "K": .281, "L": .290, "M": .295, "N": .302, "O": .316, "P": .323, "Q": .332, "R": .339,
    "S": .348, "T": .358, "U": .368, "V": .377, "W": .386, "X": .397, "Y": .404, "Z": .413,
}
# 统一英制螺纹（约 75% 牙高）的推荐底孔钻
INCH_TAP_DRILLS = {
    "0-80": "3/64", "1-64": "#53", "1-72": "#53", "2-56": "#50", "2-64": "#50", "3-48": "#47", "3-56": "#45",
    "4-40": "#43", "4-48": "#42", "5-40": "#38", "5-44": "#37", "6-32": "#36", "6-40": "#33", "8-32": "#29",
    "8-36": "#29", "10-24": "#25", "10-32": "#21", "12-24": "#16", "12-28": "#14",
    "1/4-20": "#7", "1/4-28": "#3", "5/16-18": "F", "5/16-24": "I", "3/8-16": "5/16", "3/8-24": "Q",
    "7/16-14": "U", "7/16-20": "25/64", "1/2-13": "27/64", "1/2-20": "29/64", "9/16-12": "31/64",
    "9/16-18": "33/64", "5/8-11": "17/32", "5/8-18": "37/64", "3/4-10": "21/32", "3/4-16": "11/16",
    "7/8-9": "49/64", "7/8-14": "13/16", "1-8": "7/8", "1-12": "59/64", "1-14": "15/16",
}
METRIC_COARSE_PITCH = {
    1.6: 0.35, 2: 0.4, 2.5: 0.45, 3: 0.5, 3.5: 0.6, 4: 0.7, 5: 0.8, 6: 1.0, 8: 1.25, 10: 1.5, 12: 1.75,
    14: 2.0, 16: 2.0, 18: 2.5, 20: 2.5, 22: 2.5, 24: 3.0, 27: 3.0, 30: 3.5, 36: 4.0,
}
# 粗牙推荐底孔钻（标准钻头规格，不全等于 大径−螺距）
METRIC_COARSE_TAP_DRILL = {
    1.6: 1.25, 2: 1.6, 2.5: 2.05, 3: 2.5, 3.5: 2.9, 4: 3.3, 5: 4.2, 6: 5.0, 8: 6.8, 10: 8.5, 12: 10.2,
    14: 12.0, 16: 14.0, 18: 15.5, 20: 17.5, 22: 19.5, 24: 21.0, 27: 24.0, 30: 26.5, 36: 32.0,
}

_NUMBER = re.compile(r"^(?:#|NO\.?\s*|NUMBER\s*)(\d{1,2})$")
_LETTER = re.compile(r"^(?:LETTER\s*)?\"?([A-Z])\"?$")
_FRACTION = re.compile(r"^(\d+\s+)?(\d+)/(\d+)\s*(?:\"|IN|INCH)?$")
_INCH_THREAD = re.compile(r"^(#?\d+(?:/\d+)?)\s*-\s*(\d+)$")
_METRIC = re.compile(r"^M(\d+(?:\.\d+)?)(?:\s*[X×]\s*(\d+(?:\.\d+)?))?$")
_NOISE = re.compile(r"\b(DRILL|DRILLS|THRU|UNC|UNF|UNEF|UNS|-?2B|-?3B|-?6H|TAP)\b|⌀|Ø")


def _inch_drill(text: str) -> float | None:
    match = _NUMBER.match(text)
    if match:
        number = int(match.group(1))
        if number not in NUMBER_DRILLS_IN:
            raise WorkflowError("TOOL_ARGS", f"号码钻 #{number} 不在 #1~#80 范围内")
        return NUMBER_DRILLS_IN[number] * INCH
    match = _LETTER.match(text)
    if match:
        return LETTER_DRILLS_IN[match.group(1)] * INCH
    match = _FRACTION.match(text)
    if match:
        whole = int(match.group(1) or 0)
        return float(whole + Fraction(int(match.group(2)), int(match.group(3)))) * INCH
    return None


def _thread_major_mm(size: str, tpi: int) -> float:
    if "/" in size:
        return float(Fraction(size)) * INCH
    number = int(size)
    if number > 12 or (number == 1 and tpi <= 14):
        return number * INCH
    return (0.060 + 0.013 * number) * INCH


def resolve_size(value: str, *, nominal: bool = False) -> tuple[float, str] | None:
    """把尺寸代号换算成 mm；不是代号（普通数字或表达式）时返回 None。

    nominal=False（孔径）：钻头代号给钻头直径，螺纹代号给推荐底孔钻；nominal=True（公称直径）：螺纹代号给大径。
    """
    raw = value.strip()
    if not raw or raw.startswith("="):
        return None
    text = _NOISE.sub(" ", raw.upper()).strip()
    text = re.sub(r"\s+", " ", text)
    match = _INCH_THREAD.match(text.replace(" ", ""))
    if match:
        size, tpi = match.group(1).lstrip("#"), match.group(2)
        key = f"{size}-{tpi}"
        if nominal:
            return _thread_major_mm(size, int(tpi)), f"{key} 螺纹大径"
        if key not in INCH_TAP_DRILLS:
            raise WorkflowError(
                "TOOL_ARGS", f"没有 {key} 的底孔钻数据", hint="请直接给底孔直径（mm），或写钻头代号如 \"#21\"、\"F\"、\"27/64\"。")
        drill = INCH_TAP_DRILLS[key]
        return _inch_drill(drill.upper()), f"{key} 螺纹底孔钻 {drill}"
    match = _METRIC.match(text.replace(" ", ""))
    if match:
        major = float(match.group(1))
        pitch = float(match.group(2)) if match.group(2) else METRIC_COARSE_PITCH.get(major)
        if nominal:
            return major, f"M{major:g} 公称直径"
        coarse = METRIC_COARSE_PITCH.get(major)
        if pitch == coarse and major in METRIC_COARSE_TAP_DRILL:
            return METRIC_COARSE_TAP_DRILL[major], f"M{major:g}x{pitch:g} 粗牙推荐底孔钻"
        if pitch is None:
            raise WorkflowError("TOOL_ARGS", f"M{major:g} 没有粗牙螺距数据", hint="写成 M{d}x{螺距}，或直接给底孔直径。")
        return round(major - pitch, 4), f"M{major:g}x{pitch:g} 底孔（大径−螺距）"
    drill = _inch_drill(text)
    if drill is None:
        return None
    label = "号码钻" if text.startswith(("#", "NO", "NUMBER")) else "字母钻" if _LETTER.match(text) else "分数英寸钻"
    return drill, f"{raw} {label}"
