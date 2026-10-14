#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""CI 批量翻译 - 通道链 Gemini → dots → NVIDIA（2026-10-13 改造）

## 为什么翻译重新上云

2026-09-04 翻译挪到本地，当时的理由是「CI 只有 NVIDIA 单通道、吞吐
50 条/4h 跟不上」。此后情况变了：
  · CI 陆续补齐 GEMINI_API_KEY(10-05) / DOTS_API_KEY(10-12) / NVIDIA(08-29)，
    `citizen_impact` 已在 CI 用同一通道链跑通（实测 45/50）；
  · 2026-10-13 探针（tools/probe_cloud_translate.py，已删但记录在 AGENTS.md）
    实测 CI 用 Gemini 翻译 12/12 零失败、53.6 条/分钟；
  · Gemini 免费层 RPD 实测 500×2（两个 lite 模型，AI Studio 页核对），
    每天 ~70 批 ≈ 8% 用量。

而 `cloud/translate.py` 一直停留在 09-04 的单 NVIDIA 状态——本脚本按
`cloud/citizen_impact.py` 的成熟模式接三通道，外加**中文预填**（零 AI 成本：
实测翻译候选里 81% 本就是中文源条目，只需摘标题，不该占 AI 额度）。

## 与本地翻译的关系

本地 translate_now（tools/translate_local.py）保留：CI 每 3h 一轮，本地
每小时都在进新条目，两边增量翻译互补（幂等：cn_title 非空即跳过）。

## 幂等与安全

- 只处理无 cn_title 的条目（重跑不重复消耗）。
- 所有请求过 https + 域名白名单 + IP 边界校验（与 citizen_impact 同规格；
  gemini 复用 local.gemini_client 自带守卫）。
