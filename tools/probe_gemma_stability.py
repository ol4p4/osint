#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_gemma_stability.py - Gemma 稳定性与 TPM 压测（2026-10-13，一次性）

## 背景

`probe_gemma_retry.py` 实测：`gemma-4-26b-a4b-it` 在第 3 次重试时返回 200，
判定为**临时性 500**（非未开通）。但它 500 率很高，作为翻译主力是否可行取决于：
  1. **稳定性**：连续 N 次请求的成功率是多少？若 500 率 >50%，重试成本会吃掉
     它 RPD=14.4K 的额度优势。
  2. **TPM 上限**：配额页显示 16K（lite 是 250K）。生产翻译批 5 条约 2~4K token，
     需实测确认不会撞 TPM。
  3. **译文质量**：Gemma 是开放权重模型，中文翻译质量需抽检。

## 方法

- 稳定性：连发 N=15 次最小请求（唯一 prompt 避免缓存），统计成功率与错误码分布。
- TPM：发一次生产同款翻译批（5 条），记录 usageMetadata 与实际 token 数。
- 质量：同上那批的译文抽检（人工判）。

每批之间加短间隔，避免纯 RPM 因素干扰判断。只读 + 只打印。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local.gemini_client import GEMINI_BASE, _post  # noqa: E402
import urllib.error  # noqa: E402

MODEL = "gemma-4-26b-a4b-it"      # 首轮唯一成功过的那个
MODEL_ALT = "gemma-4-31b-it"
N_TRIES = 15


def call(model, key, prompt="Reply with the single word: ok", max_tokens=16, timeout=90):
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": max_tokens},
    }).encode("utf-8")
    url = GEMINI_BASE + "/models/" + model + ":generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": key,
               "User-Agent": "osint-gemma-stab/1.0"}
    try:
        return 200, _post(url, body, headers, timeout)
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", "replace")
    except Exception as e:
        return -1, "EXC " + type(e).__name__


def main():
    print("=" * 72)
    print("Gemma 稳定性与 TPM 压测")
    print("=" * 72)

    from secrets_loader import get_gemini_key
    key = get_gemini_key()
    if not key:
        print("无 key，退出")
        return

    # ---------- 1. 稳定性 ----------
    print(f"\n[1] 稳定性：{MODEL} 连发 {N_TRIES} 次（最小请求，带唯一后缀避缓存）")
    codes = []
    t0 = time.time()
    for i in range(N_TRIES):
        code, _ = call(MODEL, key, prompt=f"Reply with the single word: ok ({i})")
        codes.append(code)
        time.sleep(1.5)
    dt = time.time() - t0
    n_ok = codes.count(200)
    from collections import Counter
    dist = Counter(codes)
    print(f"    结果分布: {dict(dist)}")
    print(f"    成功率 {n_ok}/{N_TRIES} = {n_ok/N_TRIES*100:.0f}%  耗时 {dt:.1f}s")
    if n_ok:
        print(f"    平均每次 {dt/N_TRIES:.1f}s")

    # 对照
    print(f"\n[2] 对照：gemini-3.5-flash-lite 连发 5 次")
    codes_c = []
    for i in range(5):
        code, _ = call("gemini-3.5-flash-lite", key, prompt=f"Reply with the single word: ok (c{i})")
        codes_c.append(code)
        time.sleep(1.0)
    print(f"    结果分布: {dict(Counter(codes_c))}  成功率 {codes_c.count(200)}/5")

    # ---------- 3. TPM 压测（生产同款翻译批） ----------
    print(f"\n[3] TPM 压测：生产同款翻译批（5 条）")
    items = []
    for i in range(5):
        items.append(
            f"ITEM_ID: probe{i}\n"
            f"TITLE: Central banks signal policy shift as inflation surprises to the upside\n"
            f"CONTENT: Policy makers emphasized data dependence while noting risks to growth. "
            f"Analysts revised their forecasts for rate paths, and market participants are "
            f"weighing implications for currency and bond markets. Officials said the decision "
            f"was unanimous and reflected caution about persistent price pressures. ({i})"
        )
    prompt = ("Translate each ITEM to Chinese. Output JSON array with format: "
              '[{"id": "...", "cn_title": "...", "cn_summary": "...", "impact": "..."}]. '
              "Only output JSON, no markdown.\n\n" + "\n\n".join(items))
    print(f"    prompt {len(prompt)} 字符 ≈ {len(prompt)//2} token（粗估）")

    got = None
    for attempt in range(6):
        code, body = call(MODEL, key, prompt=prompt, max_tokens=2048)
        if code == 200:
            got = body
            print(f"    第{attempt+1}次 HTTP 200（前 {attempt} 次失败）")
            break
        print(f"    第{attempt+1}次 HTTP {code}")
        time.sleep(3)

    if got:
        try:
            j = json.loads(got)
            usage = j.get("usageMetadata") or {}
            cand = (j.get("candidates") or [{}])[0]
            parts = (cand.get("content") or {}).get("parts") or []
            txt = "".join(p.get("text", "") for p in parts)
            print(f"    ✓ usage={usage}")
            print(f"    finishReason={cand.get('finishReason')}")
            parsed = _parse(txt)
            if parsed:
                print(f"    JSON 解析 ✓ {len(parsed)} 条")
                print(f"\n    --- 译文抽检 ---")
                for r in parsed[:3]:
                    print(f"      title: {str(r.get('cn_title'))[:70]}")
                    print(f"      summary: {str(r.get('cn_summary'))[:110]}")
                    print()
            else:
                print(f"    JSON 解析 ✗  原文前 300: {txt[:300]!r}")
        except Exception as e:
            print(f"    解析异常 {e}: {got[:200]}")
    else:
        print("    ✗ 6 次均未成功（TPM 或稳定性问题）")


def _parse(text):
    t = text.strip()
    if t.startswith("```"):
        parts = t.split("```")
        if len(parts) > 1:
            t = parts[1]
        if t.startswith("json"):
            t = t[4:]
    i, j = t.find("["), t.rfind("]")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except Exception:
        return None


if __name__ == "__main__":
    main()
