# -*- coding: utf-8 -*-
r"""secrets_loader.py - API 密钥统一加载（2026-08-30 密钥泄露整改）
背景：仓库是 PUBLIC，config.yaml 里的 OpenCode key 已随 git 历史公开泄露。
整改：config.yaml 不再存真实 key；读取优先级：
  1. 环境变量 OPENCODE_API_KEY（CI 用 GitHub Secrets 注入）
  2. config.local.yaml（本地文件，已 gitignore）
  3. config.yaml 的 api_key 字段（兼容旧配置，应为空）
"""
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent


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
