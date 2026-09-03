# -*- coding: utf-8 -*-
r"""serve.py - 仪表盘服务（静态页面 + 参谋长问答 API）
端口 19090（9090 会落进 Windows 动态保留段导致 WinError 10013，见 AGENTS.md）
由 Startup\osint_dashboard.bat 开机自启，也可手动运行。

POST /api/ask  {"q": "你的问题或假设"}
  → 调参谋长模型（persona.md 人设 + 四维框架）回答，存 dialogues/qa_*.jsonl
"""
import http.server
import json
import os
import socket
import socketserver
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

PORT = 19090
ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT = Path(r"D:\osint").resolve()
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "local"))
AI_ALLOWED_HOST = "opencode.ai"


def _safe_ai_post(url, payload, headers, timeout=150):
    """SSRF 防护：仅 https + opencode.ai 白名单 + 解析结果不得指向私有/环回/保留地址"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") != AI_ALLOWED_HOST:
        raise ValueError("blocked non-whitelisted AI endpoint: " + url)
    import ipaddress
    for info in socket.getaddrinfo(parsed.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
            raise ValueError("endpoint resolves to forbidden address: " + str(ip))
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def _ask_staff(question: str) -> str:
    """把问题交给参谋长（persona 人设 + 四维框架），问答落盘 dialogues/qa_*.jsonl"""
    try:
        import yaml
        from secrets_loader import get_opencode_key
    except Exception as e:
        raise RuntimeError("无法加载参谋模块: " + str(e))

    config = yaml.safe_load((PROJECT / "config.yaml").read_text(encoding="utf-8"))
    api = config.get("api", {})
    persona = ""
    persona_file = PROJECT / "persona.md"
    if persona_file.exists():
        persona = persona_file.read_text(encoding="utf-8")[:2000]

    system = (
        "你是'参谋系统'的参谋长，服务对象是一名中国年轻失业毕业生。"
        "分析框架：积累制度/空间修正/国家-市场边界/阶级利益四维推演。"
        "决策偏好：不做福利清单做结构性机会映射；给18个月窗口期的行动向量；"
        "必须给风险提示与避坑指南；输出具体可执行内容而非模糊建议。\n\n"
        "用户画像：\n" + persona
    )
    prompt = ("用户的问题或假设：\n" + question[:1000]
              + "\n\n请给出结构化研判（四维诊断+行动向量+避坑），中文回答。")

    url = (api.get("base_url") or "").rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": api.get("model"),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_tokens": 2000,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + get_opencode_key(),
        "User-Agent": "opencode/latest/1.3.15/cli",
        "x-opencode-client": "cli",
        "x-opencode-session": os.urandom(16).hex(),
    }
    raw = _safe_ai_post(url, payload, headers, 150)
    answer = json.loads(raw)["choices"][0]["message"].get("content", "").strip()

    qa_dir = Path(ROOT) / "dialogues"
    qa_dir.mkdir(parents=True, exist_ok=True)
    qa_file = qa_dir / ("qa_" + datetime.now(timezone.utc).strftime("%Y%m%d") + ".jsonl")
    entry = json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                        "q": question, "a": answer}, ensure_ascii=False)
    with qa_file.open("a", encoding="utf-8") as f:
        f.write(entry + "\n")
    return answer


class Handler(http.server.SimpleHTTPRequestHandler):
    # 仪表盘 "抓取最新" 按钮调 POST /api/refresh, 后台跑 ~90s
    # 默认 BaseHTTPRequestHandler.timeout=30s 会 504, 调大到 300s
    timeout = 300
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def do_GET(self):
        # 根路径或任何不存在的路径一律跳转仪表盘（容错手输错误 URL）
        if self.path in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/interactive_dashboard.html")
            self.end_headers()
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/api/refresh":
            return self._handle_refresh()
        if self.path != "/api/ask":
            self._json_response(404, {"error": "unknown api"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            q = str(body.get("q", "")).strip()
            if not q:
                self._json_response(400, {"error": "empty question"})
                return
            answer = _ask_staff(q)
            self._json_response(200, {"answer": answer})
        except Exception as e:
            self._json_response(500, {"error": str(e)[:200]})

    def _handle_refresh(self):
        """手动 rebuild dashboard (~5-10s).
        仪表盘 "抓取最新" 按钮调 POST /api/refresh 落此.
        设计取舍: 不在按钮里调 fetch_now, 因为它跑 2-3 分钟太慢
        (用户应配置 OsintRefresh 15min 计划任务或单独后台跑).
        这里只 rebuild_data + gen_html, 让最新 jsonl 立即呈现.
        """
        import subprocess, sys, threading
        from pathlib import Path
        project = Path(r"D:\osint")
        status_file = project / "data" / ".refresh_status.json"

        def run_in_thread():
            try:
                status_file.write_text('{"status":"running","message":"rebuild_dashboard..."}', encoding="utf-8")
                # 1. rebuild_data
                import importlib.util
                refresh_module_path = project / "data" / "refresh.py"
                spec = importlib.util.spec_from_file_location("refresh_mod", str(refresh_module_path))
                refresh_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(refresh_mod)
                n = refresh_mod.rebuild_data()
                # 2. gen_html (内部调 gen_dashboard.py + fix_dashboard.py, ~5s)
                refresh_mod.gen_html()
                status_file.write_text(
                    json.dumps({"status":"done","intel_count":n,
                                "message":f"已重建 ({n} 条), 页面请手动刷新"}),
                    encoding="utf-8"
                )
            except Exception as e:
                import traceback
                status_file.write_text(
                    json.dumps({"status":"error","error":str(e)[:200],
                                "trace":traceback.format_exc()[:300]}),
                    encoding="utf-8"
                )

        t = threading.Thread(target=run_in_thread, daemon=True)
        t.start()
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        body = json.dumps({"status":"accepted","message":"后台 rebuild 中, 轮询 /api/refresh/status"})
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

        # 启动后台线程 (daemon=True 让 serve.py 退出时自动结束)
        t = threading.Thread(target=run_in_thread, daemon=True)
        t.start()
        # 立即返回 202, 不阻塞 HTTP handler
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        body = json.dumps({"status":"accepted","message":"后台抓取中, 轮询 /api/refresh/status"})
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        # refresh status endpoint
        if self.path == "/api/refresh/status":
            status_file = Path(r"D:\osint\data\.refresh_status.json")
            if status_file.exists():
                try:
                    self._json_response(200, json.loads(status_file.read_text(encoding="utf-8")))
                    return
                except Exception:
                    pass
            self._json_response(200, {"status": "idle", "message": "未启动抓取"})
            return
        # 根路径或任何不存在的路径一律跳转仪表盘（容错手输错误 URL）
        if self.path in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/interactive_dashboard.html")
            self.end_headers()
            return
        super().do_GET()

    def _json_response(self, code, obj):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass  # 静默访问日志


try:
    httpd = socketserver.ThreadingTCPServer(("", PORT), Handler)
except OSError as e:
    print(f"[ERROR] 端口 {PORT} 绑定失败: {e}")
    print("若为 WinError 10013，端口可能被系统保留，请换端口（netsh interface ipv4 show excludedportrange protocol=tcp）")
    sys.exit(1)

print(f"Dashboard serving on http://127.0.0.1:{PORT}/interactive_dashboard.html (ask API ready)")
httpd.serve_forever()
