#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_gemini_quota.py - Gemini 真实配额探测（2026-10-13，一次性）

## 要回答的问题

1. **真实 RPM 是多少？** 代码里写的"免费层 15 RPM"是**抄文档**的，无实测依据
   （`citizen_impact.py:235` 只观测到"10 批连发会撞 429"，归因到 15 RPM 是推断）。
2. **三个 Gemini 模型的配额是否独立？** 429 错误体里的 quotaId 形如
   `...PerProjectPerModel-FreeTier`——若真按模型分开，现有"换模型"降级链有效；
   若共享，换模型只是白烧一次请求。这直接决定"429 切通道"是否合理。
3. **Retry-After / retryDelay 该等多久？** 决定退避重试的实现参数。

## 方法

不盲目加压找拐点（慢且耗配额），而是**故意触发一次 429 并读错误体**——
Google 的 429 响应含 `google.rpc.QuotaFailure` 与 `google.rpc.RetryInfo`，
里面有确切的 quotaValue 与 retryDelay。一次触发即可拿到权威数字。

小 prompt（≈10 token），最多 25 发/模型，配额消耗可忽略。
只读 + 只打印，不写生产文件。

## SSRF 守卫

本探针**复用 `local/gemini_client` 的守卫与请求实现**（`_ALLOWED_HOSTS` 白名单 +
`_post` 的协议/域名/解析 IP 校验），不自行构造请求——避免在探测脚本里再写一份
容易写漏的安全校验（Mimosa 会拦裸 urllib 调用，且那样确实是重复实现）。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local.gemini_client import GEMINI_BASE, _post, GEMINI_MODELS  # noqa: E402

# 与生产同款候选链
MODELS = list(GEMINI_MODELS)

BURST = 25          # 每模型最多连发次数（触发 429 即停）
TINY = "Reply with the single word: ok"


def _call(model, key):
    """经 gemini_client._post 发一次请求。返回 (status, retry_after, body)。

    429 需要 body 才能读配额，而 _post 会抛 HTTPError —— 故此处把异常体捞回来。
    """
    import urllib.error
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": TINY}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 16},
    }).encode("utf-8")
    url = GEMINI_BASE + "/models/" + model + ":generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": key,
               "User-Agent": "osint-quota-probe/1.0"}
    try:
        return 200, None, _post(url, body, headers, timeout=60)
    except urllib.error.HTTPError as e:
        ra = e.headers.get("Retry-After") if e.headers else None
        return e.code, ra, (e.read() or b"").decode("utf-8", "replace")
    except Exception as e:
        return -1, None, f"EXC {type(e).__name__}: {e}"


def parse_quota(body_text):
    """从 429 响应体提取配额/重试信息。"""
    out = {"quota_id": None, "quota_value": None, "quota_metric": None,
           "retry_delay": None, "status": None, "message": None}
    try:
        j = json.loads(body_text)
    except Exception:
        return out
    err = j.get("error") or {}
    out["status"] = err.get("status")
    out["message"] = (err.get("message") or "")[:220]
    for d in (err.get("details") or []):
        t = d.get("@type", "")
        if "QuotaFailure" in t:
            for v in (d.get("violations") or []):
                out["quota_id"] = v.get("quotaId") or out["quota_id"]
                out["quota_value"] = v.get("quotaValue") or out["quota_value"]
                out["quota_metric"] = v.get("quotaMetric") or out["quota_metric"]
        if "RetryInfo" in t:
            out["retry_delay"] = d.get("retryDelay") or out["retry_delay"]
    return out


def burst_model(model, key):
    """连发直到 429 或到达 BURST。返回汇总 dict。"""
    print(f"\n--- {model} ---")
    t_start = time.time()
    n_ok = n_429 = n_other = 0
    first_429_at = None
    quota_info = None
    retry_after_hdr = None
    for i in range(BURST):
        code, ra, body = _call(model, key)
        if code == 200:
            n_ok += 1
            continue
        if code == 429:
            n_429 += 1
            if first_429_at is None:
                first_429_at = i + 1
                quota_info = parse_quota(body)
                retry_after_hdr = ra
            if n_429 <= 2:
                print(f"    #{i+1} 429  {body[:240]}")
            if n_429 >= 3:
                break
        else:
            n_other += 1
            print(f"    #{i+1} HTTP {code}  {body[:200]}")
            if code in (400, 401, 403, 404):
                break
    dt = time.time() - t_start
    tail = f"首次429在第 {first_429_at} 发" if first_429_at else "未触发429"
    print(f"    ok={n_ok} 429={n_429} other={n_other}  耗时 {dt:.1f}s  {tail}")
    if quota_info:
        print(f"    配额: id={quota_info['quota_id']}")
        print(f"          value={quota_info['quota_value']}  metric={quota_info['quota_metric']}")
        print(f"    retryDelay={quota_info['retry_delay']}  Retry-After头={retry_after_hdr}")
        print(f"    status={quota_info['status']}  msg={quota_info['message']}")
    return {"model": model, "ok": n_ok, "n429": n_429, "other": n_other,
            "first_429_at": first_429_at, "quota": quota_info,
            "retry_after_header": retry_after_hdr, "seconds": round(dt, 1)}


def main():
    print("=" * 72)
    print("Gemini 真实配额探测")
    print("=" * 72)

    from secrets_loader import get_gemini_key
    key = get_gemini_key()
    if not key:
        print("无 GEMINI_API_KEY，退出")
        return
    print(f"key 前缀: {key[:6]}...  (长度 {len(key)})")

    # 1) 先看模型列表（确认账号可用模型）
    print("\n[1] 模型列表")
    try:
        raw = _post(GEMINI_BASE + "/models", None, {
            "x-goog-api-key": key, "User-Agent": "osint-quota-probe/1.0"}, timeout=30)
        j = json.loads(raw)
        names = [m.get("name", "").replace("models/", "") for m in (j.get("models") or [])]
        print(f"    可用 {len(names)} 个模型；候选链命中: {[m for m in MODELS if m in names]}")
    except Exception as e:
        print(f"    列表获取失败（继续探测）: {str(e)[:160]}")

    # 2) 逐模型打爆，读配额
    results = []
    for m in MODELS:
        results.append(burst_model(m, key))
        time.sleep(2)

    # 3) 结论
    print("\n" + "=" * 72)
    print("[结论]")
    for r in results:
        q = r.get("quota") or {}
        print(f"  {r['model']}: ok={r['ok']} 首次429@{r['first_429_at']} "
              f"quotaValue={q.get('quota_value')} quotaId={(q.get('quota_id') or '')[:70]}")
    vals = {(r.get("quota") or {}).get("quota_value") for r in results if r["n429"]}
    ids = {(r.get("quota") or {}).get("quota_id") for r in results if r["n429"]}
    print()
    if vals == {"15"}:
        print("  ✓ 实测确认 15 RPM —— 现有 4.5s/批节流正确且必要")
    elif vals:
        print(f"  真实配额 = {vals}（与文档 15 不同，节流参数应据此调整）")
    else:
        print("  未触发 429（配额较高，或上一轮探测已耗尽）")
    print(f"  quotaId 集合: {ids}")
    if len(ids) > 1:
        print("  → quotaId 各模型不同：**配额按模型独立**，'换模型'降级链有效")
    elif ids:
        print("  → quotaId 相同：**三模型共享配额**，'换模型'缓解不了 429")


if __name__ == "__main__":
    main()
