"""Cold-cache Qwen Single-turn benchmark for untested Web text examples."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

LLM_ROOT = Path(__file__).resolve().parent.parent
if str(LLM_ROOT) not in sys.path:
    sys.path.insert(0, str(LLM_ROOT))

from planner.observer import SwObserver

try:
    from .eval_three_view_multiturn import ROOT, _query_balance
    from . import metrics as geo_metrics
except ImportError:
    from eval_three_view_multiturn import ROOT, _query_balance
    import metrics as geo_metrics


MODEL = "Qwen3.5-397B-A17B"


@dataclass(frozen=True)
class TextCase:
    case_id: str
    name: str
    prompt: str
    expected_bbox_mm: tuple[float, float, float]
    bbox_tolerance_mm: float
    volume_range_mm3: tuple[float, float]
    expected_details: tuple[tuple[str, int], ...]
    split: str = "dev"


CASES = (
    TextCase(
        "phone_stand",
        "一体式手机支架",
        "创建一个一体式桌面手机支架，所有结构必须合并为单一实体。"
        "底座长110mm、宽78mm、厚8mm；78mm宽的倾斜背板厚6mm，"
        "与水平面成74°，从底座上表面升起，成品总高度118mm。"
        "底座前端设置贯穿全宽的挡唇，前后厚10mm，高出底座上表面12mm，"
        "使成品前端总高度20mm。背板左右两侧各有一个三角加强筋。"
        "背板下部中央开一个宽16mm、向下开口高度28mm的充电线U形通槽；"
        "背板中上部开3个36×8mm的水平贯穿散热槽，槽中心竖向间距18mm。"
        "底面加工两个24×10mm、深1.5mm的防滑垫浅凹槽。"
        "最后尽可能对适合的外轮廓做R4mm圆角，同时保持底面平整、支架左右对称。",
        (78.0, 116.0, 118.0),
        5.0,
        (80000.0, 180000.0),
        (("extrusions", 1), ("cuts", 2), ("fillets", 1)),
    ),
    TextCase(
        "stool",
        "四脚板凳",
        "创建四脚板凳：凳面 200×200×20mm；四条圆柱腿直径 20mm、长 150mm，腿中心距相邻边 30mm。",
        (170.0, 200.0, 200.0),
        20.0,
        (900000.0, 1050000.0),
        (("extrusions", 2),),
    ),
    TextCase(
        "rounded_stool",
        "圆角板凳",
        "创建四脚板凳：凳面 200×200×20mm；四条圆柱腿直径 20mm、长 150mm，距边 30mm；凳面顶部四条边做 R5mm 圆角。",
        (170.0, 200.0, 200.0),
        20.0,
        (900000.0, 1050000.0),
        (("extrusions", 2), ("fillets", 1)),
    ),
    TextCase(
        "storage_box",
        "收纳盒",
        "创建顶部敞开的长方形收纳盒，外部 120×80×50mm，壁厚和底厚均为 8mm，顶部开口边做 R3mm 圆角。",
        (50.0, 80.0, 120.0),
        5.0,
        (180000.0, 220000.0),
        (("extrusions", 1), ("cuts", 1), ("fillets", 1)),
    ),
    TextCase(
        "standard_die",
        "标准骰子",
        "创建边长 20mm 的标准六面骰子，全部棱边 R1.5mm；六面分别为 1 至 6 点、相对面点数和为 7；点为直径 3mm、深 1.5mm 的凹坑。",
        (20.0, 20.0, 20.0),
        2.0,
        (7000.0, 8000.0),
        (("extrusions", 1), ("cuts", 6), ("fillets", 1)),
    ),
    TextCase(
        "hex_nut",
        "六角螺母",
        "创建对边距 30mm、厚 8mm 的正六角螺母，中心有直径 10mm 的贯穿孔，厚度方向关于原点居中。",
        (8.0, 30.0, 34.641),
        3.0,
        (4500.0, 6500.0),
        (("extrusions", 1), ("polygons", 1), ("circles", 1)),
    ),
    TextCase(
        "mug",
        "马克杯",
        "创建高 90mm、外径 70mm、壁厚 5mm、底厚 10mm 的圆柱形马克杯，并添加外径 40mm、条粗 8mm、厚 8mm 的环形把手。",
        (70.0, 90.0, 100.0),
        15.0,
        (100000.0, 145000.0),
        (("extrusions", 2), ("cuts", 1), ("circles", 3)),
    ),
    TextCase(
        "wrench",
        "扳手",
        "创建厚 8mm 的单头扳手：头部外径 40mm、中心正六角卡口对边距 13mm；手柄长 120mm、宽 15mm，并与头部可靠合并。",
        (8.0, 40.0, 140.0),
        8.0,
        (17000.0, 24000.0),
        (("extrusions", 2), ("polygons", 1)),
    ),
    TextCase(
        "vase",
        "花瓶",
        "创建高 150mm 的轴对称空心花瓶：底部平坦，中部最大直径 90mm，瓶颈直径 40mm，瓶口直径 55mm，底厚 10mm。",
        (90.0, 90.0, 150.0),
        10.0,
        (300000.0, 500000.0),
        (("revolves", 1), ("centerlines", 1)),
    ),
    TextCase(
        "key",
        "钥匙",
        "创建厚 2mm 的小型钥匙：外径 20mm 的钥匙圈头、10×5mm 圈孔、42×8mm 钥匙杆，并在杆的一侧均布 5 个齿槽。",
        (2.0, 20.0, 59.0),
        8.0,
        (800.0, 1500.0),
        (("extrusions", 2), ("cuts", 1)),
    ),
    TextCase(
        "gear_disc",
        "齿轮盘",
        "创建直径 60mm、厚 8mm 的圆盘，中心有直径 12mm 贯穿孔，并在接近外缘处均布 12 个相同的小贯穿槽。",
        (8.0, 60.0, 60.0),
        4.0,
        (17500.0, 22000.0),
        (("extrusions", 1), ("cuts", 1)),
    ),
    TextCase(
        "flange",
        "法兰盘",
        "创建直径 100mm、厚 12mm 的圆形法兰，中心贯穿孔直径 30mm，并在节圆直径 80mm 上均布 6 个直径 8mm 的贯穿孔。",
        (12.0, 100.0, 100.0),
        3.0,
        (78000.0, 86000.0),
        (("extrusions", 1), ("circles", 8)),
    ),
    TextCase(
        "pulley",
        "皮带轮",
        "创建直径 100mm、厚 20mm 的简化皮带轮，中心贯穿孔直径 25mm，顶部有外径 90mm、内径 70mm、深 10mm 的环槽，外轮缘做 C1mm 倒角。",
        (20.0, 100.0, 100.0),
        3.0,
        (115000.0, 128000.0),
        (("extrusions", 1), ("cuts", 1), ("chamfers", 1)),
    ),
    TextCase(
        "stepped_shaft",
        "三段阶梯轴",
        "使用一次旋转创建总长 90mm 的三段阶梯轴：各段直径和长度依次为 35×40mm、50×30mm、25×20mm，轴向关于原点居中。",
        (50.0, 50.0, 90.0),
        3.0,
        (102000.0, 111500.0),
        (("revolves", 1), ("centerlines", 1)),
    ),
    TextCase(
        "socket_bolt",
        "内六角螺栓",
        "创建简化内六角圆柱头螺栓：杆径 10mm、杆长 50mm；头部直径 18mm、高 6mm；顶部内六角对边距 8mm、盲孔深 4mm；头杆交界 R0.5mm。",
        (18.0, 18.0, 56.0),
        2.0,
        (5200.0, 7200.0),
        (
            ("extrusions", 2),
            ("cuts", 1),
            ("polygons", 1),
            ("fillets", 1),
        ),
    ),
)

# Held-out：规格与 GT 在系统首次运行之前写定，不得据其结果调整 prompt、工具或代码。
# 体积为解析计算值（注释给出算式），区间留出圆弧离散化与合理建模差异的余量。
HELDOUT_CASES = (
    TextCase(
        "l_bracket",
        "L形角码",
        "创建厚 5mm 的 L 形角码，两条边宽度均为 30mm：水平边外侧长 50mm，竖直边外侧高 40mm，两边在拐角处连成一体。"
        "水平边上沿长度方向打 2 个直径 6mm 的贯穿孔，孔心位于宽度中线、距竖直边外侧面分别 20mm 和 38mm；"
        "竖直边上在宽度中线、距底面 25mm 处打 1 个直径 6mm 的贯穿孔。",
        (30.0, 40.0, 50.0),
        1.5,
        # (50*5 + 35*5)*30 - 3*pi*3^2*5 = 12325.9
        (11950.0, 12700.0),
        (("solid_features", 1),),
        split="heldout",
    ),
    TextCase(
        "spacer_ring",
        "隔套",
        "创建外径 40mm、内径 20mm、厚 10mm 的隔套，两个端面的外圆边都做 C1mm 倒角。",
        (10.0, 40.0, 40.0),
        1.0,
        # pi*(20^2-10^2)*10 - 2*2*pi*(20-1/3)*0.5 = 9301.2
        (9200.0, 9400.0),
        (("chamfers", 1),),
        split="heldout",
    ),
    TextCase(
        "mounting_plate",
        "安装板",
        "安装板 120×80×6mm，四个角做 R8 圆角（竖直棱边），4-Ø7 贯穿孔，孔心距相邻两边各 12mm；"
        "板中央开一个 40×20mm 的矩形贯穿窗口。",
        (6.0, 80.0, 120.0),
        1.5,
        # 120*80*6 - 4*(8^2 - pi*8^2/4)*6 - 4*pi*3.5^2*6 - 40*20*6 = 51546.8
        (51000.0, 52100.0),
        (("solid_features", 1),),
        split="heldout",
    ),
    TextCase(
        "stepped_block",
        "台阶块",
        "创建台阶块：下层 80×50×15mm；上层 40×50×15mm，放在下层的一端，与下层的端面和两个侧面齐平，整体高 30mm。"
        "在下层露出的上表面中央打一个直径 10mm、深 8mm 的盲孔。",
        (30.0, 50.0, 80.0),
        1.5,
        # 80*50*15 + 40*50*15 - pi*5^2*8 = 89371.7
        (88900.0, 89900.0),
        (("solid_features", 1), ("cuts", 1)),
        split="heldout",
    ),
    TextCase(
        "knob",
        "旋钮",
        "创建旋钮：圆柱主体直径 30mm、高 20mm；顶面中央凸起一个直径 10mm、高 5mm 的小圆柱；"
        "主体外圆周均布 12 条竖直防滑槽，每条宽 2mm、深 1mm，沿主体全高贯通；底面中央有直径 6mm、深 12mm 的安装盲孔。",
        (25.0, 30.0, 30.0),
        1.0,
        # pi*15^2*20 + pi*5^2*5 - 12*2*1*20 - pi*3^2*12 = 13710.6
        (13450.0, 13950.0),
        (("solid_features", 1), ("cuts", 1)),
        split="heldout",
    ),
    TextCase(
        "hex_standoff",
        "六角隔离柱",
        "M3 六角隔离柱：对边距 8mm、长 20mm 的正六棱柱，两个端面中心各有一个 Ø3.3mm、深 6mm 的螺纹底孔（不做螺纹）。",
        (8.0, 9.238, 20.0),
        0.6,
        # sqrt(3)/2*8^2*20 - 2*pi*1.65^2*6 = 1005.9
        (985.0, 1025.0),
        (("polygons", 1),),
        split="heldout",
    ),
    TextCase(
        "u_channel",
        "U形槽",
        "创建长 100mm 的 U 形槽：外宽 40mm、外高 30mm，两侧壁厚和底板厚都是 4mm，槽口朝上；"
        "底板上沿长度方向打 3 个直径 8mm 的贯穿孔，孔距 30mm，整体在长度方向居中。",
        (30.0, 40.0, 100.0),
        1.5,
        # (40*30 - 32*26)*100 - 3*pi*4^2*4 = 36196.8
        (35900.0, 36500.0),
        (("solid_features", 1),),
        split="heldout",
    ),
    TextCase(
        "flanged_bushing",
        "法兰衬套",
        "创建带法兰的衬套：套筒外径 20mm、内径 12mm、总长 30mm；一端带外径 36mm、厚 5mm 的法兰，法兰厚度计入总长；内孔全长贯通。",
        (30.0, 36.0, 36.0),
        1.0,
        # pi*(10^2-6^2)*25 + pi*(18^2-6^2)*5 = 9550.4
        (9400.0, 9700.0),
        (("solid_features", 1),),
        split="heldout",
    ),
    TextCase(
        "wedge_block",
        "斜楔块",
        "创建斜楔块：长 60mm、宽 30mm，一端高 20mm、另一端高 5mm，上表面是连续的平斜面，底面平整。",
        (20.0, 30.0, 60.0),
        1.0,
        # (20+5)/2*60*30 = 22500
        (22200.0, 22800.0),
        (("solid_features", 1),),
        split="heldout",
    ),
    TextCase(
        "spoked_wheel",
        "减重轮盘",
        "创建直径 120mm、厚 10mm 的轮盘，中心轴孔直径 20mm；在轴孔与外缘之间均布 4 个扇形减重孔，"
        "每个减重孔是内半径 20mm、外半径 50mm、张角 60° 的扇环，全部贯穿。",
        (10.0, 120.0, 120.0),
        1.5,
        # pi*60^2*10 - pi*10^2*10 - 4*(60/360)*pi*(50^2-20^2)*10 = 65973.4
        (65000.0, 67000.0),
        (("solid_features", 1),),
        split="heldout",
    ),
    TextCase(
        "end_cap",
        "端盖",
        "创建圆形端盖：外径 60mm、总高 15mm；从底面向上挖出直径 50mm、深 12mm 的内腔，使顶板厚 3mm；顶板中心打一个直径 8mm 的通孔。",
        (15.0, 60.0, 60.0),
        1.0,
        # pi*30^2*15 - pi*25^2*12 - pi*4^2*3 = 18698.8
        (18450.0, 18950.0),
        (("solid_features", 1),),
        split="heldout",
    ),
    TextCase(
        "link_bar",
        "连杆",
        "创建厚 6mm 的连杆：两端是直径 24mm 的圆头，两圆心相距 80mm，中间用宽 14mm 的杆身连接，整体是一个外轮廓；"
        "两个圆心处各有一个直径 10mm 的贯穿孔。",
        (6.0, 24.0, 104.0),
        1.0,
        # (2*pi*12^2 + 80*14 - 2*∫_{-7}^{7}sqrt(144-y^2)dy - 2*pi*5^2)*6 = 9310.9
        (9150.0, 9450.0),
        (("solid_features", 1),),
        split="heldout",
    ),
)

# 复杂零件：demo 为展示用自拟零件（受 CAD-1K 零件族启发，尺寸与描述均为自写）；
# heldout2 为同族新测例，GT 均在系统首次运行前由 eval/gt_complex_cases.py 计算写定。
COMPLEX_CASES = (
    TextCase(
        "fork_end",
        "叉形接头",
        "创建叉形接头，所有结构合并为单一实体。杆部为直径 20mm、长 35mm 的圆柱；"
        "杆部顶端连接一个 40×24mm、高 10mm 的叉根方块，杆部轴线穿过方块中心。"
        "叉根顶面伸出两片相同的叉耳：每片厚 8mm，外侧面与叉根 40mm 方向的两个端面齐平（两耳间开口 24mm），"
        "耳宽 24mm 与叉根等宽，耳端为 R12mm 半圆，半圆圆心距叉根顶面 18mm。"
        "在半圆圆心处沿厚度方向开一个直径 10mm 的横销孔，贯穿两片叉耳；"
        "杆部底端面中心有直径 6.8mm、深 20mm 的螺纹底孔（不做螺纹）。",
        (24.0, 40.0, 75.0),
        1.0,
        # gt_complex_cases.fork_end = 29143.7
        (28700.0, 29600.0),
        (("solid_features", 2),),
        split="demo",
    ),
    TextCase(
        "three_lug_housing",
        "三耳法兰轴承座",
        "创建三耳法兰轴承座。法兰厚 10mm，外形为半径 32mm 的圆盘加 3 个沿圆周 120° 均布的径向安装耳："
        "每个耳宽 22mm，两侧边平行于径向，端部为与侧边相切的 R11mm 半圆，半圆圆心距中心 48mm，"
        "并在该圆心处开直径 9mm 的贯穿安装孔。法兰上方同轴设置直径 46mm 的轮毂，轮毂顶面距法兰底面 38mm。"
        "中心轴承孔直径 30mm 全长贯通，轮毂顶端再加工直径 38mm、深 9mm 的轴承座沉孔。",
        (38.0, 94.0, 105.138),
        1.5,
        # gt_complex_cases.three_lug_housing = 62774.2
        (61800.0, 63700.0),
        (("solid_features", 2),),
        split="demo",
    ),
    TextCase(
        "pillow_block",
        "立式轴承座",
        "创建立式轴承座，左右对称，所有结构合并为单一实体。底座 120×40mm、厚 12mm。"
        "底座上表面中央立起座体：座体沿底座 40mm 方向全宽，正视轮廓为宽 60mm 的矩形加顶部 R30mm 半圆，"
        "半圆圆心距底座底面 45mm。在该圆心处开直径 35mm 的轴承孔，沿 40mm 方向贯穿。"
        "底座两端各有一个沿 120mm 方向的安装长圆孔，宽 11mm、两端半圆圆心相距 10mm，"
        "长圆孔中心距座体中心 45mm，贯穿底座。",
        (40.0, 75.0, 120.0),
        1.5,
        # gt_complex_cases.pillow_block = 149943.0
        (147700.0, 152200.0),
        (("solid_features", 2),),
        split="demo",
    ),
    TextCase(
        "twin_arm_rocker",
        "双臂摇臂",
        "创建双臂摇臂，所有结构合并为单一实体。轮毂为直径 30mm、长 40mm 的圆柱，中心有直径 12mm 的贯穿轴孔。"
        "两片相同的摇臂板垂直于轮毂轴线，厚度均为 8mm，外侧面分别与轮毂两个端面齐平；"
        "摇臂板外形为以轮毂轴线为圆心的 R15mm 圆弧和以距轴线 70mm 处为圆心的 R10mm 圆弧，两圆弧由两条公切直线连接。"
        "在 R10mm 圆弧圆心处开直径 8mm 的销孔，贯穿两片摇臂板。",
        (30.0, 40.0, 95.0),
        1.0,
        # gt_complex_cases.twin_arm_rocker = 47876.0
        (47150.0, 48600.0),
        (("solid_features", 2),),
        split="demo",
    ),
    TextCase(
        "clevis_bracket",
        "双耳支座",
        "创建双耳支座。底板 80×50mm、厚 10mm，四个直径 9mm 的贯穿安装孔，孔心相对底板中心位于 (±30mm, ±17mm)，"
        "其中 ±30mm 沿 80mm 方向。底板上表面立起两片相同的耳板：每片厚 10mm（沿 80mm 方向），宽 30mm 与底板 50mm 方向居中，"
        "两耳内侧面相距 30mm 且关于底板中心对称；耳板顶端为 R15mm 半圆，圆心距底板上表面 25mm。"
        "在该圆心处开直径 12mm 的销孔，沿 80mm 方向贯穿两片耳板。",
        (50.0, 50.0, 80.0),
        1.0,
        # gt_complex_cases.clevis_bracket = 57261.9
        (56400.0, 58100.0),
        (("solid_features", 2),),
        split="heldout2",
    ),
    TextCase(
        "diamond_flange",
        "菱形法兰座",
        "创建菱形法兰座。法兰板厚 8mm，外形由中心 R25mm 圆弧与两侧 R12mm 圆弧（圆心分别距中心 42mm，三个圆心共线）"
        "通过公切直线连接而成的凸轮廓；两个 R12mm 圆弧圆心处各开直径 10mm 的贯穿孔。"
        "法兰板上方同轴设置直径 34mm 的凸台，凸台顶面距法兰底面 26mm；中心直径 20mm 的孔全长贯通。",
        (26.0, 50.0, 108.0),
        1.0,
        # gt_complex_cases.diamond_flange = 36601.7
        (36050.0, 37150.0),
        (("solid_features", 2),),
        split="heldout2",
    ),
    TextCase(
        "support_seat",
        "轴支撑座",
        "创建轴支撑座，所有结构合并为单一实体。底座 100×50mm、厚 10mm；底座上表面中央立起厚 20mm 的立板，"
        "立板在底座 50mm 方向居中，正视轮廓为宽 50mm 的矩形加顶部 R25mm 半圆，半圆圆心距底座底面 40mm；"
        "在该圆心处开直径 24mm 的轴孔，沿立板厚度方向贯穿。"
        "底座上在 100mm 方向中线上、距中心 38mm 处左右各开一个直径 10mm 的贯穿孔。",
        (50.0, 65.0, 100.0),
        1.0,
        # gt_complex_cases.support_seat = 89016.2
        (87700.0, 90350.0),
        (("solid_features", 2),),
        split="heldout2",
    ),
    TextCase(
        "crank",
        "曲柄",
        "创建曲柄，所有结构合并为单一实体。轮毂为直径 36mm、长 24mm 的圆柱，中心有直径 16mm 的贯穿轴孔。"
        "曲柄臂垂直于轮毂轴线，厚 10mm，一个侧面与轮毂一个端面齐平；臂外形为以轮毂轴线为圆心的 R18mm 圆弧"
        "和以距轴线 60mm 处为圆心的 R11mm 圆弧，两圆弧由公切直线连接。"
        "在 R11mm 圆弧圆心处、臂与轮毂齐平的那一侧，凸起直径 22mm、高 15mm 的曲柄销座；"
        "销座中心开直径 10mm 的孔，贯穿销座和曲柄臂。",
        (36.0, 39.0, 89.0),
        1.0,
        # gt_complex_cases.crank = 37671.9
        (37100.0, 38250.0),
        (("solid_features", 2),),
        split="heldout2",
    ),
)

# 新工具测例：demo3 展示用，heldout3 同族新题。GT 由 eval/gt_advanced_cases.py 在首次运行前写定。
ADVANCED_CASES = (
    TextCase(
        "gusset_bracket",
        "带筋角码",
        "创建带筋角码，单一实体。水平底板 60×40mm、厚 6mm；在 60mm 方向的一端立起 40mm 宽、高 30mm、厚 6mm 的立板，"
        "立板外侧面与底板该端面齐平。在底板与立板的内侧拐角加一块厚 4mm 的直角筋，筋的两条直角边分别沿底板和立板各 18mm，"
        "筋位于 40mm 方向的对称中面。",
        (36.0, 40.0, 60.0),
        2.0,
        # gt_advanced_cases.gusset_bracket = 22248（筋为三角近似）
        (21000.0, 23500.0),
        (("solid_features", 2),),
        split="demo3",
    ),
    TextCase(
        "flanged_elbow",
        "法兰弯管",
        "创建带法兰的 90° 弯管。中心线是半径 40mm 的四分之一圆弧；管子外径 16mm、实心。"
        "两端各有外径 36mm、厚 4mm 的圆形法兰，法兰厚度沿管轴线向外凸出，中心孔与管子相贯。",
        (36.0, 58.0, 58.0),
        4.0,
        # gt_advanced_cases.flanged_elbow = 19167.6
        (17500.0, 21000.0),
        (("sweeps", 1),),
        split="demo3",
    ),
    TextCase(
        "open_box",
        "开口盒",
        "创建顶部敞开的盒体：外部 80×50×40mm，壁厚和底厚均为 4mm，用抽壳完成。",
        (40.0, 50.0, 80.0),
        1.5,
        # gt_advanced_cases.open_box = 51136
        (50000.0, 52500.0),
        (("shells", 1),),
        split="demo3",
    ),
    TextCase(
        "counterbore_plate",
        "沉头孔安装板",
        "创建 80×60×10mm 的安装板，四个沉头孔：通孔直径 6mm，沉头直径 11mm、深 3.5mm；"
        "孔心距相邻两边各 14mm（即相对板中心位于 (±26mm, ±16mm)）。沉头在板的上表面。",
        (10.0, 60.0, 80.0),
        1.0,
        # gt_advanced_cases.counterbore_plate = 45934.4
        (45200.0, 46700.0),
        (("cuts", 2),),
        split="demo3",
    ),
    TextCase(
        "side_pad",
        "侧凸台块",
        "创建 50×40×30mm 的长方体（50 沿 X，30 沿 Y 为高，40 沿 Z），底面在 Y=0。"
        "在右端面外侧 20mm 处的平行平面上画直径 16mm 的圆，向实体拉伸直到贴上右端面，形成侧凸台。",
        (30.0, 40.0, 70.0),
        1.5,
        # gt_advanced_cases.side_pad = 64021.2
        (63000.0, 65100.0),
        (("solid_features", 2),),
        split="demo3",
    ),
    TextCase(
        "sheet_angle",
        "钣金平板",
        "创建钣金平板：轮廓 70×40mm，板厚 1.5mm。",
        (1.5, 40.0, 70.0),
        0.4,
        # gt_advanced_cases.sheet_angle = 4200
        (4100.0, 4300.0),
        (("sheet_metal", 1),),
        split="demo3",
    ),
    TextCase(
        "square_to_round",
        "方圆过渡接管",
        "创建一个高 40mm 的过渡接管：底面是边长 30mm 的正方形，顶面是直径 24mm 的圆，两截面平行且中心同轴，用放样连接。",
        (30.0, 30.0, 40.0),
        2.0,
        # gt_advanced_cases.square_to_round = 27047.8（平均截面近似）
        (24000.0, 31000.0),
        (("lofts", 1),),
        split="demo3",
    ),
    TextCase(
        "corner_rib_stand",
        "带筋支架",
        "创建带筋支架，单一实体。水平底板 80×50mm、厚 8mm；在 80mm 方向的一端立起 50mm 宽、高 36mm、厚 8mm 的立板，"
        "立板外侧面与底板该端面齐平。在底板与立板的内侧拐角加一块厚 5mm 的直角筋，筋的两条直角边分别沿底板和立板各 24mm，"
        "筋位于 50mm 方向的对称中面。",
        (44.0, 50.0, 80.0),
        2.0,
        # gt_advanced_cases.corner_rib_stand = 47840
        (45500.0, 50500.0),
        (("solid_features", 2),),
        split="heldout3",
    ),
    TextCase(
        "bent_tube",
        "折弯接管",
        "创建带法兰的 90° 弯管。中心线是半径 50mm 的四分之一圆弧；管子外径 20mm、实心。"
        "两端各有外径 44mm、厚 5mm 的圆形法兰，法兰厚度沿管轴线向外凸出。",
        (44.0, 72.0, 72.0),
        5.0,
        # gt_advanced_cases.bent_tube = 36737.7
        (33500.0, 40000.0),
        (("sweeps", 1),),
        split="heldout3",
    ),
    TextCase(
        "lidless_housing",
        "敞口壳体",
        "创建顶部敞开的壳体：外部 100×60×50mm，壁厚和底厚均为 5mm，用抽壳完成。",
        (50.0, 60.0, 100.0),
        1.5,
        # gt_advanced_cases.lidless_housing = 102000
        (100000.0, 104000.0),
        (("shells", 1),),
        split="heldout3",
    ),
    TextCase(
        "csk_cover",
        "沉头盖板",
        "创建 90×70×12mm 的盖板，四个沉头孔：通孔直径 8mm，沉头直径 14mm、深 4mm；"
        "孔心距相邻两边各 15mm。沉头在板的上表面。",
        (12.0, 70.0, 90.0),
        1.0,
        # gt_advanced_cases.csk_cover = 71528.5
        (70500.0, 72600.0),
        (("cuts", 2),),
        split="heldout3",
    ),
    TextCase(
        "through_boss",
        "侧凸台座",
        "创建 60×48×36mm 的长方体（60 沿 X，36 沿 Y 为高，48 沿 Z），底面在 Y=0。"
        "在右端面外侧 24mm 处的平行平面上画直径 20mm 的圆，向实体拉伸直到贴上右端面。",
        (36.0, 48.0, 84.0),
        1.5,
        # gt_advanced_cases.through_boss = 111219.8
        (109500.0, 113000.0),
        (("solid_features", 2),),
        split="heldout3",
    ),
    TextCase(
        "sheet_channel",
        "钣金条",
        "创建钣金平板：轮廓 90×50mm，板厚 1.2mm。",
        (1.2, 50.0, 90.0),
        0.3,
        # gt_advanced_cases.sheet_channel = 5400
        (5280.0, 5520.0),
        (("sheet_metal", 1),),
        split="heldout3",
    ),
    TextCase(
        "rect_to_circle",
        "矩形圆过渡管",
        "创建一个高 50mm 的过渡管：底面是 40×24mm 的矩形，顶面是直径 28mm 的圆，两截面平行且中心同轴，用放样连接。",
        (24.0, 40.0, 50.0),
        2.5,
        # gt_advanced_cases.rect_to_circle = 39393.8
        (35000.0, 45000.0),
        (("lofts", 1),),
        split="heldout3",
    ),
)
# 第 50 题（日常物品），题面与 rubric 在首次运行前写定。
EXTRA_CASES = (
    TextCase(
        "soap_dish",
        "排水肥皂盒",
        "创建一个顶部敞开的长方形肥皂盒，单一实体。外部尺寸 110×75×25mm（高 25mm），侧壁厚 3mm，底厚 3mm；"
        "外侧四条竖直棱边做 R8mm 圆角。底板上开 15 个直径 5mm 的贯穿排水孔，排成 3 行 × 5 列："
        "沿 110mm 方向 5 列、列距 18mm，沿 75mm 方向 3 行、行距 18mm，孔阵中心与底面中心重合。",
        (25.0, 75.0, 110.0),
        1.5,
        # rubric soap_dish：内角尖角 46120.9，内角 R5 46593.0
        (45650.0, 47050.0),
        (("cuts", 2), ("fillets", 1)),
        split="heldout4",
    ),
)
# 复杂综合件（showcase）8 题，rubric 在 eval/rubrics_hard；题面与 rubric 在首次运行前写定。
HARD_CASES = (
    TextCase(
        "quad_frame",
        "四旋翼机架",
        "创建一块 X 型四旋翼无人机机架底板，单一实体，整块板厚 4mm。中心板是边长 60mm 的正方形， 中心开直径 12m"
        "m 的走线孔，并有 4 个直径 3.5mm 的飞控安装孔，孔心位于以板中心为中心、边长 30.5mm、 各边与中心板边平"
        "行的正方形四角。四根机臂沿中心板两条对角线方向伸出，机臂宽 16mm、中线通过板中心； 每根机臂末端是直径 32mm 的"
        "圆形电机座，电机座圆心距板中心 110mm，机臂的矩形部分一直延伸到电机座圆心。 每个电机座中心开直径 8mm 的轴孔，"
        "另有 4 个直径 3.2mm 的电机安装孔，分别位于电机座圆心沿中心板两边方向 （即平行于中心板各边的 4 个方向）偏移"
        " 9.5mm 处。每根机臂中线上开一个宽 6mm 的减重长圆孔， 两端半圆圆心距板中心分别为 45mm 和 75mm。机"
        "臂侧边与中心板边相交形成的 8 个内角各倒 R5mm 圆角（沿板厚方向的棱边）。",
        (4.0, 252.0, 252.0),
        2.0,
        # rubric quad_frame：参考实体 36432.1
        (35340.0, 37530.0),
        (("solid_features", 1),),
        split="hard",
    ),
    TextCase(
        "connecting_rod",
        "发动机连杆",
        "创建发动机连杆，单一实体，整体厚 20mm。大头是外径 56mm 的圆柱凸台，中心开直径 36mm 的贯穿轴承孔； 小头"
        "是外径 30mm 的圆柱凸台，中心开直径 20mm 的贯穿活塞销孔；大、小头孔中心距 140mm。 杆身外轮廓由大、小头"
        "外圆的两条外公切线构成。杆身正反两面各铣一个深 6mm 的梯形减重槽，使杆身中间腹板厚 8mm： 梯形关于连杆中线对称，"
        "两条底边都垂直于中线，靠大头的一边距大头孔心 40mm、长 24mm， 靠小头的一边距大头孔心 110mm、长 14mm"
        "，槽角不倒圆。小头顶端沿连杆中线方向钻一个直径 4mm 的油孔， 从小头外圆一直钻通到活塞销孔。大头轴承孔两端孔口各倒 "
        "1mm×45° 倒角。",
        (20.0, 56.0, 182.4),
        2.0,
        # rubric connecting_rod：参考实体 109839.2
        (106540.0, 113130.0),
        (("solid_features", 1),),
        split="hard",
    ),
    TextCase(
        "finned_housing",
        "散热电机壳",
        "创建带散热鳍片的电机外壳，单一实体。壳体是外径 80mm、内径 70mm 的圆筒，底部封闭、底板厚 8mm， 筒体从底面"
        "到顶面总高 100mm，顶端敞开。顶端有一块 100×100mm、厚 8mm 的方形安装法兰，法兰上表面与筒顶齐平， 法"
        "兰中间是与筒内径相同的直径 70mm 通孔；法兰四角倒 R10mm 圆角（沿厚度方向的棱边）， 并在孔心坐标 (±42,"
        " ±42)mm（以法兰中心为原点、坐标轴平行于法兰各边）处开 4 个直径 7mm 的贯穿安装孔。 筒体外壁有 12 片沿"
        "圆周均布的纵向散热鳍片，每片厚 3mm，从筒体外壁径向伸出到距轴线 50mm 处， 沿轴向从筒底面一直延伸到法兰下表面；"
        "其中一片鳍片的中面正对法兰一条边的中点。 底板中心开直径 22mm 的轴承孔，并在直径 40mm 的圆上开 4 个直径 "
        "4.5mm 的贯穿螺钉孔， 这 4 个孔分别与 4 个法兰安装孔位于同一方位。",
        (100.0, 100.0, 100.0),
        2.0,
        # rubric finned_housing：参考实体 216067.8
        (209590.0, 222550.0),
        (("solid_features", 1),),
        split="hard",
    ),
    TextCase(
        "sprocket",
        "滚子链链轮",
        "创建一个 18 齿滚子链链轮（节距 12.7mm），单一实体。轮盘厚 7mm、毛坯外径 80mm； 在直径 73.14m"
        "m 的节圆上均布 18 个直径 8.6mm 的齿槽圆，贯穿轮盘厚度切除，相邻齿槽之间剩下的部分就是轮齿。 轮盘一侧同轴伸"
        "出外径 40mm 的轮毂，轮毂端面距轮盘另一侧面 20mm。中心孔直径 20mm 全长贯通， 孔壁上有一条宽 6mm 的"
        "通长键槽，键槽底面距孔中心 12.8mm。轮盘上在直径 52mm 的圆上均布 6 个直径 9mm 的减重孔， 其中一个减"
        "重孔与键槽位于同一方位，并且这个方位上正好也有一个齿槽。 在轮毂上正对键槽、距轮毂端面 6.5mm 处沿径向钻一个直径 "
        "4.2mm 的紧定螺钉底孔，从轮毂外圆钻通到中心孔。",
        (20.0, 79.8, 80.0),
        2.0,
        # rubric sprocket：参考实体 35201.4
        (34150.0, 36260.0),
        (("solid_features", 1),),
        split="hard",
    ),
    TextCase(
        "handwheel",
        "摇把手轮",
        "创建一个带摇把的五辐手轮，单一实体。轮缘是圆截面圆环：截面直径 16mm，截面圆心所在圆直径 184mm（轮缘外径 20"
        "0mm）。 中心轮毂是外径 40mm、长 36mm 的圆柱，与轮缘同轴，轮毂长度方向的中点位于轮缘中面上； 轮毂中心开一"
        "个对边距 17mm 的正六边形贯穿孔。5 根直径 12mm 的圆柱形轮辐位于轮缘中面内， 沿径向从轮毂一直连到轮缘，沿圆"
        "周均布。在第一根轮辐的延长线上、距手轮中心 92mm 处装一根摇把： 摇把是直径 20mm 的圆柱，轴线平行于手轮轴线，"
        "从轮缘中面起向一侧伸出，末端为 R10mm 的半球， 摇把最高点距轮缘中面 70mm。",
        (88.0, 200.0, 202.0),
        2.0,
        # rubric handwheel：参考实体 208078.7
        (201840.0, 214320.0),
        (("solid_features", 1),),
        split="hard",
    ),
    TextCase(
        "camshaft",
        "四缸凸轮轴",
        "创建一根四缸发动机凸轮轴，单一实体，所有回转结构同轴。以左端面为 0、沿轴线向右量： 主轴直径 20mm，从 0 贯穿到"
        " 220mm；左轴颈直径 30mm、位于 0~16mm；中间轴颈直径 30mm、位于 102~118mm； 右轴颈直径 "
        "30mm、位于 204~220mm；右端再伸出直径 18mm 的驱动轴头，位于 220~245mm， 轴头上有一条宽 5"
        "mm 的通长平键槽，槽底平面距轴线 6mm。四个凸轮宽 14mm，宽度中心分别在 40、78、142、180mm 处； "
        "每个凸轮的轮廓由直径 32mm 的基圆（与轴同心）、直径 16mm 的桃尖圆（圆心距轴线 12mm） 以及两圆的两条外公"
        "切线组成。左轴颈端面上开一个直径 5mm、深 8mm 的定位销孔，孔心距轴线 9mm。 第 1 个凸轮的桃尖朝向定位销孔"
        "所在方向；从左端沿轴线向右看，第 2、3、4 个凸轮的桃尖依次比前一个顺时针转过 90°。 驱动轴头的键槽开在定位销孔所"
        "在的一侧。",
        (33.9, 33.9, 245.0),
        2.0,
        # rubric camshaft：参考实体 124231.8
        (120500.0, 127960.0),
        (("solid_features", 1),),
        split="hard",
    ),
    TextCase(
        "planet_carrier",
        "行星齿轮架",
        "创建一个行星齿轮架，单一实体。上下两块同轴圆盘，直径 120mm、厚 8mm，两盘之间净距 20mm。 两盘之间在直径 "
        "90mm 的圆上均布 3 根直径 18mm 的圆柱连接柱，每根连接柱中心开一个直径 6.6mm 的螺栓孔， 贯穿两块圆盘"
        "和连接柱。在同一个直径 90mm 的圆上、每两根相邻连接柱的正中间，开 3 个直径 12mm 的行星轮轴孔， 贯穿两块圆"
        "盘。上盘中心开直径 40mm 的太阳轮通孔。下盘下表面中心同轴伸出直径 30mm、长 40mm 的输出轴， 轴端中心有一"
        "个 M10 螺纹底孔：直径 8.5mm、深 20mm。两块圆盘外圆的上下棱边都倒 1mm×45° 倒角（共 4 条圆边）"
        "。",
        (76.0, 120.0, 120.0),
        2.0,
        # rubric planet_carrier：参考实体 203436.9
        (197330.0, 209540.0),
        (("solid_features", 1),),
        split="hard",
    ),
    TextCase(
        "impeller",
        "离心叶轮",
        "创建一个开式离心叶轮，单一实体。后盘是直径 120mm、厚 6mm 的圆盘；轮毂是外径 36mm 的圆柱， 从后盘底面算"
        "起总高 30mm，顶端外圆棱边倒 1mm×45° 倒角；中心孔直径 12mm 贯穿。 后盘上表面有 8 片沿圆周均布的后"
        "弯叶片，叶片高 20mm（从后盘上表面算起），厚 3mm（中线两侧各 1.5mm）。 从叶片一侧俯视后盘，以轴线为原点建"
        "立平面直角坐标系（X 向右、Y 向上），第 1 片叶片的中线是一段圆弧： 圆心在 (18, −40)mm、半径 40mm"
        "，从轮毂外圆上的点 (18, 0) 开始（此处中线沿径向）， 顺时针方向弯向外侧，一直到半径 58mm 处截止（叶片外端"
        "面形状不限）；叶片内端伸入轮毂并与轮毂合并。 其余 7 片由第 1 片绕轴线每隔 45° 阵列得到。",
        (30.0, 120.0, 120.0),
        2.0,
        # rubric impeller：参考实体 110009.5
        (106710.0, 113310.0),
        (("solid_features", 1),),
        split="hard",
    ),
)
CASES = CASES + HELDOUT_CASES + COMPLEX_CASES + ADVANCED_CASES + EXTRA_CASES + HARD_CASES


HISTORICAL_CASES = (
    {
        "case_id": "phone_stand",
        "name": "一体式手机支架",
        "score": 100.0,
        "status": "completed",
        "source": "PERFORMANCE.md 2026-07-21 final Single-turn rerun",
        "cold_tokens": 19945,
    },
    {
        "case_id": "stool",
        "name": "四脚板凳",
        "score": 80.0,
        "status": "completed",
        "source": "eval/text_first5_result.json 16/20 legacy score",
    },
    {
        "case_id": "rounded_stool",
        "name": "圆角板凳",
        "score": 90.0,
        "status": "completed",
        "source": "eval/text_first5_result.json 18/20 legacy score",
    },
    {
        "case_id": "storage_box",
        "name": "收纳盒",
        "score": 90.0,
        "status": "completed",
        "source": "eval/text_first5_result.json 18/20 legacy score",
    },
    {
        "case_id": "standard_die",
        "name": "标准骰子",
        "score": 100.0,
        "status": "completed",
        "source": "PERFORMANCE.md 2026-07-16 accepted Single-turn rerun",
    },
)


def _post_json(url: str, payload: dict, timeout: int) -> dict:
    response = requests.post(url, json=payload, timeout=timeout)
    try:
        body = response.json()
    except ValueError:
        body = {"error": response.text[:500]}
    if not response.ok or not body.get("success"):
        raise RuntimeError(body.get("error") or f"HTTP {response.status_code}")
    return body


def _close_documents(sw_url: str) -> dict:
    return _post_json(
        f"{sw_url}/api/close-all-documents",
        {},
        timeout=60,
    )


def _save_artifacts(sw_url: str, stem: Path | None) -> dict:
    """Keep the native part and a STEP copy so later metrics need no re-modeling."""
    if stem is None:
        return {}
    stem.parent.mkdir(parents=True, exist_ok=True)
    saved: dict[str, Any] = {}
    for key, route, field, suffix in (
        ("step", "/api/export-step", "stepPath", ".step"),
        ("sldprt", "/api/save-part-as", "savePath", ".SLDPRT"),
    ):
        path = stem.with_suffix(suffix).resolve()
        try:
            _post_json(f"{sw_url}{route}", {field: str(path)}, timeout=120)
            saved[key] = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        except Exception as exc:
            saved[f"{key}_error"] = str(exc)
    return saved


def _observation(sw_url: str) -> dict:
    observer = SwObserver(base_url=sw_url)
    try:
        return observer.collect().model_dump(mode="json")
    finally:
        observer.close()


def _feature_metrics(
    observation: dict,
    successful_operations: list[str],
) -> dict[str, int]:
    links = observation.get("feature_sketch_links") or []
    features = observation.get("features") or []
    sketches = {
        item.get("name"): item
        for item in observation.get("sketches") or []
        if isinstance(item, dict)
    }
    cut_links = [
        item
        for item in links
        if str(item.get("operationKind", "")).lower() == "cut"
    ]
    operation_counts = {
        name: successful_operations.count(name)
        for name in set(successful_operations)
    }
    return {
        "extrusions": sum(
            str(item.get("operationKind", "")).lower() == "extrusion"
            for item in links
        ),
        "cuts": len(cut_links),
        "fillets": sum(
            "fillet" in str(item.get("type", "")).lower()
            or "圆角" in str(item.get("name", ""))
            for item in features
        ),
        "chamfers": sum(
            "chamfer" in str(item.get("type", "")).lower()
            or "倒角" in str(item.get("name", ""))
            for item in features
        ),
        "revolves": operation_counts.get("feature_revolve", 0),
        "polygons": operation_counts.get("sketch_polygon", 0),
        "centerlines": operation_counts.get("sketch_centerline", 0),
        "circles": sum(
            int(item.get("circleCount") or 0)
            for item in sketches.values()
        ),
        "slots": sum(
            int(item.get("slotCount") or 0)
            for item in sketches.values()
        ),
        "cut_circles": sum(
            int(sketches.get(item.get("sketchName"), {}).get(
                "circleCount",
                0,
            ))
            for item in cut_links
        ),
        "faces": len(observation.get("faces") or []),
    }


def _dimension_score(
    actual: list[float],
    expected: tuple[float, float, float],
    tolerance: float,
) -> tuple[float, list[float], list[bool]]:
    actual_sorted = sorted(actual)
    expected_sorted = sorted(expected)
    errors = [
        abs(value - target)
        for value, target in zip(actual_sorted, expected_sorted)
    ]
    if not any(value > 0 for value in actual_sorted):
        return 0.0, [round(value, 3) for value in errors], [False] * 3
    passed = [error <= tolerance for error in errors]
    points = [
        10.0
        if ok
        else 10.0 * max(0.0, 1.0 - (error - tolerance) / (2 * tolerance))
        for error, ok in zip(errors, passed)
    ]
    return (
        round(sum(points), 2),
        [round(value, 3) for value in errors],
        passed,
    )


def _volume_score(
    actual: float,
    expected_range: tuple[float, float],
) -> tuple[float, bool]:
    low, high = expected_range
    if low <= actual <= high:
        return 20.0, True
    if actual <= 0:
        return 0.0, False
    distance = low - actual if actual < low else actual - high
    return (
        round(20.0 * max(0.0, 1.0 - distance / (high - low)), 2),
        False,
    )


def score_case_v1(
    case: TextCase,
    payload: dict,
    observation: dict,
) -> dict:
    """旧版打分：执行分以模型自己的计划步数为分母，细节指标依赖服务端记录的元数据。"""
    plan_steps = (payload.get("plan") or {}).get("steps") or []
    results = payload.get("results") or []
    successful = [
        str(item.get("operation", ""))
        for item in results
        if item.get("success")
    ]
    success_count = len(successful)
    execution_rate = success_count / len(plan_steps) if plan_steps else 0.0
    all_ok = bool(plan_steps) and success_count == len(plan_steps)
    execution_points = round(
        20.0 * execution_rate + (5.0 if all_ok else 0.0),
        2,
    )

    bbox = observation.get("bounding_box_mm") or {}
    actual_dims = [
        float(bbox.get(key) or 0)
        for key in ("sizeX", "sizeY", "sizeZ")
    ]
    dimension_points, dimension_errors, dimension_checks = _dimension_score(
        actual_dims,
        case.expected_bbox_mm,
        case.bbox_tolerance_mm,
    )
    volume = float(observation.get("volume_mm3") or 0)
    volume_points, volume_ok = _volume_score(
        volume,
        case.volume_range_mm3,
    )

    metrics = _feature_metrics(observation, successful)
    detail_checks = {
        name: metrics.get(name, 0) >= minimum
        for name, minimum in case.expected_details
    }
    detail_points = round(
        25.0
        * sum(
            min(metrics.get(name, 0) / minimum, 1.0)
            for name, minimum in case.expected_details
        )
        / len(case.expected_details),
        2,
    )
    score = round(
        execution_points
        + dimension_points
        + volume_points
        + detail_points,
        2,
    )
    acceptance = (
        all_ok
        and all(dimension_checks)
        and volume_ok
        and all(detail_checks.values())
    )
    return {
        "score": score,
        "acceptance_ok": acceptance,
        "status": "completed" if all_ok else "failed",
        "plan_steps": len(plan_steps),
        "successful_steps": success_count,
        "execution_points": execution_points,
        "dimension_points": dimension_points,
        "volume_points": volume_points,
        "detail_points": detail_points,
        "bbox_mm": bbox or None,
        "expected_bbox_mm": list(case.expected_bbox_mm),
        "dimension_errors_mm": dimension_errors,
        "dimension_checks": dimension_checks,
        "volume_mm3": round(volume, 3) if volume else None,
        "expected_volume_range_mm3": list(case.volume_range_mm3),
        "volume_ok": volume_ok,
        "actual_details": metrics,
        "expected_details": dict(case.expected_details),
        "detail_checks": detail_checks,
        "failed_steps": [
            {
                "step": item.get("step"),
                "operation": item.get("operation"),
                "message": item.get("message"),
            }
            for item in results
            if not item.get("success")
        ],
    }


score_case = score_case_v1

FAILED_STEP_PENALTY = 2.5


def score_case_v2(
    case: TextCase,
    payload: dict,
    observation: dict,
    sketch_stats: list[dict],
    *,
    strict_orientation: bool = False,
) -> dict:
    """新版打分：只看最终模型（特征树 + 真实草图几何 + 包围盒 + 体积）。

    有效实体 15 + 执行稳健性 10 + 尺寸 30 + 体积 20 + 细节 25。
    执行稳健性按失败步骤数扣分，不再以模型自己的计划步数为分母。
    """
    results = payload.get("results") or []
    failed_steps = sum(not item.get("success") for item in results)

    bbox = observation.get("bounding_box_mm") or {}
    actual_dims = [float(bbox.get(key) or 0) for key in ("sizeX", "sizeY", "sizeZ")]
    volume = float(observation.get("volume_mm3") or 0)
    has_body = volume > 0 and all(value > 0 for value in actual_dims)
    validity_points = 15.0 if has_body else 0.0
    robustness_points = (
        max(0.0, 10.0 - FAILED_STEP_PENALTY * failed_steps) if has_body else 0.0
    )

    dimension_points, dimension_errors, dimension_checks = geo_metrics.dimension_score(
        actual_dims,
        case.expected_bbox_mm,
        case.bbox_tolerance_mm,
        strict_orientation=strict_orientation,
    )
    volume_points, volume_ok = geo_metrics.volume_score(volume, case.volume_range_mm3)

    metrics = geo_metrics.model_metrics(observation.get("features") or [], sketch_stats)
    detail_checks = {
        name: metrics.get(name, 0) >= minimum
        for name, minimum in case.expected_details
    }
    detail_points = round(
        25.0
        * sum(min(metrics.get(name, 0) / minimum, 1.0) for name, minimum in case.expected_details)
        / len(case.expected_details),
        2,
    )
    score = round(
        validity_points + robustness_points + dimension_points + volume_points + detail_points,
        2,
    )
    acceptance = (
        has_body
        and all(dimension_checks)
        and volume_ok
        and all(detail_checks.values())
    )
    return {
        "score": score,
        "acceptance_ok": acceptance,
        "validity_points": validity_points,
        "robustness_points": robustness_points,
        "failed_step_count": failed_steps,
        "dimension_points": dimension_points,
        "volume_points": volume_points,
        "detail_points": detail_points,
        "dimension_errors_mm": dimension_errors,
        "dimension_checks": dimension_checks,
        "volume_ok": volume_ok,
        "actual_details": metrics,
        "detail_checks": detail_checks,
        "strict_orientation": strict_orientation,
    }


def _sketch_stats(sw_url: str) -> list[dict]:
    response = requests.get(f"{sw_url}/api/sketch-stats", timeout=120)
    body = response.json()
    if not response.ok or not body.get("success"):
        raise RuntimeError(body.get("message") or f"HTTP {response.status_code}")
    return body.get("sketches") or []


def run_one(
    case: TextCase,
    *,
    web_url: str,
    sw_url: str,
    model: str,
    strict_orientation: bool = False,
    action_level: str = "L0",
    max_llm_calls: int = 40,
    artifact_stem: Path | None = None,
) -> dict:
    started = time.perf_counter()
    cleanup_before = _close_documents(sw_url)
    time.sleep(1.0)
    payload: dict[str, Any] = {}
    observation: dict[str, Any] = {}
    request_payload = {
        "instruction": case.prompt,
        "provider": "aiping",
        "vision_model": model,
        "plan_model": model,
        "mode": "single_turn",
        "action_level": action_level,
        "use_cache": False,
        "capture_trace": True,
        "dry_run": False,
    }
    if action_level == "L1":
        request_payload["max_turns"] = max_llm_calls
    trace = {
        "request": {
            "url": f"{web_url}/api/run",
            "json": request_payload,
        },
        "response_status": None,
        "response": None,
        "final_observation": None,
    }
    try:
        response = requests.post(
            f"{web_url}/api/run",
            json=request_payload,
            # 复杂件 40 次调用可能超过 15 分钟；超时后客户端会在服务端仍在建模时发起下一次运行
            timeout=3600,
        )
        trace["response_status"] = response.status_code
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": response.text[:500]}
        trace["response"] = payload
        if not response.ok or not payload.get("success"):
            raise RuntimeError(
                payload.get("error") or f"HTTP {response.status_code}"
            )
        observation = _observation(sw_url)
        trace["final_observation"] = observation
        sketch_stats = _sketch_stats(sw_url)
        trace["sketch_stats"] = sketch_stats
        artifacts = _save_artifacts(sw_url, artifact_stem)
        scoring_v1 = score_case_v1(case, payload, observation)
        scoring = score_case_v2(
            case,
            payload,
            observation,
            sketch_stats,
            strict_orientation=strict_orientation,
        )
        return {
            "case_id": case.case_id,
            "name": case.name,
            **scoring,
            "status": scoring_v1["status"],
            "plan_steps": scoring_v1["plan_steps"],
            "successful_steps": scoring_v1["successful_steps"],
            "failed_steps": scoring_v1["failed_steps"],
            "bbox_mm": scoring_v1["bbox_mm"],
            "volume_mm3": scoring_v1["volume_mm3"],
            "v1": {
                key: scoring_v1[key]
                for key in (
                    "score",
                    "acceptance_ok",
                    "execution_points",
                    "dimension_points",
                    "volume_points",
                    "detail_points",
                    "actual_details",
                )
            },
            "token_usage": payload.get("token_usage"),
            "workflow_status": (payload.get("summary") or {}).get("status"),
            "llm_calls": (payload.get("summary") or {}).get("llm_calls"),
            "tool_calls": (payload.get("summary") or {}).get("tool_calls"),
            "duration_seconds": round(time.perf_counter() - started, 2),
            "cleanup_before": cleanup_before,
            "error": None,
            "artifacts": artifacts,
            "trace": trace,
        }
    except Exception as exc:
        if not observation:
            try:
                observation = _observation(sw_url)
            except Exception:
                observation = {}
        trace["final_observation"] = observation or None
        try:
            artifacts = _save_artifacts(sw_url, artifact_stem) if observation.get("volume_mm3") else {}
        except Exception:
            artifacts = {}
        return {
            "case_id": case.case_id,
            "name": case.name,
            "score": 0.0,
            "acceptance_ok": False,
            "v1": {"score": 0.0, "acceptance_ok": False},
            "status": "error",
            "token_usage": payload.get("token_usage"),
            "duration_seconds": round(time.perf_counter() - started, 2),
            "cleanup_before": cleanup_before,
            "error": str(exc),
            "artifacts": artifacts,
            "trace": trace,
        }
    finally:
        try:
            _close_documents(sw_url)
        except Exception:
            pass


def _per_case_summary(results: list[dict], repeats: int) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for item in results:
        grouped.setdefault(item["case_id"], []).append(item)
    summary = []
    for case_id, runs in grouped.items():
        scores = [float(run["score"]) for run in runs]
        passed = sum(bool(run.get("acceptance_ok")) for run in runs)
        summary.append({
            "case_id": case_id,
            "runs": len(runs),
            "mean_score": round(sum(scores) / len(scores), 2),
            "min_score": min(scores),
            "max_score": max(scores),
            "passed_runs": passed,
            "pass_at_1": round(geo_metrics.pass_at_k(len(runs), passed, 1), 4),
            "pass_at_k": round(geo_metrics.pass_at_k(len(runs), passed, repeats), 4),
            "mean_tokens": round(
                sum(int((run.get("token_usage") or {}).get("total_tokens") or 0) for run in runs)
                / len(runs)
            ),
        })
    return summary


def _write_report(
    output: Path,
    results: list[dict],
    balance_before: dict | None,
    balance_after: dict | None = None,
    *,
    model: str = MODEL,
    selected_cases: list[TextCase] | None = None,
    historical_skipped: list[dict] | None = None,
    repeats: int = 1,
    strict_orientation: bool = False,
    action_level: str = "L0",
) -> dict:
    spent_yuan = None
    if balance_before and balance_after:
        spent_yuan = round(
            float(balance_after["consumed_yuan"])
            - float(balance_before["consumed_yuan"]),
            6,
        )
    per_case = _per_case_summary(results, repeats)
    case_means = [item["mean_score"] for item in per_case]
    report = {
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider": "AI Ping",
        "model": model,
        "mode": "workflow" if action_level == "L1" else "single_turn",
        "action_level": action_level,
        "score_version": 2,
        "strict_orientation": strict_orientation,
        "repeats": repeats,
        "cache_enabled": False,
        "historical_skipped": historical_skipped or [],
        "case_ids": [
            case.case_id
            for case in (selected_cases or list(CASES))
        ],
        "average_score": round(
            sum(float(item["score"]) for item in results) / len(results),
            2,
        ) if results else 0.0,
        "average_score_v1": round(
            sum(float((item.get("v1") or {}).get("score") or 0) for item in results)
            / len(results),
            2,
        ) if results else 0.0,
        "average_score_ci95": geo_metrics.bootstrap_ci(case_means),
        "pass_at_1": round(
            sum(item["pass_at_1"] for item in per_case) / len(per_case), 4
        ) if per_case else 0.0,
        f"pass_at_{repeats}": round(
            sum(item["pass_at_k"] for item in per_case) / len(per_case), 4
        ) if per_case else 0.0,
        "passed": sum(
            bool(item.get("acceptance_ok")) for item in results
        ),
        "passed_v1": sum(
            bool((item.get("v1") or {}).get("acceptance_ok")) for item in results
        ),
        "total": len(results),
        "total_duration_seconds": round(
            sum(float(item.get("duration_seconds") or 0) for item in results), 2
        ),
        "per_case": per_case,
        "token_totals": {
            key: sum(
                int((item.get("token_usage") or {}).get(key) or 0)
                for item in results
            )
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "calls",
            )
        },
        "balance_before": balance_before,
        "balance_after": balance_after,
        "spent_yuan": spent_yuan,
        "results": results,
    }
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def rescore_report(source: Path, output: Path, strict_orientation: bool = False) -> dict:
    """用结果文件里保存的 trace 重新计算 v2 分数，便于打分规则修订后复现。"""
    old = json.loads(source.read_text(encoding="utf-8"))
    cases = {case.case_id: case for case in CASES}
    results = []
    for item in old["results"]:
        trace = item.get("trace") or {}
        observation = trace.get("final_observation")
        if item.get("error") or not observation or "sketch_stats" not in trace:
            results.append(item)
            continue
        scoring = score_case_v2(
            cases[item["case_id"]],
            trace.get("response") or {},
            observation,
            trace["sketch_stats"],
            strict_orientation=strict_orientation,
        )
        results.append({**item, **scoring})
    selected = [cases[case_id] for case_id in old.get("case_ids") or [] if case_id in cases]
    report = _write_report(
        output,
        results,
        old.get("balance_before"),
        old.get("balance_after"),
        model=old.get("model", MODEL),
        selected_cases=selected or None,
        historical_skipped=old.get("historical_skipped"),
        repeats=int(old.get("repeats") or 1),
        strict_orientation=strict_orientation,
        action_level=old.get("action_level", "L0"),
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-url", default="http://localhost:5000")
    parser.add_argument("--sw-url", default="http://localhost:5100")
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument(
        "--skip-qwen-historical",
        action="store_true",
        help="Skip five cases with existing Qwen Single-turn results",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--pause-every",
        type=int,
        default=3,
        help="Pause briefly after this many sequential SolidWorks cases",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Independent cold-cache runs per case, used for pass@k",
    )
    parser.add_argument(
        "--strict-orientation",
        action="store_true",
        help="Compare bbox axes X/Y/Z in order instead of sorted",
    )
    parser.add_argument(
        "--rescore",
        type=Path,
        default=None,
        help="Recompute v2 scores from traces in an existing result file (no LLM/SolidWorks calls)",
    )
    parser.add_argument(
        "--action-level",
        choices=("L0", "L1"),
        default="L0",
        help="L0: single-turn atomic plan; L1: engineer-level tools with a closed-loop agent",
    )
    parser.add_argument(
        "--max-llm-calls",
        type=int,
        default=40,
        help="LLM call budget per case for L1",
    )
    parser.add_argument(
        "--split",
        choices=("dev", "heldout", "demo", "heldout2", "demo3", "heldout3", "heldout4", "hard", "all"),
        default="dev",
        help="Case split (all = the 50 core cases; hard = the 8 showcase cases); "
        "heldout cases must never be used to tune prompts, tools or code",
    )
    parser.add_argument(
        "--no-save-artifacts",
        dest="save_artifacts",
        action="store_false",
        help="Do not keep each run's SLDPRT and STEP under eval/artifacts/<result name>/",
    )
    args = parser.parse_args()
    if args.rescore:
        report = rescore_report(args.rescore, args.output or args.rescore, args.strict_orientation)
        print(
            f"rescored score={report['average_score']} ci95={report['average_score_ci95']} "
            f"passed={report['passed']}/{report['total']} pass@1={report['pass_at_1']}",
            flush=True,
        )
        return 0
    repeats = max(1, args.repeats)
    historical_ids = {
        item["case_id"] for item in HISTORICAL_CASES
    } if args.skip_qwen_historical else set()
    selected = [
        case
        for case in CASES
        if (
            (not args.ids or case.case_id in set(args.ids))
            and case.case_id not in historical_ids
            and (case.split == args.split or (args.split == "all" and case.split != "hard"))
        )
    ]
    output = args.output
    if output is None:
        safe_model = (
            args.model.lower()
            .replace(".", "_")
            .replace("-", "_")
        )
        prefix = "text_workflow_l1" if args.action_level == "L1" else "text_singleturn"
        output = ROOT / "eval" / f"{prefix}_{safe_model}_{args.split}_result.json"
    balance_before = _query_balance(ROOT / ".env")
    results: list[dict] = []
    jobs = [(case, repeat) for case in selected for repeat in range(repeats)]
    for index, (case, repeat) in enumerate(jobs, 1):
        print(f"[{index}/{len(jobs)}] {case.name} (run {repeat + 1}/{repeats})", flush=True)
        result = run_one(
            case,
            web_url=args.web_url.rstrip("/"),
            sw_url=args.sw_url.rstrip("/"),
            model=args.model,
            strict_orientation=args.strict_orientation,
            action_level=args.action_level,
            max_llm_calls=args.max_llm_calls,
            artifact_stem=(
                ROOT / "eval" / "artifacts" / output.stem / f"{case.case_id}_r{repeat + 1}"
                if args.save_artifacts else None
            ),
        )
        result["repeat"] = repeat
        results.append(result)
        usage = result.get("token_usage") or {}
        print(
            f"  score={result['score']} (v1={result['v1']['score']}) "
            f"accepted={result['acceptance_ok']} "
            f"status={result['status']} "
            f"tokens={usage.get('total_tokens')} "
            f"duration={result['duration_seconds']}s",
            flush=True,
        )
        _write_report(
            output,
            results,
            balance_before,
            model=args.model,
            selected_cases=selected,
            historical_skipped=(
                list(HISTORICAL_CASES)
                if args.skip_qwen_historical
                else []
            ),
            repeats=repeats,
            strict_orientation=args.strict_orientation,
            action_level=args.action_level,
        )
        if (
            args.pause_every > 0
            and index < len(jobs)
            and index % args.pause_every == 0
        ):
            print("  cooling pause 5s", flush=True)
            time.sleep(5.0)

    balance_after = _query_balance(ROOT / ".env")
    report = _write_report(
        output,
        results,
        balance_before,
        balance_after,
        model=args.model,
        selected_cases=selected,
        historical_skipped=(
            list(HISTORICAL_CASES)
            if args.skip_qwen_historical
            else []
        ),
        repeats=repeats,
        strict_orientation=args.strict_orientation,
        action_level=args.action_level,
    )
    print(
        f"summary score={report['average_score']} "
        f"ci95={report['average_score_ci95']} "
        f"v1={report['average_score_v1']} "
        f"passed={report['passed']}/{report['total']} "
        f"pass@1={report['pass_at_1']} "
        f"tokens={report['token_totals']['total_tokens']} "
        f"spent_yuan={report['spent_yuan']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
