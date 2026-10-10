# -*- coding: utf-8 -*-
import os
from pathlib import Path

# 路径可配置（2026-10-10）：云端 CI 产物落在 dist/，本地默认 D:\osint\data。
# 与 gen_dashboard 共用 OSINT_HTML_OUT（同一环境变量，workflow 里一处设置两处生效）。
# 环境变量属外部输入，先校验（禁 `..` 片段）再用。
_out = (os.environ.get("OSINT_HTML_OUT") or "").strip()
if _out and ".." not in _out.replace("\\", "/").split("/"):
    path = Path(_out)
else:
    path = Path(r"D:\osint\data\interactive_dashboard.html")
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
