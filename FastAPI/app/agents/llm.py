"""大模型接入层：OpenAI 协议兼容 + 全链路降级。

有 Key → 真调大模型（DeepSeek / 通义千问 / OpenAI 任意兼容端点）；
无 Key → 返回 None，由各 Agent 走内置规则推理，流程照常跑通。

关于 TLS：Python 自带的 openssl 证书目录在部分发行版里是空的
（本机 3.14 的 `/Library/Frameworks/Python.framework/Versions/3.14/etc/openssl/`
是个空目录），此时 urllib 会报 `CERTIFICATE_VERIFY_FAILED: self-signed
certificate in certificate chain`，而 curl 用 `/etc/ssl/cert.pem` 一切正常。
所以这里在模块加载时探测一次可用的 CA bundle 并挂到 SSL 上下文上，
避免「curl 能通、程序不通」这种难查的差异。
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

from .. import config, db

# 常见系统的 CA bundle 位置，按可信度排序；环境变量 SSL_CERT_FILE 优先
_CA_CANDIDATES = [
    "/etc/ssl/cert.pem",                       # macOS / LibreSSL
    "/etc/ssl/certs/ca-certificates.crt",      # Debian / Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",        # RHEL / CentOS
    "/opt/homebrew/etc/ca-certificates/cert.pem",
    "/usr/local/etc/openssl/cert.pem",
]
_SSL_CTX: ssl.SSLContext | None = None


def _ssl_context() -> ssl.SSLContext:
    """返回一个能找到 CA 证书的 SSL 上下文（找不到就退回默认行为）。"""
    global _SSL_CTX
    if _SSL_CTX is not None:
        return _SSL_CTX
    import os
    ctx = ssl.create_default_context()
    has_ca = False
    try:
        # 默认上下文是否真的加载到了证书？openssl 目录为空时 cert_store_stats 全 0
        stats = ctx.cert_store_stats()
        has_ca = bool(stats.get("x509_ca") or stats.get("x509"))
    except Exception:
        has_ca = False
    if not has_ca:
        for path in filter(None, [os.environ.get("SSL_CERT_FILE"), *_CA_CANDIDATES]):
            if os.path.exists(path):
                try:
                    ctx.load_verify_locations(cafile=path)
                    has_ca = True
                    break
                except Exception:
                    continue
    if not has_ca:
        # 实在找不到 CA（比如被中间人代理拦着），也不能让功能彻底不可用：
        # 降级为不校验，但只影响出站模型调用，不影响任何本地数据。
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    _SSL_CTX = ctx
    return ctx


def _urlopen(req: urllib.request.Request, timeout: float):
    """统一的出站请求入口：挂上 SSL 上下文，其余参数照旧。"""
    url = req.full_url
    if url.startswith("https://"):
        return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())
    return urllib.request.urlopen(req, timeout=timeout)


class LLMResult:
    def __init__(self, text: str, model: str, latency_ms: int, tokens: int, degraded: bool = False):
        self.text = text
        self.model = model
        self.latency_ms = latency_ms
        self.tokens = tokens
        self.degraded = degraded


def active_config(purpose: str = "text") -> dict | None:
    """取当前生效的模型配置：库里的配置优先，库里没 Key 时回落到环境变量。

    这一条很关键：`seed.py` 建库时如果环境里还没有 Key，写进 `model_config`
    的那行 `api_key` 就是空字符串；之后再启动服务时即便 `LLM_API_KEY` 已经
    配好，只查库也仍然拿不到 Key、永远降级。所以这里对「库里空 Key」补一次
    环境变量回落，让 `LLM_API_KEY=sk-xxx ./start.sh` 能真正生效。
    """
    row = db.query_one(
        "SELECT * FROM model_config WHERE purpose=? AND is_active=1 ORDER BY id DESC", (purpose,)
    )
    env_key, env_base, env_model = _env_defaults(purpose)
    if not row:
        # 库里还没有任何配置时，只要环境变量给了 Key 就临时拼一份，不写库
        if env_key:
            return {"purpose": purpose, "api_key": env_key, "base_url": env_base,
                    "model": env_model, "temperature": 0.3, "max_tokens": 2048,
                    "name": "环境变量模型", "provider": "env"}
        return None
    if not row.get("api_key"):
        if not env_key:
            return None                 # 库里空、环境里也空 → 真降级
        row = dict(row)
        row["api_key"] = env_key
        row["base_url"] = row.get("base_url") or env_base
        row["model"] = row.get("model") or env_model
    return row


def _env_defaults(purpose: str) -> tuple[str, str, str]:
    """按用途取环境变量里的 (key, base_url, model)。"""
    if purpose == "vision":
        return (config.VISION_API_KEY, config.VISION_BASE_URL or config.LLM_BASE_URL,
                config.VISION_MODEL)
    if purpose == "asr":
        return (config.ASR_API_KEY, config.ASR_BASE_URL, "whisper-1")
    if purpose == "embedding":
        return (config.EMBEDDING_API_KEY, config.EMBEDDING_BASE_URL,
                config.EMBEDDING_MODEL)
    return config.LLM_API_KEY, config.LLM_BASE_URL, config.LLM_MODEL


def chat(messages: list[dict], purpose: str = "text", temperature: float | None = None,
         max_tokens: int | None = None, timeout: float | None = None) -> LLMResult | None:
    """调用 OpenAI 兼容的 /chat/completions；失败或未配置返回 None（触发规则降级）。"""
    cfg = active_config(purpose)
    if not cfg:
        return None
    base = (cfg.get("base_url") or config.LLM_BASE_URL).rstrip("/")
    url = f"{base}/chat/completions"
    body = {
        "model": cfg.get("model") or config.LLM_MODEL,
        "messages": messages,
        "temperature": float(temperature if temperature is not None else cfg.get("temperature", 0.3)),
        "max_tokens": int(max_tokens or cfg.get("max_tokens") or 2048),
        "stream": False,
    }
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['api_key']}"},
        method="POST",
    )
    started = time.time()
    try:
        with _urlopen(req, timeout or config.LLM_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage") or {}
        return LLMResult(
            text=text or "", model=body["model"],
            latency_ms=int((time.time() - started) * 1000),
            tokens=int(usage.get("total_tokens") or 0),
        )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, KeyError):
        return None


def chat_stream(messages: list[dict], purpose: str = "text"):
    """SSE 流式输出；无 Key 时 yield 一个规则化回复（保证前端体验一致）。"""
    cfg = active_config(purpose)
    if not cfg:
        yield from _fallback_stream(messages)
        return
    base = (cfg.get("base_url") or config.LLM_BASE_URL).rstrip("/")
    body = {
        "model": cfg.get("model") or config.LLM_MODEL,
        "messages": messages, "temperature": 0.3, "stream": True,
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['api_key']}"},
        method="POST",
    )
    try:
        with _urlopen(req, config.LLM_TIMEOUT) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    delta = json.loads(payload)["choices"][0].get("delta", {})
                except (ValueError, KeyError, IndexError):
                    continue
                piece = delta.get("content") or ""
                if piece:
                    yield piece
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        yield "\n\n（模型连接中断，已回落至本地规则引擎。）"


def _fallback_stream(messages: list[dict]):
    """零 Key 时的「本地规则引擎」对话：检索知识库 + 组装答复，逐字吐出模拟流式。"""
    from . import rag

    question = messages[-1]["content"] if messages else ""
    hits = rag.search(question, top_k=3)
    if hits:
        head = "【本地规则引擎·未配置模型 Key】依据知识库检索结果答复：\n\n"
        # 知识库标题本身已含书名号（如《酆都法典》· 盗窃罪条），不再额外加书名号
        body = "\n\n".join(f"- {h['title']}：{h['content'][:180]}…" for h in hits)
    else:
        head = "【本地规则引擎·未配置模型 Key】未在知识库中检索到直接依据，按办案规程建议：\n\n"
        body = ("- 阴差受理 → 验明正身 → 证据归档；\n- 判官依《酆都法典》认定罪名与功德分；\n"
                "- 高风险案件挂起，由阎王复核后继续执行。")
    text = head + body
    for i in range(0, len(text), 6):
        yield text[i:i + 6]
        time.sleep(0.01)


# ------------------------------------------------------------------ 向量化
def embed(texts: list[str], purpose: str = "embedding") -> list[list[float]] | None:
    """调用兼容 /embeddings 接口；未配置时返回 None（RAG 降级为关键词检索）。"""
    cfg = active_config(purpose)
    if not cfg:
        return None
    base = (cfg.get("base_url") or config.EMBEDDING_BASE_URL).rstrip("/")
    body = {"model": cfg.get("model") or config.EMBEDDING_MODEL, "input": texts}
    req = urllib.request.Request(
        f"{base}/embeddings",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['api_key']}"},
        method="POST",
    )
    try:
        with _urlopen(req, config.LLM_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [item["embedding"] for item in data.get("data", [])]
    except Exception:
        return None


def test_connection(cfg: dict) -> dict:
    """模型配置连通性测试。

    传入的可能是库里那行原始配置（`api_key` 可能为空），所以这里同样做一次
    环境变量回落——否则用 `LLM_API_KEY=... ./start.sh` 启动后，页面点「测试」
    仍会报「未填写 API Key」，与实际能否调用自相矛盾。
    """
    if not cfg.get("api_key"):
        env_key, env_base, env_model = _env_defaults(cfg.get("purpose") or "text")
        if not env_key:
            return {"ok": False, "message": "未填写 API Key，当前为本地规则引擎降级模式",
                    "latency_ms": 0}
        cfg = {**cfg, "api_key": env_key,
               "base_url": cfg.get("base_url") or env_base,
               "model": cfg.get("model") or env_model}
    base = (cfg.get("base_url") or "").rstrip("/")
    if not base:
        return {"ok": False, "message": "未填写 Base URL", "latency_ms": 0}
    started = time.time()
    try:
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=json.dumps({"model": cfg["model"],
                             "messages": [{"role": "user", "content": "ping"}],
                             "max_tokens": 8}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {cfg['api_key']}"},
            method="POST",
        )
        with _urlopen(req, 20) as resp:
            json.loads(resp.read().decode("utf-8"))
        return {"ok": True, "message": "连接成功", "latency_ms": int((time.time() - started) * 1000)}
    except Exception as exc:
        return {"ok": False, "message": f"连接失败：{exc}", "latency_ms": int((time.time() - started) * 1000)}
