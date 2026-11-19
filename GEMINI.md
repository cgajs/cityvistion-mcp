# CityVistion (CityEngine / CGA.js / NL2CGA)

You have the `cityvistion` MCP server: 11 tools covering the full
"natural language → CGA rules → 3D model (GLB)" pipeline.

Typical chains:
- `nl2cga_generate_cga` (prompt → CGA source) → `cgajs_compile_cga` (CGA → GLB, ×1 quota)
- `cityengine_generate_model` (prompt → GLB directly, official engine)
- `cityengine_parse_cga` (CGA → GLB via official CityEngine engine, for alignment checks)
- Free, key-less: `cityengine_inspect_cga`, `cityengine_list_demos`, `cityengine_get_demo`

Rules:
- CGA source must contain `@StartRule` and a `Lot` rule before compiling.
- If `cgajs_compile_cga` fails with engine-dialect errors, retry with `engine="python"` or use `cityengine_parse_cga`.
- Use `cgajs_get_quota` / `cityengine_get_quota` when the user asks about remaining quota.
- Demo viewer URLs look like https://www.cityengine.cn/simple.html?demo={id}
