"""SolidWorks 图形区多视角截图：C# /api/view/capture 逐个视向存 BMP，这里拼成一张带标注的 PNG 交给多模态模型。"""

from __future__ import annotations

import base64
import io
import math
import tempfile
from pathlib import Path
from typing import Callable

from planner.workflow.errors import WorkflowError

DEFAULT_VIEWS = ["isometric", "front", "top", "right"]
MAX_VIEWS = 4
# SaveBMP 给定尺寸时按窗口缩放结果裁切，宽高比与窗口不同就会切掉模型；0 表示按图形窗口原尺寸截取，拼图时再等比缩小
CAPTURE_SIZE = (0, 0)
CELL_WIDTH = 512
MAX_CELL_HEIGHT = 384
LABEL_HEIGHT = 30
# SolidWorks 标准视向与模型坐标的对应关系，与三视图图纸的说法一致
VIEW_NOTES = {
    "isometric": "等轴测",
    "trimetric": "不等轴测",
    "dimetric": "二等角",
    "front": "前视：沿 -Z 看，右 = +X，上 = +Y",
    "back": "后视：沿 +Z 看，右 = -X，上 = +Y",
    "top": "俯视：从 +Y 往下看，右 = +X，下 = +Z",
    "bottom": "仰视：从 -Y 往上看，右 = +X，上 = +Z",
    "right": "右视：从 +X 看，右 = -Z，上 = +Y",
    "left": "左视：从 -X 看，右 = +Z，上 = +Y",
}
FONT_PATH = Path("C:/Windows/Fonts/msyh.ttc")

RequestFn = Callable[[str, str, dict | None], dict]


def normalize_views(views) -> list[str]:
    if views in (None, "", []):
        return list(DEFAULT_VIEWS)
    if isinstance(views, str):
        views = [views]
    out = []
    for name in views:
        key = str(name).strip().lower()
        if key not in VIEW_NOTES:
            raise WorkflowError("TOOL_ARGS", f"未知视向 {name}，可选：{', '.join(VIEW_NOTES)}")
        if key not in out:
            out.append(key)
    if len(out) > MAX_VIEWS:
        raise WorkflowError("TOOL_ARGS", f"一次最多 {MAX_VIEWS} 个视向")
    return out


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(str(FONT_PATH), size)
    except OSError:
        return ImageFont.load_default()


def compose_grid(images: list[tuple[str, bytes]]) -> bytes:
    """(视向, 图片字节) 列表拼成每行两格的 PNG，每格上方写视向与坐标说明。"""
    from PIL import Image, ImageDraw

    tiles = [(view, Image.open(io.BytesIO(data)).convert("RGB")) for view, data in images]
    cols = 1 if len(tiles) == 1 else 2
    rows = math.ceil(len(tiles) / cols)
    # 格子跟随图形窗口的宽高比，宽屏窗口不留大片空白
    first = tiles[0][1]
    cell_w = CELL_WIDTH
    cell_h = min(MAX_CELL_HEIGHT, max(1, round(cell_w * first.height / first.width)))
    canvas = Image.new("RGB", (cols * cell_w, rows * (cell_h + LABEL_HEIGHT)), "white")
    draw = ImageDraw.Draw(canvas)
    font = _font(18)
    for index, (view, tile) in enumerate(tiles):
        x, y = (index % cols) * cell_w, (index // cols) * (cell_h + LABEL_HEIGHT)
        tile.thumbnail((cell_w, cell_h))
        canvas.paste(tile, (x + (cell_w - tile.width) // 2, y + LABEL_HEIGHT + (cell_h - tile.height) // 2))
        draw.text((x + 6, y + 4), VIEW_NOTES[view], fill="black", font=font)
        draw.rectangle([x, y, x + cell_w - 1, y + cell_h + LABEL_HEIGHT - 1], outline="#999999")
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


def capture_grid(request: RequestFn, target: str, views=None, highlight: list[str] | None = None) -> tuple[str, dict]:
    """截图并拼图，返回 (PNG base64, 说明)。request 即 session._request。"""
    names = normalize_views(views)
    with tempfile.TemporaryDirectory(prefix="sw_views_") as tmp:
        data = request("POST", "/api/view/capture", {
            "target": target, "views": names, "width": CAPTURE_SIZE[0], "height": CAPTURE_SIZE[1],
            "outDir": tmp, "highlight": list(highlight or []),
        })
        if not data.get("success"):
            raise WorkflowError("CAPTURE_FAILED", str(data.get("message") or "截图失败"))
        result = data.get("result") or {}
        images = []
        for item in result.get("images") or []:
            path = Path(item["path"])
            if not path.is_file():
                raise WorkflowError("CAPTURE_FAILED", f"截图文件不存在: {path}")
            images.append((item["view"], path.read_bytes()))
    if not images:
        raise WorkflowError("CAPTURE_FAILED", "没有得到截图")
    meta = {"views": [VIEW_NOTES[view] for view, _ in images]}
    if result.get("missing_highlight"):
        meta["missing_highlight"] = result["missing_highlight"]
    return base64.b64encode(compose_grid(images)).decode("ascii"), meta
