"""
test_dice.py — 日常物体：骰子（立方体 + 棱边圆角）

考验能力：拉伸（立方体）+ 圆角（所有棱边，做出圆润骰子手感）。

简化说明：完整骰子需要在 6 个面切除 1~6 个圆点凹坑（共 21 个），
对 LLM 规划与射线选面是较大考验。本测试先做"圆角立方体"基础形态，
点数凹坑作为进阶项后续单独验证。

建模思路：
  1. 拉伸一个正方体（20mm 边长）
  2. 对所有 12 条棱边做 R2mm 圆角，得到圆润的骰子外形

用法：
  python test/objects/test_dice.py
"""

import sys

from _common import check_prerequisites, run_object_test


def test_dice() -> bool:
    """骰子基础形态：20mm 正方体 + 所有棱边 R2mm 圆角。"""
    prompt = (
        "创建一个骰子的基础外形：先做一个边长 20mm 的正方体，"
        "然后对正方体的所有 12 条棱边做 R2mm 的圆角，"
        "使其成为一个边角圆润的立方块。"
    )
    return run_object_test(
        title="骰子（圆角立方体）",
        prompt=prompt,
        required_ops=["new_part", "feature_extrusion", "feature_fillet"],
        expected_bbox={"sizeX": 20, "sizeY": 20, "sizeZ": 20},
        bbox_tolerance_mm=5.0,
        feature_checks=[("Extrusion", 1), ("Fillet", 1)],
    )


if __name__ == "__main__":
    if not check_prerequisites():
        sys.exit(1)

    passed = test_dice()

    print("\n" + "=" * 60)
    print("骰子测试结果")
    print("=" * 60)
    print(f"  骰子: {'✅ 通过' if passed else '❌ 失败'}")
    sys.exit(0 if passed else 1)
