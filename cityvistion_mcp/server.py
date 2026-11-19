"""CityVistion 统一 MCP server —— CityEngine / CGA.js / NL2CGA 一套工具全接入。

让任意支持 MCP 的大模型（Claude Code、codex、Gemini CLI、Kimi Code、Qwen Code、
GLM Coding、Cherry Studio、Cline、Cursor、MiniMax Agent 等）直接调用三项目云端能力：

  NL2CGA（自然语言 → CGA 规则）
    - nl2cga_generate_cga    一句话 → CGA 规则源码（同步等待）
    - nl2cga_get_job         查询生成任务状态
  CGA.js（CGA 规则 → 三维模型）
    - cgajs_compile_cga      CGA 源码编译为 GLB（js 引擎 ×1 / python 引擎 ×3 配额）
    - cgajs_get_quota        CGA.js 额度查询
  CityEngine（官方引擎 + 案例库）
    - cityengine_parse_cga       CGA + 属性 → 官方引擎解析出 GLB
    - cityengine_generate_cga    一句话 → CGA（CE 网关 NL2CGA 通道）
    - cityengine_generate_model  一句话 → 直接出三维模型（一键串联）
    - cityengine_inspect_cga     解析 CGA 可调属性（免 key）
    - cityengine_list_demos      官方案例库列表（免 key）
    - cityengine_get_demo        案例参数规格详情（免 key）
    - cityengine_get_quota       CityEngine 额度查询

鉴权（stdio 本地模式用环境变量；托管 HTTP 模式用请求头，头优先于环境变量）：
  cgajs       CGAJS_API_KEY       / 头 x-cgajs-key        （www.cgajs.com 控制台签发）
  nl2cga      NL2CGA_API_KEY      / 头 x-nl2cga-key       （www.nl2cga.com「我的 Key」）
  cityengine  CITYENGINE_API_KEY  / 头 x-cityengine-key   （www.cityengine.cn 插件 Key，cvk_ 前缀）
  统一 key    CITYVISTION_API_KEY / 头 Authorization: Bearer <key>
              （单服务未配专属 key 时回退使用统一 key 尝试调用）

只调用计算型只读端点，无任何写操作。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from fastmcp import FastMCP

try:
    from fastmcp.server.dependencies import get_http_headers
except Exception:  # stdio-only 环境
    def get_http_headers() -> dict:  # type: ignore
        return {}

# ---------------------------------------------------------------- 配置

CGAJS_API_BASE = os.environ.get("CGAJS_API_BASE", "https://www.cgajs.com/api/v1").rstrip("/")
NL2CGA_API_BASE = os.environ.get("NL2CGA_API_BASE", "https://www.nl2cga.com/api/v1").rstrip("/")
CE_GATEWAY_BASE = os.environ.get("CITYENGINE_GATEWAY_BASE", "https://api.cityengine.cn").rstrip("/")
CE_DEMOS_BASE = os.environ.get("CITYENGINE_DEMOS_BASE", "https://www.cityengine.cn").rstrip("/")
CE_INSPECT_URL = os.environ.get("CITYENGINE_INSPECT_URL", CE_DEMOS_BASE + "/api/v1/cv/inspect")

DEFAULT_OUTPUT_DIR = os.environ.get(
    "CITYVISTION_OUTPUT_DIR", str(Path.cwd() / "cityvistion_mcp_output"))
MAX_CGA_SOURCE_LEN = 200_000
MAX_TEXT_LEN = 2_000
POLL_INTERVAL_S = float(os.environ.get("CITYVISTION_POLL_INTERVAL_S", "3"))
MAX_POLL_S = float(os.environ.get("CITYVISTION_MAX_POLL_S", "300"))

CE_DEFAULT_LOT = {
    "type": "Polygon",
    "pts": [[0, 0], [30, 0], [30, 20], [0, 20]],
    "centerX": 15,
    "centerZ": 10,
}

mcp = FastMCP(
    "cityvistion",
    instructions=(
        "CityVistion 三维城市生成套件：NL2CGA（自然语言生成 CGA 规则）、"
        "CGA.js（CGA 编译为三维模型）、CityEngine（官方引擎解析/一键出模/官方案例库）。"
        "典型链路：nl2cga_generate_cga 生成规则 → cgajs_compile_cga 或 "
        "cityengine_parse_cga 出三维模型；也可 cityengine_generate_model 一键直达。"
    ),
)

# ---------------------------------------------------------------- 工具函数


class ApiError(RuntimeError):
    def __init__(self, status: int, payload: str, stage: str):
        try:
            d = json.loads(payload)
            if isinstance(d, dict) and "detail" in d:
                d = d["detail"]
            message = d.get("message", "") if isinstance(d, dict) else str(d)
        except Exception:
            message = payload[:300]
        super().__init__(f"[{stage}] HTTP {status}: {message}")
        self.status = status


def _key(service: str) -> str:
    """按服务取 key：请求头 > 服务专属环境变量 > 统一 key（头/环境变量）。"""
    headers = {k.lower(): v for k, v in (get_http_headers() or {}).items()}
    unified = (headers.get("authorization", "").removeprefix("Bearer ").strip()
               or os.environ.get("CITYVISTION_API_KEY", "").strip())
    specific = (headers.get(f"x-{service}-key", "").strip()
                or os.environ.get(f"{service.upper()}_API_KEY", "").strip())
    key = specific or unified
    if not key:
        site = {"cgajs": "https://www.cgajs.com 控制台",
                "nl2cga": "https://www.nl2cga.com「我的 Key」",
                "cityengine": "https://www.cityengine.cn 用户中心「插件 Key」"}[service]
        raise ApiError(0, json.dumps({"message": (
            f"缺少 {service} 的 API key（{site} 签发）。stdio 模式设环境变量 "
            f"{service.upper()}_API_KEY；托管模式传请求头 x-{service}-key "
            "或 Authorization: Bearer <统一 key>。")}), "config")
    return key


def _out_path(prefix: str, digest: str, output_dir: Optional[str]) -> str:
    out_dir = Path(output_dir or DEFAULT_OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    return str(out_dir / f"{prefix}_{digest[:12]}.glb")


# ---------------------------------------------------------------- NL2CGA


@mcp.tool(name="nl2cga_generate_cga")
def nl2cga_generate_cga(prompt: str, timeout_s: float = 300) -> dict:
    """用一句自然语言生成 CGA 规则源码（NL2CGA，同步等待直到完成，不渲染模型）。

    Args:
        prompt: 自然语言建模描述，如「三层临街商铺，骑楼退界」。
        timeout_s: 最长等待秒数（上限 300）。

    Returns:
        job_id、status、cga_source（CGA 规则全文，可喂给 cgajs_compile_cga /
        cityengine_parse_cga）、usage。
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt 不能为空")
    headers = {"Authorization": "Bearer " + _key("nl2cga"),
               "User-Agent": "cityvistion-mcp/0.1.2"}
    with httpx.Client(timeout=150.0) as client:
        resp = client.post(NL2CGA_API_BASE + "/cga/generate",
                           json={"prompt": prompt}, headers=headers)
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text, "POST /cga/generate")
    result = resp.json()
    job_id = result.get("job_id")
    deadline = time.time() + min(timeout_s, MAX_POLL_S)
    while True:
        status = (result.get("status") or "").lower()
        if status == "succeeded":
            cga = result.get("cga") or ""
            if not cga.strip():
                raise ApiError(0, '{"message":"job succeeded but returned empty CGA"}', "generate")
            return {"job_id": job_id, "status": "succeeded", "cga_source": cga,
                    "usage": result.get("usage")}
        if status in ("failed", "cancelled"):
            raise ApiError(0, json.dumps({"message": "generation %s: %s" % (
                status, result.get("error") or "")}), "generate")
        if not job_id:
            raise ApiError(0, json.dumps({"message": "no job_id in response"}), "generate")
        if time.time() > deadline:
            raise ApiError(0, json.dumps({"message":
                "轮询超时，可稍后调用 nl2cga_get_job('%s') 查询" % job_id}), "generate")
        time.sleep(POLL_INTERVAL_S)
        with httpx.Client(timeout=30.0) as client:
            r = client.get(NL2CGA_API_BASE + "/cga/jobs/" + job_id, headers=headers)
        if r.status_code != 200:
            raise ApiError(r.status_code, r.text, "GET /cga/jobs")
        result = r.json()


