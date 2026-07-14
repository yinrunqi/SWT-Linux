"""逐时间点导出 SolidWorks 齿轮动画 BMP 帧和 GIF，供视觉验收。"""

from __future__ import annotations

import json
import os

import requests
import win32com.client
from PIL import Image


BASE_URL = "http://localhost:5100"
SMALL_COMPONENT = "gear_small_involute_bored-1"
ASSEMBLY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "gear_pair_assembly_v4.SLDASM",
)
OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "log",
    "gear_validation",
)


def post(path: str, body: dict | None = None) -> dict:
    response = requests.post(
        f"{BASE_URL}{path}",
        json=body or {},
        timeout=90,
    )
    data = response.json()
    if not response.ok or not data.get("success"):
        raise RuntimeError(f"{path} 失败: {data.get('message')}")
    return data


def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    post("/api/assembly/open", {"assemblyPath": ASSEMBLY_PATH})
    post("/api/assembly/unfix-component", {"componentName": SMALL_COMPONENT})
    post(
        "/api/assembly/motion/create-gear-motor",
        {
            "componentName": SMALL_COMPONENT,
            "rpm": 3,
            "durationSeconds": 3,
            "reverseDirection": False,
        },
    )

    solidworks = win32com.client.GetActiveObject("SldWorks.Application")
    model = solidworks.ActiveDoc
    model.ViewZoomtofit2()

    frame_paths: list[str] = []
    interference_report: list[dict] = []
    # 3 RPM = 18°/s；2 秒恰好覆盖小齿轮一个 36° 齿周期。
    for index in range(25):
        seconds = 2.0 * index / 24
        post("/api/assembly/motion/set-time", {"seconds": seconds})
        interference = post("/api/assembly/check-interference")["result"]
        interference_report.append(
            {
                "frame": index,
                "seconds": seconds,
                **interference,
            }
        )
        frame_path = os.path.join(OUTPUT_DIR, f"frame_{index:02d}.bmp")
        if not model.SaveBMP(frame_path, 960, 720):
            raise RuntimeError(f"SolidWorks 导出帧失败: {frame_path}")
        frame_paths.append(frame_path)

    frames: list[Image.Image] = []
    for frame_path in frame_paths:
        with Image.open(frame_path) as bitmap:
            frame = bitmap.convert("RGB")
        frame.save(os.path.splitext(frame_path)[0] + ".png")
        frames.append(frame)
    gif_path = os.path.join(OUTPUT_DIR, "gear_cycle.gif")
    frames[0].save(
        gif_path,
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    for frame in frames:
        frame.close()

    report_path = os.path.join(OUTPUT_DIR, "interference_report.json")
    with open(report_path, "w", encoding="utf-8") as report_file:
        json.dump(interference_report, report_file, ensure_ascii=False, indent=2)

    post("/api/assembly/motion/set-time", {"seconds": 0})
    interfering_frames = [
        item for item in interference_report if item["interferenceCount"] > 0
    ]
    if interfering_frames:
        raise RuntimeError(
            f"检测到 {len(interfering_frames)} 个穿模帧，详见 {report_path}"
        )
    print(f"✅ 已导出 {len(frame_paths)} 帧: {OUTPUT_DIR}")
    print(f"✅ 动画 GIF: {gif_path}")
    print(f"✅ SolidWorks 原生干涉检测: 25/25 帧零干涉")


if __name__ == "__main__":
    main()
