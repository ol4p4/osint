# -*- coding: utf-8 -*-
r"""gemini_client.py - Google Gemini 原生 API 客户端（2026-10-05）

背景：CI 上 OpenCode 免费层已锁死（403 FreeTierError "can only be used from
within OpenCode"）、dots key 只在本地 config.local.yaml → citizen_impact 长期
analyzed 0/50 空转。Gemini key 存 GitHub Secrets，CI（美国服务器）可直连。

**为什么走原生 :generateContent 而非 OpenAI 兼容端点**：新签发的 key 是
`AQ.` 开头（Auth key，Google 正把 AI Studio key 从 AIza 迁移过来），实测在
/v1beta/openai/chat/completions 返回 404，原生端点正常。

**为什么带可达性预检**：本地境内到 generativelanguage.googleapis.com 不可达，
且 TCP connect 是黑洞式超时（实测 socket timeout=6 仍耗时 48s —— Windows 上
socket timeout 对 connect 不生效）。若不加预检，本地每批翻译/研判都会先白等
48s 才降级。预检用线程 + join 硬超时（4s），结果**进程内缓存**，只探一次。

**通道顺序**：调用方把 Gemini 放链首（可达时优先），dots 次之。不可达时
reachable() 返回 False，调用方自然跳过。
"""
import ipaddress
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

GEMINI_HOST = "generativelanguage.googleapis.com"
GEMINI_BASE = "https://" + GEMINI_HOST + "/v1beta"

# 候选模型链（2026-10-05 CI 实测探测确定）：
#   gemini-3.5-flash-lite / 3.1-flash-lite / 3.5-flash → HTTP 200
#   gemini-2.5-flash-lite / 2.5-flash / 2.5-pro        → HTTP 404（对新用户下架）
# 按「快/便宜优先」排序，逐个尝试，404/400 换下一个（404 不耗 token，代价可忽略）。
#
# **配额独立性（2026-10-13 实测，探针 tools/probe_gemini_quota.py）**：
#   quotaId 全为 `GenerateRequestsPerMinutePerProjectPerModel-FreeTier`，**按模型分开**。
#   逐个打爆实测：3.5-flash-lite → quotaValue **15**（第 15 发首次 429）
#                 3.1-flash-lite → quotaValue **15**（第 13 发首次 429）
#                 3.5-flash     → quotaValue **5** （第 8 发首次 429）
#   证据：第一个模型打爆后，第二个模型仍能连发 16 条——**共享配额的话会立刻 429**。
#   故三模型 = 三个独立桶（合计 35 RPM），"429 换下一个模型"是有效降级而非白费。
#
# **RPD（日额度）实况（2026-10-13，AI Studio rate-limit 页截图，用户账号）**：
#     模型                  RPM    TPM      RPD
#     gemini-3.5-flash-lite  15   250K     500    ← 主力，宽裕
#     gemini-3.1-flash-lite  15   250K     500    ← 备援，宽裕
#     gemini-3.5-flash        5   250K      20    ← **非 lite 的 Flash 族 RPD 仅 20**
#     （3.6/3.8 Flash 同族，RPD 也是 20；其余多数模型 0/0 未开通）
#   **两个 lite 的 RPD 才是真实容量**（合计 1000/天），第三个基本形同虚设。
#   这直接决定 429 的处置：见 generate() 里"只有全模型皆 RPD 才跳过退避"的判据。
GEMINI_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
]

# 全部模型都 429 后的退避重试（2026-10-13 新增）。
#
# 背景：原实现对 429 与 404 一视同仁（`except HTTPError` → 换下一个模型），
# 三个模型全热后直接抛异常给调用方 → 切到 dots 等质量更差的备援通道。
# 但实测 429 是**临时容量**信号且带 `retryDelay`（实测 21s/55s/36s），
# **等一会儿原模型就能复用**——把"等 20 秒能好的活"降级给差通道并不划算。
#
# 策略：三模型全 429 → 按 retryDelay 退避（无该字段则用默认值）→ 重跑整条链。
# 最多 GEMINI_429_ROUNDS 轮，仍失败才抛给调用方去切通道。
GEMINI_429_ROUNDS = 2          # 额外重试轮数（总尝试 = 1 + 2 轮）
GEMINI_429_MIN_WAIT = 8        # retryDelay 缺失时的兜底等待（秒）
GEMINI_429_MAX_WAIT = 90       # 等待上限，防服务端给出离谱值把批次卡死