@mcp.tool(name="nl2cga_get_job")
def nl2cga_get_job(job_id: str) -> dict:
    """查询 NL2CGA 生成任务状态（queued/running/succeeded/failed）与结果。

    Args:
        job_id: nl2cga_generate_cga 返回的 job_id。
    """
    if not job_id or not job_id.strip():
        raise ValueError("job_id 不能为空")
    headers = {"Authorization": "Bearer " + _key("nl2cga"),
               "User-Agent": "cityvistion-mcp/0.1.2"}
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(NL2CGA_API_BASE + "/cga/jobs/" + job_id.strip(), headers=headers)
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text, "GET /cga/jobs")
    result = resp.json()
    return {"job_id": job_id, "status": result.get("status"),
            "cga_source": result.get("cga"), "error": result.get("error"),
            "usage": result.get("usage")}


# ---------------------------------------------------------------- CGA.js


@mcp.tool(name="cgajs_compile_cga")
def cgajs_compile_cga(
    cga_source: str,
    engine: str = "",
    download_glb: bool = True,
    output_dir: Optional[str] = None,
) -> dict:
    """把 CGA 规则源码编译为三维模型（GLB），经 CGA.js 云端引擎。

    Args:
        cga_source: CGA 规则源码全文（需含 @StartRule 与 Lot 规则）。
        engine: "" = js（默认，×1 配额）；"python" = 服务器原生引擎（×3 配额，
                对 CityEngine 方言兼容性更好）。
        download_glb: 是否把 glb 落盘（默认 True）。
        output_dir: glb 落盘目录。

    Returns:
        success、engine、metadata（vertexCount 等）、glb 本地路径、字节数、sha256；
        失败抛出带服务端错误信息的异常（错误信息含行号，可用于修正 CGA 后重试）。
    """
    if not cga_source or not cga_source.strip():
        raise ValueError("cga_source 不能为空")
    if len(cga_source) > MAX_CGA_SOURCE_LEN:
        raise ValueError(f"cga_source 超过契约上限 {MAX_CGA_SOURCE_LEN} 字符")
    body: dict[str, Any] = {
        "source": cga_source,
        "initialShape": {"geometry": {"type": "box", "width": 12, "height": 0, "depth": 8}},
    }
    if engine:
        body["engine"] = engine
    with httpx.Client(timeout=90.0) as client:
        resp = client.post(CGAJS_API_BASE + "/compile", json=body,
                           headers={"x-api-key": _key("cgajs"),
                                    "User-Agent": "cityvistion-mcp/0.1.2"})
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text, "POST /compile")
    result = resp.json()
    if not result.get("success"):
        errs = result.get("errors") or []
        msg = "; ".join(
            e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in errs
        ) or result.get("error") or "compile failed"
        raise ApiError(0, json.dumps({"message": msg}), "compile")
    glb = base64.b64decode(result.get("model") or "")
    if glb[:4] != b"glTF":
        raise ApiError(0, '{"message":"model payload is not GLB (bad magic)"}', "compile")
    sha = hashlib.sha256(glb).hexdigest()
    out: dict[str, Any] = {
        "success": True,
        "engine": result.get("engine") or (result.get("metadata") or {}).get("engine") or "js",
        "metadata": result.get("metadata"),
        "size_bytes": len(glb),
        "sha256": sha,
    }
    if download_glb:
        out["glb_path"] = _out_path("cgajs", sha, output_dir)
        Path(out["glb_path"]).write_bytes(glb)
    return out


