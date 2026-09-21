# -*- coding: utf-8 -*-
r"""jev_client.py - JEV (TypeSafe System One) 决策模型客户端（2026-09-21）

用途：ACH 证据诊断的决策层。相比通用大模型，JEV 非自回归、一次前向输出
schema 内全部概率，实测 0.66s/条（mimo 为 ~45s/条），且概率有真实梯度。

**提问模板是性能关键**（Phase 0 实测教训，勿随意改）：
  同一证据仅改措辞，结论就从 inconsistent(0.77) 翻成 neutral(0.31)；
  15 条样本上，带证伪判据版一致率 20% vs 简单版 40%——直接减半。
  原因符合官方 jaggedness 第 5 条「无关细节拉低精度」：证伪判据里的
  具体数字指标对「方向判断」是噪声。**不要把 falsification_criteria 塞进 instructions**。

**conf 取值**（实测验证）：
  官方 confidence 字段 = (P_max − 1/K)/(1 − 1/K)，是分布集中度归一化，
  **不是正确性估计**。喂 derive_lr() 必须取 probabilities[choice] 原始概率。

用法：
    from jev_client import JevClient
    c = JevClient()
    codes = c.diagnose_evidence("证据文本", majors)   # {hyp_id: {"code","conf","probs"}}
"""
import ipaddress
import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

JEV_HOST = "api.typesafe.ai"
JEV_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"

# 官方限流动态调整（250k tok/s、1200 req/min），偶发失败重试即可
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2.0

# JEV choice → ACH 判定码
CODE_MAP = {"consistent": "C", "inconsistent": "I", "neutral": "N"}


class JevError(Exception):
    pass


