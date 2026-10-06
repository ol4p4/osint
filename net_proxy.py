# -*- coding: utf-8 -*-
r"""net_proxy.py - git 联网代理配置（2026-10-06）

背景：本机 GitHub 直连常态不通（境外站被阻），需经本地 Clash 代理（mixed-port
默认 7897）才能 pull/push。此前 osint 的 git 操作（refresh.commit_hypotheses、
local_sync.git_pull、watchdog）**都不带代理**，所以一旦直连失效，整条自动链路
静默失败——假设树 commit 推不上去、CI 数据拉不回来，日志只留一句
`Failed to connect to github.com port 443`。

设计：
- **不改命令行**：用 git 原生的 `GIT_CONFIG_COUNT/KEY/VALUE` 环境变量传代理配置，
  调用方仍是 `["git", "pull", ...]` 纯参数列表，**不做任何字符串拼接**。
- **只在代理可用时注入**：探测不到代理就返回空 dict（直连可能本来就通，
  别给正常环境塞死代理）。
- **缓存 300s**：避免每轮 refresh 反复探端口。
- **共享**：三处 git 调用点共用，避免各写一份漂移（AGENTS.md 记过
  「同逻辑多副本修一处漏一处」的教训）。

CI（GitHub Actions）无本地代理 → 探测失败 → 返回空 dict → git 直连，互不影响。
环境变量 `OSINT_GIT_PROXY` 可显式指定；设为 `none` 强制禁用。

安全：外部传入的代理 URL 必须通过白名单正则（仅 http:// + 回环 + 端口），
不合规一律丢弃退回直连；注入的是环境变量而非命令参数。
"""
import os
import re
import socket
import time

# 本地代理候选端口（按优先级）：Clash Verge mixed-port 默认 7897；
# 7890/7891 为 Clash 经典端口，7888 为本机实测出现的 Clash 端口。
_PROXY_CANDIDATES = (
    "http://127.0.0.1:7897",
    "http://127.0.0.1:7890",
    "http://127.0.0.1:7891",
    "http://127.0.0.1:7888",
)
_CACHE = {"at": 0.0, "proxy": None}
_TTL = 300          # 探测缓存秒数

# 代理 URL 白名单：只允许 http:// + 回环地址 + 端口
_PROXY_RE = re.compile(r"^http://(?:127\.0\.0\.1|localhost|\[::1\]):\d{1,5}$")


def _valid_proxy(url):
    """校验代理 URL 合规性（白名单正则 + 端口范围）。不合规返回 None。"""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not _PROXY_RE.match(url):
        return None
    port = int(url.rsplit(":", 1)[1])
    if not (1 <= port <= 65535):
        return None
    return url


def _port_open(host, port, timeout=0.4):
    """TCP 连通性快速判断；只探回环地址（防被误配去探外网）"""
    import ipaddress
    try:
        if not ipaddress.ip_address(host).is_loopback:
            return False
    except ValueError:
        if host != "localhost":
            return False
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def _proxy_works(url, timeout=3.0):
    """实测代理能否真的连上 GitHub（CONNECT 探测）。

    **为什么必须实活检测**（2026-10-06 踩坑）：原先只看「端口在监听」，
    但 Clash 节点不稳时端口照样 LISTENING——实测代理 TLS 握手失败
    （`schannel: failed to receive handshake`），而**直连反而通了**。
    此时仍注入代理 → 把本来能成的 git 操作搞挂。所以端口开着还要实探一次。
    """
    import http.client
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    try:
        conn = http.client.HTTPSConnection(parts.hostname, parts.port or 80, timeout=timeout)
        # 经代理向 github.com:443 发 CONNECT，隧道建立即说明代理可用
        conn.set_tunnel(_PROBE_HOST, _PROBE_PORT)
        conn.request("HEAD", "/")
        resp = conn.getresponse()
        resp.read(0)
        conn.close()
        return True
    except Exception:
        return False


def _probe():
    """返回**实测可用**的代理 URL；都不可用返回 None。

    两级判据：① 端口在监听（快、无网络开销）→ ② 实探能否连上 GitHub
    （3s CONNECT 探测）。只满足①不满足②的（节点挂了/被墙）直接跳过，
    退回直连——否则会把本来正常的直连拖死。
    """
    for url in _PROXY_CANDIDATES:
        host_port = url.split("//", 1)[1]
        host, _, port = host_port.partition(":")
        if not _port_open(host, int(port or 80)):
            continue
        if _proxy_works(url):
            return url
    return None


def git_proxy():
    """探测本地代理；返回 URL 或 None（进程内缓存 300s）。"""
    env = os.environ.get("OSINT_GIT_PROXY", "").strip()
    if env:
        if env.lower() in ("none", "0", "off", "false", "no"):
            return None
        return _valid_proxy(env)     # 不合规 → None（退回直连）
    now = time.time()
    if _CACHE["at"] and now - _CACHE["at"] < _TTL:
        return _CACHE["proxy"]
    _CACHE["proxy"] = _probe()
    _CACHE["at"] = now
    return _CACHE["proxy"]


def git_env(base_env=None):
    """返回执行 git 子进程所需的环境变量 dict（含代理配置）。

    用法：
        subprocess.run(["git", "pull", "origin", "master"],
                       cwd=..., env=git_env(), ...)

    代理不可用时返回 base_env（或 os.environ 副本），行为与不传 env 一致。
    通过 git 原生的 GIT_CONFIG_* 变量传 http.proxy/https.proxy，
    命令行保持纯参数列表，不做字符串拼接。
    """
    env = dict(base_env if base_env is not None else os.environ)
    proxy = git_proxy()
    if proxy:
        env["GIT_CONFIG_COUNT"] = "2"
        env["GIT_CONFIG_KEY_0"] = "http.proxy"
        env["GIT_CONFIG_VALUE_0"] = proxy
        env["GIT_CONFIG_KEY_1"] = "https.proxy"
        env["GIT_CONFIG_VALUE_1"] = proxy
    return env


def describe():
    """一行描述当前策略（供日志打印，排查时一眼看清走没走代理）"""
    proxy = git_proxy()
    if proxy:
        return "git 经本地代理 " + proxy
    return "git 直连（未探测到本地代理）"
