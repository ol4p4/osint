# -*- coding: utf-8 -*-
r"""citizen_impact.py - AI 三层研判（2026-08-30 升级：双层身份 + 四维框架）
对 intel jsonl 中未研判的条目，用参谋长模型批量输出：
  第一层 impact          —— 对中国普通公民的传导（物价/安全/资产/政策）
  第二层 graduate_impact —— 对"年轻失业毕业生"的传导（就业/技能/政策/避坑，persona.md 语料）
  四维 dims              —— 积累制度/空间修正/国家-市场边界/阶级利益 各一句
写回条目字段，仪表盘直接显示。

CI 在翻译步骤后调用；本地可手动跑：python cloud/citizen_impact.py --dir D:\osint\data
增量：已有 impact_level 的跳过；每次最多 50 条，batch 5，8 分钟时间预算。
2026-08-30 起扫描全部 intel_2*.jsonl（此前只扫最新文件，历史条目永无研判）。
"""
import argparse
import glob
import ipaddress
import json
import os
import socket
import sys
import time
import uuid
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

AI_ALLOWED_HOST = "opencode.ai"
MAX_PER_RUN = 50
BATCH_SIZE = 5

SYSTEM_PROMPT = (
    "你是面向中国普通公民与年轻失业毕业生的政治经济分析参谋。对每条新闻做两层传导判断："
    "第一层对中国普通公民（物价/安全/资产/政策），第二层对年轻失业毕业生（就业市场/技能需求/"
    "政策机会/避坑）。再做四维政治经济学诊断（积累制度/空间修正/国家-市场边界/阶级利益）。"
    "客观具体不夸大，确实没有影响就如实判低。"
)

GRADUATE_CONTEXT = (
    "第二层身份画像：中国年轻失业毕业生（本科/硕士），处境=学历贬值、技能错配、资产无对冲、"
    "路径依赖断裂；关注=就业市场变化、技能需求迁移、就业/落户/补贴政策、国企央企与新能源等"
    "行业招聘信号、18个月窗口期的行动向量与避坑。"
)