- 时间预算（--budget）到点即停，剩余留待下轮（翻译是增量的，不污染）。
"""
import glob
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ------------------------------------------------------------------ 通道配置

DOTS_BASE = "https://note3-prev-api.askdiandian.com/v1"
NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
# _safe_post 白名单（gemini 走 local.gemini_client 自带守卫，不在这里）
OPENAI_HOSTS = {"note3-prev-api.askdiandian.com", "integrate.api.nvidia.com"}

# NVIDIA 翻译模型降级链（2026-08-30 bench 实测可用，按速度/质量排序）
MODEL_CHAIN = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "meta/llama-3.2-11b-vision-instruct",
]
BATCH_SIZE = 5
GEMINI_MIN_GAP = 4.5          # 免费层 15 RPM → ≥4.5s/批留余量（实测口径，同 citizen_impact）
_last_gemini_call = 0.0

PROMPT = (
    "Translate each ITEM to Chinese. Output JSON array with format: "
    '[{"id": "原ITEM_ID原样返回", "cn_title": "中文标题", "cn_summary": "4-6句中文摘要", '
    '"impact": "对中国宏观经济、就业市场和青年失业毕业生的影响分析"}]. '
    "Each output object MUST carry the id of the ITEM it translates. "
    "Only output JSON, no markdown."
)


# ------------------------------------------------------------------ HTTP 守卫

def _safe_post(url, payload, headers, timeout=60):
    """https + 白名单 host + 解析 IP 不得为私网/环回/保留（SSRF 守卫）。"""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("blocked non-https scheme: " + str(parsed.scheme))
    if (parsed.hostname or "") not in OPENAI_HOSTS:
        raise ValueError("blocked non-whitelisted translate endpoint: " + url)
    for info in socket.getaddrinfo(parsed.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_reserved
                or ip.is_link_local or ip.is_multicast):
            raise ValueError("endpoint resolves to forbidden address: " + str(ip))
    req = urllib.request.Request(url, data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ------------------------------------------------------------------ 通道构造

def build_channels():
    """返回可用通道列表（有序：Gemini → dots → NVIDIA）。

    通道顺序与 cloud/citizen_impact.py 一致（2026-10-05 用户指定）；
    Gemini 带可达性预检（本地境内不可达时自动跳过，CI 美国 runner 可达）。
    """
    channels = []
    try:
        from secrets_loader import get_gemini_key
        from local.gemini_client import reachable as gemini_reachable
        gkey = get_gemini_key()
        if gkey and gemini_reachable():
            channels.append({"kind": "gemini", "api_key": gkey})
        else:
            print("[TRANSLATE] gemini 不可用（无 key 或不可达）")
    except Exception as e:
        print("[TRANSLATE] gemini 通道初始化跳过: " + str(e)[:120])
    try:
        from secrets_loader import get_dots_key
        dkey = get_dots_key()
        if dkey:
            channels.append({"kind": "openai", "base_url": DOTS_BASE,
                             "model": "dots3-note-prev", "api_key": dkey,
                             # dots3 推理模型：reasoning 计入 max_tokens，
                             # 4096 会把正文截成非法 JSON（translate_local 同款教训）→ 12288
                             "max_tokens": 12288,
                             "extra_headers": {"api-key": dkey}})
    except Exception as e:
        print("[TRANSLATE] dots 通道初始化跳过: " + str(e)[:120])
    try:
        from secrets_loader import get_nvidia_key
        nvkey = get_nvidia_key()
        if nvkey:
            channels.append({"kind": "openai", "base_url": NVIDIA_BASE,
                             "models": list(MODEL_CHAIN), "api_key": nvkey,
                             "max_tokens": 4096})
    except Exception as e:
        print("[TRANSLATE] NVIDIA 通道初始化跳过: " + str(e)[:120])
    return channels


# ------------------------------------------------------------------ 解析与回填

def parse_response(content):
    """模型输出 → list（容错 markdown 围栏/前后缀文本）。失败返回 None。"""
    t = (content or "").strip()
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
        arr = json.loads(t[i:j + 1])
        return arr if isinstance(arr, list) else None
    except Exception:
        return None


def apply_translations(batch, translations):
    """按 id 回填（乱序安全）；模型没给 id 时退回按位置并告警。返回成功数。"""
    n = 0
    by_id = {t.get("id"): t for t in translations
             if isinstance(t, dict) and t.get("id")}
    if by_id:
        for _, item in batch:
            trans = by_id.get(item.get("id", ""))
            if not trans:
                continue
            item.update(trans)
            item["language"] = "cn"
            n += 1
    else:
        print("  [TRANSLATE] model returned no ids, falling back to positional match")
        for j, trans in enumerate(translations):
            if j < len(batch) and isinstance(trans, dict):
                _, item = batch[j]
                item.update(trans)
                item["language"] = "cn"
                n += 1
    return n


# ------------------------------------------------------------------ 中文预填
# 与 tools/translate_local.py 的 _is_chinese_text/_extract_cn_title/local_prefill
# 同判据。**改动需同步两处并跑同一测试**（AGENTS.md：同一类问题在多个模块有副本）。

def _is_chinese_text(text):
    t = text or ""
    if not t:
        return False
    cjk = len(re.findall(r"[\u4e00-\u9fff]", t))
    return cjk >= 6 and cjk / max(len(t), 1) > 0.3


def _extract_cn_title(title):
    """中文长标题摘纯标题：优先【…】，否则截到句末标点，兜底控长 80 字。"""
    t = (title or "").strip()
    if not t:
        return ""
    m = re.match(r"^\s*【([^】]+)】", t)
    if m:
        return m.group(1).strip()
    m = re.match(r"^(.{4,80}?)[。！？；\n]", t)
    if m:
        return m.group(1).strip()
    return t[:80].strip()


def local_prefill_chinese(items):
    n = 0
    for it in items:
        if it.get("cn_title"):
            continue
        title = it.get("title") or ""
        if not _is_chinese_text(title):
            continue
        cn = _extract_cn_title(title)
        if cn:
            it["cn_title"] = cn
            n += 1
    return n


# ------------------------------------------------------------------ 单通道调用

def _call_gemini(ch, prompt):
    """经 local.gemini_client 调用（自带 429 换桶/退避与 SSRF 守卫）。
    批间 4.5s 节流在本函数做（RPM 按项目+模型计，跨批累计）。"""
    from local.gemini_client import generate
    global _last_gemini_call
    gap = GEMINI_MIN_GAP - (time.time() - _last_gemini_call)
    if _last_gemini_call and gap > 0:
        time.sleep(gap)
    try:
        return generate(ch["api_key"], prompt, timeout=120, max_tokens=8192)
    finally:
        _last_gemini_call = time.time()


def _call_openai(ch, prompt, model=None):
    url = ch["base_url"].rstrip("/") + "/chat/completions"
    body = {
        "model": model or ch.get("model"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": ch.get("max_tokens", 4096),
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + ch["api_key"],
        "User-Agent": "osint-translate/1.0",
    }
    headers.update(ch.get("extra_headers") or {})
    raw = _safe_post(url, json.dumps(body).encode("utf-8"), headers, timeout=180)
    result = json.loads(raw)
    msg = result["choices"][0]["message"]
    content = msg.get("content", "")
    if not content:
        # 推理模型可能把正文放在 reasoning 字段（translate_local 同款兜底）
        for k in ("reasoning_content", "reasoning", "output"):
            v = msg.get(k, "")
            if v:
                content = v
                break
    return content


def translate_one_batch(batch, channels):
    """一个批次走通道链。返回 (成功数, 通道标签)；全失败 (0, None)。"""
    prompt = PROMPT + "\n\n" + "\n\n".join(
        "ITEM_ID: {id}\nTITLE: {title}\nCONTENT: {content}".format(
            id=item.get("id", ""),
            title=item.get("title", ""),
            content=(item.get("content_preview") or item.get("content") or "")[:500])
        for _, item in batch)
    for ch in channels:
        models = [ch.get("model")] if ch.get("model") else (ch.get("models") or ["gemini"])
        for model in models:
            host = (ch.get("base_url") or "gemini").split("//")[-1].split(".")[0]
            label = host + ":" + str(model)
            try:
                if ch["kind"] == "gemini":
                    content = _call_gemini(ch, prompt)
                else:
                    content = _call_openai(ch, prompt, model)
                arr = parse_response(content)
                if arr is None:
                    raise ValueError("unparseable output: " + str(content)[:120])
                n = apply_translations(batch, arr)
                if n:
                    return n, label
                raise ValueError("parsed array applied 0 items")
            except Exception as e:
                print(f"  [TRANSLATE] {label} 失败: {str(e)[:140]}，换下一通道")
    return 0, None


# ------------------------------------------------------------------ 主流程

def run(dir_path=".", max_items=100, budget=600):
    channels = build_channels()
    if not channels:
        print("[TRANSLATE] 无可用通道（GEMINI/DOTS/NVIDIA 均不可用），跳过")
        sys.exit(0)
    print("[TRANSLATE] 通道链: " + " -> ".join(
        ("gemini" if c["kind"] == "gemini"
         else c["base_url"].split("//")[-1].split(".")[0]) for c in channels))

    files = sorted(glob.glob(os.path.join(dir_path, "intel_2*.jsonl")), reverse=True)
    if not files:
        print("No intel files found")
        sys.exit(0)

    file_items = {}
    translated_ids = set()
    for f in files:
        arr = []
        try:
            for line in Path(f).read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line:
                    try:
                        arr.append(json.loads(line))
                    except Exception:
                        pass
        except OSError as e:
            print(f"[TRANSLATE] 读 {f} 失败: {e}")
            continue
        file_items[f] = arr
        for it in arr:
            if it.get("cn_title"):
                translated_ids.add(it.get("id"))

    # 未翻译条目（跨文件按 id 去重）
    todo = []
    seen = set()
    for f in files:
        for it in file_items[f]:
            iid = it.get("id")
            if iid in seen or iid in translated_ids:
                continue
            seen.add(iid)
            todo.append((f, it))

    # ① 中文预填：零 AI 成本（实测翻译候选里 81% 本就是中文源条目）
    n_prefill = local_prefill_chinese([it for _, it in todo])
    english = [(f, it) for f, it in todo if not it.get("cn_title")]
    print(f"[TRANSLATE] 未翻译 {len(todo)} 条 -> 中文预填 {n_prefill}（零 AI），"
          f"待 AI 翻译 {len(english)} 条")

    # ② 真英文按最新优先取 max_items，走通道链
    english.sort(key=lambda p: p[1].get("published_at", ""), reverse=True)
    todo_en = english[:max_items]

    translated = 0
    if todo_en:
        deadline = time.time() + budget
        for i in range(0, len(todo_en), BATCH_SIZE):
            if time.time() > deadline:
                print(f"[TRANSLATE] 时间预算 {budget}s 用尽，剩 {len(todo_en)-i} 条留下轮")
                break
            batch = todo_en[i:i + BATCH_SIZE]
            n, via = translate_one_batch(batch, channels)
            translated += n
            if via:
                print(f"[TRANSLATE] 批 {i//BATCH_SIZE+1}: {n}/{len(batch)} 经 {via}")

    # ③ 写回（含预填）。写条件按「条目内容是否变过」记，不只按 id 是否在旧集合——
    # 预填与翻译都是原地改 dict，这里数出新增 cn_title 的条目数决定是否重写文件。
    total_written = 0
    for f, arr in file_items.items():
        changed = sum(1 for it in arr
                      if it.get("cn_title") and it.get("id") not in translated_ids)
        if changed:
            Path(f).write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in arr) + "\n",
                encoding="utf-8")
            total_written += changed
    print(f"[TRANSLATE] AI 翻译 {translated} + 预填 {n_prefill}，"
          f"写回 {total_written} 条 / {len(file_items)} 文件")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="CI 批量翻译（Gemini->dots->NVIDIA）")
    parser.add_argument("--dir", default=".", help="intel_*.jsonl 所在目录")
    parser.add_argument("--max", type=int, default=100, help="本轮 AI 翻译上限")
    parser.add_argument("--budget", type=int, default=600, help="AI 调用时间预算（秒）")
    args = parser.parse_args()
    run(args.dir, max_items=args.max, budget=args.budget)


if __name__ == "__main__":
    main()
