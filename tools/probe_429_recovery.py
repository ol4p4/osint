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


def burn_buckets(key, per_model=40):
    """把三个模型的桶打爆。返回每个模型的状态。

    只在**拿到 429** 时停（那才证明桶热了）；其它错误码（503/超时等）继续打
    ——否则中间偶发一个 503 就会提前退出，留下未热的桶（首版探针踩过：
    3.1-flash-lite 因第 6 发非 429 退出，导致"三桶全热"为 False，退避分支没被测到）。
    """
    print("\n[1] 烧桶（打爆三个模型的 RPM 配额）")
    status = {}
    for m in GEMINI_MODELS:
        n_ok = n_429 = n_other = 0
        for _ in range(per_model):
            code, body = _raw_call(m, key)
            if code == 200:
                n_ok += 1
            elif code == 429:
                n_429 += 1
                break
            else:
                n_other += 1        # 不 break：偶发 503 不代表桶热了
                time.sleep(1)
        status[m] = {"ok": n_ok, "hit_429": n_429 > 0, "other": n_other}
        print(f"    {m}: ok={n_ok} 触发429={n_429 > 0} 其他错误={n_other}")
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

    # 1) 烧桶（连打，中间不 sleep——RPM 窗口只有 1 分钟，停一下就自然恢复了）
    st = burn_buckets(key)
    all_hot = all(v["hit_429"] for v in st.values())
    print(f"    三桶全热: {all_hot}")
    if not all_hot:
        cold = [m for m, v in st.items() if not v["hit_429"]]
        print(f"    ⚠ 未打爆: {cold} —— 退避分支不会被测到，本次结果仅验证换桶")
        # 补打一轮冷桶（可能是刚才被 503 打断）
        for m in cold:
            for _ in range(30):
                code, _ = _raw_call(m, key)
                if code == 429:
                    st[m]["hit_429"] = True
                    print(f"    补打 {m}: 已触发 429")
                    break
        all_hot = all(v["hit_429"] for v in st.values())
        print(f"    补打后三桶全热: {all_hot}")

    # 2) 立刻调 generate()，观察退避恢复
    print("\n[2] 立即调用 generate()（预期：换桶；全热时走退避重试）")
    expect_backoff = all_hot
    t0 = time.time()
    try:
        out = generate(key, TINY, timeout=90, max_tokens=32)
        dt = time.time() - t0
        print(f"    ✓ 成功  耗时 {dt:.1f}s")
        print(f"    返回: {out[:80]!r}")
        if expect_backoff:
            if dt >= 8:
                print(f"    → 走了退避路径（耗时 {dt:.1f}s ≥ GEMINI_429_MIN_WAIT=8s），"
                      f"**退避恢复已验证**")
            else:
                print(f"    → ⚠ 耗时仅 {dt:.1f}s，未走退避（可能桶已自然恢复）")
        else:
            print(f"    → 本次验证的是**换桶**路径（三桶未全热）")
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
