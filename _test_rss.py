import sys
sys.path.insert(0, r"C:\Users\admin\Documents\osint")
from cloud.fetch_rss import fetch_all_rss
items = fetch_all_rss(r"C:\Users\admin\Documents\osint\sources.yaml")
print(f"Fetched {len(items)} items")
if items:
    print(f"Sample: {items[0].get('title','')[:60]}")
