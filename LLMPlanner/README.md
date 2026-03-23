# LLM Planner — SolidWorks 自然语言自动建模

将自然语言建模指令（如"创建一个 100mm x 50mm 的长方体"）通过 LLM 解析为 SolidWorks 操作序列，自动调用 C# HTTP API 完成建模。

## 架构

```
用户自然语言指令
       ↓
   LLM Client (planner/llm_client.py)
   - OpenAI 兼容 API (结构化输出)
   - diskcache 磁盘缓存
   - tenacity 自动重试
       ↓
   SwModelingPlan (planner/schemas.py)
   - Pydantic 结构化输出模型
   - 有序操作步骤列表
       ↓
   Executor (planner/executor.py)
   - HTTP 请求发送到 C# 服务
   - 逐步执行 + 错误处理
       ↓
   SolidWorks C# HTTP API (localhost:5100)
```

## 文件结构

```
LLMPlanner/
├── planner/
│   ├── __init__.py        # 包入口
│   ├── schemas.py         # Pydantic 结构化输出模型
│   ├── llm_client.py      # LLM 客户端（缓存+重试+结构化输出）
│   ├── executor.py        # HTTP API 执行器
│   └── main.py            # 命令行交互入口
├── test/
│   └── test_create_box.py # 直接调用 HTTP API 的测试
├── requirements.txt       # Python 依赖
├── venv/                  # Python 虚拟环境
└── README.md              # 本文件
```

## 快速开始

### 1. 激活虚拟环境并安装依赖

```bash
# Windows
C:\codes\sw\SolidWorksTutorial\LLMPlanner\venv\Scripts\activate.bat

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
# 设置 OpenAI API Key（必须）
export OPENAI_API_KEY=your_api_key_here

# 设置自定义 API 地址（可选，用于兼容第三方 API 如 DeepSeek 等）
export OPENAI_BASE_URL=https://api.openai.com/v1
```

Windows CMD:
```cmd
set OPENAI_API_KEY=your_api_key_here
set OPENAI_BASE_URL=https://api.openai.com/v1
```

### 3. 确保 SolidWorks C# 服务已启动

```bash
# 在另一个终端启动 C# HTTP API 服务
.\Chapter1.ConnectToSw\bin\Debug\Chapter1.ConnectToSw.exe
```

### 4. 运行 LLM Planner

```bash
# 从 LLMPlanner 目录运行
cd C:\codes\sw\SolidWorksTutorial\LLMPlanner

# 交互模式
python -m planner.main

# 单次执行
python -m planner.main "创建一个 100mm x 50mm x 30mm 的长方体"

# 模拟执行（不调用 SolidWorks，仅查看计划）
python -m planner.main --dry-run "画一个半径 20mm 的圆柱"

# 跳过确认直接执行
python -m planner.main -y "创建一个正方体，边长 5cm"

# 指定模型
python -m planner.main --model gpt-4o "创建一个复杂零件"

# 禁用缓存
python -m planner.main --no-cache "创建一个长方体"
```

## 交互模式命令

| 命令 | 说明 |
|---|---|
| 任意文字 | 作为建模指令发送给 LLM |
| `quit` / `exit` / `q` | 退出程序 |
| `clear-cache` | 清空 LLM 缓存 |
| `dry-run` | 切换模拟执行模式 |

## 缓存机制

- 使用 `diskcache` 库进行磁盘缓存
- 缓存文件存放在 `planner/.llm_cache/` 目录
- 对 (模型名, system_prompt, 用户指令) 进行 SHA256 哈希作为缓存 key
- 相同指令不会重复调用 API，节省费用
- 可通过 `--no-cache` 禁用，或在交互模式中输入 `clear-cache` 清空

## 模型选择

默认使用 `gpt-4o-mini`，性价比高且支持结构化输出。可通过 `--model` 参数切换：

- `gpt-4o-mini` — 默认，快速且便宜
- `gpt-4o` — 更强的推理能力，适合复杂建模
- 其他兼容 OpenAI 接口的模型（通过 OPENAI_BASE_URL 配置）

## 退出虚拟环境

```bash
deactivate
```

## Web Demo（可视化界面）

除命令行外，还提供了一个 ChatGPT 风格的 Web 界面用于演示。

### 安装额外依赖

```bash
pip install flask flask-cors
```

### 启动

```bash
cd C:\codes\sw\SolidWorksTutorial\LLMPlanner

# 启动 Web Demo（默认端口 5000）
python -m web.app

# 指定端口
python -m web.app --port 8080

# 指定模型
python -m web.app --model gpt-4o
```

打开浏览器访问 `http://localhost:5000`，在输入框输入自然语言建模指令即可。

### Web Demo 文件结构

```
LLMPlanner/
└── web/
    ├── app.py              # Flask 后端（复用 planner 模块）
    └── static/
        ├── index.html      # 前端页面
        ├── style.css       # 样式
        └── app.js          # 交互逻辑
```

## TODO List
- [ ] 查看 json mode 是否报错


## demo 示例
+ 创建一个凸台，底为长 100 mm，宽 50 mm 的长方体，向上拉伸 30 mm
+ 将刚才的凸台再向上拉伸 50 mm
