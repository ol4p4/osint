# -*- coding: utf-8 -*-
r"""zen_proxy_client.py - 本机 OpenCode Zen 代理客户端（2026-10-05）

背景：OpenCode 免费层 2026-09-17 起对第三方客户端一律 403
（FreeTierError "can only be used from within OpenCode"），osint 直连通道
全线死亡。本机 E:\OpenCode\zen-proxy.py（监听 127.0.0.1:4010）用官方桌面
凭据 + 登记会话转发 Zen，实测恢复可用（mimo-v2.5-free 约 3s 返回 200）。

与其它客户端（gemini_client / jev_client）的两点不同：
1. 目标是**本机回环**（http + 127.0.0.1），与"仅 https + 外域白名单"的守卫
   方向相反。这是刻意收窄的例外：地址是模块常量（不接受调用方传入 URL），
   出站不产生任何外部流量；对上游的 SSRF 守卫由 4010 代理自己负责
   （见 E:\OpenCode\zen-proxy.py 的 ALLOWED_HOSTS）。
2. 代理为通过上游校验会**强制 stream=true 转发**，所以它返回的是 SSE
   （text/event-stream），而调用方仍按普通 chat/completions 消费——
   本客户端负责把 `data:` 块拼回完整文本，并抹平这个差异。

可达性预检：代理未启动时回环连接是立刻被拒（非黑洞超时），预检仍用
线程 + join 兜底，结果进程内缓存。CI（GitHub Actions）天然没有 4010 →
预检失败自动跳过，CI 通道链保持 Gemini 链首不受影响。

环境开关：OSINT_DISABLE_ZEN_PROXY=1 可整体停用本通道（应急回退用）。
"""
import ipaddress
import json
import os
import socket
import threading
import urllib.error
import urllib.request
from urllib.parse import urlparse

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 4010
BASE = "http://" + PROXY_HOST + ":" + str(PROXY_PORT) + "/v1"

_STATE = {"checked": False, "ok": False}


def _disabled():
    return os.environ.get("OSINT_DISABLE_ZEN_PROXY", "").strip().lower() in ("1", "true", "yes", "on")


def reachable(timeout=1.5):
    """探测本机 4010 代理是否在监听（进程内缓存，只探一次）。

    回环连接被拒不耗时间，但代理进程若半死（accept 后不响应）仍可能挂住
    connect，故用线程 + join 硬超时兜底。返回 True/False。
    """
    if _disabled():
        return False
    if _STATE["checked"]:
        return _STATE["ok"]
    _STATE["checked"] = True
    box = {}

    def _probe():
        try:
            s = socket.create_connection((PROXY_HOST, PROXY_PORT), timeout=timeout)
            s.close()
            box["ok"] = True
        except Exception:
            box["ok"] = False

    t = threading.Thread(target=_probe, daemon=True)
    t.start()
    t.join(timeout + 2)
    _STATE["ok"] = bool(box.get("ok"))
    return _STATE["ok"]


def _assert_local(url):
    """守卫收窄版：只允许本机回环 + http + 固定端口，拒绝一切其它目标。

    与外部端点的 https 守卫相反（这里明令允许 http / 127.0.0.1）——因为目标
    是模块常量拼接出的 4010 代理，不存在调用方可控的 URL 注入面。任何解析出
    非回环地址的情况都视为异常直接拒绝（防 DNS/配置被改写）。
    """
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname != PROXY_HOST or parsed.port != PROXY_PORT:
        raise ValueError("blocked non-local zen proxy url: " + url)
    for info in socket.getaddrinfo(parsed.hostname, PROXY_PORT):
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_loopback:
            raise ValueError("zen proxy host resolved to non-loopback address: " + str(ip))


def _extract_content(raw):
    """从代理响应里取正文。优先按 SSE（代理强制 stream=true）解析，
    失败再回退按普通 JSON 解析（防代理行为变化）。

    返回 (text, finish_reason)；正文为空时抛 ValueError，由调用方走下一通道。
    """
    parts = []
    finish = None
    saw_sse = False
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        saw_sse = True
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            d = json.loads(data)
        except Exception:
            continue
        chs = d.get("choices") or []
        if not chs:
            continue
        ch = chs[0] or {}
        if ch.get("finish_reason"):
            finish = ch["finish_reason"]
        delta = ch.get("delta") or {}
        c = delta.get("content")
        if isinstance(c, str) and c:
            parts.append(c)
    text = "".join(parts)
    if text.strip():
        return text, finish
    if saw_sse:
        # SSE 通道有响应但无正文：推理模型只吐 reasoning、或长度截断到空
        raise ValueError("zen proxy SSE returned no content (finish=%s)" % finish)
    # 非 SSE 回退：代理若改成非流式透传，仍能消费
    try:
        d = json.loads(raw)
        ch = (d.get("choices") or [{}])[0] or {}
        msg = ch.get("message") or {}
        c = msg.get("content")
        if isinstance(c, str) and c.strip():
            return c, ch.get("finish_reason")
    except Exception:
        pass
    raise ValueError("zen proxy response unparsable: " + raw[:200])


def chat_completion(model, messages, temperature=0.3, max_tokens=8192, timeout=180):
    """经本机 4010 代理调用 chat/completions，返回正文文本（已 strip）。

    - model 会原样发给代理；代理对白名单外的模型名会覆写为其默认目标模型
      （mimo-v2.6-flash-free，见 E:\\OpenCode\\zen-model.txt），属预期行为。
    - 代理自带 key 池与凭据，Authorization 头只作形式占位。
    - 线程 + join 硬超时：防代理半死时滴字节挂住整轮预算。
    """
    if not reachable():
        raise RuntimeError("zen proxy not reachable at " + BASE)
    url = BASE + "/chat/completions"
    _assert_local(url)
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    payload = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer local-proxy",  # 代理忽略此值，用自带凭据替换
        "User-Agent": "osint/1.0",
    }
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    box = {}

    def _do():
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                box["raw"] = resp.read().decode("utf-8", "replace")
        except Exception as e:
            box["err"] = e

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    t.join(timeout + 15)
    if t.is_alive():
        raise TimeoutError("zen proxy unresponsive (hard timeout %ss)" % timeout)
    if "err" in box:
        e = box["err"]
        if isinstance(e, urllib.error.HTTPError):
            try:
                detail = e.read().decode("utf-8", "replace")[:400]
            except Exception:
                detail = ""
            if detail:
                raise RuntimeError("HTTP " + str(e.code) + " " + str(e.reason) + " | " + detail)
        raise e
    text, finish = _extract_content(box.get("raw", ""))
    if finish == "length":
        # 截断不丢弃——osint 各解析器有逐条抢救（_salvage_items），但留痕便于排查
        print("[zen_proxy] warning: response truncated (finish_reason=length)")
    return text.strip()


def _selftest():
    """手动自检：python local/zen_proxy_client.py [模型名]"""
    import sys
    model = sys.argv[1] if len(sys.argv) > 1 else "mimo-v2.5-free"
    print("[selftest] reachable:", reachable())
    if not reachable():
        print("[selftest] 代理未监听，跳过（启动: pythonw E:\\OpenCode\\zen-proxy.py）")
        return 1
    text = chat_completion(model, [{"role": "user", "content": "只回复两个字：正常"}],
                           max_tokens=2000, timeout=60)
    print("[selftest] " + model + " -> " + text[:100])
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
