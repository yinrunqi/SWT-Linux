"""Motion Study 齿轮动画端到端测试（不调用 LLM）。"""

from __future__ import annotations

import os
import time

import requests


BASE_URL = "http://localhost:5100"
ASSEMBLY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "gear_pair_assembly_v3.SLDASM",
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


def get_transform(component_name: str) -> list[float]:
    data = post(
        "/api/assembly/get-component-transform",
        {"componentName": component_name},
    )
    return data["result"]["rotationMatrix3x3"]


def matrix_delta(before: list[float], after: list[float]) -> float:
    return max(abs(a - b) for a, b in zip(before, after))


def main() -> None:
    if not os.path.exists(ASSEMBLY_PATH):
        raise FileNotFoundError(f"缺少预置装配体: {ASSEMBLY_PATH}")

    print("=" * 64)
    print("Motion Study 齿轮联动端到端测试")
    print("=" * 64)

    post("/api/assembly/open", {"assemblyPath": ASSEMBLY_PATH})
    post("/api/assembly/unfix-component", {"componentName": "gear_small-1"})

    probe = post("/api/assembly/motion/probe")["result"]
    assert probe["managerAvailable"]
    assert probe["rotationalMotorDefinitionAvailable"]
    print(
        f"✅ Motion Study 可用: {probe['studyName']}, "
        f"supported={probe['supportedStudyTypes']}"
    )

    motor = post(
        "/api/assembly/motion/create-gear-motor",
        {
            "componentName": "gear_small-1",
            "rpm": 30,
            "durationSeconds": 4,
            "reverseDirection": False,
        },
    )["result"]
    print(f"✅ 旋转马达已就绪: {motor['motorFeatureName']}")

    post("/api/assembly/motion/play")
    try:
        small_before = get_transform("gear_small-1")
        big_before = get_transform("gear_big-1")
        time.sleep(1.0)
        status = requests.get(
            f"{BASE_URL}/api/assembly/motion/status",
            timeout=15,
        ).json()["result"]
        small_after = get_transform("gear_small-1")
        big_after = get_transform("gear_big-1")
    finally:
        post("/api/assembly/motion/stop")

    small_delta = matrix_delta(small_before, small_after)
    big_delta = matrix_delta(big_before, big_after)
    print(
        f"Motion time={status['currentTime']:.3f}s, "
        f"small_delta={small_delta:.6f}, big_delta={big_delta:.6f}"
    )

    assert status["currentTime"] > 0.5, "Motion timebar 未推进"
    assert small_delta > 0.1, "主动小齿轮 Transform 未变化"
    assert big_delta > 0.1, "从动大齿轮未通过 Gear Mate 联动"
    print("🎉 Motion Study 齿轮联动测试通过")


if __name__ == "__main__":
    main()
