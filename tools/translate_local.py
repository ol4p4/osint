"""本地翻译 (OpenCode Zen, 无 NVIDIA key 依赖)
- 复用 local/analyze.py 已配的 SSRF 白名单 (_safe_ai_post)
- 同 cloud/translate.py 的批 5 + JSON 输出 + 3 字段 (cn_title/cn_summary/impact)
- 适用: 本地 refresh.py hourly 跑, 走 OpenCode Zen mimo-v2.5-free 翻译 Top 200
- 2026-09-19: OpenCode 免费层 429/403 常态化, 加小红书 dots 备援通道(链首, analyze.py 同款)
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

# 复用 local/analyze.py 的白名单 + SSRF 防护（analyze._safe_ai_post 已含 dots 域）
from local.analyze import _safe_ai_post  # noqa: E402
from secrets_loader import get_opencode_key, get_dots_key  # noqa: E402

# 模型降级链 (2026-10-08 实测更新: mimo-v2.5-free 已 410 弃用; ling-3.0-fin 返 404)
MODEL_CHAIN = [
    "mimo-v2.6-flash-free",
    "nemotron-3.5-lightning-free",
]

# 备援通道: 小红书 dots (2026-09-19 接入, 实测 ~1s 响应; OpenCode 限流时顶上)
DOTS_BASE = "https://note3-prev-api.askdiandian.com/v1"
DOTS_MODEL = "dots3-note-prev"

API_BASE = "https://opencode.ai/zen/v1"
TIMEOUT = 90  # 90s/批, 3 条/批 (单条 ~15s, 5 条 + JSON 拼装 ~60-90s)
BATCH_SIZE = 3
MAX_PER_RUN = 30  # 单次最多翻译 30 条, 10 批 * 30s ≈ 5 分钟内

# Gemini 免费层 15 RPM，批次间节流用（进程级，跨 batch 生效）
_LAST_GEMINI_CALL = 0.0


def _build_prompt():
    return (
        "Translate each ITEM to Chinese. Output JSON array with format: "
        '[{"id": "原ITEM_ID原样返回", "cn_title": "中文标题", "cn_summary": "4-6句中文摘要", '
        '"impact": "对中国宏观经济、就业市场和青年失业毕业生的影响分析"}]. '
        "Each output object MUST carry the id of the ITEM it translates. "
        "Only output JSON, no markdown."
    )


def _is_chinese_text(text):
    """文本是否已是中文（判定「无需翻译」用）。

    判据：中文字符 >= 6 个 且 占比 > 0.3。用占比而非绝对数，
    避免英文长文里夹几个汉字被误判成中文。
    """
    t = text or ""
    if not t:
        return False
    cjk = len(re.findall(r"[\u4e00-\u9fff]", t))
    return cjk >= 6 and cjk / max(len(t), 1) > 0.3


def _extract_cn_title(title):
    """从中文长文标题里摘出纯标题（零 AI 成本）。

    **为什么需要**（2026-10-08）：中文源（金十/新浪/财联社）的 title 常是
    「【标题】正文…」的长文格式，而下游（仪表盘/简报/聚类/证据匹配）统一读
    cn_title。此前靠 AI「翻译」把它们摘成纯标题——实测翻译管线候选里
    **81% 是这种本就中文的条目**，AI 只是在做摘标题的活，30s/2 条的额度
    全浪费在这里，而真英文（19%）排不上队。

    实测中文条目里 31% 是「【…】」长文格式、59% 本身已是短标题（<60 字）。
    两者都能纯本地处理：短标题原样用作 cn_title，长文取【】内容。
    """
    t = (title or "").strip()
    if not t:
        return ""
    m = re.match(r"^\s*【([^】]+)】", t)
    if m:
        return m.group(1).strip()
    # 无【】：截到第一个句末标点（标题通常在第一句）
    m = re.match(r"^(.{4,80}?)[。！？；\n]", t)
    if m:
        return m.group(1).strip()
    # 仍是长文（无标点）→ 控长
    return t[:80].strip()


def local_prefill_chinese(items):
    """把「本就中文」的条目就地补上 cn_title，不需要任何 AI 调用。

    返回补好的条数。补过的条目 language 保持原样（zh/cn 都是中文），
    只填 cn_title——它正是下游判定「已翻译」的标志位，填上后就不再进翻译队列。

    **摘要不在此处生成**：cn_summary 留给研判链路（citizen_impact）按需产出，
    翻译管线的职责只是「让中文条目具备对外展示所需的 cn_title」。
    """
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


def _parse_response(content):
    """剥 markdown 围栏, 解析 JSON array; 失败抛 ValueError。"""
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    content = content.strip().rstrip("`").strip()
    return json.loads(content)


def _apply_translations(batch, translations, batch_idx):
    """把模型返回的译文写回 batch 条目，返回成功条数。

    2026-09-04 修复: 原按位置 batch[j] 匹配, 模型返回乱序时译文张冠李戴。
    改按 id 匹配; 模型未返回 id 时退回按位置(兼容), 并打日志提示。"""
    n = 0
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
            n += 1
    else:
        print(f"[translate_local] batch {batch_idx}: model returned no ids, falling back to positional match")
        for j, trans in enumerate(translations):
            if j < len(batch) and isinstance(trans, dict):
                batch[j].update({
                    "cn_title": trans.get("cn_title", ""),
                    "cn_summary": trans.get("cn_summary", ""),
                    "impact": trans.get("impact", ""),
                    "language": "cn",
                })
                n += 1
    return n


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
        # 通道链（2026-10-05 用户指定顺序）: Gemini 优先 → dots 备援 → OpenCode。
        # OpenCode 腿 = 本机 4010 代理（官网直连已被指纹校验 403；代理用官方桌面
        # 凭据转发恢复可用）→ 官网直连兜底。CI 无 4010，预检自动跳过。
        # Gemini 带可达性预检：本地境内不可达时跳过（否则每批白等 ~48s 黑洞超时）。
        channels = []
        try:
            from secrets_loader import get_gemini_key
            from local.gemini_client import generate as _gemini_generate, reachable as _gemini_reachable
            gkey = get_gemini_key()
            if gkey and _gemini_reachable():
                channels.append({"provider": "gemini", "model": "gemini", "api_key": gkey})
        except Exception as e:
            print(f"[translate_local] gemini 通道初始化跳过: {str(e)[:120]}")
        dkey = get_dots_key()
        if dkey:
            channels.append({"base_url": DOTS_BASE, "model": DOTS_MODEL, "api_key": dkey})
        try:
            from local.zen_proxy_client import reachable as _zen_reachable
            if _zen_reachable():
                for m in MODEL_CHAIN:
                    channels.append({"provider": "zen_proxy", "model": m, "api_key": ""})
        except Exception as e:
            print(f"[translate_local] zen 代理通道初始化跳过: {str(e)[:120]}")
        channels.extend({"base_url": API_BASE, "model": m, "api_key": api_key} for m in MODEL_CHAIN)
        for ch in channels:
            if batch_done:
                break
            model = ch["model"]
            if ch.get("provider") == "zen_proxy":
                try:
                    from local.zen_proxy_client import chat_completion as _zen_chat
                    content = _zen_chat(
                        model,
                        [{"role": "user", "content": prompt + "\n\n" + "\n".join(texts)}],
                        temperature=0.3, max_tokens=12288, timeout=TIMEOUT)
                    translations = _parse_response(content)
                    if not isinstance(translations, list):
                        raise ValueError("not a JSON array")
                    translated += _apply_translations(batch, translations, i // BATCH_SIZE)
                    batch_done = True
                except Exception as e:
                    print(f"[translate_local] batch {i//BATCH_SIZE} [{model}@zen_proxy] failed: {e}, trying next model")
                continue
            if ch.get("provider") == "gemini":
                for attempt in range(2):
                    try:
                        # 免费层 15 RPM：批次间节流到 ≥4.5s/批，避免短时连发撞 429。
                        global _LAST_GEMINI_CALL
                        gap = 4.5 - (time.time() - _LAST_GEMINI_CALL)
                        if _LAST_GEMINI_CALL and gap > 0:
                            time.sleep(gap)
                        try:
                            content = _gemini_generate(ch["api_key"], prompt + "\n\n" + "\n".join(texts))
                        finally:
                            _LAST_GEMINI_CALL = time.time()
                        translations = _parse_response(content)
                        if not isinstance(translations, list):
                            raise ValueError("not a JSON array")
                        translated += _apply_translations(batch, translations, i//BATCH_SIZE)
                        batch_done = True
                        break
                    except Exception as e:
                        if attempt == 0:
                            print(f"[translate_local] batch {i//BATCH_SIZE} [gemini] attempt 1 failed: {e}, retrying")
                        else:
                            print(f"[translate_local] batch {i//BATCH_SIZE} [gemini] failed: {e}, trying next model")
                continue
            payload = json.dumps({
                "model": ch["model"],
                "messages": [{"role": "user", "content": prompt + "\n\n" + "\n".join(texts)}],
                "temperature": 0.3,
                # 2026-09-19: dots3 是推理模型, reasoning 计入 max_tokens（实测翻译批 reasoning ~8k 字符）,
                # 4096 时正文常被截成非法 JSON (Unterminated string); 提到 12288 降截断率
                "max_tokens": 12288,
            }).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + ch["api_key"],
                "User-Agent": "opencode/latest/1.3.15/cli",
                "x-opencode-client": "cli",
                "x-opencode-session": uuid.uuid4().hex,
                "x-opencode-project": uuid.uuid4().hex[:8],
                "x-opencode-request": uuid.uuid4().hex,
            }
            if "askdiandian" in ch["base_url"]:
                headers["api-key"] = ch["api_key"]
                headers.pop("x-opencode-client", None)
            for attempt in range(2):
                try:
                    url = ch["base_url"].rstrip("/") + "/chat/completions"
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
                    translated += _apply_translations(batch, translations, i//BATCH_SIZE)
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
    """从 jsonl 文件收集未翻译条目，按优先级排序后取前 max_n 条。

    2026-10-06：排序键改为「新鲜度优先 → base_score 降序 → 时间降序」，
    并加**跨源去重**（select_priority_unique）——同一事件被多源采到时标题近乎相同
    （实测 jaccard 1.00/0.83）却因 id 不同全保留，导致 AI 对同一件事分析多遍
    （未翻译池 14~17% 是这类重复）。去重与排序的说明见 local/intel_gate.py。
    候选本身不丢弃，只是"本轮该处理谁"里同一事件只留最优先的那条代表。
    """
    try:
        sys.path.insert(0, str(ROOT))
        from local.intel_gate import select_priority_unique
    except Exception:
        select_priority_unique = None  # noqa: F811  模块缺失时降级为纯时间序

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
                "base_score": d.get("base_score"),
                "_file": str(fp),
            })
    items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    if select_priority_unique:
        # 内部含「新鲜优先 → 分数 → 时间」排序 + 跨源去重
        return select_priority_unique(items, max_n)
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

    # 2026-10-08：先把「本就中文」的条目就地补 cn_title（零 AI 成本），
    # 它们不再占用翻译额度——实测原先 81% 的额度浪费在这上面（AI 只是把
    # 中文标题摘一遍），修后同样额度可多翻约 4 倍真英文。
    # 先在更大的池子里摘（max×4），让 AI 额度能被真英文填满；否则中文占满
    # 候选窗口后，本轮只剩少数英文可翻，额度空转。
    pool = collect_unjtranslated(jsonl_files, max_n=max(args.max * 4, args.max))
    n_local = local_prefill_chinese(pool)
    if n_local:
        written = write_back_to_jsonl(pool, jsonl_files)
        print(f"[translate_local] 本地摘标题 {n_local} 条（中文条目，零 AI 成本），写回 {written} 条")
    # 只剩真需要翻译的（英文等），取前 args.max 条占用本轮额度
    candidates = [c for c in pool if not c.get("cn_title")][:args.max]
    print(f"[translate_local] 其中需 AI 翻译 {len(candidates)} 条")

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
