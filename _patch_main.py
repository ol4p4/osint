import re
path = r'C:\Users\admin\Documents\osint\cloud\main_cloud.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '    print("=== Running dedup + scoring ===")\n    from cloud.clean_dedup_score import pipeline\n    final_items = pipeline(raw_file, final_file, "sources.yaml")'

new = '''    print("=== Running dedup + scoring ===")
    sys.stdout.flush()
    try:
        from cloud.clean_dedup_score import pipeline
        final_items = pipeline(raw_file, final_file, "sources.yaml")
    except Exception as e:
        print(f"[ERROR] Dedup pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        final_items = all_items'''

if old in content:
    content = content.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Patched main_cloud.py")
else:
    print("Pattern not found")