# 判定「日额度 RPD」的 retryDelay 阈值（秒，2026-10-13）。
# 依据：RPM 实测 retryDelay 21~55s，而分钟窗口本身最长 60s——若服务端报出的等待
# 远超一分钟，那基本不是分钟配额。此判据不依赖 quotaId 命名（RPD 的字符串未实测过），
# 与 "PerDay" 字符串判据**并列**，任一命中即按日额度处理（不退避、速切通道）。
GEMINI_RPD_RETRY_HINT = 300

_ALLOWED_HOSTS = {GEMINI_HOST}
_STATE = {"checked": False, "ok": False}


def _parse_retry_delay(body_text):
    """从 429 响应体取 google.rpc.RetryInfo.retryDelay（如 "21s"）。

    实测（2026-10-13）：**没有 Retry-After 响应头**，等待信息只在 body 里。
    返回秒数（int）或 None。"""
    try:
        j = json.loads(body_text)
    except Exception:
        return None
    for d in ((j.get("error") or {}).get("details") or []):
        if "RetryInfo" in (d.get("@type") or ""):
            raw = (d.get("retryDelay") or "").strip()
            if raw.endswith("s"):
                try:
                    return int(float(raw[:-1]))
                except ValueError:
                    return None
    return None


def reachable(timeout=4):
    """探测 Gemini 端点可达性（进程内缓存，只探一次）。

    本地不可达时 TCP connect 黑洞到 ~48s，故用线程 + join 硬超时兜底
    （socket timeout 对 connect 不生效）。返回 True/False。"""
    if _STATE["checked"]:
        return _STATE["ok"]
    _STATE["checked"] = True
    box = {}

    def _probe():
        try:
            s = socket.create_connection((GEMINI_HOST, 443), timeout=timeout)
            s.close()
            box["ok"] = True
        except Exception:
            box["ok"] = False

    t = threading.Thread(target=_probe, daemon=True)
    t.start()
    t.join(timeout + 2)
    _STATE["ok"] = bool(box.get("ok"))
    return _STATE["ok"]


