# 多模态图纸→建模 功能规划

> 状态：待实施（先完善纯文字功能后再启动）

## 核心思路

在现有架构上最小化改动，让图纸识别走和文本指令完全相同的管线：

```
图纸图片 + 文字说明 → LLM Vision 分析 → SwModelingPlan → Executor 执行 → SolidWorks 建模
```

## 输入需求

- **图纸类型**：手绘草图/白板照片、正式工程图纸（三视图+标注）、3D 效果图/渲染图，全部支持
- **LLM 模型**：前端自选（和文字模式一样灵活配置）
- **输入方式**：上传图片文件 + 可选文字补充说明
- **功能位置**：加在现有 Web Demo 对话界面中

## 改动范围（共 5 步）

| # | 模块 | 改动 |
|---|------|------|
| 1 | `planner/llm_client.py` | `plan()` 方法加 `image_base64` 可选参数；构造 OpenAI Vision 格式消息（text + image_url）；缓存键纳入图片哈希；SYSTEM_PROMPT 追加图纸分析指导 |
| 2 | `web/app.py` | `/api/run` 端点从请求中提取 `image` 字段，透传给 `plan()` |
| 3 | `web/static/index.html` + `style.css` | 输入区增加📎图片上传按钮 + 图片预览条；用户气泡支持展示图片；拖拽上传样式 |
| 4 | `web/static/app.js` | 图片选择/预览/压缩/base64 转换；`send()` 附带图片数据；渲染图片气泡 |
| 5 | `README.md` | 补充多模态功能说明 |

## 关键技术决策

### 图片传输方式
前端将图片转 base64，通过 JSON body 发给 Flask，Flask 再以 `data:image/...;base64,...` 格式传给 OpenAI Vision API。

### OpenAI Vision API 消息格式
```python
messages = [
    {"role": "system", "content": "..."},
    {"role": "user", "content": [
        {"type": "text", "text": "用户文字说明"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,{base64_data}"}}
    ]}
]
```

### 不改 schemas.py
输出结构（`SwModelingPlan`）和执行器（`SwExecutor`）完全不变，图纸只影响 LLM 输入端。

### 兼容性
`plan(instruction)` 原有纯文本调用不受影响，`image_base64` 默认 None。

### 前端体验
点击📎上传或拖拽图片 → 输入框上方出现缩略图预览 → 可点击 ✕ 移除 → 发送后在用户气泡中展示图片。

## SYSTEM_PROMPT 补充内容（图纸分析指导）

在现有 SYSTEM_PROMPT 末尾追加图纸分析规则：
- 识别图纸类型（三视图/手绘/效果图）
- 从标注中提取精确尺寸，单位统一转为米
- 对手绘草图合理推断比例和尺寸
- 对效果图根据形状推断几何特征和大致尺寸
- 优先使用明确标注的尺寸，对未标注的部分做合理假设并在 notes 中说明

## 前置条件

在实施本方案前，需确保以下纯文字功能已稳定：
1. 基本建模指令的规划和执行
2. 复杂组合特征的正确生成
3. 错误处理和重试机制
4. Web Demo 界面的基础交互体验
