#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_gemma_retry.py - Gemma 500 错误的性质判定（2026-10-13，一次性）

## 背景

`probe_gemma.py` 实测：两个官方文档确认的 Gemma 4 模型名
（`gemma-4-26b-a4b-it` / `gemma-4-31b-it`）都返回 **HTTP 500 Internal error**，
而同一次运行里对照组 `gemini-3.5-flash-lite` 返 200。

**500 而非 404 是重要信号**：404 说明模型名不存在，500 说明 API 认识该模型
（路由已接受）但后端失败。要区分三种可能：
  ① 临时服务端故障 → 重试会成功
  ② 该模型对本账号/免费层未开通 → 稳定 500
  ③ 需要不同端点/参数（如 v1 而非 v1beta、或 streamGenerateContent）→ 换法能通

## 方法

对两个模型名各做多轮变体测试：
  A. 同端点重试 3 次（含间隔）—— 判是否临时
  B. 换 streamGenerateContent 端点 —— 部分模型只在流式后端注册
  C. 换 v1（非 beta）端点 —— 版本差异
每轮只发最小请求。只读 + 只打印。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local.gemini_client import _post  # noqa: E402
import urllib.error  # noqa: E402

MODELS = ["gemma-4-26b-a4b-it", "gemma-4-31b-it"]
BODY = {
    "contents": [{"role": "user", "parts": [{"text": "Reply with the single word: ok"}]}],
    "generationConfig": {"temperature": 0, "maxOutputTokens": 16},
}


def call(url, key, body=None, timeout=60):
    payload = json.dumps(body or BODY).encode("utf-8")
    headers = {"Content-Type": "application/json", "x-goog-api-key": key,
               "User-Agent": "osint-gemma-retry/1.0"}
    try:
        return 200, _post(url, payload, headers, timeout)
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", "replace")
    except Exception as e:
        return -1, "EXC " + type(e).__name__ + ": " + str(e)


def brief(body, n=120):
    try:
        j = json.loads(body)
        err = j.get("error") or {}
        return f"{err.get('status')}: {str(err.get('message'))[:n]}"
    except Exception:
        return body[:n].replace("\n", " ")


def main():
    print("=" * 72)
    print("Gemma 500 性质判定")
    print("=" * 72)

    from secrets_loader import get_gemini_key
    key = get_gemini_key()
    if not key:
        print("无 key，退出")
        return

    V1BETA = "https://generativelanguage.googleapis.com/v1beta"
    V1 = "https://generativelanguage.googleapis.com/v1"

    print("\n[A] 对照：lite 三个端点各一次（确认探测方法本身有效）")
    for label, base in (("v1beta", V1BETA), ("v1", V1)):
        code, body = call(f"{base}/models/gemini-3.5-flash-lite:generateContent", key)
        print(f"    {label:8s} HTTP {code}  {brief(body) if code != 200 else 'OK'}")

    for model in MODELS:
        print(f"\n--- {model} ---")

        print("  [A] 同端点重试 3 次（间隔 3s）—— 判是否临时故障")
        for i in range(3):
            code, body = call(f"{V1BETA}/models/{model}:generateContent", key)
            print(f"      第{i+1}次 HTTP {code}  {brief(body) if code != 200 else 'OK'}")
            if i < 2:
                time.sleep(3)

        print("  [B] streamGenerateContent（部分模型只在流式后端注册）")
        code, body = call(f"{V1BETA}/models/{model}:streamGenerateContent", key)
        print(f"      HTTP {code}  {brief(body) if code != 200 else 'OK ' + body[:100]}")

        print("  [C] v1（非 beta）端点")
        code, body = call(f"{V1}/models/{model}:generateContent", key)
        print(f"      HTTP {code}  {brief(body) if code != 200 else 'OK'}")

    print("\n" + "=" * 72)
    print("[判定]")
    print("  · 若重试稳定 500 且换端点同样失败 → ② 该模型对本账号未开通（非临时）")
    print("  · 若某次成功 → ① 临时故障，可加入重试逻辑")
    print("  · 若只有某端点成功 → ③ 需按该端点调用")


if __name__ == "__main__":
    main()
