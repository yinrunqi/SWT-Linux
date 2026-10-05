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
    "REF_NOT_PLANAR": "引用的面不是平面",
    "REF_NOT_AXIS_ALIGNED": "引用的平面法向不与坐标轴对齐",
    "SKETCH_EMPTY": "草图没有任何轮廓",
    "SKETCH_DEGENERATE": "轮廓退化（点数不足、零面积或非正半径）",
    "SKETCH_OPEN_LOOP": "轮廓未闭合",
    "SKETCH_SELF_INTERSECT": "轮廓自相交",
    "SKETCH_LOOPS_OVERLAP": "两个轮廓相交但互不包含",
    "SKETCH_OUTSIDE_BOUNDARY": "轮廓超出所在面的边界",
    "SKETCH_THIN_WALL": "壁厚或边距小于下限",
    "SKETCH_MISSES_FACE": "轮廓与所在面没有重叠",
    "SKETCH_CROSSES_AXIS": "旋转轮廓跨越了旋转轴",
    "TOOL_ARGS": "工具参数缺失或非法",
    "NO_PART": "还没有打开的零件",
    "NO_BODY": "零件中还没有实体",
    "FEATURE_FAILED": "SolidWorks 未能创建特征",
    "FEATURE_NO_EFFECT": "特征已创建但没有改变实体（方向或位置可能不对）",
    "FEATURE_DISCONNECTED": "特征执行后零件不再是单一实体（L1_v2）",
    "SW_UNAVAILABLE": "无法连接 SolidWorks 服务",
    "RENDER_FAILED": "无法生成当前模型的三视图",
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
