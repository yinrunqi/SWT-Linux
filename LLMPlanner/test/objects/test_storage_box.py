"""
test_storage_box.py — 日常物体：收纳盒（带内腔的盒子）

考验能力：拉伸（盒体）+ 切除拉伸（内腔，抽壳替代法）+ 圆角（可选）。

建模思路：
  1. 拉伸一个实心长方体作为盒体外形
  2. 在顶面切除一个略小的矩形凹槽（四周留壁厚、底部留底厚）
  3. （可选）对顶部边做圆角美化

用法：
  python test/objects/test_storage_box.py
"""

import sys

from _common import check_prerequisites, run_object_test


def test_storage_box() -> bool:
    """收纳盒：外形 120×80×50mm，壁厚 8mm。"""
    prompt = (
        "创建一个长方形收纳盒：外部尺寸为长 120mm、宽 80mm、高 50mm，"
        "盒壁厚度 8mm，盒子内部是一个开口向上的空腔（顶部敞开），"
        "底部厚度也是 8mm。最后对盒子顶部开口的四条边做 R3mm 圆角。"
    )
    # 外形包围盒 120(X) × 50(Y,高) × 80(Z)（上视面建模：x→X, y→-Z, 拉伸→+Y）
    return run_object_test(
        title="收纳盒（带内腔）",
        prompt=prompt,
        required_ops=["new_part", "feature_extrusion", "feature_cut_extrusion"],
        expected_bbox={"sizeX": 120, "sizeY": 50, "sizeZ": 80},
        bbox_tolerance_mm=20.0,
        feature_checks=[("Extrusion", 1), ("Cut", 1)],
    )


if __name__ == "__main__":
    if not check_prerequisites():
        sys.exit(1)

    passed = test_storage_box()

    print("\n" + "=" * 60)
    print("收纳盒测试结果")
    print("=" * 60)
    print(f"  收纳盒: {'✅ 通过' if passed else '❌ 失败'}")
    sys.exit(0 if passed else 1)
