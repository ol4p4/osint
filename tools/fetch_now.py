"""一次性本地全量 RSS 拉取 + 写入 jsonl + rebuild dashboard
适用于: 本地全量 24h 拉取 (不依赖本地 RSSHub 容器 — 路由源自动退公共镜像,
金十/新浪走官方直连 API), refresh.py 默认配置没走 24h 全量窗口
"""
import sys, json
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(r"D:\osint")))
import yaml
from cloud.fetch_rss import RSSFetcher

BASE = Path(r"D:\osint\data")

# 1. 加载 sources (scope:ci 的外国源仅由 GitHub Actions 采集, 本地跳过 — 境内直连不通, 省超时)
config = yaml.safe_load((Path(r"D:\osint") / "sources.yaml").read_text(encoding="utf-8"))
sources = []
skipped_ci = 0
for key in ["rss_sources", "east_asia_sources"]:
    for s in config.get(key, []):
        if s.get("scope") == "ci":
            skipped_ci += 1
        else:
            sources.append(s)
print(f"[fetch_now] {len(sources)} sources (跳过 {skipped_ci} 个 scope:ci 外国源, 由 CI 采集)")

# 2. 拉取最近 24h
fetcher = RSSFetcher(sources, config.get("keyword_weights", {}))
new_items = fetcher.fetch_all(max_age_hours=24)
print(f"[fetch_now] {len(new_items)} new items in last 24h")

# 3. 写入今天 jsonl
today = datetime.now(timezone.utc).strftime("%Y%m%d")
jsonl_path = BASE / f"intel_{today}.jsonl"

# 读已有 entries 做去重
existing_ids = set()
if jsonl_path.exists():
    for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
            if d.get("id"):
                existing_ids.add(d["id"])
        except Exception:
            pass

# 追加新条目
appended = 0
with jsonl_path.open("a", encoding="utf-8") as f:
    for it in new_items:
        if it["id"] in existing_ids:
            continue
        f.write(json.dumps(it, ensure_ascii=False) + "\n")
        appended += 1
print(f"[fetch_now] {appended} appended to {jsonl_path.name}")
print(f"[fetch_now] Total now: {len(existing_ids) + appended}")