def _post(url, payload, headers, timeout):
    """SSRF 守卫 + 线程硬超时（与 analyze._safe_ai_post 同规格，独立实现
    以免扩大 analyze 的白名单攻击面；JEV 客户端也是独立守卫）。"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") not in _ALLOWED_HOSTS:
        raise ValueError("blocked non-whitelisted endpoint: " + url)
    for info in socket.getaddrinfo(parsed.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
            raise ValueError("endpoint resolves to forbidden address: " + str(ip))
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    box = {}

    def _do():
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                box["raw"] = resp.read().decode("utf-8")
        except Exception as e:
            box["err"] = e

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    t.join(timeout + 15)
    if t.is_alive():
        raise TimeoutError("gemini endpoint unresponsive (slow-drip bypassed socket timeout)")
    if "err" in box:
        raise box["err"]
    return box["raw"]


def generate(api_key, user_prompt, system_prompt="", timeout=120, max_tokens=8192):
    """原生 generateContent 调用，遍历 GEMINI_MODELS；429 走退避重试。

    三级处理（2026-10-13 按实测配额重写）：
      404/400          → 换下一个模型（模型不存在，立刻换是正解）
      429              → 换下一个模型（配额**按模型独立**，换模型 = 换桶，零等待）
      三模型全 429     → 按 retryDelay 退避后**重跑整条链**（最多 GEMINI_429_ROUNDS 轮）

    只有全部重试仍失败才抛异常，让调用方去切备援通道。

    返回文本（已 strip）。
    """
    last = None
    rounds = 1 + GEMINI_429_ROUNDS
    for rnd in range(rounds):
        all_429 = True          # 本轮是否全因 429 失败（决定要不要退避重试）
        retry_delay = None
        # 本轮各模型的 429 性质（2026-10-13）：用来判断"退避是否有意义"。
        # 只有全模型皆 RPD 时才不该等；只要还有 RPM 限流的模型，等一会儿就能复用。
        _rpd_models = set()     # 撞日额度的模型
        _rpm_models = set()     # 撞分钟配额的模型
        for model in GEMINI_MODELS:
            url = GEMINI_BASE + "/models/" + model + ":generateContent"
            body = {
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens},
            }
            if system_prompt:
                body["system_instruction"] = {"parts": [{"text": system_prompt}]}
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
                "User-Agent": "osint/1.0",
            }
            try:
                raw = _post(url, json.dumps(body).encode("utf-8"), headers, timeout)
                result = json.loads(raw)
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 429:
                    # 读服务端建议的等待时间（实测无 Retry-After 头，只在 body）
                    _d = None
                    _body = ""
                    try:
                        _body = (e.read() or b"").decode("utf-8", "replace")
                        _d = _parse_retry_delay(_body)
                        if _d:
                            retry_delay = max(retry_delay or 0, _d)
                    except Exception:
                        pass
                    # 区分 RPM / RPD（2026-10-13）：两者都返 429，但处置相反——
                    # RPM 等 1 分钟就够（退避重试有效）；RPD 是**日额度**耗尽，
                    # 等到次日才恢复，此时退避纯属白等，应立即切备援通道。
                    #
                    # ⚠ 判据可靠度（诚实标注）：实测只见过 RPM 的 quotaId
                    # （`...PerMinutePerProjectPerModel-FreeTier`）；**RPD 的字符串没实测过**
                    # （要烧光整日额度才能触发，不划算）。故用**双判据**，任一命中即算日额度，
                    # 避免押注单一字符串：
                    #   ① body 含 "PerDay"（按命名惯例；可能因命名不同而失效）
                    #   ② retryDelay 明显偏长（> GEMINI_RPD_RETRY_HINT）——RPM 实测仅
                    #      21~55s，而分钟窗口最长 60s；若服务端报出远超分钟的等待，
                    #      基本不是分钟配额。此判据不依赖命名，更稳。
                    _is_rpd = ("PerDay" in _body) or (
                        isinstance(_d, int) and _d > GEMINI_RPD_RETRY_HINT)
                    print("[GEMINI] " + model + " 429(" + ("日额度 RPD 耗尽" if _is_rpd
                          else "分钟配额 RPM") + "), 换下一个模型")
                    if _is_rpd:
                        _rpd_models.add(model)
                    else:
                        _rpm_models.add(model)
                else:
                    all_429 = False
                    print("[GEMINI] " + model + " HTTP " + str(e.code) + ", 尝试下一个")
                continue
            cands = result.get("candidates") or []
            if not cands:
                fb = result.get("promptFeedback") or {}
                last = ValueError("gemini no candidates: " + json.dumps(fb, ensure_ascii=False)[:200])
                all_429 = False
                continue
            parts = (cands[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
            if not text.strip():
                last = ValueError("gemini empty text (finishReason=" + str(cands[0].get("finishReason")) + ")")
                all_429 = False
                continue
            return text.strip()

        # 本轮结束：决定要不要退避重试。
        # 只有**全部模型都撞 RPD** 才跳过退避——那时等到次日才有意义，速切备援通道。
        #
        # 为什么不能"任一模型 RPD 就跳过"（2026-10-13 按 AI Studio 实际配额修正）：
        # 实测本账号配额 3.5-flash-lite / 3.1-flash-lite 各 RPD=500，
        # 而 gemini-3.5-flash（非 lite 的 Flash 族）**RPD 仅 20**——它几乎必然先撞日额度。
        # 若见它一个就放弃，会白白浪费另两个"等 30~60 秒就能复用"的桶
        # （它们通常只是 RPM 限流）。故判据收紧为「全模型皆 RPD」。
        if not (all_429 and rnd + 1 < rounds):
            break
        if _rpd_models and not _rpm_models:
            print("[GEMINI] 全部模型日额度(RPD)耗尽，不做退避（等到次日无效）→ 交调用方切通道")
            break
        if _rpd_models:
            print("[GEMINI] 部分模型 RPD 耗尽（" + ",".join(sorted(_rpd_models)) +
                  "），其余仅 RPM 限流 → 仍退避重试")
        wait = retry_delay or GEMINI_429_MIN_WAIT
        wait = max(GEMINI_429_MIN_WAIT, min(wait, GEMINI_429_MAX_WAIT))
        print("[GEMINI] 三模型配额全热，退避 " + str(wait) + "s 后重试"
              " (第 " + str(rnd + 2) + "/" + str(rounds) + " 轮)")
        time.sleep(wait)

    raise last if last else RuntimeError("gemini: no model available")
