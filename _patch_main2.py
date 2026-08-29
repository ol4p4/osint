path = r'C:\Users\admin\Documents\osint\cloud\main_cloud.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old_chunk = '''    print("=== Running dedup + scoring ===")
    from cloud.clean_dedup_score import pipeline
    final_items = pipeline(raw_file, final_file, "sources.yaml")'''

new_chunk = '''    print("=== Running dedup + scoring ===")
    import sys as _sys
    _sys.stdout.flush()
    try:
        from cloud.clean_dedup_score import pipeline
        final_items = pipeline(raw_file, final_file, "sources.yaml")
    except Exception as _e:
        print(f"[ERROR] Dedup pipeline failed: {_e}")
        import traceback as _tb
        _tb.print_exc()
        _sys.stdout.flush()
        final_items = all_items'''

if old_chunk in content:
    content = content.replace(old_chunk, new_chunk)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Patched main_cloud.py")
else:
    print("Pattern not found - printing relevant section")
    idx = content.find("dedup")
    print(content[max(0,idx-200):idx+200])
