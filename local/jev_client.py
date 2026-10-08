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

**通道（2026-10-08 新增免费通道，默认走它）**：
  1. free —— https://opencode.ai/zen/v1/systemone，模型 jev-1.13-free，
     **keyless 匿名直连**（不需要任何密钥）。实测与付费通道判定几乎逐条一致
     （噪声 0.03~0.05 / 信号 0.75，付费为 0.01~0.03 / 0.76），~1s/条，
     40 次连发 0 失败。⚠️ **必须显式设 User-Agent**——urllib 默认的
     Python-urllib/3.x 会被 Cloudflare 拦成 403。
  2. paid —— https://api.typesafe.ai/v1/systemone，模型 jev-latest，需要 key。
  默认 auto：free 优先（零成本）→ paid 兜底（有 key 时）。环境变量
  OSINT_JEV_CHANNEL=free|paid|auto 可覆盖；free 失败自动降级 paid，反之不然。

用法：
    from jev_client import JevClient
    c = JevClient()
    codes = c.diagnose_evidence("证据文本", majors)   # {hyp_id: {"code","conf","probs"}}
"""
import ipaddress
import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

# ---- 通道一：免费（2026-10-08 接入，默认优先）----
# opencode.ai 的 Zen 网关提供 System One 兼容端点，**无需 key**（匿名直连），
# 模型 jev-1.13-free。实测与付费判定几乎逐条一致，~1s/条。
FREE_HOST = "opencode.ai"
FREE_URL = "https://opencode.ai/zen/v1/systemone"
FREE_MODEL = "jev-1.13-free"

# ---- 通道二：付费（原主通道，现为兜底）----
JEV_HOST = "api.typesafe.ai"
JEV_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"

# SSRF 白名单：只放行上述两个 host
ALLOWED_HOSTS = (JEV_HOST, FREE_HOST)
# 免费通道必需的 UA：非 Python-urllib 的任意串均可（Python 默认 UA 被 CF 403）
DEFAULT_UA = "osint-jev/1.0"
# 通道选择环境变量（auto|free|paid）
CHANNEL_ENV = "OSINT_JEV_CHANNEL"

# 官方限流动态调整（250k tok/s、1200 req/min），偶发失败重试即可
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2.0

# JEV choice → ACH 判定码
CODE_MAP = {"consistent": "C", "inconsistent": "I", "neutral": "N"}

# 门控双阈值（2026-09-28 实测校准，学 Selective Prediction 的 reliability+relevance 双层）
#   GATE_ABS_THRESHOLD：绝对门槛——该证据对这个假设确有推动
#     实测新措辞下真信号 gate 在 0.26~0.56，噪声上界约 0.19，取 0.15 偏保守
#   SEL_MIN：相对优势——gate_max / gate_median，衡量证据能否**区分**竞争假设
#     实测：台海军演 10.2 / 伊拉克石油 8.0（信号） vs 水星半径 1.0（噪声）
#     取 2.5 作为下界（分布 p50=2.3，p90=7.2，信号集中在 >6）
GATE_ABS_THRESHOLD = 0.15
SEL_MIN = 2.5


class JevError(Exception):
    pass


def _safe_jev_post(url, payload, key, timeout=120):
    """SSRF 守卫（与 analyze._safe_ai_post 同规格）：仅 https + 白名单 + 非私有地址"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") not in ALLOWED_HOSTS:
        raise ValueError("blocked non-whitelisted JEV endpoint: " + url)
    for info in socket.getaddrinfo(parsed.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
            raise ValueError("endpoint resolves to forbidden address: " + str(ip))
    headers = {"Content-Type": "application/json", "User-Agent": DEFAULT_UA}
    if key:                       # 免费通道无需 Authorization；付费通道带 key
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class JevClient:
    """JEV 客户端，支持两个通道（2026-10-08 加免费通道）：

      free —— opencode.ai/zen/v1/systemone，模型 jev-1.13-free，**无需 key**
      paid —— api.typesafe.ai/v1/systemone，模型 jev-latest，需要 key

    channel 取值（也可用环境变量 OSINT_JEV_CHANNEL 指定）：
      "auto"（默认）—— free 优先，失败降级 paid（无 key 时只用 free）
      "free"         —— 只用 free（可离线复现、零成本）
      "paid"         —— 只用 paid（需 key；free 与付费的偏差需对照组时用）

    available 语义变化：free 免 key，故**只要网络可达就为 True**——调用方
    原有的「无 key → 退出/回退 mimo」分支在无 key 环境下现在会走 JEV。
    """

    def __init__(self, key=None, model=DEFAULT_MODEL, caller="ach_matrix",
                 retries=RETRY_ATTEMPTS, record_usage=True, channel=None):
        channel = (channel or os.environ.get(CHANNEL_ENV) or "auto").strip().lower()
        if channel not in ("auto", "free", "paid"):
            channel = "auto"
        if key is None and channel != "free":
            # 仅付费通道需要 key；free 通道缺 key 不影响可用性（不再去读）
            try:
                import sys
                root = Path(__file__).resolve().parent.parent
                if str(root) not in sys.path:
                    sys.path.insert(0, str(root))
                from secrets_loader import get_jev_key
                key = get_jev_key()
            except Exception:
                key = ""
        self.key = key or ""
        self.model = model
        self.caller = caller
        self.retries = retries
        self.record_usage = record_usage
        self.channel = channel
        self._channels = self._build_channels()

    def _build_channels(self):
        """按优先级排出可用通道 [(name, url, model, key), ...]。"""
        chans = []
        if self.channel in ("auto", "free"):
            chans.append(("free", FREE_URL, FREE_MODEL, ""))
        if self.channel in ("auto", "paid") and self.key:
            chans.append(("paid", JEV_URL, self.model, self.key))
        return chans

    def describe(self):
        """一行诊断串（日志/排查用）"""
        if not self._channels:
            return "JEV: 无可用通道（paid 需 key；可设 OSINT_JEV_CHANNEL=free 走免 key 通道）"
        return "JEV: " + " → ".join(c[0] + "(" + c[2] + ")" for c in self._channels)

    @property
    def available(self):
        return bool(self._channels)

    # ---------- 核心调用 ----------
    def systemone(self, state, questions, timeout=120):
        """POST /systemone → 原始响应 dict。按通道顺序尝试，全失败抛 JevError。"""
        if not self._channels:
            raise JevError("JEV 无可用通道（paid 需 key：环境变量 TYPESAFE_API_KEY "
                           "或 config.local.yaml 的 jev.api_key；free 通道无需 key）")
        errors = []
        for name, url, model, key in self._channels:
            payload = {"model": model, "state": state, "questions": questions}
            last = None
            for attempt in range(1, self.retries + 1):
                try:
                    d = _safe_jev_post(url, payload, key, timeout=timeout)
                    # 免费端点对不支持的原语/非法请求会返 error body（实测 score 原语
                    # HTTP 422；读到后换通道，不能当成有效答案继续解析）
                    if isinstance(d, dict) and d.get("error"):
                        raise JevError("通道返回 error: " + str(d["error"])[:150])
                    if name == "paid":
                        # 只有付费通道记账——free 通道 cost 恒为 0，
                        # 记进去会把免费 token 按付费单价算成假成本
                        self._account(d.get("usage") or {})
                    return d
                except urllib.error.HTTPError as ex:
                    body = ""
                    try:
                        body = ex.read().decode("utf-8", "replace")[:200]
                    except Exception:
                        pass
                    last = f"HTTP {ex.code}: {body}"
                    # 4xx（除 429）是参数/鉴权问题，本通道重试无用 → 立即换通道
                    if ex.code != 429 and 400 <= ex.code < 500:
                        break
                except JevError as ex:
                    last = str(ex)
                    break   # 业务级错误（error body），换通道而非重试
                except Exception as ex:
                    last = f"{type(ex).__name__}: {str(ex)[:150]}"
                if attempt < self.retries:
                    time.sleep(RETRY_BACKOFF * attempt)
            errors.append(name + ": " + str(last))
        raise JevError("JEV 全部通道失败（" + str(len(self._channels)) + " 个）: "
                       + " | ".join(errors))

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
    def _hyp_def(h):
        """把节点自己的**情景定义**拼成问题片段（2026-09-28）。

        **只取 rationale，不取 falsification_criteria**——后者含具体数字指标
        （"到2027年…频率较2026年无显著增长"），对方向判断是噪声，实测会让
        一致率减半（官方 jaggedness 第 5 条「无关细节拉低精度」，见文件头）。

        但 rationale 必须给：`probe_mega.py` 首版只写议题名，JEV 把霍尔木兹
        油价、美债收益率全算成危机信号（120 条里 60 条命中）；把情景定义写进
        问题后 signal 档从 50% 收紧到 7.67%。光给名字必然过宽。
        """
        rationale = str(h.get("rationale") or "").strip()
        return ("该情景的定义是：" + rationale[:180]) if rationale else ""

    @staticmethod
    def build_gate_questions(majors):
        """第一段：议题相关性门控（Noul，每假设一问）。

        2026-09-28 重写。**旧版失效原因**：问「是否直接涉及该假设的议题」，
        JEV 把"议题"理解为**主题域**（台海/军事/地缘），于是「A股军工板块拉升」
        「美原油价格飙升」「日本防务开支翻倍」全部过线（gate 0.08~0.15），
        再被判成支持证据——实测 HM001 的 51 条 C/I 里 gate>=0.5 的仅 6 条。

        新版问「是否**改变**该情景发生的可能性」——衡量的是**因果贡献**，
        而非主题相关。市场行情是他国行为反映的是既有局势，不改变概率。
        """
        return {"g_" + h["id"]: {
            "type": "noul",
            "instructions": (
                "这条情报是否直接改变了「" + h.get("title", "") + "」这一情景"
                "发生的可能性？" + JevClient._hyp_def(h) + " "
                "若情报只是反映相关主题（如股市行情、商品价格波动、第三方国家"
                "的行为、其他地区的事件），并未改变上述定义所描述的状态，"
                "应给低概率。"
            ),
        } for h in majors}

    @staticmethod
    def build_direction_questions(majors_subset):
        """第二段：仅对通过门控的假设判方向（Choice）。

        2026-09-28 重写。**旧版失效原因**：问「与假设的预期是否一致」，
        把"是否支持该假设成立"字面化——`A股军工板块拉升` 因"军工活跃"
        与"冲突升级"字面同向而被判 consistent（conf 0.93），
        但市场反应并不推动冲突升级。

        新版问「是否**实质推动**该情景向发生靠近」，并带上节点自己的定义，
        标准是**因果方向**而非语义同向。
        """
        qs = {}
        for h in majors_subset:
            qs["d_" + h["id"]] = {
                "type": "choice",
                "instructions": (
                    "这条情报是否实质推动了「" + h.get("title", "") + "」"
                    "这一情景向发生靠近？" + JevClient._hyp_def(h) + " "
                    "判断标准：只有直接改变上述定义所描述状态的情报才算支持或"
                    "反对；仅与议题主题相关（如市场行情、他国行为、其他地区冲突）"
                    "但未推动该定义的，应判 neutral。"
                ),
                "criteria": {
                    "consistent": "该情报实质推动了这一情景向发生靠近",
                    "inconsistent": "该情报实质降低了这一情景发生的可能性",
                    "neutral": "该情报未实质改变这一情景的可能性",
                },
            }
        return qs

    def gate_and_diagnose(self, evidence_text, majors, gate_threshold=None,
                          selectivity_threshold=None, timeout=120):
        """两段式诊断（推荐入口）：先门控议题相关性，再对相关假设判方向。

        第一段：Noul 问「是否改变该情景发生的可能性」→ 判 N 或进入第二段
        第二段：仅对通过门控的假设问 Choice 方向

        **门控判据的演进**（2026-09-28 重写）：

        旧版（单阈值 gate >= 0.08）实测失效：HM001「台海冲突升级」的 51 条
        C/I 里 gate>=0.5 仅 6 条，其余是「A股军工板块拉升」（gate 0.13,
        conf 0.93）这类字面含"军工/地缘"但语义无关的条目。根因是旧措辞问
        「是否涉及该议题」——JEV 把"议题"理解为**主题域**而非**概率变化**。

        新版双判据（学 Selective Prediction 的 reliability + relevance 双层，
        见 Srinivasan et al. 2024 的 ReCoVERR：相关性定义为「证据存在与否对
        假设概率的影响差」，即因果贡献而非语义相似）：

          1. **绝对门槛** gate >= GATE_ABS_THRESHOLD（0.15）
             —— 该证据对这个假设确有推动
          2. **相对优势** selectivity = gate_max / gate_median >= SEL_MIN（2.5）
             —— 该证据能**区分**竞争假设（Heuer 的 diagnosticity）

        为什么加相对判据：实测 selectivity 能把信号与噪声干净分开——
        台海军演 10.2 / 伊拉克石油 8.0 / 台海军售 7.8（真信号）；
        水星半径 1.0 / 富士山滑坡 1.0 / 金鱼饼干 1.0（噪声）。
        ACH 第一性原理要求证据能区分竞争假设，只看绝对值的证据会同时"支持"多个假设。

        参数可调，已落盘 gate 值可重算（无需重跑 AI）。
        返回 {hyp_id: {"code","conf","probs","gate","selectivity"}}。
        """
        import statistics as _st

        abs_thr = GATE_ABS_THRESHOLD if gate_threshold is None else gate_threshold
        sel_thr = SEL_MIN if selectivity_threshold is None else selectivity_threshold

        d1 = self.systemone(evidence_text, self.build_gate_questions(majors), timeout=timeout)
        a1 = d1.get("answers") or {}
        gates = {}
        for h in majors:
            g = a1.get("g_" + h["id"]) or {}
            gates[h["id"]] = round(float(g.get("noul", 0.0)), 3)

        vals = list(gates.values())
        gmax = max(vals) if vals else 0.0
        gmed = _st.median(vals) if vals else 0.0
        selectivity = round(gmax / max(gmed, 0.01), 2)
        # 相对优势不足 → 该证据无区分力，全部判 N（不进入第二段）。
        # 例外：只有 1 个假设时 gate_max/gate_median 恒为 1，相对判据无意义
        # （实测踩坑：单假设测试时台海军演被误杀），此时退化为只看绝对门槛。
        discriminative = (len(majors) <= 1) or (selectivity >= sel_thr)

        passed = [h for h in majors
                  if discriminative and gates[h["id"]] >= abs_thr]

        out = {}
        for h in majors:
            if h not in passed:
                out[h["id"]] = {"code": "N", "conf": round(1.0 - gates[h["id"]], 3),
                                "probs": {}, "gate": gates[h["id"]],
                                "selectivity": selectivity}

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
                    "selectivity": selectivity,
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
