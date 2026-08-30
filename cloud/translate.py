#!/usr/bin/env python3
"""NVIDIA API批量翻译 - CI调用"""
import json, os, glob, sys, uuid
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

def translate_batch(items, api_key, model):
    base_url = "https://integrate.api.nvidia.com/v1"
    translated = 0
    
    for i in range(0, len(items), 5):
        batch = items[i:i+5]
        texts = []
        for item in batch:
            title = item.get("title", "")
            content = item.get("content_preview", "")[:500]
            texts.append(f"ITEM: {title}\nCONTENT: {content}")
        
        prompt = (
            "Translate each ITEM to Chinese. Output JSON array with format: "
            '[{"cn_title": "中文标题", "cn_summary": "4-6句中文摘要", '
            '"impact": "对中国宏观经济、就业市场和青年失业毕业生的影响分析"}]. '
            "Only output JSON, no markdown."
        )
        
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt + "\n\n" + "\n".join(texts)}],
            "temperature": 0.3,
            "max_tokens": 4096
        }).encode()
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        
        try:
            raw = _safe_nvidia_post(base_url + "/chat/completions", payload, headers)
            result = json.loads(raw)
            content = result["choices"][0]["message"]["content"]
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            content = content.strip().rstrip("`")
            translations = json.loads(content)
            for j, trans in enumerate(translations):
                if i+j < len(items):
                    items[i+j].update(trans)
                    items[i+j]["language"] = "cn"
                    translated += 1
        except Exception as e:
            print(f"  Batch {i} failed: {e}")
    
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

    model = "meta/llama-3.2-11b-vision-instruct"

    # 只处理 intel_YYYYMMDD.jsonl（intel_raw_*/intel_final_* 不在翻译范围）
    files = sorted(glob.glob(os.path.join(dir_path, "intel_2*.jsonl")), reverse=True)[:1]
    if not files:
        print("No intel files found")
        sys.exit(0)
    
    items = []
    with open(files[0], "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except:
                    pass
    
    # Incremental: skip already-translated items (have cn_title)
    untranslated = [i for i in items if not i.get("cn_title")]
    translated_count = len(items) - len(untranslated)
    print(f"Total: {len(items)}, already translated: {translated_count}, to translate: {len(untranslated)}")
    # Take only 50 newest untranslated
    untranslated.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    untranslated = untranslated[:50]
    print(f"Translating {len(untranslated)} new items...")
    translated = translate_batch(untranslated, api_key, model)
    
    # Merge translated back into full items
    id_map = {i["id"]: i for i in untranslated if i.get("cn_title")}
    for i, item in enumerate(items):
        if item["id"] in id_map:
            items[i] = id_map[item["id"]]
    Path(files[0]).write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in items) + "\n",
        encoding="utf-8")
    
    print(f"Translated {translated}/{len(items)} items")

if __name__ == "__main__":
    main()
