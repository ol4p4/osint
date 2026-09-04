"""本地翻译 (OpenCode Zen, 无 NVIDIA key 依赖)
- 复用 local/analyze.py 已配的 SSRF 白名单 (_safe_ai_post)
- 同 cloud/translate.py 的批 5 + JSON 输出 + 3 字段 (cn_title/cn_summary/impact)
- 适用: 本地 refresh.py hourly 跑, 走 OpenCode Zen mimo-v2.5-free 翻译 Top 200
"""
import json
import os
import re
import sys
import time
import uuid
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(r"D:\osint")
sys.path.insert(0, str(ROOT))

# 复用 local/analyze.py 的白名单 + SSRF 防护
from local.analyze import _safe_ai_post  # noqa: E402
from secrets_loader import get_opencode_key  # noqa: E402

# 模型降级链 (实测可用: mimo + nemotron; 其他 deepseek-v4-flash-free 报 400)
MODEL_CHAIN = [
    "mimo-v2.5-free",
    "nemotron-3.5-lightning-free",
]

API_BASE = "https://opencode.ai/zen/v1"
TIMEOUT = 90  # 90s/批, 3 条/批 (单条 ~15s, 5 条 + JSON 拼装 ~60-90s)
BATCH_SIZE = 3
MAX_PER_RUN = 30  # 单次最多翻译 30 条, 10 批 * 30s ≈ 5 分钟内


def _build_prompt():
    return (
        "Translate each ITEM to Chinese. Output JSON array with format: "
        '[{"id": "原ITEM_ID原样返回", "cn_title": "中文标题", "cn_summary": "4-6句中文摘要", '
        '"impact": "对中国宏观经济、就业市场和青年失业毕业生的影响分析"}]. '
        "Each output object MUST carry the id of the ITEM it translates. "
        "Only output JSON, no markdown."
    )


def _parse_response(content):
    """剥 markdown 围栏, 解析 JSON array; 失败抛 ValueError。"""
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    content = content.strip().rstrip("`").strip()
    return json.loads(content)


def translate_batch(items, api_key, deadline=None):
    """items: list[dict] (单条情报); 返回 (translated, failed) 计数"""
    if not api_key:
        return 0, 0

    translated = 0
    failed = 0
    for i in range(0, len(items), BATCH_SIZE):
        if deadline and time.time() > deadline:
            print(f"[translate_local] time budget exhausted, {len(items)-i} items left for next run")
            break
        batch = items[i:i + BATCH_SIZE]
        texts = []
        for it in batch:
            title = it.get("title", "")
            content = it.get("content_preview", "")[:500]
            texts.append(f"ITEM_ID: {it.get('id', '')}\nTITLE: {title}\nCONTENT: {content}")

        prompt = _build_prompt()
        batch_done = False
        for model in MODEL_CHAIN:
            if batch_done:
                break
            payload = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": prompt + "\n\n" + "\n".join(texts)}],
                "temperature": 0.3,
                "max_tokens": 4096,
            }).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + api_key,
                "User-Agent": "opencode/latest/1.3.15/cli",
                "x-opencode-client": "cli",
                "x-opencode-session": uuid.uuid4().hex,
                "x-opencode-project": uuid.uuid4().hex[:8],
                "x-opencode-request": uuid.uuid4().hex,
            }
            for attempt in range(2):
                try:
                    url = API_BASE + "/chat/completions"
                    raw = _safe_ai_post(url, payload, headers, TIMEOUT)
                    result = json.loads(raw)
                    msg = result["choices"][0]["message"]
                    content = msg.get("content", "")
                    if not content:
                        for k in ("reasoning_content", "reasoning", "output"):
                            v = msg.get(k, "")
                            if v:
                                content = v
                                break
                    if not content or not content.strip():
                        raise ValueError("empty response")
                    translations = _parse_response(content)
                    if not isinstance(translations, list):
                        raise ValueError("not a JSON array")
                    # 2026-09-04 修复: 原按位置 batch[j] 匹配, 模型返回乱序时译文张冠李戴。
                    # 改按 id 匹配; 模型未返回 id 时退回按位置(兼容), 并打日志提示
                    trans_have_id = any(isinstance(t, dict) and t.get("id") for t in translations)
                    if trans_have_id:
                        by_id = {t.get("id"): t for t in translations if isinstance(t, dict) and t.get("id")}
                        for it in batch:
                            trans = by_id.get(it.get("id", ""))
                            if not trans:
                                continue
                            it.update({
                                "cn_title": trans.get("cn_title", ""),
                                "cn_summary": trans.get("cn_summary", ""),
                                "impact": trans.get("impact", ""),
                                "language": "cn",
                            })
                            translated += 1
                    else:
                        print(f"[translate_local] batch {i//BATCH_SIZE}: model returned no ids, falling back to positional match")
                        for j, trans in enumerate(translations):
                            if j < len(batch) and isinstance(trans, dict):
                                batch[j].update({
                                    "cn_title": trans.get("cn_title", ""),
                                    "cn_summary": trans.get("cn_summary", ""),
                                    "impact": trans.get("impact", ""),
                                    "language": "cn",
                                })
                                translated += 1
                    batch_done = True
                    break
                except Exception as e:
                    if attempt == 0:
                        print(f"[translate_local] batch {i//BATCH_SIZE} [{model}] attempt 1 failed: {e}, retrying")
                    else:
                        print(f"[translate_local] batch {i//BATCH_SIZE} [{model}] failed: {e}, trying next model")
        if not batch_done:
            failed += len(batch)
    return translated, failed


