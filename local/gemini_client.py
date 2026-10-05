# -*- coding: utf-8 -*-
r"""gemini_client.py - Google Gemini 原生 API 客户端（2026-10-05）

背景：CI 上 OpenCode 免费层已锁死（403 FreeTierError "can only be used from
within OpenCode"）、dots key 只在本地 config.local.yaml → citizen_impact 长期
analyzed 0/50 空转。Gemini key 存 GitHub Secrets，CI（美国服务器）可直连。

**为什么走原生 :generateContent 而非 OpenAI 兼容端点**：新签发的 key 是
`AQ.` 开头（Auth key，Google 正把 AI Studio key 从 AIza 迁移过来），实测在
/v1beta/openai/chat/completions 返回 404，原生端点正常。

**为什么带可达性预检**：本地境内到 generativelanguage.googleapis.com 不可达，
且 TCP connect 是黑洞式超时（实测 socket timeout=6 仍耗时 48s —— Windows 上
socket timeout 对 connect 不生效）。若不加预检，本地每批翻译/研判都会先白等
48s 才降级。预检用线程 + join 硬超时（4s），结果**进程内缓存**，只探一次。

**通道顺序**：调用方把 Gemini 放链首（可达时优先），dots 次之。不可达时
reachable() 返回 False，调用方自然跳过。
"""
import ipaddress
import json
import socket
import threading
import urllib.error
import urllib.request
from urllib.parse import urlparse

GEMINI_HOST = "generativelanguage.googleapis.com"
GEMINI_BASE = "https://" + GEMINI_HOST + "/v1beta"

# 候选模型链（2026-10-05 CI 实测探测确定）：
#   gemini-3.5-flash-lite / 3.1-flash-lite / 3.5-flash → HTTP 200
#   gemini-2.5-flash-lite / 2.5-flash / 2.5-pro        → HTTP 404（对新用户下架）
# 按「快/便宜优先」排序，逐个尝试，404/400 换下一个（404 不耗 token，代价可忽略）。
GEMINI_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
]

_ALLOWED_HOSTS = {GEMINI_HOST}
_STATE = {"checked": False, "ok": False}


def reachable(timeout=4):
    """探测 Gemini 端点可达性（进程内缓存，只探一次）。

    本地不可达时 TCP connect 黑洞到 ~48s，故用线程 + join 硬超时兜底
    （socket timeout 对 connect 不生效）。返回 True/False。"""
    if _STATE["checked"]:
        return _STATE["ok"]
    _STATE["checked"] = True
    box = {}

    def _probe():
        try:
            s = socket.create_connection((GEMINI_HOST, 443), timeout=timeout)
            s.close()
            box["ok"] = True
        except Exception:
            box["ok"] = False

    t = threading.Thread(target=_probe, daemon=True)
    t.start()
    t.join(timeout + 2)
    _STATE["ok"] = bool(box.get("ok"))
    return _STATE["ok"]


def _post(url, payload, headers, timeout):
    """SSRF 守卫 + 线程硬超时（与 analyze._safe_ai_post 同规格，独立实现
    以免扩大 analyze 的白名单攻击面；JEV 客户端也是独立守卫）。"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") not in _ALLOWED_HOSTS:
        raise ValueError("blocked non-whitelisted endpoint: " + url)
    for info in socket.getaddrinfo(parsed.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
            raise ValueError("endpoint resolves to forbidden address: " + str(ip))
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    box = {}

    def _do():
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                box["raw"] = resp.read().decode("utf-8")
        except Exception as e:
            box["err"] = e

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    t.join(timeout + 15)
    if t.is_alive():
        raise TimeoutError("gemini endpoint unresponsive (slow-drip bypassed socket timeout)")
    if "err" in box:
        raise box["err"]
    return box["raw"]


def generate(api_key, user_prompt, system_prompt="", timeout=120, max_tokens=8192):
    """原生 generateContent 调用，遍历 GEMINI_MODELS，404/400 换下一个。

    返回文本（已 strip）；全部模型失败抛最后一个异常。
    """
    last = None
    for model in GEMINI_MODELS:
        url = GEMINI_BASE + "/models/" + model + ":generateContent"
        body = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens},
        }
        if system_prompt:
            body["system_instruction"] = {"parts": [{"text": system_prompt}]}
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
            "User-Agent": "osint/1.0",
        }
        try:
            raw = _post(url, json.dumps(body).encode("utf-8"), headers, timeout)
            result = json.loads(raw)
        except urllib.error.HTTPError as e:
            last = e
            print("[GEMINI] " + model + " HTTP " + str(e.code) + ", 尝试下一个")
            continue
        cands = result.get("candidates") or []
        if not cands:
            fb = result.get("promptFeedback") or {}
            last = ValueError("gemini no candidates: " + json.dumps(fb, ensure_ascii=False)[:200])
            continue
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text.strip():
            last = ValueError("gemini empty text (finishReason=" + str(cands[0].get("finishReason")) + ")")
            continue
        return text.strip()
    raise last if last else RuntimeError("gemini: no model available")