@mcp.tool(name="cgajs_get_quota")
def cgajs_get_quota() -> dict:
    """查询 CGA.js 当前 API key 的套餐档位与本月额度用量。"""
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(CGAJS_API_BASE + "/quota",
                          headers={"x-api-key": _key("cgajs")})
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text, "GET /quota")
    return resp.json()


# ---------------------------------------------------------------- CityEngine


def _ce_request(method: str, path: str, body: Optional[dict] = None,
                expect: int = 200, timeout: float = 30.0, retries: int = 3) -> dict:
    key = _key("cityengine")
    url = path if path.startswith("http") else CE_GATEWAY_BASE + path
    headers = {"X-API-Key": key, "User-Agent": "cityvistion-mcp/0.1.2"}
    for attempt in range(retries + 1):
        with httpx.Client(timeout=timeout) as client:
            resp = client.request(method, url, json=body, headers=headers)
        if resp.status_code == 429 and attempt < retries:
            try:
                retry_after = (resp.json().get("detail") or {}).get("retry_after")
            except Exception:
                retry_after = None
            time.sleep(min(retry_after or 2, 60) + 0.2)
            continue
        if resp.status_code != expect:
            raise ApiError(resp.status_code, resp.text, f"{method} {path}")
        return resp.json()
    raise ApiError(429, '{"message":"retry budget exhausted"}', f"{method} {path}")


