# -*- coding: utf-8 -*-
r"""check_opencode_session.py - OpenCode 桌面 session 有效性检测（2026-10-12）

## 为什么需要它

云端 5 条 AI 腿中，`opencode-desktop` 是唯一**依赖用户个人登录态**的通道
（复用本地桌面客户端的 session，见 `secrets_loader.get_opencode_session`）。

**它会静默失效**：用户在本地重新登录 OpenCode、切换账号，或上游收紧校验时，
该 session 不再被认可；而这条通道**恒排链尾**——只有前 4 条正常 API 通道全不可用
时才会被触发，平时根本不调用。结果是"失效了也没人知道"，直到某天真的需要兜底
才发现腿是断的。

本工具主动验证一次，把**静默失效变成明确警告**。

## 判据

上游认可 session 时返 HTTP 200；不认则 403 `FreeTierError`
（"free tier can only be used from within OpenCode"）。

**关键设计一**：本工具复用 `secrets_loader` 里的 `OPENCODE_DESKTOP_TOOLS` /
`OPENCODE_DESKTOP_UA` —— 与生产通道**同一套指纹常量**。若各自维护一份，
上游要求一变就会出现"检测通过但通道不通"或反之，检测失去意义。

**关键设计二**：本工具走 `local/zen_proxy_client` 之外**独立的最小 SSRF 守卫**
（仅 https + 白名单主机 + 解析后 IP 边界），与 `analyze._safe_ai_post` /
`cloud/*._safe_post` 同规格——本仓库所有外发请求都必须带这道守卫
（AGENTS.md：禁止绕过白名单）。

## 用法

    python tools/check_opencode_session.py                    # 本地 / CI 通用
    python tools/check_opencode_session.py --github-annotate  # CI 里发 warning 注解

**退出码恒 0**：这是观测步骤，不该阻断流水线（同 probe / assess 的教训——
外部依赖的观测步骤不阻塞核心流程）。真正的信号是输出与 CI 注解。
"""
import argparse
import ipaddress
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ZEN_HOST = "opencode.ai"
ZEN_URL = "https://" + ZEN_HOST + "/zen/v1/chat/completions"

# 状态含义
STATE_OK = "ok"                # session 有效
STATE_INVALID = "invalid"      # 上游明确拒绝（session 失效/被吊销）
STATE_NO_SESSION = "no_session"  # 压根没取到 session
STATE_NO_KEY = "no_key"        # 没有 key（本通道 key 与 session 缺一不可）
STATE_ERROR = "error"          # 网络/其他异常（非 session 问题)


def _safe_post(url, payload, headers, timeout=45):
    """SSRF 守卫：仅 https + 白名单主机 + 解析结果不得指向私有/环回/保留地址。

    与 local/analyze.py 的 `_safe_ai_post`、cloud/*.py 的 `_safe_post` 同规格。
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") != ZEN_HOST:
        raise ValueError("blocked non-whitelisted endpoint: " + url)
    for info in socket.getaddrinfo(parsed.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_reserved
                or ip.is_link_local or ip.is_multicast):
            raise ValueError("endpoint resolves to forbidden address: " + str(ip))
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read(256).decode("utf-8", "replace")


def _load_model():
    """探测用的模型名，取自 config.yaml（与生产通道同源，避免写死漂移）"""
    try:
        import yaml
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
        return (cfg.get("api") or {}).get("model") or "mimo-v2.6-flash-free"
    except Exception:
        return "mimo-v2.6-flash-free"


def check(timeout=45):
    """验证 session 是否仍被上游认可。

    返回 (state, detail)。**只读探针**：发一次最小请求，不写任何状态、不改链。
    """
    try:
        from secrets_loader import (get_opencode_session, get_opencode_key,
                                    OPENCODE_DESKTOP_TOOLS, OPENCODE_DESKTOP_UA)
    except Exception as ex:
        return STATE_ERROR, "secrets_loader 导入失败: " + str(ex)[:120]

    sess = get_opencode_session()
    key = get_opencode_key()
    if not sess:
        return STATE_NO_SESSION, ("未取到 session（环境变量 OC_DESKTOP_SESSION 为空，"
                                  "且本地无桌面版 sqlite）")
    if not key:
        return STATE_NO_KEY, "未取到 OPENCODE_API_KEY（本通道 key 与 session 缺一不可）"

    # 与生产通道完全同款请求（stream=true + 6 个官方工具是放行条件）
    body = {
        "model": _load_model(),
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        "stream_options": {"include_usage": True},
        "tools": OPENCODE_DESKTOP_TOOLS,
        "tool_choice": "auto",
        "max_tokens": 8,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + key,
        "User-Agent": OPENCODE_DESKTOP_UA,
        "x-opencode-client": "desktop",
        "x-opencode-session": sess,
    }
    try:
        status, _ = _safe_post(ZEN_URL, json.dumps(body).encode("utf-8"),
                               headers, timeout=timeout)
        return STATE_OK, "HTTP %s（session 仍被认可）" % status
    except urllib.error.HTTPError as ex:
        detail = ""
        try:
            detail = ex.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        # 上游明确拒绝 session：这是本工具要抓的信号
        if "FreeTierError" in detail or "from within OpenCode" in detail:
            return STATE_INVALID, "HTTP %s %s" % (ex.code, detail)
        return STATE_ERROR, "HTTP %s %s" % (ex.code, detail)
    except Exception as ex:
        return STATE_ERROR, "%s: %s" % (type(ex).__name__, str(ex)[:140])


def main():
    ap = argparse.ArgumentParser(description="OpenCode 桌面 session 有效性检测")
    ap.add_argument("--timeout", type=int, default=45)
    ap.add_argument("--github-annotate", action="store_true",
                    help="失败时输出 GitHub Actions ::warning:: 注解（CI 用）")
    args = ap.parse_args()

    t0 = time.time()
    state, detail = check(timeout=args.timeout)
    el = time.time() - t0

    if state == STATE_OK:
        print("[SESSION-CHECK] ✓ session 有效（%.1fs）：%s" % (el, detail))
        return 0

    # 失败分级：invalid/no_session 是"这条腿断了"，需要人去处理；
    # error 多为网络抖动，提示但不建议立即行动。
    if state == STATE_INVALID:
        msg = ("opencode-desktop 通道的 session **已失效**（上游拒绝）。"
               "云端第 5 条腿当前不可用——不影响前 4 条正常通道，"
               "但该兜底已失效。修复：在本机重新登录 OpenCode 后，"
               "重新执行 gh secret set OC_DESKTOP_SESSION 注入新 session。")
    elif state == STATE_NO_SESSION:
        msg = "未配置 session，opencode-desktop 通道不生效（前 4 条通道不受影响）。"
    elif state == STATE_NO_KEY:
        msg = "缺少 OPENCODE_API_KEY，opencode-desktop 通道不生效。"
    else:
        msg = "检测未能完成（可能是网络抖动，非 session 问题）：" + detail

    print("[SESSION-CHECK] ⚠ %s（%.1fs）" % (msg, el))
    if state != STATE_ERROR:
        print("[SESSION-CHECK]   原始错误: " + detail)
    if args.github_annotate:
        # GitHub Actions 注解：在 UI 上直接可见（日志易被忽略）
        print("::warning title=OpenCode session 失效::" + msg.replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
