"""workflow 层的标准化错误码，供 LLM 修复与评测统计使用。"""

from __future__ import annotations

ERROR_CODES = {
    "PARAM_SYNTAX": "参数表达式语法错误",
    "PARAM_FORBIDDEN": "参数表达式使用了不允许的语法或函数",
    "PARAM_UNDEFINED": "引用了未定义的参数",
    "PARAM_CYCLE": "参数之间存在循环引用",
    "PARAM_DOMAIN": "参数计算结果非法（除零、非有限值等）",
    "REF_SYNTAX": "引用写法无法解析",
    "REF_NOT_FOUND": "当前模型中找不到满足条件的面",
    "REF_AMBIGUOUS": "有多个同样匹配的面，需要更具体的引用",
    "REF_UNBOUND": "命名引用尚未绑定",
    "SKETCH_EMPTY": "草图没有任何轮廓",
    "SKETCH_DEGENERATE": "轮廓退化（点数不足、零面积或非正半径）",
    "SKETCH_OPEN_LOOP": "轮廓未闭合",
    "SKETCH_SELF_INTERSECT": "轮廓自相交",
    "SKETCH_LOOPS_OVERLAP": "两个轮廓相交但互不包含",
    "SKETCH_OUTSIDE_BOUNDARY": "轮廓超出所在面的边界",
    "SKETCH_THIN_WALL": "壁厚或边距小于下限",
}


class WorkflowError(ValueError):
    def __init__(self, code: str, message: str = "", **context):
        if code not in ERROR_CODES:
            raise KeyError(f"未知错误码: {code}")
        self.code = code
        self.context = context
        super().__init__(f"[{code}] {message or ERROR_CODES[code]}")

    def to_dict(self) -> dict:
        return {"code": self.code, "message": str(self), **self.context}