def _safe_ai_post(url, payload, headers, timeout=120):
    """SSRF 防护：仅 https + opencode.ai 白名单 + 解析结果不得指向私有/环回/保留地址"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") != AI_ALLOWED_HOST:
        raise ValueError("blocked non-whitelisted AI endpoint: " + url)
    for info in socket.getaddrinfo(parsed.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
            raise ValueError("endpoint resolves to forbidden address: " + str(ip))
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def call_ai(config, prompt):
    """走 OpenCode Zen 免费代理（参谋长 Mimo，失败依次降级 fallback 模型）"""
    api_cfg = config.get("api", {})
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from secrets_loader import get_opencode_key
        key = get_opencode_key()
    except Exception:
        key = api_cfg.get("api_key", "")
    attempts = [{"base_url": api_cfg.get("base_url"), "model": api_cfg.get("model"),
                 "api_key": key}]
    for fb in (api_cfg.get("fallback_models") or []):
        fb = dict(fb)
        fb["api_key"] = fb.get("api_key") or key
        attempts.append(fb)
    last_err = None
    for m in attempts:
        try:
            url = (m.get("base_url") or "").rstrip("/") + "/chat/completions"
            payload = json.dumps({
                "model": m.get("model"),
                "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 3000,
            }).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + (m.get("api_key") or ""),
                "User-Agent": "opencode/latest/1.3.15/cli",
                "x-opencode-client": "cli",
                "x-opencode-session": uuid.uuid4().hex,
            }
            raw = _safe_ai_post(url, payload, headers, 150)
            result = json.loads(raw)
            content = result["choices"][0]["message"].get("content", "")
            if content and content.strip():
                return content.strip()
            raise ValueError("empty response")
        except Exception as e:
            last_err = e
            print("[IMPACT] model " + str(m.get("model")) + " failed: " + str(e))
    raise last_err if last_err else RuntimeError("no models available")


def parse_json_array(text):
    """解析 AI 输出的 JSON 数组（容忍 markdown 包裹）"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    start = text.find("[")
    if start < 0:
        return []
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    return []
    return []


def build_prompt(batch):
    news = [{"id": it.get("id", ""),
             "title": ((it.get("cn_title") or it.get("title", "")) or "")[:80],
             "summary": ((it.get("cn_summary") or it.get("content_preview", "")) or "")[:300]}
            for it in batch]
    return ("新闻列表(JSON)：\n" + json.dumps(news, ensure_ascii=False)
            + "\n\n" + GRADUATE_CONTEXT
            + '\n\n对每条输出 JSON（严格只输出数组，不要 markdown）：'
              '[{"id":"原id",'
              '"impact_level":"高|中|低",'
              '"impact":"第一层：对普通公民的传导一句话(≤50字，指明物价/安全/资产/政策哪条线)",'
              '"graduate_impact":"第二层：对年轻失业毕业生的传导一句话(≤60字，指向就业/技能/政策/避坑)",'
              '"dims":{"accumulation_node":"≤30字","spatial_layer":"≤30字",'
              '"state_market_shift":"≤30字","class_interest":"≤30字"}}]')


def load_jsonl(path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description="AI 三层研判（公民/毕业生/四维）")
    parser.add_argument("--dir", default=".", help="intel_*.jsonl 所在目录（CI 默认 cwd，本地传产物目录）")
    parser.add_argument("--max", type=int, default=MAX_PER_RUN)
    parser.add_argument("--budget", type=int, default=480,
                        help="AI 调用总时间预算(秒)。CI 保持 480(job 上限内), 本地放宽到 900 提升吞吐")
    args = parser.parse_args()

    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    import yaml
    config = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    # 扫描全部 intel_2*.jsonl（不只最新文件），跨文件按 id 去重后取最新 50 条未研判条目
    files = sorted(glob.glob(os.path.join(args.dir, "intel_2*.jsonl")), reverse=True)
    if not files:
        print("[IMPACT] no intel files found")
        sys.exit(0)
    file_items = {f: load_jsonl(Path(f)) for f in files}
    analyzed_ids = {it.get("id") for f in files for it in file_items[f] if it.get("impact_level")}
    todo = []
    seen = set()
    for f in files:
        for it in file_items[f]:
            iid = it.get("id")
            if iid in seen or iid in analyzed_ids:
                continue
            seen.add(iid)
            todo.append((f, it))
    todo.sort(key=lambda p: p[1].get("published_at", ""), reverse=True)
    todo = todo[:args.max]
    if not todo:
        print("[IMPACT] all items already analyzed")
        sys.exit(0)
    print(f"[IMPACT] to analyze: {len(todo)} across {len(files)} files")

    analyzed = 0
    deadline = time.time() + args.budget  # 默认 480s(CI), 本地传 900 提升吞吐(2026-09-04 参数化)
    for i in range(0, len(todo), BATCH_SIZE):
        if time.time() > deadline:
            print(f"[IMPACT] time budget exhausted, {len(todo)-i} items left for next CI run")
            break
        batch = todo[i:i + BATCH_SIZE]
        try:
            results = parse_json_array(call_ai(config, build_prompt([it for _, it in batch])))
        except Exception as e:
            print(f"[IMPACT] batch {i} failed: {e}")
            continue
        by_id = {r.get("id"): r for r in results if isinstance(r, dict) and r.get("id")}
        for f, it in batch:
            r = by_id.get(it.get("id"))
            if not r or not r.get("impact"):
                continue
            level = r.get("impact_level", "低")
            it["impact_level"] = level if level in ("高", "中", "低") else "低"
            it["impact"] = str(r["impact"])
            if r.get("graduate_impact"):
                it["graduate_impact"] = str(r["graduate_impact"])
            if isinstance(r.get("dims"), dict):
                it["dims"] = {k: str(v)[:60] for k, v in r["dims"].items() if v}
            analyzed += 1

    # 按文件写回（条目分布在多个文件）
    for f, fitems in file_items.items():
        Path(f).write_text(
            "\n".join(json.dumps(it, ensure_ascii=False) for it in fitems) + "\n",
            encoding="utf-8")
    print(f"[IMPACT] analyzed {analyzed}/{len(todo)} items across {len(file_items)} files")


if __name__ == "__main__":
    main()
