"""
main.py — LLM Planner 命令行交互入口

用法:
    python -m planner.main                      # 交互模式
    python -m planner.main "创建一个长方体"       # 单次执行模式
    python -m planner.main --dry-run "创建圆柱"   # 模拟执行（不实际调用 SolidWorks）
"""

from __future__ import annotations

import argparse
import sys

from .llm_client import SwLLMClient
from .executor import SwExecutor
from .schemas import SwModelingPlan


def print_plan(plan: SwModelingPlan) -> None:
    """美观地打印建模计划。"""
    print(f"\n{'─'*60}")
    print(f"📋 建模计划")
    print(f"{'─'*60}")
    print(f"  意图: {plan.user_intent}")
    print(f"  步骤数: {len(plan.steps)}")
    if plan.notes:
        print(f"  备注: {plan.notes}")
    print()

    for i, step in enumerate(plan.steps, 1):
        desc = f" — {step.description}" if step.description else ""
        params = f"  参数: {step.params}" if step.params else ""
        print(f"  {i}. [{step.operation.value}]{desc}{params}")

    print(f"{'─'*60}\n")


def run_once(
    instruction: str,
    llm_client: SwLLMClient,
    executor: SwExecutor,
    dry_run: bool = False,
    auto_confirm: bool = False,
) -> bool:
    """
    执行一次完整的 "自然语言 → 建模计划 → 执行" 流程。

    Args:
        instruction: 用户输入的自然语言建模指令
        llm_client: LLM 客户端
        executor: SolidWorks 执行器
        dry_run: 是否为模拟执行模式
        auto_confirm: 是否跳过确认直接执行

    Returns:
        True 如果执行成功
    """
    # 1. 调用 LLM 生成建模计划
    try:
        plan = llm_client.plan(instruction)
    except Exception as e:
        print(f"\n[错误] LLM 调用失败: {e}", file=sys.stderr)
        return False

    # 2. 展示计划
    print_plan(plan)

    # 3. 用户确认
    if not auto_confirm and not dry_run:
        while True:
            choice = input("是否执行此计划？(y=执行 / n=取消 / d=模拟执行): ").strip().lower()
            if choice in ("y", "yes", "是"):
                break
            elif choice in ("n", "no", "否"):
                print("已取消执行。\n")
                return False
            elif choice in ("d", "dry"):
                dry_run = True
                break
            else:
                print("请输入 y/n/d")

    # 4. 执行计划
    results = executor.execute_plan(plan, dry_run=dry_run)

    # 5. 判断是否全部成功
    all_success = all(r.get("success", False) or r.get("status") == "dry_run" for r in results)
    return all_success


def interactive_mode(
    llm_client: SwLLMClient,
    executor: SwExecutor,
    dry_run: bool = False,
) -> None:
    """交互式命令行模式。"""
    print("\n" + "=" * 60)
    print("🔧 SolidWorks LLM 自动建模助手")
    print("=" * 60)
    print("输入建模指令（自然语言），例如：")
    print('  - "创建一个 100mm x 50mm x 30mm 的长方体"')
    print('  - "画一个半径 20mm 的圆柱，高 50mm"')
    print('  - "创建一个底面为正方形的棱柱"')
    print()
    print("特殊命令:")
    print("  quit / exit    — 退出程序")
    print("  clear-cache    — 清空 LLM 缓存")
    print("  dry-run        — 切换模拟执行模式")
    print("=" * 60 + "\n")

    current_dry_run = dry_run

    while True:
        try:
            instruction = input(f"{'[模拟] ' if current_dry_run else ''}🎯 请输入建模指令: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n再见！👋")
            break

        if not instruction:
            continue

        # 特殊命令
        cmd = instruction.lower()
        if cmd in ("quit", "exit", "q", "退出"):
            print("\n再见！👋")
            break
        elif cmd == "clear-cache":
            llm_client.clear_cache()
            continue
        elif cmd == "dry-run":
            current_dry_run = not current_dry_run
            status = "开启" if current_dry_run else "关闭"
            print(f"模拟执行模式已{status}\n")
            continue

        # 执行建模
        run_once(instruction, llm_client, executor, dry_run=current_dry_run)


def main():
    """主入口函数。"""
    parser = argparse.ArgumentParser(
        description="SolidWorks LLM 自动建模助手 — 将自然语言指令转换为 SolidWorks 操作",
    )
    parser.add_argument(
        "instruction",
        nargs="?",
        default=None,
        help="建模指令（自然语言），不提供则进入交互模式",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="模拟执行模式，仅打印步骤不实际调用 SolidWorks",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="跳过确认直接执行",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="指定 LLM 模型名称（默认: gpt-4o，支持结构化输出）",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="禁用缓存",
    )
    parser.add_argument(
        "--sw-url",
        default="http://localhost:5100",
        help="SolidWorks C# 服务地址（默认: http://localhost:5100）",
    )

    args = parser.parse_args()

    # 初始化客户端
    llm_client = SwLLMClient(
        model=args.model,
        use_cache=not args.no_cache,
    )
    executor = SwExecutor(base_url=args.sw_url)

    # 检查 SolidWorks 服务连接（非 dry-run 模式下）
    if not args.dry_run:
        print("[启动] 检查 SolidWorks 服务连接...")
        if executor.ping():
            print("[启动] ✅ SolidWorks 服务连接正常\n")
        else:
            print("[启动] ⚠️  无法连接到 SolidWorks 服务，请确保 C# 程序已启动")
            print(f"[启动] 服务地址: {args.sw_url}")
            print("[启动] 你仍然可以使用 --dry-run 模式查看建模计划\n")

    try:
        if args.instruction:
            # 单次执行模式
            success = run_once(
                args.instruction,
                llm_client,
                executor,
                dry_run=args.dry_run,
                auto_confirm=args.yes,
            )
            sys.exit(0 if success else 1)
        else:
            # 交互模式
            interactive_mode(llm_client, executor, dry_run=args.dry_run)
    finally:
        llm_client.close()
        executor.close()


if __name__ == "__main__":
    main()
