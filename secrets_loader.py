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
