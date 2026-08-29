path = r'C:\Users\admin\Documents\osint\cloud\clean_dedup_score.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Remove unused simhash import
content = content.replace('import simhash\n', '')

# Add flush after each print
import re
content = re.sub(r'(print\(.*?\))', r'\1; import sys; sys.stdout.flush()', content)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Patched clean_dedup_score.py")
