# cityvistion-mcp

mcp-name: com.cgajs/cityvistion-mcp

一套 MCP server 打通三个云端服务，让任何支持 MCP 的大模型客户端（Claude Code、codex、Gemini CLI、Kimi Code、Qwen Code、GLM Coding、Cherry Studio、Cline、Cursor、MiniMax Agent…）直接完成「一句话 → CGA 规则 → 三维模型」全链路。

- **NL2CGA**：自然语言生成 CGA 规则源码（`nl2cga_generate_cga`）
- **CGA.js**：CGA 规则在线编译为三维模型 GLB（`cgajs_compile_cga`）
- **CityEngine**：官方引擎解析、一句话直接出模、官方案例库查询（`cityengine_*`，其中 `inspect_cga` / `list_demos` / `get_demo` 免 key）

## 安装

```bash
pip install cityvistion-mcp
# 或 uvx cityvistion-mcp（免安装直接跑）
```

## API Key

| 服务 | 环境变量 | 签发地址 |
|---|---|---|
| CGA.js | `CGAJS_API_KEY` | https://www.cgajs.com 控制台 |
| NL2CGA | `NL2CGA_API_KEY` | https://www.nl2cga.com 「我的 Key」 |
| CityEngine | `CITYENGINE_API_KEY` | https://www.cityengine.cn 用户中心「插件 Key」 |
| 统一 key | `CITYVISTION_API_KEY` | 单服务未配专属 key 时回退尝试 |

## 客户端配置

**Claude Code**（`.mcp.json` 或 `claude mcp add`）：

```json
{
  "mcpServers": {
    "cityvistion": {
      "command": "uvx",
      "args": ["cityvistion-mcp"],
      "env": {
        "CGAJS_API_KEY": "<你的key>",
        "NL2CGA_API_KEY": "<你的key>",
        "CITYENGINE_API_KEY": "<你的key>"
      }
    }
  }
}
```

**codex CLI**（`~/.codex/config.toml`）：

```toml
[mcp_servers.cityvistion]
command = "uvx"
args = ["cityvistion-mcp"]
env = { CGAJS_API_KEY = "<你的key>", NL2CGA_API_KEY = "<你的key>", CITYENGINE_API_KEY = "<你的key>" }
```

**Gemini CLI**（`~/.gemini/settings.json`）、**Kimi Code**（`~/.kimi/config.toml`）、**Qwen Code**、**GLM Coding**：同上结构，详见 https://www.cgajs.com/ai-access.html

**托管模式（免本地安装）**：`https://mcp.cgajs.com/mcp`（streamable-http），在客户端填 URL，key 用请求头 `x-cgajs-key` / `x-nl2cga-key` / `x-cityengine-key` 或 `Authorization: Bearer <统一key>` 传递。

## 典型用法（对模型说）

> 用 nl2cga 生成「两层坡屋顶小屋」的 CGA，再用 cgajs 编译成模型，告诉我顶点数和文件路径。

## License

MIT