def collect_unjtranslated(jsonl_files, max_n=MAX_PER_RUN):
    """从 jsonl 文件收集未翻译条目 (按 published_at 倒序, 取前 max_n 条)
    避免反复翻老数据, 优先翻最新 24h 新抓的。
    """
    items = []
    for fp in jsonl_files:
        try:
            lines = Path(fp).read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for line in lines:
            try:
                d = json.loads(line)
            except Exception:
                continue
            # 已翻译 (cn_title 非空) 跳过
            if d.get("cn_title"):
                continue
            # 必须有原文
            if not d.get("title"):
                continue
            items.append({
                "id": d.get("id", ""),
                "title": d.get("title", ""),
                "content_preview": d.get("content_preview", ""),
                "published_at": d.get("published_at", ""),
                "_file": str(fp),
            })
    items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return items[:max_n]


def write_back_to_jsonl(translated_items, jsonl_files):
    """把翻译结果 (cn_title/cn_summary/impact/language=cn) 写回原 jsonl 文件。
    按 id 匹配, 避免全量重写。
    """
    # 建 id -> {cn_title,cn_summary,impact,language} 映射
    upd = {}
    for it in translated_items:
        if it.get("id") and it.get("cn_title"):
            upd[it["id"]] = {
                "cn_title": it["cn_title"],
                "cn_summary": it.get("cn_summary", ""),
                "impact": it.get("impact", ""),
                "language": "cn",
            }
    if not upd:
        return 0
    total_written = 0
    for fp in jsonl_files:
        try:
            lines = Path(fp).read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        new_lines = []
        dirty = False
        for line in lines:
            try:
                d = json.loads(line)
            except Exception:
                new_lines.append(line)
                continue
            iid = d.get("id", "")
            if iid in upd and not d.get("cn_title"):
                d.update(upd[iid])
                dirty = True
                total_written += 1
            new_lines.append(json.dumps(d, ensure_ascii=False))
        if dirty:
            Path(fp).write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return total_written


def main():
    # 2026-09-04 参数化: 翻译挪出 CI 后, 本地要消化每轮回流的全部英文条目,
    # 默认 30 条/360s 不够。refresh 传 --max 100 --budget 900。
    import argparse
    parser = argparse.ArgumentParser(description="本地 OpenCode Zen 翻译")
    parser.add_argument("--max", type=int, default=MAX_PER_RUN)
    parser.add_argument("--budget", type=int, default=360, help="时间预算(秒), 默认 360 与历史一致")
    args = parser.parse_args()

    # 默认产物目录, 与 refresh.py 一致
    base = Path(r"D:\osint\data")
    jsonl_files = sorted(base.glob("intel_2*.jsonl"))  # 只读 dated jsonl
    print(f"[translate_local] scanning {len(jsonl_files)} jsonl files")
    if not jsonl_files:
        print(f"[translate_local] no jsonl found, skip")
        return

    api_key = get_opencode_key()
    if not api_key:
        print("[translate_local] no OpenCode API key (config.local.yaml / env), skip")
        return

    # 取最新 24h 未翻译, 限 max 条
    candidates = collect_unjtranslated(jsonl_files, max_n=args.max)
    print(f"[translate_local] {len(candidates)} untranslated candidates")

    if not candidates:
        print("[translate_local] nothing to translate")
        return

    deadline = time.time() + args.budget
    translated, failed = translate_batch(candidates, api_key, deadline)
    print(f"[translate_local] {translated} translated, {failed} failed")

    if translated:
        # 写回 jsonl
        written = write_back_to_jsonl(candidates, jsonl_files)
        print(f"[translate_local] wrote back {written} entries to jsonl")


if __name__ == "__main__":
    main()
