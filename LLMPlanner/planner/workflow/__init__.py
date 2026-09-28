"""工程师级 workflow 层（L1）：参数表、语义引用、草图预检。

这些模块是纯 Python，不依赖 SolidWorks，可离线单测；执行层通过现有原子操作落地。
"""

from .errors import WorkflowError
from .params import ParamTable
from .refs import RefRegistry, ResolvedRef, resolve_ref
from .sketch_check import SketchCheckResult, check_profile, check_within

__all__ = [
    "ParamTable",
    "RefRegistry",
    "ResolvedRef",
    "SketchCheckResult",
    "WorkflowError",
    "check_profile",
    "check_within",
    "resolve_ref",
]