def _ce_wait_job(job_id: str, timeout_s: float) -> dict:
    deadline = time.time() + min(timeout_s, MAX_POLL_S)
    while True:
        job = _ce_request("GET", f"/v1/jobs/{job_id}")
        status = job.get("status")
        if status == "succeeded":
            return job
        if status == "failed":
            err = job.get("error") or {}
            raise ApiError(0, json.dumps({"message": err.get("message", "job failed")}),
                           f"job {job_id}")
        if time.time() > deadline:
            raise ApiError(0, json.dumps({"message":
                f"job {job_id} 轮询超时，可稍后凭 job_id 到网关 GET /v1/jobs/{job_id} 查询"}),
                f"job {job_id}")
        time.sleep(2)


def _ce_usage(d: dict) -> dict:
    usage = d.get("usage") or {}
    return {"quota_kind": usage.get("quota_kind"),
            "quota_cost": usage.get("quota_cost"),
            "quota_remaining": usage.get("quota_remaining"),
            "engine_ms": usage.get("engine_ms")}


def _ce_download_glb(gltf: dict, job_id: str, output_dir: Optional[str]) -> dict:
    url = gltf["url"]
    if not url.startswith("http"):
        url = CE_GATEWAY_BASE + "/" + url.lstrip("/")
    with httpx.Client(timeout=120.0) as client:
        resp = client.get(url)
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text, f"download {gltf['url']}")
    data = resp.content
    sha = hashlib.sha256(data).hexdigest()
    expected = gltf.get("sha256")
    if expected and sha != expected:
        raise ApiError(0, json.dumps({"message":
            f"glb 校验失败：期望 {expected}，实际 {sha}"}), "download")
    path = _out_path("cityengine", sha, output_dir)
    Path(path).write_bytes(data)
    return {"glb_path": path, "size_bytes": len(data), "sha256": sha}


@mcp.tool(name="cityengine_parse_cga")
def cityengine_parse_cga(
    cga_source: str,
    attributes: Optional[dict] = None,
    initial_shape: Optional[dict] = None,
    seed: Optional[int] = None,
    download_glb: bool = True,
    output_dir: Optional[str] = None,
    timeout_s: float = 300,
) -> dict:
    """用 CityEngine 官方引擎解析 CGA 规则源码，生成三维模型（GLB）。

    Args:
        cga_source: CGA 规则源码全文。
        attributes: CGA attr 覆盖，如 {"height": 30}（键须在规则 attr 声明内）。
        initial_shape: 初始地块多边形 {"type":"Polygon","pts":[[x,z],...]}，米制。
        seed: 随机种子（固定后可复现）。
        download_glb / output_dir / timeout_s: 同 cgajs_compile_cga。

    Returns:
        job_id、模型统计、gltf 签名下载地址(24h)、glb 本地路径、sha256、usage、warnings。
    """
    if not cga_source or not cga_source.strip():
        raise ValueError("cga_source 不能为空")
    if len(cga_source) > MAX_CGA_SOURCE_LEN:
        raise ValueError(f"cga_source 超过契约上限 {MAX_CGA_SOURCE_LEN} 字符")
    body: dict[str, Any] = {
        "cga_source": cga_source,
        "initial_shape": initial_shape or CE_DEFAULT_LOT,
    }
    if attributes:
        body["attributes"] = attributes
    if seed is not None:
        body["seed"] = seed
    job = _ce_request("POST", "/v1/generate", body, expect=202, timeout=30)
    job = _ce_wait_job(job["job_id"], timeout_s)
    result = job.get("result") or {}
    gltf = result.get("gltf") or {}
    out: dict[str, Any] = {
        "job_id": job.get("job_id"), "status": "succeeded",
        "stats": gltf.get("stats"), "gltf_url": gltf.get("url"),
        "gltf_expires_at": gltf.get("expires_at"), "sha256": gltf.get("sha256"),
        "size_bytes": gltf.get("size_bytes"),
        "warnings": result.get("warnings") or [], "usage": _ce_usage(job),
    }
    if download_glb and gltf.get("url"):
        out["glb"] = _ce_download_glb(gltf, out["job_id"], output_dir)
    return out


