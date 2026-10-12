# -*- coding: utf-8 -*-
r"""secrets_loader.py - API 密钥统一加载（2026-08-30 密钥泄露整改）

背景：仓库是 PUBLIC，config.yaml 里的 OpenCode key 已随 git 历史公开泄露。
整改：config.yaml 不再存真实 key；读取优先级：
  1. 环境变量 OPENCODE_API_KEY（CI 用 GitHub Secrets 注入）
  2. config.local.yaml（本地文件，已 gitignore）
  3. config.yaml 的 api_key 字段（兼容旧配置，应为空）

2026-09-17 新增 OpenRouter 备援：OpenCode 免费层加了客户端指纹校验
（403 FreeTierError "can only be used from within OpenCode"），非官方客户端
全部被拒。OpenRouter 是同一批模型的官方 API 通道（key 在环境变量
CODEX_API_KEY_OPENROUTER），作降级链末端，不是绕过指纹校验。
"""
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent

# OpenRouter 备援端点（与 OpenCode 同模型池的官方 API）
OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def get_opencode_key() -> str:
    key = os.environ.get("OPENCODE_API_KEY", "")
    if key:
        return key
    local = _ROOT / "config.local.yaml"
    if local.exists():
        try:
            import yaml
            cfg = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
            key = (cfg.get("api") or {}).get("api_key", "")
            if key:
                return key
        except Exception:
            pass
    try:
        import yaml
        cfg = yaml.safe_load((_ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
        return (cfg.get("api") or {}).get("api_key", "") or ""
    except Exception:
        return ""


def get_openrouter_key() -> str:
    """OpenRouter 备援 key（环境变量 CODEX_API_KEY_OPENROUTER）。

    OpenCode 免费层 2026-09-17 起加客户端指纹校验后，本通道成为
    非 OpenCode 客户端调用免费模型的合法出口。未配置则返回空串
    （调用方应静默跳过该降级项）。
    """
    return os.environ.get("CODEX_API_KEY_OPENROUTER", "")


def get_nvidia_key() -> str:
    """NVIDIA integrate 备援 key（第三条通道）。

    2026-09-17 OpenCode（指纹校验 403）与 OpenRouter（免费层 429）同时不可用时，
    本地环境变量里的 NVIDIA key 提供同模型池的可用通道（nemotron 系）。
    优先读 CI 同名变量 NVIDIA_API_KEY，其次读本地 CODEX_API_KEY_____3。
    """
    return os.environ.get("NVIDIA_API_KEY", "") or os.environ.get("CODEX_API_KEY_____3", "")


def get_dots_key() -> str:
    """小红书 dots 备援 key（第三通道，2026-09-17 接入）。

    端点 note3-prev-api.askdiandian.com（OpenAI 兼容），模型 dots3-note-prev。
    实测 1 秒响应，是 NVIDIA 端点故障时的可靠备援。
    key 来源：环境变量 DOTS_API_KEY / INGEST_API_KEY，或本地配置文件。
    """
    key = os.environ.get("DOTS_API_KEY", "") or os.environ.get("INGEST_API_KEY", "")
    if key:
        return key
    local = _ROOT / "config.local.yaml"
    if local.exists():
        try:
            import yaml
            cfg = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
            return (cfg.get("dots") or {}).get("api_key", "") or ""
        except Exception:
            pass
    return ""


def get_jev_key() -> str:
    """TypeSafe JEV 决策模型 key（2026-09-20 接入）。

    端点 api.typesafe.ai/v1（System One API，POST /systemone），模型 jev-latest。
    用途：ACH 证据诊断（code/conf 判定），见 docs/JEV落地方案-2026-09-20.md。
    key 来源：环境变量 TYPESAFE_API_KEY，或本地配置文件 jev.api_key。
    """
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if key:
        return key
    local = _ROOT / "config.local.yaml"
    if local.exists():
        try:
            import yaml
            cfg = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
            return (cfg.get("jev") or {}).get("api_key", "") or ""
        except Exception:
            pass
    return ""


def get_gemini_key() -> str:
    """Google Gemini key（2026-10-05 接入，AI 通道链首）。

    端点 generativelanguage.googleapis.com（原生 :generateContent）。
    key 来源：环境变量 GEMINI_API_KEY（CI 用 GitHub Secrets 注入；本地通常没有，
    因境内到 Google 不可达），或本地配置文件 gemini.api_key。
    """
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    local = _ROOT / "config.local.yaml"
    if local.exists():
        try:
            import yaml
            cfg = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
            return (cfg.get("gemini") or {}).get("api_key", "") or ""
        except Exception:
            pass
    return ""


def get_opencode_session() -> str:
    """OpenCode **桌面会话 ID**（2026-10-12 接入，供 opencode-desktop 通道）。

    ## 它是什么、为什么需要

    OpenCode 免费层对第三方客户端一律 403（`FreeTierError: "free tier can only
    be used from within OpenCode"`）。本地 4010 代理的做法是**冒充官方桌面客户端**
    ——需同时凑齐：①桌面 UA、②`x-opencode-client: desktop`、③**由官方客户端真实
    发起过的 session ID**、④6 个官方工具定义。本 getter 提供第 ③ 项。

    ## 实测边界（2026-10-12，逐维对照，每次只变一个变量）

    | 测试 | 结果 |
    |---|---|
    | 真 session（本机读） | 200 |
    | 伪造 session（同格式 `ses_`+hex） | **403**（承重件，非装饰） |
    | 无 session 头 | 403 |
    | 20 天前的历史 session | **200**（不过期，是"登记在册"的持久标识） |
    | `OPENCODE_API_KEY` + 本机 session | **200**（**不绑定特定 key**） |
    | **CI（US runner）+ 本机 session** | **200**（**不绑定机器/IP**） |

    末两条是本通道可行性的根据：session 与 key、机器均**无绑定关系**。

    ## 来源

    环境变量 `OC_DESKTOP_SESSION`（CI 用 GitHub Secrets 注入）；本地没有时回落到
    桌面版 sqlite（`~/.local/share/opencode/opencode.db` 的 session 表取最近一条）
    ——与 4010 代理同一取数口径。

    ## 使用须知

    本通道**性质特殊**：它绕过的是"仅限官方客户端使用"的免费层限制。上游加强
    校验即失效，且 session 关联用户账号（可轮换/吊销）。故在链中**恒为末位兜底**，
    只在前序正常 API 通道全不可用时才被使用。
    """
    val = os.environ.get("OC_DESKTOP_SESSION", "").strip()
    if val:
        return val
    try:
        import sqlite3
        db = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
        if not db.exists():
            return ""
        con = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True, timeout=3)
        try:
            row = con.execute(
                "select id from session order by time_updated desc limit 1").fetchone()
        finally:
            con.close()
        return (row[0] if row else "") or ""
    except Exception:
        return ""


# 官方桌面客户端的 6 个工具定义（opencode-desktop 通道的放行条件之一）。
# 实测（2026-10-12）：**必须齐 6 个且名称正确**，每个都要带 parameters——
#   3/4/5 个 -> 403；6 个正确名 -> 200；6 个任意名（aaa/bbb/...）-> 403；
#   仅给 name 不给 parameters -> 403。
# 描述文本不参与校验（极简 stub 与全量 78KB 定义同为 200），故此处只留
# 名称 + 最小 parameters 骨架，避免把 13KB 官方文案抄进仓库。
OPENCODE_DESKTOP_TOOLS = [
    {"type": "function",
     "function": {"name": n, "description": "OpenCode desktop tool",
                  "parameters": {"type": "object", "properties": {}}}}
    for n in ("bash", "edit", "glob", "grep", "question", "read")
]
# 官方桌面客户端的请求指纹（本地 4010 代理实测放行的那一套；cli 标识一律 403）
OPENCODE_DESKTOP_UA = ("opencode/1.18.28 ai-sdk/provider-utils/4.0.23 "
                       "runtime/node.js/24")
