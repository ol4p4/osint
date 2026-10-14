# -*- coding: utf-8 -*-
r"""probe_cn_sources.py - 境内源可达性探针（2026-10-13，一次性）

## 要回答的问题

AGENTS.md 说金十/新浪/财联社是"主要渠道，本地+CI 双采"。但 CI 推的 intel 里
财联社每天只有 **1 条**，而本地有 **90+ 条**——这不像"双采对齐"。

本探针从**同一份代码**在两台机器上各跑一次，对比"境内(本机)" vs "境外(CI US runner)"
到底能采到多少，据此判断"哪些境内源能上云"。

## 测什么

金十/新浪是官方直连 API（sources.yaml direct 字段），财联社走 RSSHub 路由。
逐个测端点，报 HTTP 码 + 解析条数 + 最新一条时间，区分三种失败：
  连不上(异常/非200) / 连上但空(路由失效) / 连上且正常。

RSSHub 路由同时测本地容器(localhost:1200) 与公共镜像(slarker/rssforever)——
本机容器可能没起、CI 容器是 CI 自起的，两者命运不同。

只读，不写任何文件。

## SSRF 守卫

与生产 `cloud/fetch_rss._safe_rss_fetch` 同规格：仅 http/https + 主机白名单
+ 解析 IP 不得为私网/保留/**但环回放行**（localhost:1200 是可信的本地 RSSHub
转换服务——本探针的用途之一就是测它）。URL 全为代码内常量，非外部输入。
"""
import ipaddress
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
HDRS = {"User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, application/json, */*"}

# 主机白名单（含端口）。环回 RSSHub 单独放行。
_ALLOWED_HOSTS = {
    "www.jin10.com",
    "zhibo.sina.com.cn",
    "feed.mix.sina.com.cn",
    "localhost:1200", "127.0.0.1:1200",
    "hub.slarker.me",
    "rsshub.rssforever.com",
}


def _safe_fetch(url, timeout=15):
    """返回 (status, byte_len, text)。协议+白名单+IP 三重校验，异常归一 status=-1。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return -1, 0, "scheme not allowed"
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    hostport = f"{host}:{port}"
    is_loopback_probe = host in ("localhost", "127.0.0.1") and port == 1200
    if hostport not in _ALLOWED_HOSTS and host not in _ALLOWED_HOSTS:
        return -1, 0, "host not in whitelist: " + hostport
    try:
        for info in socket.getaddrinfo(host, port):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_loopback:
                if not is_loopback_probe:
                    return -1, 0, "unexpected loopback"
                continue
            if ip.is_private or ip.is_reserved or ip.is_link_local or ip.is_multicast:
                return -1, 0, "resolves to forbidden address"
    except Exception as e:
        return -1, 0, "DNS fail " + type(e).__name__

    req = urllib.request.Request(url, headers=HDRS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(1_000_000)
            return r.status, len(body), body.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, 0, str(e)
    except Exception as e:
        return -1, 0, type(e).__name__ + ": " + str(e)[:80]


def count_wrapped_list(text, key=None):
    """金十 flash_newest.js: 'var newest = [{...},...]' → 数顶层对象。"""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return -1
    try:
        arr = json.loads(m.group(0))
        return len(arr) if isinstance(arr, list) else -1
    except Exception:
        return -1


def count_json_path(text, path):
    """按点分路径取列表长度（新浪 JSON）。"""
    try:
        node = json.loads(text)
        for k in path.split("."):
            node = node[k] if node is not None else None
        return len(node) if isinstance(node, list) else -1
    except Exception:
        return -1


def count_rss_items(text):
    return len(re.findall(r"<item[ >]", text)) if text else -1


def latest_time(text):
    m = re.search(r"(20\d\d-\d\d-\d\d[\sT]\d\d:\d\d)", text)
    return m.group(1) if m else "-"


def run():
    side = sys.argv[1] if len(sys.argv) > 1 else "local"
    print("=" * 74)
    print(f"境内源可达性探针  ·  视角={side}  ·  {datetime.now(timezone.utc).isoformat()[:19]}Z")
    print("=" * 74)

    tests = [
        ("金十数据-快讯", "https://www.jin10.com/flash_newest.js",
         lambda t: count_wrapped_list(t)),
        ("新浪7x24", "https://zhibo.sina.com.cn/api/zhibo/feed?zhibo_id=152&page=1&page_size=100",
         lambda t: count_json_path(t, "result.data.feed.list")),
        ("新浪滚动", "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=50&page=1",
         lambda t: count_json_path(t, "result.data")),
        ("财联社·本地RSSHub", "http://localhost:1200/cls/telegraph", count_rss_items),
        ("财联社·slarker", "https://hub.slarker.me/cls/telegraph", count_rss_items),
        ("财联社·rssforever", "https://rsshub.rssforever.com/cls/telegraph", count_rss_items),
    ]

    print(f"{'源':20s} {'HTTP':>5s} {'字节':>8s} {'条数':>6s}  {'最新时间':16s} 备注")
    print("-" * 74)
    for name, url, counter in tests:
        status, blen, text = _safe_fetch(url)
        n = counter(text) if status == 200 else -1
        lt = latest_time(text) if status == 200 else "-"
        note = ""
        if status == -1:
            note = text[:42]
        elif status != 200:
            note = "非200"
        elif n == 0:
            note = "⚠ 连上但 0 条（路由失效/空）"
        elif n < 0:
            note = "200 但解析失败"
        print(f"{name:20s} {status:>5} {blen:>8} {n:>6}  {lt:16s} {note}")
        time.sleep(0.5)

    print()
    print("判读：条数>0 且最新时间接近当前 = 该源在此视角可采；0 条/连不上 = 不可采。")


if __name__ == "__main__":
    run()