def _safe_jev_post(url, payload, key, timeout=120):
    """SSRF 守卫（与 analyze._safe_ai_post 同规格）：仅 https + 白名单 + 非私有地址"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") != JEV_HOST:
        raise ValueError("blocked non-whitelisted JEV endpoint: " + url)
    for info in socket.getaddrinfo(parsed.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
            raise ValueError("endpoint resolves to forbidden address: " + str(ip))
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class JevClient:
    def __init__(self, key=None, model=DEFAULT_MODEL, caller="ach_matrix",
                 retries=RETRY_ATTEMPTS, record_usage=True):
        if key is None:
            import sys
            root = Path(__file__).resolve().parent.parent
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from secrets_loader import get_jev_key
            key = get_jev_key()
        self.key = key or ""
        self.model = model
        self.caller = caller
        self.retries = retries
        self.record_usage = record_usage

    @property
    def available(self):
        return bool(self.key)

    # ---------- 核心调用 ----------
    def systemone(self, state, questions, timeout=120):
        """POST /v1/systemone → 原始响应 dict。失败抛 JevError。"""
        if not self.available:
            raise JevError("JEV key 未配置（环境变量 TYPESAFE_API_KEY 或 config.local.yaml 的 jev.api_key）")
        payload = {"model": self.model, "state": state, "questions": questions}
        last = None
        for attempt in range(1, self.retries + 1):
            try:
                d = _safe_jev_post(JEV_URL, payload, self.key, timeout=timeout)
                self._account(d.get("usage") or {})
                return d
            except urllib.error.HTTPError as ex:
                body = ""
                try:
                    body = ex.read().decode("utf-8", "replace")[:200]
                except Exception:
                    pass
                # 4xx（除 429）不重试——参数/鉴权问题重试无用
                if ex.code != 429 and 400 <= ex.code < 500:
                    raise JevError(f"HTTP {ex.code}: {body}")
                last = f"HTTP {ex.code}: {body}"
            except Exception as ex:
                last = f"{type(ex).__name__}: {str(ex)[:150]}"
            if attempt < self.retries:
                time.sleep(RETRY_BACKOFF * attempt)
        raise JevError(f"JEV 调用失败（{self.retries} 次重试）: {last}")

    def _account(self, usage):
        """用量记账（API 无用量端点，必须本地累计）"""
        if not self.record_usage:
            return
        try:
            import sys
            root = Path(__file__).resolve().parent.parent
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from tools.jev_usage import record_usage
            record_usage(usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                         caller=self.caller)
        except Exception:
            pass

    # ---------- ACH 诊断专用 ----------
    @staticmethod
    def build_ach_questions(majors):
        """8 个假设 → 8 个 Choice 问题（一次请求并行求值）。

        ⚠️ 提问措辞是性能关键。**2026-09-21 二次实测的重要教训**：
        首版问法「与假设的预期是否一致」有**系统性误判**——JEV 把它字面理解为
        "这条消息是否支持该假设成立"，于是把「铁路客运创新高」「原油跳水」
        「A股高开」这类**与议题完全无关**的内容判成 inconsistent（"不支持升级"），
        把「日经低开」判成 consistent。结果噪声被大量吸入（台海 C/I 从 20 → 32 条），
        后验被打到地板值 0.050。

        实测三种问法对照（噪声=铁路/原油/A股，真信号=台海实弹演习）：
          问法A「预期是否一致」    → 噪声全判 inconsistent(0.63~0.79)  ❌
          问法B「是否涉及该议题」  → 噪声全判 unrelated(1.00)          ✅
          问法C Noul「是否直接涉及」→ 噪声 0.01~0.04 / 真信号 0.95     ✅ 最佳

        **结论：方向判断必须以"议题相关性"为前提。** 故改用两段式：
          第一段 Noul 门控「是否直接涉及该假设的议题」→ 低于阈值直接判 N
          第二段 仅对通过门控的假设做 Choice 方向判断
        见 gate_and_diagnose()。
        """
        qs = {}
        for h in majors:
            qs["h_" + h["id"]] = {
                "type": "choice",
                "instructions": "这条证据与假设「" + h.get("title", "") + "」的预期是否一致？",
                "criteria": {
                    "consistent": "证据支持该假设的预期",
                    "inconsistent": "证据与该假设的预期相斥",
                    "neutral": "证据与该假设无关",
                },
            }
        return qs

    @staticmethod
    def build_gate_questions(majors):
        """第一段：议题相关性门控（Noul，每假设一问）。

        问「是否直接涉及该假设的议题」而非「预期是否一致」——实测前者能干净地
        把无关内容（铁路/原油/股市）判到 0.01~0.04，后者会误判为"相斥"。
        """
        return {"g_" + h["id"]: {
            "type": "noul",
            "instructions": "这条证据的内容是否直接涉及「" + h.get("title", "") + "」这一议题？",
        } for h in majors}

    @staticmethod
    def build_direction_questions(majors_subset):
        """第二段：仅对通过门控的假设判方向（Choice）"""
        qs = {}
        for h in majors_subset:
            qs["d_" + h["id"]] = {
                "type": "choice",
                "instructions": "这条证据与假设「" + h.get("title", "") + "」的预期是否一致？",
                "criteria": {
                    "consistent": "证据支持该假设的预期",
                    "inconsistent": "证据与该假设的预期相斥",
                    "neutral": "证据与该假设无关",
                },
            }
        return qs

    def gate_and_diagnose(self, evidence_text, majors, gate_threshold=0.08, timeout=120):
        """两段式诊断（推荐入口）：先门控议题相关性，再对相关假设判方向。

        第一段：Noul 问「是否直接涉及该假设的议题」→ 概率 < gate_threshold 直接判 N
        第二段：仅对通过门控的假设问 Choice 方向

        收益（实测）：噪声被干净滤除，避免"无关内容被误判为相斥"导致的
        后验塌缩；且第二段问题数大减（通常 8 → 1~3），成本与延迟同步下降。

        **阈值 0.08 的实测依据**（台海假设，2026-09-21）：
            噪声样本 gate：铁路 0.01 / 原油 0.02 / A股 0.02 / 日经 0.03
            真信号 gate：核潜艇 0.14 / 防务开支翻倍 0.14 / 台海巡艇 0.18 / 实弹演习 0.86
        噪声上界 0.04 与真信号下界 0.14 之间有 3.5 倍间隔，取中点偏下 0.08 稳妥。
        调参入口即本参数；已落盘的 gate 值可用于重算（无需重跑 AI）。

        返回 {hyp_id: {"code","conf","probs","gate"}}；gate 为第一段相关性概率。
        """
        d1 = self.systemone(evidence_text, self.build_gate_questions(majors), timeout=timeout)
        a1 = d1.get("answers") or {}
        gates, passed = {}, []
        for h in majors:
            g = a1.get("g_" + h["id"]) or {}
            p = float(g.get("noul", 0.0))
            gates[h["id"]] = round(p, 3)
            if p >= gate_threshold:
                passed.append(h)

        out = {}
        for h in majors:
            if h["id"] not in [x["id"] for x in passed]:
                out[h["id"]] = {"code": "N", "conf": round(1.0 - gates[h["id"]], 3),
                                "probs": {}, "gate": gates[h["id"]]}

        if passed:
            d2 = self.systemone(evidence_text, self.build_direction_questions(passed), timeout=timeout)
            a2 = d2.get("answers") or {}
            for h in passed:
                a = a2.get("d_" + h["id"]) or {}
                choice = a.get("choice")
                probs = a.get("probabilities") or {}
                out[h["id"]] = {
                    "code": CODE_MAP.get(choice, "N"),
                    "conf": round(float(probs.get(choice, 0.0)), 3),
                    "probs": probs, "gate": gates[h["id"]],
                }
        return out

    def diagnose_evidence(self, evidence_text, majors, timeout=120):
        """单条证据 × 全部 major 假设 → {hyp_id: {"code","conf","probs"}}

        conf 取 probabilities[choice] 原始概率（非 confidence 字段，见文件头）。
        """
        d = self.systemone(evidence_text, self.build_ach_questions(majors), timeout=timeout)
        answers = d.get("answers") or {}
        out = {}
        for h in majors:
            a = answers.get("h_" + h["id"])
            if not a:
                continue
            choice = a.get("choice")
            probs = a.get("probabilities") or {}
            out[h["id"]] = {
                "code": CODE_MAP.get(choice, "N"),
                "conf": round(float(probs.get(choice, 0.0)), 3),
                "probs": probs,
            }
        return out

    def relevance(self, evidence_text, hypothesis_title, timeout=60):
        """单问相关性（Noul）→ 0~1 概率。用于两段式门控的前置筛选。"""
        q = {"q": {"type": "noul",
                   "instructions": "这条证据是否直接涉及假设「" + hypothesis_title + "」的核心议题？"}}
        d = self.systemone(evidence_text, q, timeout=timeout)
        return float((d.get("answers") or {}).get("q", {}).get("noul", 0.0))
