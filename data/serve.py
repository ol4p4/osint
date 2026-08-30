# -*- coding: utf-8 -*-
"""仪表盘 HTTP 服务器
端口 19090（9090 会落进 Windows 动态保留段导致 WinError 10013，见 AGENTS.md）
由 Startup\osint_dashboard.bat 开机自启，也可手动运行
"""
import http.server, socketserver, os, sys

PORT = 19090
os.chdir(os.path.dirname(os.path.abspath(__file__)))


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # 根路径或任何不存在的路径一律跳转仪表盘（容错手输错误 URL）
        if self.path in ("/", "/index.html") or not os.path.exists(self.translate_path(self.path)):
            self.send_response(302)
            self.send_header("Location", "/interactive_dashboard.html")
            self.end_headers()
            return
        super().do_GET()

    def translate_path(self, path):
        return super().translate_path(path)

    def log_message(self, fmt, *args):
        pass  # 静默访问日志


try:
    httpd = socketserver.TCPServer(("", PORT), Handler)
except OSError as e:
    print(f"[ERROR] 端口 {PORT} 绑定失败: {e}")
    print("若为 WinError 10013，端口可能被系统保留，请换端口（netsh interface ipv4 show excludedportrange protocol=tcp）")
    sys.exit(1)

print(f"Dashboard serving on http://127.0.0.1:{PORT}/interactive_dashboard.html")
httpd.serve_forever()