@mcp.tool(name="cityengine_generate_cga")
def cityengine_generate_cga(
    text: Optional[str] = None,
    base_cga: Optional[str] = None,
    instruction: Optional[str] = None,
) -> dict:
    """自然语言 → CGA 规则源码（CityEngine 网关 NL2CGA 通道，不渲染）。

    两种模式二选一：
      1. text：自然语言描述，如「三层小楼，坡屋顶，底层商铺」。
      2. base_cga + instruction：在既有 CGA 上按指令修改。

    Returns:
        cga_source、engine_path、warnings、usage。
    """
    if bool(text) == bool(base_cga and instruction):
        raise ValueError("text 与 (base_cga + instruction) 必须二选一")
    if text and len(text) > MAX_TEXT_LEN:
        raise ValueError(f"text 超过契约上限 {MAX_TEXT_LEN} 字符")
    body: dict[str, Any] = {"text": text} if text else {
        "cga_edit": {"base_cga": base_cga, "instruction": instruction}}
    resp = _ce_request("POST", "/v1/nl2cga", body, expect=200, timeout=150)
    return {"request_id": resp.get("request_id"),
            "cga_source": resp.get("cga_source"),
            "engine_path": resp.get("engine_path"),
            "warnings": resp.get("warnings") or [], "usage": _ce_usage(resp)}


@mcp.tool(name="cityengine_generate_model")
def cityengine_generate_model(
    text: Optional[str] = None,
    base_cga: Optional[str] = None,
    instruction: Optional[str] = None,
    attributes: Optional[dict] = None,
    initial_shape: Optional[dict] = None,
    seed: Optional[int] = None,
    download_glb: bool = True,
    output_dir: Optional[str] = None,
    timeout_s: float = 300,
) -> dict:
    """自然语言一句话 → 直接生成三维模型（NL2CGA + CityEngine 引擎一键串联）。

    两种模式二选一：
      1. text：自然语言描述，如「生成一栋 12 层现代玻璃幕墙办公楼」。
      2. base_cga + instruction：回传既有 CGA + 修改指令，二次生成再出模型。

    Returns:
        cga_source（可供 cga_edit 回传）、模型统计、glb 本地路径、签名下载地址、
        sha256、usage、warnings。
    """
    if bool(text) == bool(base_cga and instruction):
        raise ValueError("text 与 (base_cga + instruction) 必须二选一")
    if text and len(text) > MAX_TEXT_LEN:
        raise ValueError(f"text 超过契约上限 {MAX_TEXT_LEN} 字符")
    body: dict[str, Any] = {"initial_shape": initial_shape or CE_DEFAULT_LOT}
    if text:
        body["text"] = text
    else:
        body["cga_edit"] = {"base_cga": base_cga, "instruction": instruction}
    if attributes:
        body["attributes"] = attributes
    if seed is not None:
        body["seed"] = seed
    job = _ce_request("POST", "/v1/nl2model", body, expect=202, timeout=30)
    job = _ce_wait_job(job["job_id"], timeout_s)
    result = job.get("result") or {}
    gltf = result.get("gltf") or {}
    out: dict[str, Any] = {
        "job_id": job.get("job_id"), "status": "succeeded",
        "cga_source": result.get("cga_source"),
        "stats": gltf.get("stats"), "gltf_url": gltf.get("url"),
        "gltf_expires_at": gltf.get("expires_at"), "sha256": gltf.get("sha256"),
        "size_bytes": gltf.get("size_bytes"),
        "warnings": result.get("warnings") or [], "usage": _ce_usage(job),
    }
    if download_glb and gltf.get("url"):
        out["glb"] = _ce_download_glb(gltf, out["job_id"], output_dir)
    return out


