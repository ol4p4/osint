# -*- coding: utf-8 -*-
r"""model_bench.py - NVIDIA 翻译模型测速
对手动挑选的翻译候选模型各发同一短翻译请求，测延迟（两轮取最小），输出排序表。
在 CI 里跑（需要 NVIDIA_API_KEY）；本地设置该环境变量也可跑。
用途：translate.py 选型参考。背景：llama-3.2-11b-vision 高峰期 60s 超时。
"""
import json
import os
import socket
import sys
import time
import ipaddress
import urllib.request
from urllib.parse import urlparse

BASE = "https://integrate.api.nvidia.com/v1"
AI_ALLOWED_HOST = "integrate.api.nvidia.com"

CANDIDATES = [
    # 2026-10-18 bench（run 39118560082）重整：/models 列表 88 项里实际可服务的
    # 只剩下列这些——gpt-oss-120b 已 410 Gone、mistral/gemma/nemotron-51b 全 404。
    # 新增 z-ai/glm-5.3：它是 citizen_impact / weekly_report_cloud 的 NVIDIA
    # 备援腿首位模型（NV_SLUG_MAP），其延迟决定该腿是否实际可用。
    "openai/gpt-oss-20b",
    "z-ai/glm-5.3",
    "z-ai/glm-5.3-flash",
    "meta/llama-3.2-11b-vision-instruct",
]

PROMPT = ('Translate the news item to Chinese. Output JSON: '
          '[{"cn_title":"中文标题","cn_summary":"两句话中文摘要"}]\n\n'
          'ITEM: Fed signals rate cut as inflation cools\n'
          'CONTENT: The Federal Reserve signaled it may cut interest rates '
          'as consumer inflation eased for a third straight month, a move that '
          'would lower borrowing costs for households and businesses.')

RESULT_HEADER = f"{'model':<45} {'min_latency':>11}  result"


def _post(url, payload, headers, timeout):
    """仅 https + integrate.api.nvidia.com 白名单 + 拒私有/环回/保留地址"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") != AI_ALLOWED_HOST:
        raise ValueError("blocked non-whitelisted endpoint: " + url)
    for info in socket.getaddrinfo(parsed.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
            raise ValueError("endpoint resolves to forbidden address: " + str(ip))
    req = urllib.request.Request(url, data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def timed_call(model, key, timeout=150):
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "temperature": 0.1,
        "max_tokens": 400,
    }).encode()
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
    t0 = time.time()
    raw = _post(BASE + "/chat/completions", payload, headers, timeout)
    dt = time.time() - t0
    content = json.loads(raw)["choices"][0]["message"].get("content", "")
    return dt, bool(content.strip())


def main():
    key = os.environ.get("NVIDIA_API_KEY", "")
    if not key:
        print("No NVIDIA_API_KEY, skip bench")
        sys.exit(0)

    print(RESULT_HEADER)
    results = []
    for model in CANDIDATES:
        times, err = [], ""
        for attempt in (1, 2):
            try:
                dt, ok = timed_call(model, key)
                times.append(dt)
                if attempt == 1 and dt < 15:
                    break  # 首轮就很快, 不必测第二轮
            except Exception as e:
                err = str(e)[:60]
                break
        if times:
            results.append((min(times), model, "ok"))
            print(f"{model:<45} {min(times):>9.1f}s  ok (rounds={len(times)})")
        else:
            results.append((9999, model, "FAIL: " + err))
            print(f"{model:<45} {'--':>11}  FAIL: {err}")

    print("\n=== 排序（快 → 慢）===")
    for dt, model, note in sorted(results):
        mark = "  <-- 当前在用" if "vision" in model else ""
        if dt < 9999:
            print(f"{dt:>9.1f}s  {model}{mark}")
        else:
            print(f"   FAIL    {model}  ({note})")


if __name__ == "__main__":
    main()
