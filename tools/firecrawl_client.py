# -*- coding: utf-8 -*-
r"""firecrawl_client.py - Firecrawl REST API 直调客户端（2026-10-14）

## 为什么有它

估值分位与失业率历史两个面板此前依赖 **zcode 的 firecrawl MCP**——那是本地
agent 专用工具，CI 上不存在，导致两块数据长期无法上云。MCP 只是包装层，
底层就是 REST API（`api.firecrawl.dev`），脚本直调即可在 CI 无人值守运行。

模块化（而非两个 tools 各写一份）：**副本代码是定时炸弹**——AGENTS.md 已有
教训（中文预填判据两份副本、修一处必须同步另一处）。SSRF 守卫与 429 处理
这类安全/健壮性逻辑尤其不能分叉。

## 设计要点

- **SSRF 守卫**：仅 https + 端点/目标 host 白名单 + 解析后 IP 边界校验
  （拒绝私网/环回/链路本地/保留地址），与 fetch_macro_indicators._get 同规格。
- **429 退避重试**：2026-10-14 CI 实测——两个 run 并发时三个页面全 429，
  而本地同刻同 key 完全正常（同码不同因）。Firecrawl 对并发有限流，
  单次失败就放弃太脆。改为指数退避重试（默认 3 次、2s/4s/8s）。
- **429 响应体进错误信息**：同项目规则（"只看状态码做决策必然误判"），
  429 可能是限流/配额/并发，body 里才有区分依据。
- **key 经环境变量**：FIRECRAWL_API_KEY（CI 走 GitHub Secrets），
  不进代码、不进命令行参数。
"""
import json
import time
import urllib.error
import urllib.request

FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v2/scrape"
_ALLOWED_ENDPOINT_HOSTS = {"api.firecrawl.dev"}

# 429 退避（秒）：CI 双 run 并发实测会触发，重试可自愈
_RETRY_DELAYS = (2, 4, 8)


def _host_is_safe(host):
    """解析 host 的 IP，若属 RFC1918/loopback/link-local 等则拒绝。"""
    import ipaddress
    import socket
    try:
        for info in socket.getaddrinfo(host, 443):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_multicast or ip.is_reserved):
                return False
    except Exception:
        return False
    return True


def scrape(url, allowed_targets, api_key=None, timeout=60, retries=None):
    """抓取单个 URL 返回 markdown。

    url:             目标页面（必须 https 且 host ∈ allowed_targets）
    allowed_targets: 调用方声明的目标域名白名单（本模块不内置业务白名单，
                     由调用方按用途最小化声明）
    api_key:         默认从 FIRECRAWL_API_KEY 环境变量读

    抛出 RuntimeError（带响应体摘要）——调用方决定是跳过还是终止。
    """
    import os
    from urllib.parse import urlparse

    # ① 端点校验（硬编码常量，仍过一遍守卫以防被改动）
    ep = urlparse(FIRECRAWL_ENDPOINT)
    if ep.scheme != "https" or ep.hostname not in _ALLOWED_ENDPOINT_HOSTS:
        raise RuntimeError(f"firecrawl 端点不在白名单: {FIRECRAWL_ENDPOINT}")
    if not _host_is_safe(ep.hostname):
        raise RuntimeError(f"firecrawl 端点解析到受限 IP: {ep.hostname}")
    # ② 目标校验：仅 https + 调用方白名单 + IP 边界
    tp = urlparse(url)
    if tp.scheme != "https" or tp.hostname not in (allowed_targets or set()):
        raise RuntimeError(f"抓取目标不在白名单: {url}")
    if not _host_is_safe(tp.hostname):
        raise RuntimeError(f"抓取目标解析到受限 IP: {tp.hostname}")

    key = api_key or (os.environ.get("FIRECRAWL_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("FIRECRAWL_API_KEY 未设置")

    body = json.dumps({
        "url": url, "formats": ["markdown"], "onlyMainContent": True,
    }).encode("utf-8")
    delays = _RETRY_DELAYS if retries is None else tuple(retries)
    last_err = None
    for attempt, delay in enumerate((0,) + delays):
        if delay:
            time.sleep(delay)
        req = urllib.request.Request(
            FIRECRAWL_ENDPOINT, data=body, headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = json.loads(r.read().decode("utf-8", errors="replace"))
            md = ((payload or {}).get("data") or {}).get("markdown") or ""
            if not md:
                raise RuntimeError(
                    f"firecrawl 返回空 markdown（success={payload.get('success')}）")
            return md
        except urllib.error.HTTPError as e:
            # 读响应体：同码不同因（限流/配额/并发）必须靠 body 区分
            try:
                detail = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                detail = ""
            last_err = RuntimeError(f"HTTP {e.code}: {detail or e.reason}")
            # 仅 429/5xx 值得重试；4xx（除 429）立即失败
            if e.code not in (429, 500, 502, 503, 504):
                raise last_err
        except Exception as e:
            last_err = e
            # 网络类异常也重试（DNS/超时偶发）
    raise RuntimeError(f"firecrawl 重试 {len(delays)} 次仍失败: {last_err}")
