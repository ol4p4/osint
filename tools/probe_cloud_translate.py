#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_cloud_translate.py - 云端翻译可行性探针（2026-10-13，一次性）

## 要回答的问题

`cloud/translate.py` 至今只支持 NVIDIA 一条通道（2026-09-04 决定"翻译挪到本地"），
但之后 CI 陆续补齐了 Gemini(10-05)/dots(10-12)/NVIDIA 三条 key，而语料结构也已变成
**中文源 79%**（中文条目由 `translate_local._mark_chinese` 纯本地打标，不需要 AI）。
所以真正需要 AI 翻译的只有约 20% 英文源。

本探针实测：**在 CI 上，用 Gemini 翻译英文条目能不能跑通？吞吐多少？质量如何？**

## 设计

- 语料取自仓库根 `intel_*.jsonl`（CI checkout 能拿到的就是这些），挑出**未翻译的
  英文条目**（cn_title 为空且标题非中文）。
- 用生产同款 prompt（抄 `tools/translate_local.py` 的 `_build_prompt`）逐条/逐批调用。
- 报告：可用通道、成功率、单条耗时、总吞吐、译样若干（人工可判质量）。
- 只读 + 只打印，**不写任何生产文件**。
"""
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 与 tools/translate_local.py 同款 prompt（保持一致才有可比性）
PROMPT = (
    "Translate each ITEM to Chinese. Output JSON array with format: "
    '[{"id": "原ITEM_ID原样返回", "cn_title": "中文标题", "cn_summary": "4-6句中文摘要", '
    '"impact": "对中国宏观经济、就业市场和青年失业毕业生的影响分析"}]. '
    "Each output object MUST carry the id of the ITEM it translates. "
    "Only output JSON, no markdown."
)


def _is_chinese(text):
    """与 translate_local._is_chinese_text 同口径：中文字符占比。"""
    if not text:
        return False
    han = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return han >= max(2, len(text) * 0.25)


def collect_english(limit=12):
    """从仓库根 intel_*.jsonl 收集未翻译的英文条目。"""
    out = []
    for fp in sorted(glob.glob(os.path.join(ROOT, "intel_2*.jsonl")))[-3:]:
        try:
            for line in open(fp, encoding="utf-8", errors="replace"):
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if d.get("cn_title"):
                    continue
                title = d.get("title") or ""
                if not title or _is_chinese(title):
                    continue
                out.append({
                    "id": d.get("id") or "",
                    "title": title[:300],
                    "summary": (d.get("summary") or d.get("content_preview") or "")[:500],
                    "source": d.get("source_name") or "",
                    "_file": os.path.basename(fp),
                })
        except Exception as e:
            print(f"[probe] 读 {fp} 失败: {e}")
    return out[:limit]


def _extract_json_array(text):
    """从模型输出里抓 JSON 数组（容错 markdown 围栏）。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1] if len(t.split("```")) > 1 else t
        if t.startswith("json"):
            t = t[4:]
    i, j = t.find("["), t.rfind("]")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except Exception:
        return None


def main():
    print("=" * 70)
    print("云端翻译可行性探针")
    print("=" * 70)

    # ---------- 1. 通道可用性 ----------
    print("\n[1] 通道探测")
    channels = []

    try:
        from secrets_loader import get_gemini_key
        gk = get_gemini_key()
        print(f"    Gemini key: {'有' if gk else '无'}")
        if gk:
            from local.gemini_client import reachable as gemini_reachable
            ok = gemini_reachable()
            print(f"    Gemini 可达: {ok}")
            if ok:
                channels.append(("gemini", gk))
    except Exception as e:
        print(f"    Gemini 初始化失败: {str(e)[:120]}")

    try:
        from secrets_loader import get_dots_key
        dk = get_dots_key()
        print(f"    dots key: {'有' if dk else '无'}")
    except Exception as e:
        print(f"    dots 检查失败: {str(e)[:100]}")

    try:
        from secrets_loader import get_nvidia_key
        nk = get_nvidia_key()
        print(f"    NVIDIA key: {'有' if nk else '无'}")
    except Exception as e:
        print(f"    NVIDIA 检查失败: {str(e)[:100]}")

    if not channels:
        print("\n[结论] 无可用通道，探针退出")
        return

    # ---------- 2. 语料 ----------
    print("\n[2] 语料（仓库根 intel_*.jsonl，未翻译英文条目）")
    items = collect_english(limit=12)
    print(f"    收集到 {len(items)} 条")
    for it in items[:5]:
        print(f"      [{it['source'][:18]}] {it['title'][:70]}")
    if not items:
        print("    无语料，退出")
        return

    # ---------- 3. 逐批翻译（5 条/批，与生产 batch_size 同量级） ----------
    print("\n[3] 翻译实测")
    from local.gemini_client import generate

    BATCH = 5
    results = []
    t0 = time.time()
    n_ok = n_fail = 0

    for i in range(0, len(items), BATCH):
        batch = items[i:i + BATCH]
        payload = "\n\n".join(
            f"ITEM {k+1} (id={it['id']})\nTITLE: {it['title']}\nSUMMARY: {it['summary']}"
            for k, it in enumerate(batch)
        )
        t1 = time.time()
        try:
            text = generate(channels[0][1], PROMPT + "\n\n" + payload,
                            timeout=120, max_tokens=8192)
            dt = time.time() - t1
            parsed = _extract_json_array(text)
            if parsed:
                n_ok += len(parsed)
                results.extend(parsed)
                print(f"    批 {i//BATCH+1}: OK {len(parsed)}/{len(batch)} 条  {dt:.1f}s  "
                      f"({dt/len(batch):.1f}s/条)")
            else:
                n_fail += len(batch)
                print(f"    批 {i//BATCH+1}: 解析失败  {dt:.1f}s  原文前 200 字: {text[:200]}")
        except Exception as e:
            n_fail += len(batch)
            print(f"    批 {i//BATCH+1}: 调用失败 {str(e)[:140]}")

    total_dt = time.time() - t0
    print(f"\n    合计: 成功 {n_ok} / 失败 {n_fail} / 总 {len(items)} 条")
    print(f"    总耗时 {total_dt:.1f}s，吞吐 {n_ok/total_dt*60:.1f} 条/分钟" if total_dt > 0 else "")

    # ---------- 4. 质量抽检 ----------
    print("\n[4] 译文抽检（人工判质量）")
    for r in results[:5]:
        print(f"      id={str(r.get('id'))[:12]}")
        print(f"        cn_title: {str(r.get('cn_title'))[:90]}")
        print(f"        cn_summary: {str(r.get('cn_summary'))[:140]}")

    # ---------- 5. 结论 ----------
    print("\n[5] 结论")
    if n_ok > 0:
        rate = n_ok / total_dt * 60
        print(f"    ✓ 云端 Gemini 可翻译：{n_ok} 条成功，{rate:.1f} 条/分钟")
        english_share = 0.20  # 实测语料英文源约占 20%
        daily = 3600
        print(f"    按每小时可用 {daily}s 估算，可翻 {rate*daily/60:.0f} 条/小时")
        print(f"    → 覆盖每小时新增英文条目（实测约 100~300 条）"
              f"{'可行' if rate*daily/60 >= 100 else '可能需要延长窗口或多通道并行'}")
    else:
        print("    ✗ 云端 Gemini 翻译未跑通，需排查")


if __name__ == "__main__":
    main()
