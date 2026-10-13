#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_429_recovery.py - 429 退避恢复的端到端验证（2026-10-13，一次性）

## 要回答的问题

`local/gemini_client.generate()` 新加了"三模型全 429 → 按 retryDelay 退避 →
重跑整条链"的逻辑。桩测（mock）已过，但**真实 429 场景下是否真能恢复**？

## 方法

1. **烧桶**：先连发把三个模型的 RPM 桶全部打爆（已知配额：15/15/5）。只确认
   确实进入"全热"状态，不依赖具体数字。
2. **验证恢复**：紧接一次真实 `generate()` 调用，观察：
   - 是否打印"三模型配额全热，退避 Ns 后重试"
   - 等待后是否成功返回
   - 实际等待时长是否落在 retryDelay 的合理区间
3. **对照**：报告总耗时与结果，判断"等待后恢复"是否优于"切备援通道"。

只读 + 只打印，不写生产文件。需要 GEMINI_API_KEY。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local.gemini_client import (GEMINI_BASE, GEMINI_MODELS, _post,  # noqa: E402
                                 generate)
import urllib.error  # noqa: E402

TINY = "Reply with the single word: ok"


def _raw_call(model, key):
    """一次最小请求。返回 (status, body_text)。经 _post 复用 SSRF 守卫。"""
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": TINY}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 16},
    }).encode("utf-8")
    url = GEMINI_BASE + "/models/" + model + ":generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": key,
               "User-Agent": "osint-429-probe/1.0"}
    try:
        return 200, _post(url, body, headers, timeout=45)
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", "replace")
    except Exception as e:
        return -1, f"EXC {e}"


def burn_buckets(key, per_model=18):
    """把三个模型的桶打爆。返回每个模型的状态。"""
    print("\n[1] 烧桶（打爆三个模型的 RPM 配额）")
    status = {}
    for m in GEMINI_MODELS:
        n_ok = n_429 = 0
        for _ in range(per_model):
            code, body = _raw_call(m, key)
            if code == 200:
                n_ok += 1
            elif code == 429:
                n_429 += 1
                break
            else:
                break
        status[m] = {"ok": n_ok, "hit_429": n_429 > 0}
        print(f"    {m}: ok={n_ok} 触发429={n_429 > 0}")
    return status


def main():
    print("=" * 72)
    print("429 退避恢复端到端验证")
    print("=" * 72)

    from secrets_loader import get_gemini_key
    key = get_gemini_key()
    if not key:
        print("无 GEMINI_API_KEY，退出")
        return

    # 1) 烧桶
    st = burn_buckets(key)
    all_hot = all(v["hit_429"] for v in st.values())
    print(f"    三桶全热: {all_hot}")

    # 2) 立刻调 generate()，观察退避恢复
    print("\n[2] 立即调用 generate()（应触发退避重试）")
    t0 = time.time()
    try:
        out = generate(key, TINY, timeout=60, max_tokens=32)
        dt = time.time() - t0
        print(f"    ✓ 恢复成功  耗时 {dt:.1f}s")
        print(f"    返回: {out[:80]!r}")
        print(f"    → 等待后复用原通道可行，无需降级到备援")
    except Exception as e:
        dt = time.time() - t0
        print(f"    ✗ 仍失败（耗时 {dt:.1f}s）: {type(e).__name__}: {str(e)[:200]}")
        print(f"    → 退避重试未能在本轮恢复（可能 retryDelay 超过 MAX_WAIT 或额度更深）")

    # 3) 对照组：等 70s 后再试一次（验证"等够时间就能恢复"，不依赖重试逻辑）
    print("\n[3] 对照：静默等待 70s 后再试（验证窗口确实会恢复）")
    time.sleep(70)
    ok_after = {}
    for m in GEMINI_MODELS:
        code, _ = _raw_call(m, key)
        ok_after[m] = code
        print(f"    {m}: HTTP {code}")
    recovered = [m for m, c in ok_after.items() if c == 200]
    print(f"    → 恢复的模型: {recovered}")

    print("\n" + "=" * 72)
    print("[结论]")
    if any(c == 200 for c in ok_after.values()):
        print("  ✓ 配额窗口恢复：等待后原通道可复用")
        print("  → '退避重试' 优于 '降级到 dots 等备援通道'（后者质量更差）")
    else:
        print("  ✗ 70s 后仍未恢复，窗口可能更长——需据 retryDelay 调 MAX_WAIT")


if __name__ == "__main__":
    main()
