# TT Switch API 调用示例

本机通过 WSL 运行了一个 OpenAI 兼容网关，把腾讯 CodeBuddy 的模型（Claude、GPT、Gemini、
GLM、Kimi、DeepSeek、混元等）包装成标准 OpenAI API。任何支持 OpenAI 接口的 SDK 或工具，
改一下 `base_url` 和 `api_key` 就能用。

```
你的程序 → LiteLLM 网关 127.0.0.1:4200 → TT Switch（WSL 内）→ copilot.tencent.com
```

## 连接信息

| 项 | 值 |
|---|---|
| Base URL | `http://127.0.0.1:4200/native` |
| API Key | `sk-ttsw-local` |
| 接口格式 | OpenAI Chat Completions（`/chat/completions`、`/models`） |

只能在本机访问。`/native` 会把请求原样透传给上游，流式 usage 和腾讯计费字段（`credit` 等）
都能完整拿到，建议一律用它。网关另外还有一条 `/v1` 路径，会改写请求，一般用不到。

## 启动网关

电脑重启后网关不会自动运行，先在 PowerShell 里执行：

```powershell
wsl -d Ubuntu -u fansq -- bash -c "cd ~/ttsw-gateway && LITELLM_BIN=~/litellm-venv/bin/litellm ./start.sh"
```

看到 `LiteLLM 运行中 :4200` 就可以用了。启动时如果 4200 被占用，网关会自动换成下一个空闲端口，
以输出里显示的端口为准。停止服务把 `./start.sh` 换成 `./stop.sh`。

## 运行示例

需要 Python 和 `openai` 包（`pip install openai`）。

| 脚本 | 内容 |
|---|---|
| `01_basic_chat.py` | 单轮对话 |
| `02_stream.py` | 流式输出 |
| `03_multi_turn_cache.py` | 多轮对话，带 `X-Session-Id` 以命中 prompt 缓存 |
| `04_tool_call.py` | 工具调用（function calling） |
| `05_list_models.py` | 列出全部可用模型 |

每个脚本都可以在命令行后面跟一个模型名来切换模型：

```powershell
python 01_basic_chat.py claude-opus-5
python 02_stream.py deepseek-v4-pro-ioa
```

端口或 key 变了的话，用环境变量覆盖，不用改代码：

```powershell
$env:TTSW_BASE_URL = "http://127.0.0.1:4201/native"
$env:TTSW_API_KEY = "sk-ttsw-local"
```

## 最小代码

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:4200/native", api_key="sk-ttsw-local")
resp = client.chat.completions.create(
    model="gpt-5.5",
    messages=[{"role": "user", "content": "你好"}],
)
print(resp.choices[0].message.content)
```

不用 Python 的话，直接发 HTTP 请求也行（PowerShell）。响应没有声明字符集，Windows PowerShell 5.1
会把中文解码成乱码，所以这里手动按 UTF-8 解码：

```powershell
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$body = @{ model = "gpt-5.5"; messages = @(@{ role = "user"; content = "你好" }) } | ConvertTo-Json -Depth 5
$r = Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:4200/native/chat/completions -Method Post `
  -Headers @{ Authorization = "Bearer sk-ttsw-local" } -ContentType "application/json; charset=utf-8" `
  -Body ([Text.Encoding]::UTF8.GetBytes($body))
([Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) | ConvertFrom-Json).choices[0].message.content
```

## 可用模型

共 37 个，2026-10-01 逐个实测过。除 `hy4-dev` 当时上游返回"暂时不可用"（错误码 11134）
外，其余 36 个都能正常返回。所有模型都支持推理和工具调用，除 `glm-5.2-internal-ioa` 外都支持图片输入。

"上下文"是最大输入 token 数，"输出"是最大输出 token 数。"倍率"是 CodeBuddy 目录里的相对
计费倍率，数字越大越贵，只用来横向比较，不是美元价格。

### Claude

| 模型 | 上下文 | 输出 | 倍率 |
|---|---|---|---|
| `claude-opus-5` | 1M | 128K | 3.33 |
| `claude-opus-4.8` | 176K | 64K | 3.33 |
| `claude-opus-4.8-1m` | 1M | 128K | 3.33 |
| `claude-opus-4.7` | 176K | 64K | 3.33 |
| `claude-opus-4.7-1m` | 1M | 128K | 3.33 |
| `claude-opus-4.6` | 176K | 24K | 3.33 |
| `claude-opus-4.6-1m` | 1M | 64K | 3.33 |
| `claude-sonnet-5` | 200K | 64K | 1.33 |
| `claude-sonnet-5-1m` | 1M | 128K | 1.33 |
| `claude-sonnet-4.6` | 176K | 24K | 2.00 |
| `claude-sonnet-4.6-1m` | 1M | 24K | 2.00 |

### OpenAI GPT

| 模型 | 上下文 | 输出 | 倍率 |
|---|---|---|---|
| `gpt-5.6-sol` | 1M | 128K | 3.47 |
| `gpt-5.6-terra` | 1M | 128K | 1.39 |
| `gpt-5.6-luna` | 1M | 128K | 0.14 |
| `gpt-5.5` | 1M | 128K | 3.31 |
| `gpt-5.4` | 272K | 128K | 1.65 |
| `gpt-5.3-codex` | 272K | 128K | 1.25 |
| `ttsw-gpt-5.6-sol-272k` | 272K | 128K | 3.47 |
| `ttsw-gpt-5.6-terra-272k` | 272K | 128K | 1.39 |
| `ttsw-gpt-5.6-luna-272k` | 272K | 128K | 0.14 |

`ttsw-` 开头的三个是 TT Switch 提供的预设，底层就是对应的 `gpt-5.6-*`，只是把上下文限制在
272K，用于低成本档。

### Gemini

| 模型 | 上下文 | 输出 | 倍率 |
|---|---|---|---|
| `gemini-3.1-pro` | 400K | 64K | 1.32 |
| `gemini-3.5-flash` | 1M | 64K | 0.99 |

### 国产模型

| 模型 | 厂商 | 上下文 | 输出 | 倍率 |
|---|---|---|---|---|
| `deepseek-v4-pro-ioa` | DeepSeek | 1M | 50K | 0.51 |
| `deepseek-v4-flash-ioa` | DeepSeek | 1M | 50K | 0.17 |
| `glm-5.3-ioa` | 智谱 GLM | 1M | 48K | 0.79 |
| `glm-5.3-flash-ioa` | 智谱 GLM | 1M | 32K | 0.06 |
| `glm-5.2-ioa` | 智谱 GLM | 1M | 48K | 0.79 |
| `glm-5.2-internal-ioa` | 智谱 GLM | 200K | 48K | 0.79（不支持图片） |
| `glm-5v-turbo-ioa` | 智谱 GLM | 200K | 38K | 0.71 |
| `kimi-k3-ioa` | Kimi | 1M | 32K | 1.62 |
| `kimi-k2.7-ioa` | Kimi | 256K | 32K | 0.65 |
| `kimi-k2.6-ioa` | Kimi | 256K | 32K | 0.52 |
| `minimax-m3-ioa` | MiniMax | 512K | 48K | 0.25 |
| `minimax-m2.7-ioa` | MiniMax | 200K | 48K | 0.19 |
| `hy4-preview-ioa` | 腾讯混元 | 1M | 64K | 0 |
| `hy3-ioa` | 腾讯混元 | 192K | 64K | 0 |
| `hy4-dev` | 腾讯混元 | 1M | 64K | 0（实测时上游暂不可用） |

### 怎么选

| 场景 | 推荐 |
|---|---|
| 最强效果 | `claude-opus-5`、`gpt-5.6-sol`、`gpt-5.5` |
| 效果和成本平衡 | `claude-sonnet-5`、`gpt-5.6-terra`、`gemini-3.1-pro` |
| 便宜、量大的批处理 | `gpt-5.6-luna`、`glm-5.3-flash-ioa`、`deepseek-v4-flash-ioa` |
| 写代码 | `gpt-5.3-codex`、`claude-sonnet-5` |
| 超长上下文 | 带 `-1m` 后缀的或上下文为 1M 的模型 |

实时的模型列表以 `python 05_list_models.py` 的输出为准。

## 注意事项

- **多轮对话要带 `X-Session-Id`。** 同一轮对话的每个请求都带同一个值，prompt 缓存才能命中，
  见 `03_multi_turn_cache.py`。不带的话缓存完全不命中，`gpt-5.6-sol` 这类缓存写入收费的模型
  反而更贵。prompt 太短时上游本来也不缓存，所以示例里的短对话看不到命中。
- **推理模型的 `max_tokens` 别设太小。** 这些模型都会先思考再回答，思考也占输出 token。
  `max_tokens` 只给十几个时，`claude-opus-5` 这类模型可能把额度全用在思考上，返回空内容。
- **返回的 `usage` 里有腾讯计费字段。** 例如 `credit`（本次消耗）、`prompt_cache_hit_tokens`
  （缓存命中的 token 数）、`completion_thinking_tokens`（思考 token 数），可以用来观察成本。
- **可以传 `reasoning_effort`。** 例如 `reasoning_effort="low"`，在 `/native` 上会原样透传给上游。

## 出错了怎么办

| 现象 | 原因与处理 |
|---|---|
| 连接被拒绝 / 超时 | 网关没启动，执行上面的启动命令 |
| 返回 500，且没带 API Key | LiteLLM 无 key 时返回 500 而不是 401，检查 `api_key` |
| `HTTP 401: inactive` | CodeBuddy 凭据失效，需要换新 token（在 WSL 里执行 `cd ~/ttsw-gateway && ./set_key.py`） |
| `does not exist in the Tencent catalog` | 模型名写错了，或不在本地目录里，对照上面的列表 |
| 错误码 11134 | 上游该模型临时不可用，稍后重试或换一个模型 |

查看日志（在 WSL 里）：

```bash
tail -f ~/ttsw-gateway/litellm.log          # 网关日志
~/ttsw-gateway/bin/tt-switch-cli log | tail  # TT Switch 日志
```
