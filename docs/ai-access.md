# AI 助手接入指南 · CityEngine / CGA.js / NL2CGA

三种接入方式，按助手能力选择：

| 方式 | 适用 | 说明 |
| --- | --- | --- |
| A. MCP stdio（推荐） | Claude Code、codex、Gemini CLI、Kimi Code、Qwen Code、Cursor、Cline、Cherry Studio | 本地 `pip install cityvistion-mcp`，11 个工具即装即用 |
| B. MCP 托管端点 | 支持远程 MCP（HTTP）的客户端 | `https://mcp.cgajs.com/mcp`，免安装，key 走请求头 |
| C. Function-calling / REST | DeepSeek、GLM、MiniMax、Grok、Kimi(Moonshot API)、自研智能体 | 按 `openapi.yaml` / `function-tools.json` 直接调 REST |

统一 API key：一个 key 三项目通用。认证头因服务而异（见下表），MCP 方式下只需配环境变量，工具内部自动分流。

| 服务 | 认证头 | 环境变量 |
| --- | --- | --- |
| NL2CGA | `Authorization: Bearer <key>` | `NL2CGA_API_KEY` |
| CGA.js | `x-api-key: <key>` | `CGAJS_API_KEY` |
| CityEngine | `X-API-Key: <key>` | `CITYENGINE_API_KEY` |
| 统一兜底 | — | `CITYVISTION_API_KEY`（三者未单独配置时生效） |

工具清单（11）：`nl2cga_generate_cga`、`nl2cga_get_job`、`cgajs_compile_cga`、`cgajs_get_quota`、`cityengine_parse_cga`、`cityengine_generate_cga`、`cityengine_generate_model`、`cityengine_get_job`、`cityengine_inspect_cga`(免key)、`cityengine_list_demos`(免key)、`cityengine_get_demo`(免key)、`cityengine_get_quota`。

---

## A. MCP stdio 配置（按客户端）

安装：`pip install cityvistion-mcp`（PyPI 已上架，Python ≥3.10）。

### Claude Code

项目根目录 `.mcp.json`（或 `claude mcp add` 等价）：

```json
{
  "mcpServers": {
    "cityvistion": {
      "command": "python3",
      "args": ["-m", "cityvistion_mcp.server"],
      "env": { "CITYVISTION_API_KEY": "你的key" }
    }
  }
}
```

GLM Coding（智谱，走 Claude Code 通道）配置相同。

### codex（OpenAI）

`~/.codex/config.toml`：

```toml
[mcp_servers.cityvistion]
command = "python3"
args = ["-m", "cityvistion_mcp.server"]
env = { CITYVISTION_API_KEY = "你的key" }
```

`codex mcp list` 应显示 `cityvistion` enabled。

### Gemini CLI

`~/.gemini/settings.json`：

```json
{
  "mcpServers": {
    "cityvistion": {
      "command": "python3",
      "args": ["-m", "cityvistion_mcp.server"],
      "env": { "CITYVISTION_API_KEY": "你的key" }
    }
  }
}
```

### Kimi Code

`~/.kimi-code/mcp.json`（用户级）或项目级 `.kimi-code/mcp.json`：

```json
{
  "mcpServers": {
    "cityvistion": {
      "command": "python3",
      "args": ["-m", "cityvistion_mcp.server"],
      "env": { "CITYVISTION_API_KEY": "你的key" }
    }
  }
}
```

### Qwen Code

`~/.qwen/settings.json`（与 Gemini CLI 同 schema）：

```json
{
  "mcpServers": {
    "cityvistion": {
      "command": "python3",
      "args": ["-m", "cityvistion_mcp.server"],
      "env": { "CITYVISTION_API_KEY": "你的key" }
    }
  }
}
```

### Cursor

`~/.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "cityvistion": {
      "command": "python3",
      "args": ["-m", "cityvistion_mcp.server"],
      "env": { "CITYVISTION_API_KEY": "你的key" }
    }
  }
}
```

### Cline（VS Code 扩展）

`cline_mcp_settings.json`：

```json
{
  "mcpServers": {
    "cityvistion": {
      "command": "python3",
      "args": ["-m", "cityvistion_mcp.server"],
      "env": { "CITYVISTION_API_KEY": "你的key" }
    }
  }
}
```

### Cherry Studio

设置 → MCP 服务器 → 添加：类型 stdio，命令 `python3`，参数 `-m cityvistion_mcp.server`，环境变量 `CITYVISTION_API_KEY=你的key`。

---

## B. MCP 托管端点（免安装）

端点：`https://mcp.cgajs.com/mcp`（streamable-http）。key 通过请求头传入，支持 `Authorization: Bearer <统一key>` 或分服务头 `x-cgajs-key` / `x-nl2cga-key` / `x-cityengine-key`。

支持远程 MCP 的客户端示例（Kimi Code mcp.json）：

```json
{
  "mcpServers": {
    "cityvistion": {
      "url": "https://mcp.cgajs.com/mcp",
      "headers": { "Authorization": "Bearer 你的key" }
    }
  }
}
```

---

## C. Function-calling / REST（DeepSeek / GLM / MiniMax / Grok / Moonshot 等）

1. 把 `function-tools.json` 的 `tools` 数组直接传给模型的 `tools` 参数（OpenAI 兼容格式，以上平台均支持）。
2. 模型返回 `tool_calls` 后，按每项的 `x_endpoint` 发 HTTP 请求，注入 `x_auth` 指定的认证头。
3. 把响应 JSON 作为 `role=tool` 消息回传模型。

完整 REST 契约见 `openapi.yaml`（OpenAPI 3.0，可直接导入 GPT Actions / 智能体平台）。

最小示例（DeepSeek，OpenAI SDK）：

```python
from openai import OpenAI
import json, urllib.request

client = OpenAI(api_key="DEEPSEEK_KEY", base_url="https://api.deepseek.com")
tools = json.load(open("function-tools.json"))["tools"]

def call_tool(name, args):
    if name == "cityengine_list_demos":  # 免 key
        with urllib.request.urlopen("https://www.cityengine.cn/home-demos.json") as r:
            return json.loads(r.read())
    # ... 其余按 x_endpoint 映射

msgs = [{"role": "user", "content": "看看 CityEngine 官方案例库有哪些案例"}]
resp = client.chat.completions.create(model="deepseek-chat", messages=msgs, tools=tools)
```

---

## 典型链式用法（告诉你的 AI 助手）

> 「用 nl2cga_generate_cga 把『三层临街商铺，骑楼退界』生成 CGA，再用 cgajs_compile_cga 编译成模型，最后用 cityengine_parse_cga 出官方引擎对照版本。」

三服务天然串联：NL2CGA 出规则 → CGA.js 快速编译（×1 配额）→ CityEngine 官方引擎对齐验证。
