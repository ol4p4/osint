# -*- coding: utf-8 -*-
r"""model_healthcheck.py - OpenCode Zen 模型健康巡检（2026-09-13）
逐个探测 config 降级链上的模型 + 列出 /models 全量可用清单。
安全模式与 cloud/citizen_impact._safe_ai_post 一致（同函数内白名单+IP边界校验）。
最小 prompt（max_tokens=16）控成本。用法: python tools/model_healthcheck.py
"""
import ipaddress
import json
import socket
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

BASE = "https://opencode.ai/zen/v1"
AI_ALLOWED_HOST = "opencode.ai"
PROBE_MODELS = [
    "mimo-v2.5-free",                # 参谋长：主分析/研判/对话/世界观
    "nemotron-3.5-lightning-free",   # 裁判/降级1
    "deepseek-v4-flash-free",        # 降级2（translate_local 实测 400 被剔除）
]


def _zen_post(path, payload, headers, timeout):
    """仅 https + opencode.ai 白名单 + 解析结果不得指向私有/环回/保留地址（同函数内校验）"""
    url = BASE + path
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") != AI_ALLOWED_HOST:
        raise ValueError("blocked non-whitelisted AI endpoint: " + url)
    for info in socket.getaddrinfo(parsed.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
            raise ValueError("endpoint resolves to forbidden address: " + str(ip))
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def _zen_get(path, headers, timeout):
    """GET 变体，同款校验"""
    url = BASE + path
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") != AI_ALLOWED_HOST:
        raise ValueError("blocked non-whitelisted AI endpoint: " + url)
    for info in socket.getaddrinfo(parsed.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
            raise ValueError("endpoint resolves to forbidden address: " + str(ip))
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def _headers(key):
    return {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + key,
        "User-Agent": "opencode/latest/1.3.15/cli",
        "x-opencode-client": "cli",
        "x-opencode-session": "healthcheck-" + str(int(time.time())),
    }


def probe_model(key, model, timeout=60, raw=False, max_tokens=2000):
    """单模型最小请求 → (状态, 延迟s, 详情)。max_tokens 默认 2000（生产级预算：
    reasoning 模型会先消耗思考 token 再产出 content，16 会得到伪 EMPTY）"""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "只回复两个字：正常"}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }).encode("utf-8")
    t0 = time.time()
    try:
        raw_resp = _zen_post("/chat/completions", payload, _headers(key), timeout)
        dt = time.time() - t0
        data = json.loads(raw_resp)
        if raw:
            print(f"  --- {model} 原始响应 ---")
            print(json.dumps(data, ensure_ascii=False, indent=1)[:1200])
        msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
        content = msg.get("content")
        if content and str(content).strip():
            return "OK", dt, "回复: " + str(content).strip()[:24]
        keys = ",".join(msg.keys()) if msg else "无message"
        return "EMPTY", dt, f"content=None（消息字段: {keys}）"
    except Exception as e:
        return "FAIL", time.time() - t0, f"{type(e).__name__}: {str(e)[:90]}"


def list_models(key, timeout=20):
    """GET /models → 可用模型 id 列表（失败返回 []）"""
    try:
        data = json.loads(_zen_get("/models", _headers(key), timeout))
        return sorted(m.get("id", "") for m in data.get("data", []))
    except Exception as e:
        print(f"[MODELS] 列表获取失败: {type(e).__name__} {str(e)[:80]}")
        return []


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=None, help="逗号分隔的模型列表（默认探测 PROBE_MODELS）")
    ap.add_argument("--raw", action="store_true", help="打印第一个模型的原始响应")
    ap.add_argument("--max-tokens", type=int, default=2000, help="探测预算（默认 2000 生产级）")
    args = ap.parse_args()

    sys.path.insert(0, str(PROJECT / "local"))
    sys.path.insert(0, str(PROJECT / "cloud"))
    from secrets_loader import get_opencode_key
    key = get_opencode_key()
    if not key:
        print("[HEALTH] 未找到 OPENCODE_API_KEY（env / config.local.yaml 均空）")
        return 1
    models = [m.strip() for m in args.models.split(",")] if args.models else PROBE_MODELS
    print(f"[HEALTH] 探测 OpenCode Zen ({BASE}) · key 尾号 ...{key[-4:]}")
    print()
    results = {}
    for i, m in enumerate(models):
        status, dt, detail = probe_model(key, m, raw=(args.raw and i == 0),
                                         max_tokens=args.max_tokens)
        results[m] = status
        print(f"  {m:<36} {status:<6} {dt:5.1f}s  {detail}")
    print()
    alive = [m for m, s in results.items() if s == "OK"]
    print(f"[HEALTH] 结论: 存活 {len(alive)}/{len(models)} -> {', '.join(alive) if alive else '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
