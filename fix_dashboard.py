path = r"D:\Codex输出\osint_卫星图\interactive_dashboard.html"
with open(path, "r", encoding="utf-8") as f:
    html = f.read()

# Find the byId line and add esc right after it
old = "var byId={};H.forEach(function(h){byId[h.id]=h});\nvar orderedMajors"
new = 'var byId={};H.forEach(function(h){byId[h.id]=h});\nfunction esc(t){return String(t).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}\nvar orderedMajors'

html = html.replace(old, new, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(html)

# Verify
with open(path, "r", encoding="utf-8") as f:
    html2 = f.read()
idx = html2.index("var byId")
print(html2[idx:idx+300])