@mcp.tool(name="cityengine_inspect_cga")
def cityengine_inspect_cga(cga_source: str) -> dict:
    """解析 CGA 源码的可调属性（名称/类型/默认值/取值范围/枚举），免 API key。

    与 cityengine.cn 网页端 /api/v1/cv/inspect 同一接口，适合在渲染前自检参数面板。

    Args:
        cga_source: CGA 规则源码全文。
    """
    if not cga_source or not cga_source.strip():
        raise ValueError("cga_source 不能为空")
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(CE_INSPECT_URL, json={"source": cga_source})
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text, "POST /api/v1/cv/inspect")
    data = resp.json()
    return {"success": data.get("success"), "attrs": data.get("attrs") or []}


@mcp.tool(name="cityengine_list_demos")
def cityengine_list_demos() -> list[dict]:
    """列出 CityEngine 官方案例库（id、名称、引擎、参数个数、缩略图），免 API key。"""
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(CE_DEMOS_BASE + "/home-demos.json")
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text, "GET home-demos.json")
    return [{
        "id": d.get("id"), "name": d.get("name"), "engine": d.get("engine"),
        "source_date": d.get("sourceDate"), "attr_count": len(d.get("attrs") or []),
        "viewer_url": f"{CE_DEMOS_BASE}/simple.html?demo={d.get('id')}",
    } for d in resp.json()]


@mcp.tool(name="cityengine_get_demo")
def cityengine_get_demo(demo_id: str) -> dict:
    """查看官方案例的参数规格（属性 schema/规模/引擎指标），免 API key。

    Args:
        demo_id: cityengine_list_demos 返回的 id，如 building-glass-20260928。
    """
    with httpx.Client(timeout=60.0) as client:
        r1 = client.get(CE_DEMOS_BASE + "/home-demos.json")
        r2 = client.get(f"{CE_DEMOS_BASE}/home-scenes/{demo_id}.json")
    if r1.status_code != 200:
        raise ApiError(r1.status_code, r1.text, "GET home-demos.json")
    entry = next((d for d in r1.json() if d.get("id") == demo_id), None)
    if entry is None:
        raise ApiError(404, json.dumps({"message": f"demo '{demo_id}' 不在在册清单"}),
                       "get_demo")
    out: dict[str, Any] = {
        "id": demo_id, "name": entry.get("name"), "engine": entry.get("engine"),
        "source_date": entry.get("sourceDate"), "attrs": entry.get("attrs") or [],
        "viewer_url": f"{CE_DEMOS_BASE}/simple.html?demo={demo_id}",
    }
    if r2.status_code == 200:
        scene = (r2.json() or {}).get("scene") or {}
        out["snapshot"] = {
            "nodes": len(scene.get("nodes") or []),
            "materials": len(scene.get("materials") or []),
            "warnings": scene.get("warnings") or [],
            "timing": (scene.get("meta") or {}).get("timing"),
        }
    else:
        out["snapshot"] = None
        out["snapshot_notice"] = f"快照不可用（HTTP {r2.status_code}）"
    return out


@mcp.tool(name="cityengine_get_quota")
def cityengine_get_quota() -> dict:
    """查询 CityEngine 当前 API key 的套餐、额度用量（generate/NL2CGA）与限流参数。"""
    return _ce_request("GET", "/v1/account/quota")


# ---------------------------------------------------------------- 入口


def main() -> None:
    transport = os.environ.get("CITYVISTION_MCP_TRANSPORT", "stdio")
    if not (os.environ.get("CGAJS_API_KEY") or os.environ.get("NL2CGA_API_KEY")
            or os.environ.get("CITYENGINE_API_KEY") or os.environ.get("CITYVISTION_API_KEY")):
        print("[cityvistion-mcp] 提示：未设置任何 API key 环境变量，"
              "调用工具时会按服务报配置错误", file=sys.stderr)
    if transport == "streamable-http":
        host = os.environ.get("CITYVISTION_MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("CITYVISTION_MCP_PORT", "8300"))
        mcp.run(transport="streamable-http", host=host, port=port, path="/mcp")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
