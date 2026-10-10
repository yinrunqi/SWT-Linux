"""参数表：工程师习惯用“壁厚 wall=3、孔距 = L - 2*margin”这样的驱动尺寸建模。

表达式用受限 AST 求值（只允许数字、参数名、四则/幂/取模、白名单函数），
长度统一以 mm 存储，下发到 SolidWorks 时再换算成米。
"""

from __future__ import annotations

import ast
import math
import operator
from dataclasses import dataclass, field
from typing import Any, Mapping

from .errors import WorkflowError

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

FUNCTIONS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
    # 工程图纸上的角度默认是度
    "sind": lambda deg: math.sin(math.radians(deg)),
    "cosd": lambda deg: math.cos(math.radians(deg)),
    "tand": lambda deg: math.tan(math.radians(deg)),
    "atand": lambda value: math.degrees(math.atan(value)),
    "atan2d": lambda y, x: math.degrees(math.atan2(y, x)),
    "asind": lambda value: math.degrees(math.asin(value)),
    "acosd": lambda value: math.degrees(math.acos(value)),
    "exp": math.exp,
    "log": math.log,
    "hypot": math.hypot,
    "sign": lambda value: float((value > 0) - (value < 0)),
    # 分段函数：step(x) 在 x ≥ 0 时为 1；clamp(x, lo, hi) 把 x 限制在区间内
    "step": lambda value: 1.0 if value >= 0 else 0.0,
    "clamp": lambda value, lo, hi: min(max(value, lo), hi),
}
# 参数曲线（渐开线、螺线、翼型）习惯用弧度；普通参数表达式里不提供，免得 tan(30) 被当成弧度
CURVE_FUNCTIONS = {
    **FUNCTIONS,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
}
CONSTANTS = {"pi": math.pi}

UNITS_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}
ANGLE_UNITS = {"deg"}
SCALAR_UNITS = {"", "count", "ratio"}


def _names(tree: ast.AST) -> set[str]:
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id not in FUNCTIONS and node.id not in CONSTANTS
    }


def _parse(expr: str, functions: Mapping[str, Any] = FUNCTIONS) -> ast.Expression:
    try:
        tree = ast.parse(str(expr).strip(), mode="eval")
    except SyntaxError as exc:
        raise WorkflowError("PARAM_SYNTAX", f"无法解析表达式 {expr!r}: {exc.msg}", expr=expr) from exc
    for node in ast.walk(tree):
        allowed = (
            ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load, ast.Call,
            *tuple(_BINARY), *tuple(_UNARY),
        )
        if not isinstance(node, allowed):
            raise WorkflowError(
                "PARAM_FORBIDDEN", f"表达式 {expr!r} 含不允许的语法 {type(node).__name__}", expr=expr
            )
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise WorkflowError("PARAM_FORBIDDEN", f"表达式 {expr!r} 只允许数值常量", expr=expr)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in functions or node.keywords:
                raise WorkflowError("PARAM_FORBIDDEN", f"表达式 {expr!r} 调用了不允许的函数", expr=expr)
    return tree


def _eval(node: ast.AST, env: Mapping[str, float], functions: Mapping[str, Any] = FUNCTIONS) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body, env, functions)
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id in CONSTANTS:
            return CONSTANTS[node.id]
        if node.id not in env:
            raise WorkflowError("PARAM_UNDEFINED", f"未定义的参数 {node.id}", name=node.id)
        return env[node.id]
    if isinstance(node, ast.BinOp):
        return _BINARY[type(node.op)](_eval(node.left, env, functions), _eval(node.right, env, functions))
    if isinstance(node, ast.UnaryOp):
        return _UNARY[type(node.op)](_eval(node.operand, env, functions))
    if isinstance(node, ast.Call):
        return float(functions[node.func.id](*(_eval(arg, env, functions) for arg in node.args)))
    raise WorkflowError("PARAM_FORBIDDEN", f"不支持的节点 {type(node).__name__}")


