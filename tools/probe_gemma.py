#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_gemma.py - Gemma 4 可用性探测（2026-10-13，一次性）

## 要回答的问题

AI Studio 配额页显示 Gemma 4 的 **RPD=14,400**（RPM 30 / TPM 16K），
比当前主力 gemini-3.5-flash-lite（RPD=500 / TPM 250K）日额度高 28 倍。
若可用，翻译链的容量瓶颈直接消失。

**但三个疑点必须先验证**（不能照配额页推断）：
1. **协议兼容**：Gemma 是开放权重模型，未必支持 `:generateContent` 原生接口
   （可能只走别的端点/serving）。配额页列出 ≠ 该 key 能通过该接口调用。
2. **TPM=16K 是否够**：比 lite 的 250K 低一个量级。翻译批 5 条的 prompt
   实测约 2~4K token，需确认不会撞 TPM。
3. **模型名**：配额页显示 "Gemma 4 26B" / "Gemma 4 31B"，实际 API 名待探测
   （可能类似 gemma-4-26b-it）。

## 方法

逐个候选模型名发**最小请求**（几十 token），只读 HTTP 状态与错误体，
不消耗可观配额。只读 + 只打印，不写生产文件。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local.gemini_client import GEMINI_BASE, _post  # noqa: E402
import urllib.error  # noqa: E402

TINY = "Reply with the single word: ok"

# 候选模型名（配额页显示的是展示名，API 名已按官方文档修正）
# 官方文档 ai.google.dev/gemma/docs/core/gemma_on_gemini_api 明确：
#   gemma-4-26b-a4b-it（MoE，26B/4B激活）/ gemma-4-31b-it（Dense）
#   **走同一个 :generateContent 接口**，支持 system instruction / function calling
#   / Google Search —— 协议与本项目现有调用完全兼容。
# 首轮探针用了 "gemma-4-26b-it"（缺 a4b 后缀）故未命中，此处修正。
CANDIDATES = [
    "gemma-4-26b-a4b-it",
    "gemma-4-31b-it",
    "gemma-4-12b-it",
    "gemma-4-e4b-it",
    "gemma-4-e2b-it",
]

# 对照：当前主力（应 200）
CONTROL = "gemini-3.5-flash-lite"


def call(model, key, prompt=TINY, max_tokens=16):
    """发一次最小 generateContent。返回 (status, body_text)。经 _post 复用 SSRF 守卫。"""
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": max_tokens},
    }).encode("utf-8")
    url = GEMINI_BASE + "/models/" + model + ":generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": key,
               "User-Agent": "osint-gemma-probe/1.0"}
    try:
        return 200, _post(url, body, headers, timeout=60)
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", "replace")
    except Exception as e:
        return -1, "EXC " + type(e).__name__ + ": " + str(e)


def main():
    print("=" * 72)
    print("Gemma 4 可用性探测")
    print("=" * 72)

    from secrets_loader import get_gemini_key
    key = get_gemini_key()
    if not key:
        print("无 GEMINI_API_KEY，退出")
        return

    print("\n[0] 对照组（当前主力，预期 200）")
    code, body = call(CONTROL, key)
    print(f"    {CONTROL}: HTTP {code}")
    if code != 200:
        print(f"    ⚠ 对照组异常，探测结果不可信: {body[:200]}")

    print("\n[1] 候选 Gemma 模型逐个探测")
    results = {}
    for m in CANDIDATES:
        code, body = call(m, key)
        results[m] = code
        note = ""
        if code == 200:
            try:
                txt = json.loads(body)
                parts = ((txt.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
                note = "✓ 可用  返回=" + repr("".join(p.get("text", "") for p in parts)[:40])
            except Exception:
                note = "✓ 200（解析异常）"
        elif code == 404:
            note = "✗ 模型不存在（404）"
        elif code == 429:
            note = "429 配额/限流"
        else:
            note = (body[:150].replace("\n", " ") if body else "")
        print(f"    {m:24s} HTTP {code:>4}  {note}")

    print("\n[2] 结论")
    ok = [m for m, c in results.items() if c == 200]
    if ok:
        print(f"    ✓ 可用模型: {ok}")
        print(f"    → Gemma 可用！RPD=14.4K 是 lite(500) 的 28 倍")
        print()
        print("[3] TPM 压力测试（Gemma TPM=16K，仅为 lite 250K 的 6.4%）")
        print("    用生产同款翻译批（5 条，约 2~4K token）实测能否通过")
        best = ok[0]
        # 构造近似生产的翻译批：5 条带标题+摘要
        items = []
        for i in range(5):
            items.append(
                f"ITEM_ID: probe{i}\nTITLE: Global markets react to policy shift number {i}\n"
                f"CONTENT: Central banks signaled a change in stance as inflation data "
                f"came in above expectations, prompting analysts to revise forecasts. "
                f"Policy makers emphasized data dependence while noting risks to growth. "
                f"Market participants are weighing the implications for rate paths. ({i})"
            )
        prompt = ("Translate each ITEM to Chinese. Output JSON array with format: "
                  '[{"id": "...", "cn_title": "...", "cn_summary": "...", "impact": "..."}]. '
                  "Only output JSON, no markdown.\n\n" + "\n\n".join(items))
        approx_tokens = len(prompt) // 2  # 粗估：中文/英文混合约 2 字符/token
        print(f"    prompt 长度 {len(prompt)} 字符 ≈ {approx_tokens} token（粗估）")
        code, body = call(best, key, prompt=prompt, max_tokens=2048)
        print(f"    HTTP {code}")
        if code == 200:
            try:
                j = json.loads(body)
                usage = j.get("usageMetadata") or {}
                cand = (j.get("candidates") or [{}])[0]
                parts = (cand.get("content") or {}).get("parts") or []
                txt = "".join(p.get("text", "") for p in parts)
                print(f"    ✓ 通过  usage={usage}")
                print(f"    finishReason={cand.get('finishReason')}")
                print(f"    返回长度 {len(txt)} 字符，前 150: {txt[:150]!r}")
                parsed = _try_parse(txt)
                print(f"    JSON 解析: {'✓ ' + str(len(parsed)) + ' 条' if parsed else '✗ 失败'}")
            except Exception as e:
                print(f"    解析异常: {e}  原文前 200: {body[:200]}")
        elif code == 429:
            print(f"    ✗ TPM 或 RPM 超限: {body[:200]}")
        else:
            print(f"    ✗ {body[:250]}")
    elif any(c == 429 for c in results.values()):
        print("    候选名可能不对（返 429 说明模型存在但受限），或今日额度已耗尽")
    else:
        print("    ✗ 以上候选名均不可用——可能需要精确的 API 名")
        print("    → 建议在 AI Studio 页面点开该模型的详情，或查 /models 列表")


def _try_parse(text):
    """从模型输出抓 JSON 数组。"""
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
