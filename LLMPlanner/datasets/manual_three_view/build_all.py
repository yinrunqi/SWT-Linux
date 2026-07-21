"""
build_all.py — 一键（重）生成 manual_three_view 目录下所有样例三视图。

**单一数据源**：`datasets/manual_three_view/samples.json`
    该 JSON 也同时被 test/test_three_view_e2e.py 和 web/static/app.js 使用。
    新增样例只需要改 samples.json，本脚本、e2e 测试、Web demo 三处都会自动跟随。

用法（从 LLMPlanner 目录运行）：
    venv\\Scripts\\python.exe -X utf8 datasets\\manual_three_view\\build_all.py

每个样例都由**同一个 ExtrudedPart 定义派生**三视图 + groundtruth，
保证视图之间 100% 一致，人工无法引入矛盾。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 让脚本可以直接从 LLMPlanner 或 datasets/manual_three_view 目录运行
_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

from _toolkit import (  # noqa: E402
    ExtrudedPart, Hole, FilletEdges, CutoutOnFace, build_part,
)


SAMPLES_JSON = _THIS_DIR / "samples.json"


def load_samples() -> list[dict]:
    """从 samples.json 读取全部样例配置。"""
    if not SAMPLES_JSON.exists():
        raise FileNotFoundError(f"samples.json 不存在: {SAMPLES_JSON}")
    data = json.loads(SAMPLES_JSON.read_text(encoding="utf-8"))
    return data["samples"]


def sample_to_part(cfg: dict) -> ExtrudedPart:
    """把 JSON 中的一个样例条目转换为 ExtrudedPart 实例。"""
    holes = [
        Hole(
            cx=h["cx"], cy=h["cy"], radius=h["radius"],
            label=h.get("label", ""),
        )
        for h in cfg.get("holes", [])
    ]
    profile = [tuple(pt) for pt in cfg["profile_xy"]]
    operations = _parse_operations(cfg.get("operations", []))
    return ExtrudedPart(
        name=cfg["name"],
        part_type=cfg.get("part_type", "extrude_prism"),
        profile_xy=profile,
        depth_z=cfg["depth_z"],
        holes=holes,
        notes=cfg.get("notes", ""),
        top_scale=float(cfg.get("top_scale", 1.0)),
        profile_centered=bool(cfg.get("profile_centered", False)),
        operations=operations,
    )


def _parse_operations(ops_cfg: list) -> list:
    """把 JSON 里的 operations 数组解析为 FilletEdges/CutoutOnFace 实例列表。"""
    parsed: list = []
    for o in ops_cfg:
        t = o.get("type")
        if t == "fillet_edges":
            parsed.append(FilletEdges(
                edges=o["edges"],
                radius=float(o["radius"]),
                label=o.get("label", ""),
            ))
        elif t == "cutout_on_face":
            parsed.append(CutoutOnFace(
                face=o["face"],
                shape=o["shape"],
                depth=float(o.get("depth", 0.0)),
                through_all=bool(o.get("through_all", False)),
                cx=float(o.get("cx", 0.0)),
                cy=float(o.get("cy", 0.0)),
                radius=float(o.get("radius", 0.0)),
                width=float(o.get("width", 0.0)),
                height=float(o.get("height", 0.0)),
                length=float(o.get("length", 0.0)),
                positions=[tuple(p) for p in o.get("positions", [])],
                surface_level=float(o.get("surface_level", 0.0)),
                label=o.get("label", ""),
            ))
        else:
            raise ValueError(f"未知 operation type: {t}")
    return parsed


def main() -> None:
    samples = load_samples()

    print("=" * 60)
    print(f"批量生成三视图样例（共 {len(samples)} 个，来源：{SAMPLES_JSON.name}）")
    print("=" * 60)

    for cfg in samples:
        part_id = cfg["part_id"]
        subdir = cfg["subdir"]
        part = sample_to_part(cfg)
        out_dir = _THIS_DIR / subdir
        build_part(part, out_dir=out_dir, part_id=part_id)
        print()

    print("=" * 60)
    print("全部完成。图纸与 groundtruth 均从 ExtrudedPart 派生，保证一致。")
    print("=" * 60)


if __name__ == "__main__":
    main()
