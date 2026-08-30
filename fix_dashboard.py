# -*- coding: utf-8 -*-
from pathlib import Path

path = r"D:\osint\data\interactive_dashboard.html"
html = Path(path).read_text(encoding="utf-8")

# Find the byId line and add esc right after it
old = "var byId={};H.forEach(function(h){byId[h.id]=h});\nvar orderedMajors"
new = 'var byId={};H.forEach(function(h){byId[h.id]=h});\nfunction esc(t){return String(t).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}\nvar orderedMajors'

html = html.replace(old, new, 1)

Path(path).write_text(html, encoding="utf-8")

# Verify
html2 = Path(path).read_text(encoding="utf-8")
idx = html2.index("var byId")
print(html2[idx:idx+300])
