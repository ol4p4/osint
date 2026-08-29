#!/usr/bin/env python3
"""NVIDIA API批量翻译 - CI调用"""
import json, os, glob, sys, uuid
import urllib.request

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
            req = urllib.request.Request(base_url + "/chat/completions", data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
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

def main():
    api_key = os.environ.get("NVIDIA_API_KEY", "")
    if not api_key:
        print("No NVIDIA_API_KEY, skipping translation")
        sys.exit(0)
    
    model = "nvidia/llama-3.1-nemotron-70b-instruct"
    
    files = sorted(glob.glob("intel_*.jsonl"), reverse=True)[:1]
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
    
    print(f"Translating {len(items)} items...")
    translated = translate_batch(items, api_key, model)
    
    with open(files[0], "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    
    print(f"Translated {translated}/{len(items)} items")

if __name__ == "__main__":
    main()
