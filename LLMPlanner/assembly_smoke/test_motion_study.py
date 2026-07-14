"""Motion Study 齿轮动画端到端测试（不调用 LLM）。"""

from __future__ import annotations

import math
import os
import time

import requests


BASE_URL = "http://localhost:5100"
SMALL_COMPONENT = "gear_small_involute_bored-1"
BIG_COMPONENT = "gear_big_involute_bored-1"
EXPECTED_CENTER_DISTANCE_M = 0.060
ASSEMBLY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "gear_pair_assembly_v4.SLDASM",
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


def get_component_state(component_name: str) -> dict:
    return post(
        "/api/assembly/get-component-transform",
        {"componentName": component_name},
    )["result"]


def matrix_delta(before: list[float], after: list[float]) -> float:
    return max(abs(a - b) for a, b in zip(before, after))


def rotation_angle_degrees(matrix: list[float]) -> float:
    return math.degrees(math.atan2(matrix[1], matrix[0]))


def wrapped_angle_delta(before: float, after: float) -> float:
    return (after - before + 180) % 360 - 180


def main() -> None:
    if not os.path.exists(ASSEMBLY_PATH):
        raise FileNotFoundError(f"缺少预置装配体: {ASSEMBLY_PATH}")

    print("=" * 64)
    print("Motion Study 齿轮联动端到端测试")
    print("=" * 64)

    post("/api/assembly/open", {"assemblyPath": ASSEMBLY_PATH})
    post("/api/assembly/unfix-component", {"componentName": SMALL_COMPONENT})

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
            "componentName": SMALL_COMPONENT,
            "rpm": 3,
            "durationSeconds": 10,
            "reverseDirection": False,
        },
    )["result"]
    print(f"✅ 旋转马达已就绪: {motor['motorFeatureName']}")

    small_state = get_component_state(SMALL_COMPONENT)
    big_state = get_component_state(BIG_COMPONENT)
    center_distance = abs(
        big_state["translationXYZ"][0] - small_state["translationXYZ"][0]
    )
    assert abs(center_distance - EXPECTED_CENTER_DISTANCE_M) < 1e-6, (
        f"齿轮中心距应为 60mm，实际为 {center_distance * 1000:.3f}mm"
    )
    print(f"✅ 齿轮啮合中心距: {center_distance * 1000:.3f}mm")

    post("/api/assembly/motion/play")
    try:
        small_before = get_component_state(SMALL_COMPONENT)["rotationMatrix3x3"]
        big_before = get_component_state(BIG_COMPONENT)["rotationMatrix3x3"]
        time.sleep(0.25)
        status = requests.get(
            f"{BASE_URL}/api/assembly/motion/status",
            timeout=15,
        ).json()["result"]
        small_after = get_component_state(SMALL_COMPONENT)["rotationMatrix3x3"]
        big_after = get_component_state(BIG_COMPONENT)["rotationMatrix3x3"]
    finally:
        post("/api/assembly/motion/stop")

    small_delta = matrix_delta(small_before, small_after)
    big_delta = matrix_delta(big_before, big_after)
    small_angle_delta = wrapped_angle_delta(
        rotation_angle_degrees(small_before),
        rotation_angle_degrees(small_after),
    )
    big_angle_delta = wrapped_angle_delta(
        rotation_angle_degrees(big_before),
        rotation_angle_degrees(big_after),
    )
    print(
        f"Motion time={status['currentTime']:.3f}s, "
        f"small_angle={small_angle_delta:.3f}°, "
        f"big_angle={big_angle_delta:.3f}°"
    )

    assert status["currentTime"] > 0.1, "Motion timebar 未推进"
    assert small_delta > 0.1, "主动小齿轮 Transform 未变化"
    assert big_delta > 0.1, "从动大齿轮未通过 Gear Mate 联动"
    assert small_angle_delta * big_angle_delta < 0, "外啮合齿轮没有反向旋转"
    actual_ratio = abs(small_angle_delta / big_angle_delta)
    assert abs(actual_ratio - 2.0) < 0.05, (
        f"齿轮角速度比应为 2:1，实际为 {actual_ratio:.4f}:1"
    )
    print("🎉 Motion Study 齿轮联动测试通过")


if __name__ == "__main__":
    main()
