# -*- coding: utf-8 -*-
r"""citizen_impact.py - AI 公民影响研判（第三层漏斗）
关键词命中只能接住显性话题，"抽象新闻对普通公民的影响"需要语义判断：
对 intel jsonl 中 impact 缺失的条目，用参谋长模型(Mimo)批量研判传导影响，
写回 impact_level(高/中/低) + impact 字段，仪表盘直接显示。

CI 在翻译步骤后调用（config.yaml 的 OpenCode key 就在仓库里）；
本地也可手动跑：python cloud/citizen_impact.py --dir D:\osint\data
增量：已有 impact_level 的跳过；每次最多 50 条，batch 5（与翻译同规格）。
"""
import argparse
import glob
import ipaddress
import json
import os
import socket
import sys
import uuid
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

AI_ALLOWED_HOST = "opencode.ai"
MAX_PER_RUN = 50
BATCH_SIZE = 5

SYSTEM_PROMPT = ("你是面向中国普通公民的政治经济分析参谋。对每条新闻判断它对普通公民的传导影响"
                 "（物价、就业、资产、政策、安全）。客观具体不夸大，确实没有实际影响就如实判低。")


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
                "max_tokens": 2000,
            }).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + (m.get("api_key") or ""),
                "User-Agent": "opencode/latest/1.3.15/cli",
                "x-opencode-client": "cli",
                "x-opencode-session": uuid.uuid4().hex,
            }
            raw = _safe_ai_post(url, payload, headers, 120)
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
            + '\n\n对每条输出：{"id":"原id","impact_level":"高|中|低",'
              '"impact":"对普通公民的传导路径一句话(≤60字，指明经由物价/就业/资产/政策哪条线)"}'
            + "\n严格只输出 JSON 数组，不要 markdown。")


def load_jsonl(path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description="AI 公民影响研判")
    parser.add_argument("--dir", default=".", help="intel_*.jsonl 所在目录（CI 默认 cwd，本地传产物目录）")
    parser.add_argument("--max", type=int, default=MAX_PER_RUN)
    args = parser.parse_args()

    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    import yaml
    config = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    files = sorted(glob.glob(os.path.join(args.dir, "intel_2*.jsonl")), reverse=True)[:1]
    if not files:
        print("[IMPACT] no intel files found")
        sys.exit(0)
    target = Path(files[0])
    items = load_jsonl(target)

    todo = [i for i in items if not i.get("impact_level")]
    todo.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    todo = todo[:args.max]
    if not todo:
        print("[IMPACT] all items already analyzed")
        sys.exit(0)
    print(f"[IMPACT] to analyze: {len(todo)}/{len(items)} in {target.name}")

    analyzed = 0
    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i + BATCH_SIZE]
        try:
            results = parse_json_array(call_ai(config, build_prompt(batch)))
        except Exception as e:
            print(f"[IMPACT] batch {i} failed: {e}")
            continue
        by_id = {r.get("id"): r for r in results if isinstance(r, dict) and r.get("id")}
        for it in batch:
            r = by_id.get(it.get("id"))
            if r and r.get("impact"):
                level = r.get("impact_level", "低")
                it["impact_level"] = level if level in ("高", "中", "低") else "低"
                it["impact"] = "【公民影响·" + it["impact_level"] + "】" + str(r["impact"])
                analyzed += 1

    target.write_text(
        "\n".join(json.dumps(it, ensure_ascii=False) for it in items) + "\n",
        encoding="utf-8")
    print(f"[IMPACT] analyzed {analyzed}/{len(todo)} items -> {target.name}")


if __name__ == "__main__":
    main()
