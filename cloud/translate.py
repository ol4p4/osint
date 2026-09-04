#!/usr/bin/env python3
"""NVIDIA API批量翻译 - CI调用"""
import json, os, glob, sys, uuid, time
import urllib.request
from pathlib import Path

NVIDIA_HOST = "integrate.api.nvidia.com"

def _safe_nvidia_post(url, payload, headers, timeout=60):
    """翻译端点固定为 NVIDIA：https + 域名白名单 + 解析结果不得指向私有/环回/保留地址"""
    import socket, ipaddress
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") != NVIDIA_HOST:
        raise ValueError("blocked non-whitelisted translate endpoint: " + url)
    for info in socket.getaddrinfo(parsed.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
            raise ValueError("endpoint resolves to forbidden address: " + str(ip))
    req = urllib.request.Request(url, data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()

# 翻译模型降级链（2026-08-30 bench 实测可用的三个，按速度/质量排序）
MODEL_CHAIN = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "meta/llama-3.2-11b-vision-instruct",
]

def translate_batch(items, api_key, deadline=None):
    """items: [(file_path, item_dict), ...]（跨文件元组）；deadline: Unix 时间戳，批间检查"""
    base_url = "https://integrate.api.nvidia.com/v1"
    translated = 0

    for i in range(0, len(items), 5):
        if deadline and time.time() > deadline:
            print(f"[TRANSLATE] time budget exhausted, {len(items)-i} items left for next CI run")
            break
        batch = items[i:i+5]
        texts = []
        for _, item in batch:
            title = item.get("title", "")
            content = item.get("content_preview", "")[:500]
            texts.append(f"ITEM_ID: {item.get('id', '')}\nTITLE: {title}\nCONTENT: {content}")

        prompt = (
            "Translate each ITEM to Chinese. Output JSON array with format: "
            '[{"id": "原ITEM_ID原样返回", "cn_title": "中文标题", "cn_summary": "4-6句中文摘要", '
            '"impact": "对中国宏观经济、就业市场和青年失业毕业生的影响分析"}]. '
            "Each output object MUST carry the id of the ITEM it translates. "
            "Only output JSON, no markdown."
        )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        # NVIDIA 免费接口高峰期响应慢：超时 180s，整批失败重试一次，
        # 重试仍失败则沿 MODEL_CHAIN 降级到下一个模型（翻译是增量的，不污染已翻条目）
        batch_done = False
        for model in MODEL_CHAIN:
            if batch_done:
                break
            payload = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": prompt + "\n\n" + "\n".join(texts)}],
                "temperature": 0.3,
                "max_tokens": 4096
            }).encode()
            for attempt in range(2):
                try:
                    raw = _safe_nvidia_post(base_url + "/chat/completions", payload, headers, timeout=180)
                    result = json.loads(raw)
                    content = result["choices"][0]["message"]["content"]
                    content = content.strip()
                    if content.startswith("```"):
                        content = content.split("```")[1]
                        if content.startswith("json"):
                            content = content[4:]
                    content = content.strip().rstrip("`")
                    translations = json.loads(content)
                    # 2026-09-04 修复: 原按位置 batch[j] 匹配, 模型返回乱序时译文张冠李戴。
                    # 改按 id 匹配; 模型未返回 id 时退回按位置(兼容), 并打日志提示
                    trans_have_id = any(isinstance(t, dict) and t.get("id") for t in translations if isinstance(t, dict))
                    if trans_have_id:
                        by_id = {t.get("id"): t for t in translations if isinstance(t, dict) and t.get("id")}
                        for _, item in batch:
                            trans = by_id.get(item.get("id", ""))
                            if not trans:
                                continue
                            item.update(trans)
                            item["language"] = "cn"
                            translated += 1
                    else:
                        print(f"  Batch {i}: model returned no ids, falling back to positional match")
                        for j, trans in enumerate(translations):
                            if j < len(batch) and isinstance(trans, dict):
                                _, item = batch[j]
                                item.update(trans)
                                item["language"] = "cn"
                                translated += 1
                    batch_done = True
                    break
                except Exception as e:
                    if attempt == 0:
                        print(f"  Batch {i} [{model}] attempt 1 failed ({e}), retrying...")
                    else:
                        print(f"  Batch {i} [{model}] failed: {e}, trying next model")

    return translated

def get_api_key():
    """CI 从环境变量取 key；本地跑可回退到 config.yaml 的 api.nvidia_api_key（可选字段）"""
    key = os.environ.get("NVIDIA_API_KEY", "")
    if key:
        return key
    try:
        import yaml
        cfg_file = Path(__file__).resolve().parent.parent / "config.yaml"
        with open(cfg_file, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return (cfg.get("api", {}) or {}).get("nvidia_api_key", "") or ""
    except Exception:
        return ""

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=".", help="intel_*.jsonl 所在目录（CI 用默认当前目录，本地传产物目录）")
    args = parser.parse_args()
    run(args.dir)

def run(dir_path="."):
    api_key = get_api_key()
    if not api_key:
        print("No NVIDIA_API_KEY, skipping translation")
        sys.exit(0)

    # 模型选择见 MODEL_CHAIN（主 gpt-oss-120b，失败降级 20b / llama）
    # 2026-08-30: 扫描全部 intel_2*.jsonl（此前只扫最新文件，历史条目永无翻译）
    files = sorted(glob.glob(os.path.join(dir_path, "intel_2*.jsonl")), reverse=True)
    if not files:
        print("No intel files found")
        sys.exit(0)
    file_items = {}
    translated_ids = set()
    for f in files:
        arr = []
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    arr.append(json.loads(line))
                except Exception:
                    pass
        file_items[f] = arr
        for it in arr:
            if it.get("cn_title"):
                translated_ids.add(it.get("id"))

    # 跨文件按 id 去重，收集未翻译条目，最新优先取 50 条（时间预算内能翻多少翻多少）
    todo = []
    seen = set()
    for f in files:
        for it in file_items[f]:
            iid = it.get("id")
            if iid in seen or iid in translated_ids:
                continue
            seen.add(iid)
            todo.append((f, it))
    todo.sort(key=lambda p: p[1].get("published_at", ""), reverse=True)
    todo = todo[:50]
    total = sum(len(v) for v in file_items.values())
    print(f"Total: {total}, unique untranslated: {len(todo)}, to translate now: {len(todo)}")
    if not todo:
        print("All items already translated")
        sys.exit(0)
    translated = translate_batch(todo, api_key, deadline=time.time() + 600)

    # 翻译结果按文件写回（条目分布在多个文件）
    translated_by_id = {it.get("id"): it for _, it in todo if it.get("cn_title")}
    for f, arr in file_items.items():
        changed = False
        for i, it in enumerate(arr):
            rep = translated_by_id.get(it.get("id"))
            if rep is not None and rep is not it:
                arr[i] = rep
                changed = True
        if changed:
            Path(f).write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in arr) + "\n",
                encoding="utf-8")
    print(f"Translated {translated}/{len(todo)} items across {len(file_items)} files")

if __name__ == "__main__":
    main()