def evaluate(expr: str | float | int, env: Mapping[str, float] | None = None) -> float:
    if isinstance(expr, (int, float)):
        return float(expr)
    try:
        value = _eval(_parse(expr), env or {})
    except ZeroDivisionError as exc:
        raise WorkflowError("PARAM_DOMAIN", f"表达式 {expr!r} 除零", expr=expr) from exc
    except (OverflowError, ValueError) as exc:
        if isinstance(exc, WorkflowError):
            raise
        raise WorkflowError("PARAM_DOMAIN", f"表达式 {expr!r} 计算失败: {exc}", expr=expr) from exc
    if not math.isfinite(value):
        raise WorkflowError("PARAM_DOMAIN", f"表达式 {expr!r} 结果非有限值", expr=expr)
    return value


@dataclass
class Param:
    name: str
    expr: str | float
    unit: str = "mm"
    note: str = ""


@dataclass
class ParamTable:
    """有依赖关系的驱动参数表。长度参数值以 mm 为单位。"""

    params: dict[str, Param] = field(default_factory=dict)

    def define(self, name: str, expr: str | float, unit: str = "mm", note: str = "") -> None:
        if not name.isidentifier() or name in FUNCTIONS or name in CONSTANTS:
            raise WorkflowError("PARAM_SYNTAX", f"参数名 {name!r} 非法", name=name)
        if unit not in UNITS_TO_MM and unit not in ANGLE_UNITS and unit not in SCALAR_UNITS:
            raise WorkflowError("PARAM_SYNTAX", f"未知单位 {unit!r}", name=name)
        if isinstance(expr, str):
            _parse(expr)
        self.params[name] = Param(name, expr, unit, note)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParamTable":
        """接受 {"wall": 3, "L": {"expr": "2*W", "unit": "mm"}} 这样的写法。"""
        table = cls()
        for name, spec in data.items():
            if isinstance(spec, Mapping):
                table.define(name, spec.get("expr", spec.get("value")), spec.get("unit", "mm"),
                             spec.get("note", ""))
            else:
                table.define(name, spec)
        return table

    def _order(self) -> list[str]:
        deps = {
            name: (_names(_parse(p.expr)) if isinstance(p.expr, str) else set())
            for name, p in self.params.items()
        }
        for name, needed in deps.items():
            missing = needed - set(self.params)
            if missing:
                raise WorkflowError(
                    "PARAM_UNDEFINED", f"{name} 引用了未定义的参数 {sorted(missing)}",
                    name=name, missing=sorted(missing),
                )
        order: list[str] = []
        state: dict[str, int] = {}

        def visit(name: str, path: list[str]) -> None:
            if state.get(name) == 2:
                return
            if state.get(name) == 1:
                cycle = path[path.index(name):] + [name]
                raise WorkflowError("PARAM_CYCLE", " -> ".join(cycle), cycle=cycle)
            state[name] = 1
            for dep in sorted(deps[name]):
                visit(dep, path + [name])
            state[name] = 2
            order.append(name)

        for name in self.params:
            visit(name, [])
        return order

    def resolve(self) -> dict[str, float]:
        """按依赖顺序求值。长度统一换算成 mm，角度保持度。"""
        values: dict[str, float] = {}
        for name in self._order():
            param = self.params[name]
            raw = evaluate(param.expr, values)
            values[name] = raw * UNITS_TO_MM.get(param.unit, 1.0)
        return values

    def value(self, name_or_expr: str | float, *, unit: str = "mm") -> float:
        """对参数名或表达式求值；unit="m" 时换算为 SolidWorks API 需要的米。"""
        result = evaluate(name_or_expr, self.resolve())
        if unit == "m":
            return result / 1000.0
        if unit == "mm":
            return result
        raise WorkflowError("PARAM_SYNTAX", f"不支持的输出单位 {unit!r}")

    def substitute(self, params: Mapping[str, Any], *, length_keys: set[str] | None = None) -> dict:
        """把操作参数里以 "=" 开头的表达式替换为数值。

        length_keys 中的键视为长度，结果换算为米；其余（角度、数量）保持原值。
        """
        values = self.resolve()
        result: dict[str, Any] = {}
        for key, raw in params.items():
            if isinstance(raw, str) and raw.startswith("="):
                number = evaluate(raw[1:], values)
                result[key] = number / 1000.0 if length_keys and key in length_keys else number
            else:
                result[key] = raw
        return result
