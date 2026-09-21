#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local Staff Officer - AI Deep Analysis Engine"""

import json, os, re, sys, yaml
from pathlib import Path
from typing import List, Dict, Any
from dataclasses import dataclass, asdict
import urllib.request, urllib.error, uuid

AI_ALLOWED_HOST = "opencode.ai"
# AI 通道白名单（2026-09-17 多通道备援）：
#   1. OpenCode（默认）——免费层已加客户端指纹校验，非官方客户端 403，靠熔断器跳过
#   2. NVIDIA integrate——本地 key，gpt-oss-20b（+reasoning_effort=low）/ glm-5.3
#   3. 小红书 dots——note3-prev-api.askdiandian.com（双通道备援，实测 1s 响应）
# OpenRouter 已移除（免费层日限 10 次请求）
AI_ALLOWED_HOSTS = {"opencode.ai", "integrate.api.nvidia.com", "note3-prev-api.askdiandian.com"}
NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
DOTS_BASE = "https://note3-prev-api.askdiandian.com/v1"

# OpenCode 模型名 → NVIDIA integrate 模型名（备援通道）
# 2026-09-17 实测选型（重要修正）：
#   gpt-oss-20b 是推理模型，**默认会陷入思考循环**（"说一个字"烧 4096 token 仍无 content），
#   但加 reasoning_effort=low 后 2 秒返回正常答案；真实裁判任务 20s vs glm-flash 173s 空响应。
#   → 裁判/副笔用 gpt-oss-20b（快、稳）；主笔生成用 glm-5.3（中文长文质量好）。
_NV_SLUG_MAP = {
    "mimo-v2.5-free": "z-ai/glm-5.3",
    "nemotron-3.5-lightning-free": "openai/gpt-oss-20b",
    "nemotron-3-ultra-free": "openai/gpt-oss-20b",
    "ling-3.0-flash-fin-free": "openai/gpt-oss-20b",
}

# 推理型模型的思考量控制（不加会陷入思考循环，content 永远为空）
_NV_REASONING_EFFORT = {
    "openai/gpt-oss-20b": "low",
}


def _nv_slug(model_name):
    """OpenCode 模型名映射到 NVIDIA integrate 模型名；未收录返回空串（跳过该备援项）。"""
    return _NV_SLUG_MAP.get(model_name, "")


def _dots_slug(model_name):
    """OpenCode 模型名 → 小红书 dots 模型名（第三通道，2026-09-17 接入）。"""
    return "dots3-note-prev"


# ── 端点熔断器（2026-09-17 卡顿修复）────────────────────────────────
# 背景：OpenCode 免费层 403 + OpenRouter 免费层 429 成为常态，每次调用都要
# 白撞 8 个死端点（每个几秒），整轮裁判因此动辄 10 分钟。熔断后直接跳过。
import time as _time

_DEAD = {}          # (base_url, model) -> 解封时间戳
_LAST_GOOD = [None]  # 最近成功的端点（优先复用）


def _is_dead(key_id):
    until = _DEAD.get(key_id)
    if not until:
        return False
    if _time.time() >= until:
        del _DEAD[key_id]
        return False
    return True


def _mark_dead(key_id, err):
    """按错误类型设熔断时长。

    2026-09-17 调优：超时**不熔断**——NVIDIA glm 端点本身会 43-150s 波动，
    一次超时不代表端点死了（曾因首节超时熔断 10 分钟，导致后续 5 节全部
    circuit-open 跳过，整轮报废）。只有明确的鉴权/不存在错误（403/404）
    才长熔断，限流（429）短熔断。

    2026-09-21 补充：**内容安全拦截的 403 不熔断**——那是 prompt 内容触发的
    （实测 dots 对含敏感内容的批次返 governance.content_safety_input_rejected），
    端点本身健康、换个 prompt 立刻可用。误熔断会把可用通道拉黑 30 分钟，
    放大成整轮失败（本次踩坑：Step2 全程 403 而独立进程同 key 测试全 200）。
    """
    s = str(err)
    if "content_safety" in s or "safety system" in s or "content safety" in s.lower():
        return   # 内容触发，端点健康，不熔断
    if "403" in s or "404" in s:
        _DEAD[key_id] = _time.time() + 1800
    elif "429" in s:
        _DEAD[key_id] = _time.time() + 180
    # 超时/空响应/思维链：不熔断，下次调用可重试

def _safe_ai_post(url, payload, headers, timeout=180):
    """SSRF 防护：仅 https + 白名单域名 + 解析结果不得指向私有/环回/保留地址。
    2026-09-12 加固慢速响应硬超时：socket timeout 防不了服务器收下请求后滴字节保活
    （http.client._read_status 每次 read 都有数据到达，180s 永不触发，实测挂死 22 分钟，
    faulthandler 栈定位于 ssl.read）。Windows 无 SIGALRM，改为白名单校验后
    线程 + join 执行请求——超时放弃本次尝试（daemon 孤儿线程自行终结），
    由 _call_api 走模型降级链。"""
    import socket, ipaddress, threading
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") not in AI_ALLOWED_HOSTS:
        raise ValueError("blocked non-whitelisted AI endpoint: " + url)
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
        raise TimeoutError("AI endpoint unresponsive (slow-drip bypassed socket timeout)")
    if "err" in box:
        raise box["err"]
    return box["raw"]

@dataclass
class AnalysisResult:
    intel_id: str
    macro_diagnosis: Dict[str, str]
    structural_implication: str
    personal_action_space: Dict[str, Any]
    knowledge_links: List[str]
    confidence: int
    contradictions: str
    raw_reasoning: str

class MacroAnalyzer:
    def __init__(self, config, persona, knowledge_base):
        self.config = config
        self.persona = persona
        self.kb = knowledge_base
        api_cfg = config.get("api", {})
        # 密钥走 secrets_loader（env > config.local.yaml > config.yaml），仓库内 config.yaml 不存真实 key
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from secrets_loader import get_opencode_key
        self.api_key = get_opencode_key() or os.environ.get("OPENAI_API_KEY")
        self.base_url = api_cfg.get("base_url", "https://api.deepseek.com/v1")
        self.model = api_cfg.get("model", "deepseek-chat")
        self.fallback_models = api_cfg.get("fallback_models", [])
        ai_cfg = config.get("ai_analysis", {})
        self.batch_size = ai_cfg.get("batch_size", 10)
        self.temperature = ai_cfg.get("temperature", 0.3)
        self.max_tokens = ai_cfg.get("max_tokens_per_item", 800)
        # 输出预算（含推理模型的 reasoning）：默认 32768，实测 dots 接受 65536。
        # 此前硬编码 8192，对"10 条 × 8 字段"的批次必然截断（见 _parse_response 注释）。
        self.max_tokens_budget = int(ai_cfg.get("max_tokens_budget", 32768))

    @staticmethod
    def _nvidia_key():
        """NVIDIA integrate 备援 key（OpenCode 免费层指纹校验后的可用通道）。"""
        try:
            from secrets_loader import get_nvidia_key
            return get_nvidia_key()
        except Exception:
            return ""

    @staticmethod
    def _dots_key():
        """小红书 dots 备援 key（第三通道，2026-09-17 接入）。"""
        try:
            from secrets_loader import get_dots_key
            return get_dots_key()
        except Exception:
            return ""

    def analyze_batch(self, items, macro_context):
        """逐批分析；连续多批无有效产出时提前放弃。

        2026-09-21：AI 通道全线不可用时（dots 长 prompt 超时 + 其余 403），
        原实现会对每批都白撞完整的降级链（实测单批约 8 分钟），60 条约 6 批
        要空转近 50 分钟才结束。改为连续 CONSECUTIVE_FAIL_LIMIT 批无**有效**产出
        即中止，让 Step2 快速失败，把时间留给下次调度。

        注意判据是"有效产出"而非"非空列表"：解析失败时 _parse_response 会
        返回一批 fallback 占位结果（structural_implication="AI 分析失败，需人工复核"），
        列表非空但毫无信息量——用非空判断会让熔断永不触发（首版踩过）。
        """
        results = []
        CONSECUTIVE_FAIL_LIMIT = 3
        consecutive_empty = 0
        for i in range(0, len(items), self.batch_size):
            batch = items[i:i + self.batch_size]
            got = self._analyze_single_batch(batch, macro_context)
            usable = [r for r in got if not self._is_fallback(r)]
            if usable:
                consecutive_empty = 0
                results.extend(got)
            else:
                consecutive_empty += 1
                print(f"[AI] batch {i // self.batch_size + 1} 无有效产出"
                      f"（连续 {consecutive_empty}/{CONSECUTIVE_FAIL_LIMIT}）")
                if consecutive_empty >= CONSECUTIVE_FAIL_LIMIT:
                    print("[AI] 连续多批无有效产出，判定 AI 通道不可用，提前中止分析")
                    break
                results.extend(got)   # 保留占位结果，便于人工复核是哪几条
        return results

    @staticmethod
    def _is_fallback(result):
        """判断是否为解析失败产生的占位结果。"""
        return (result.structural_implication or "").startswith("AI 分析失败")

    @staticmethod
    def _is_content_block(err):
        """识别内容安全拦截（dots 的 governance.content_safety_input_rejected）。

        2026-09-21 定位：dots 网关对 prompt 做内容安全审查，命中即返回 403
        `{"error_type":"governance.content_safety_input_rejected"}`。
        实测 10 条批次里只要有 1 条涉政敏感（如"习特会"），**整批 10 条全被拒**，
        而逐条单发时其余 9 条全部正常。这类 403 与鉴权失败同码不同因，
        必须区分处理：不能熔断端点（端点健康），也不能整批放弃（丢 9 条）。
        """
        s = str(err)
        return "content_safety" in s or "safety system" in s or "content safety" in s.lower()

    def _analyze_single_batch(self, items, macro_context):
        # 2026-09-16 修复: cache 回退路径的条目缺 content 字段曾炸 KeyError('content')
        # （9-14 周一 14:23 补跑时当日文件未产出 → main_local 回退旧 cache → Step2 崩溃），
        # title/source_name 同样做防御（GDELT 等源字段不齐）
        system_prompt = self._build_system_prompt(macro_context)
        try:
            return self._analyze_one_call(items, macro_context, system_prompt)
        except Exception as e:
            if self._is_content_block(e) and len(items) > 1:
                # 内容审查拦截：某条敏感内容拖垮整批 → 拆半递归，隔离问题条目
                print(f"[AI] 批次被内容安全拦截（{len(items)} 条），拆半重试以隔离敏感条目")
                mid = len(items) // 2
                return (self._analyze_single_batch(items[:mid], macro_context)
                        + self._analyze_single_batch(items[mid:], macro_context))
            if self._is_content_block(e):
                # 单条仍被拦：该条确实敏感，记占位结果跳过（不拖累其他批次）
                print("[AI] 单条被内容安全拦截，记占位结果跳过")
                return [self._fallback_result(items[0], "内容安全拦截：" + str(e)[:120])]
            # 主分析链路的降级语义：全部模型不可用时返回空结果集（不中断批次）
            print("[AI] batch analyze degraded (all models unavailable): " + str(e)[:120])
            return []

    def _analyze_one_call(self, items, macro_context, system_prompt):
        items_json = json.dumps([{
            "id": item.get("id", ""), "title": item.get("title", ""),
            "source": item.get("source_name", ""),
            "content": (item.get("content") or item.get("content_preview")
                        or item.get("summary") or "")[:3000],
            "keywords_hit": item.get("keywords_hit", []),
            "entities": item.get("entities", []),
            "base_score": item.get("final_score", 0),
            "published_at": item.get("published_at", "")
        } for item in items], ensure_ascii=False, indent=2)

        user_prompt = f"""\u4eca\u65e5\u60c5\u62a5 {len(items)} \u6761\uff08JSON\uff09\uff1a\n{items_json}\n\n\u8bf7\u5bf9\u6bcf\u6761\u60c5\u62a5\u8fdb\u884c\u56db\u7ef4\u7ed3\u6784\u7814\u5224\uff0c\u8f93\u51fa JSON \u6570\u7ec4\uff0c\u6bcf\u4e2a\u5143\u7d20\u5305\u542b\uff1a\n1. intel_id: \u60c5\u62a5ID\n2. macro_diagnosis: \u56db\u7ef4\u8bca\u65ad accumulation_node/spatial_layer/state_market_shift/class_interest\n3. structural_implication: \u7ed3\u6784\u6027\u542b\u4e49\n4. personal_action_space: window_months/concrete_moves/avoid_traps/signals_to_watch\n5. knowledge_links: \u53cc\u5411\u94fe\u63a5\u6570\u7ec4\n6. confidence: 1-10\u7f6e\u4fe1\u5ea6\n7. contradictions: \u77db\u76fe\u5f85\u9a8c\u8bc1\u70b9\n8. raw_reasoning: \u5b8c\u6574\u63a8\u7406\u94fe\n\u53ea\u8f93\u51fa\u7eafJSON\u6570\u7ec4\uff0c\u4e0d\u8981\u5176\u4ed6\u5185\u5bb9\u3002"""

        # 2026-09-21：timeout 由默认 180s 提到 240s（即 _safe_ai_post 的 cap 上限）。
        # 实测唯一可用通道 dots 在 10 条/批（约 14K 字符 prompt）下需 137s，
        # 逼近 180s 会频繁误杀正常请求；240s 留出余量。
        response = self._call_api(system_prompt, user_prompt, timeout=240)
        return self._parse_response(response, items)

    def _build_system_prompt(self, macro_context):
        # 五维宏观框架注入 (read-macro 下沉, 数据缺失时静默降级)
        try:
            from macro_framework import MACRO_FIVE_DIM, build_macro_state_prompt
            five_dim = "\n\u3010\u4e94\u7ef4\u5b8f\u89c2\u6846\u67b6\u3011\n" + MACRO_FIVE_DIM + "\n\n"
            macro_state = build_macro_state_prompt()
            if macro_state:
                five_dim += "\u3010\u5b8f\u89c2\u5feb\u7167\u3011" + macro_state + "\n\n"
        except Exception:
            five_dim = ""
        # 用户三观注入 (worldview.yaml, 文件缺失时静默降级; 裁判链路不注入保持校准客观)
        try:
            from worldview_loader import build_worldview_prompt
            worldview = build_worldview_prompt()
            if worldview:
                five_dim += worldview + "\n\n"
        except Exception:
            pass
        return (
            "\u4f60\u662f\u4e3a\u300c\u5904\u4e8e\u7ed3\u6784\u6027\u8f6c\u6298\u671f\u7684\u5e74\u8f7b\u52b3\u52a8\u8005\u300d\u670d\u52a1\u7684\u53c2\u8c0b\u957f\u3002"
            + "\n\u7528\u6237\u753b\u50cf\uff1a" + self.persona + "\n\n"
            + "\u77e5\u8bc6\u5e93\u6838\u5fc3\u5b8f\u89c2\u6982\u5ff5\uff1a" + macro_context + "\n\n"
            + five_dim
            + "\u3010\u56db\u7ef4\u5206\u6790\u6846\u67b6\u3011\u5fc5\u987b\u4e25\u683c\u9075\u5b88\n"
            + "1. \u79ef\u7d2f\u5236\u5ea6\u89c6\u89d2: accumulation_node = \u751f\u4ea7/\u5b9e\u73b0/\u5206\u914d/\u518d\u751f\u4ea7\n"
            + "2. \u7a7a\u95f4\u4fee\u6b63\u89c6\u89d2: spatial_layer = \u4e2d\u5fc3/\u5916\u56f4/\u7279\u533a/\u90fd\u5e02\u5708\n"
            + "3. \u56fd\u5bb6-\u5e02\u573a\u8fb9\u754c\u89c6\u89d2: state_market_shift = \u56fd\u5bb6\u8fdb\u573a/\u5e02\u573a\u9000\u573a/\u8fb9\u754c\u6a21\u7cca/\u8bd5\u70b9\u5148\u884c\n"
            + "4. \u9636\u7ea7/\u5229\u76ca\u96c6\u56e2\u89c6\u89d2: class_interest\n\n"
            + "\u8f93\u51fa\u8981\u6c42: \u6bcf\u6761\u60c5\u62a5\u5fc5\u987b\u8f93\u51fa\u5b8c\u65748\u5b57\u6bb5JSON\n"
            + "\u53ea\u8f93\u51fa\u7eafJSON\u6570\u7ec4\uff0c\u4e0d\u8981\u5176\u4ed6\u5185\u5bb9\u3002"
        )

    def _call_api(self, system_prompt, user_prompt, timeout=180):
        """timeout 可按调用调整：推理型模型（输出 thinking 过程）在长 prompt 下
        180s 不够（实测 nemotron 40K 字符 prompt 连续超时），调用方传更大值。

        降级链（2026-09-17 定稿）：
          1. OpenCode（免费层已加客户端指纹校验，403；靠熔断器快速跳过）
          2. NVIDIA integrate（本地 key；gpt-oss-20b / glm-5.3）
          3. 小红书 dots（note3-prev-api.askdiandian.com；实测 1s 响应）
        OpenRouter 已移除：免费层日限 10 次请求，无实用价值（用户 2026-09-17 确认）。

        卡顿修复：
        1) 熔断器 _DEAD：403/404 标记 30 分钟、429 标记 3 分钟，后续跳过；
        2) 单次尝试限时 min(timeout, 240)s；
        3) 优先复用上次成功的通道。"""
        models = []
        # 小红书 dots 通道（2026-09-17 接入）：实测 1s 响应，比 NVIDIA 快一个数量级。
        # 放在**链首**——OpenCode 已全线 403、NVIDIA 端点故障时，前面每一个都是白等
        # （2026-09-17 实测：dots 排 NVIDIA 后面时，主笔仍要等 4×240s 才轮到，常超时失败）。
        dots_key = self._dots_key()
        if dots_key:
            models.append({"model": _dots_slug(self.model), "base_url": DOTS_BASE, "api_key": dots_key})
        models.append({"model": self.model, "base_url": self.base_url, "api_key": self.api_key})
        models.extend(self.fallback_models)
        nv_key = self._nvidia_key()
        if nv_key:
            # NVIDIA integrate 备援通道（本地有 NVIDIA key 时启用）
            nv_seen = set()
            for name in [self.model] + [m.get("model", "") for m in self.fallback_models]:
                slug = _nv_slug(str(name))
                if slug and slug not in nv_seen:
                    nv_seen.add(slug)
                    models.append({"model": slug, "base_url": NVIDIA_BASE, "api_key": nv_key})
        # 优先复用上次成功的通道（同进程内连续调用场景）
        if _LAST_GOOD[0]:
            models.sort(key=lambda m: 0 if (m.get("base_url"), m.get("model")) == _LAST_GOOD[0] else 1)
        last_err = None
        for i, m in enumerate(models):
            key_id = (m.get("base_url", ""), m.get("model", ""))
            if _is_dead(key_id):
                last_err = last_err or RuntimeError(f"endpoint circuit-open: {key_id[1]}")
                continue
            try:
                api_base = m.get("base_url", self.base_url).rstrip("/")
                key = m.get("api_key") or self.api_key
                url = api_base + "/chat/completions"
                body = {
                    "model": m["model"],
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": self.temperature,
                    # 2026-09-21：8192 太小——dots3 是推理模型，reasoning 计入配额
                    # （实测 10 条批次的 reasoning 7159 + 正文 6331 字符 ≈ 13K），
                    # 8192 会在数组中途截断，10 条只写出 7 条。
                    # 实测 dots 接受 65536，取 32768 留足余量（正文 + reasoning 双份）。
                    "max_tokens": self.max_tokens_budget,
                }
                # 推理型模型需限制思考量，否则陷入思考循环（content 永远为空）
                eff = _NV_REASONING_EFFORT.get(m["model"])
                if eff:
                    body["reasoning_effort"] = eff
                payload = json.dumps(body).encode("utf-8")
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + key,
                    "User-Agent": "opencode/latest/1.3.15/cli",
                    "x-opencode-client": "cli",
                    "x-opencode-session": uuid.uuid4().hex,
                    "x-opencode-project": uuid.uuid4().hex[:8],
                    "x-opencode-request": uuid.uuid4().hex,
                }
                if "askdiandian" in api_base:
                    # 小红书 dots 网关要求 api-key 头（Authorization 之外）
                    headers["api-key"] = key
                    headers.pop("x-opencode-client", None)
                # 单次尝试限时：防止某个卡住的端点把整轮预算烧光
                # （2026-09-17：cap 从 150s 提到 240s——NVIDIA glm 在 1800 字
                #  输入下实测需 43-200s，150s 会误杀正常请求）
                raw = _safe_ai_post(url, payload, headers, min(timeout, 240))
                result = json.loads(raw)
                if "choices" not in result:
                    # 有些网关在过载/拒答时返回非标准结构（error/message 字段），
                    # 原先直接 result["choices"] 抛 KeyError: 'choices' 丢失上下文
                    raise ValueError("unexpected response shape: " + raw[:300])
                msg = result["choices"][0]["message"]
                content = msg.get("content", "")
                if not content:
                    for k in ("reasoning_content", "reasoning", "output"):
                        val = msg.get(k, "")
                        if val:
                            content = val
                            break
                if content and content.strip():
                    # 推理型模型偶发把思维链当正文返回（nemotron 长生成实测：
                    # content 直接是 "Here's a thinking process:..."，正文被挤掉）
                    # 检测到即视为失败，走降级链换模型重试
                    head = content.lstrip()[:120].lower()
                    if head.startswith(("here's a thinking process", "here is a thinking process",
                                        "let me think", "thinking process:")):
                        raise ValueError("model returned reasoning trace instead of answer: "
                                         + content.lstrip()[:120])
                    _LAST_GOOD[0] = key_id
                    return content.strip()
                raise ValueError("empty response")
            except Exception as e:
                # 2026-09-21：HTTPError 的 str() 只有 "HTTP Error 403: Forbidden"，
                # 不含响应体——而 dots 的内容安全拦截信息（error_type=governance.
                # content_safety_input_rejected）只在响应体里。这里补读一次 body
                # 并合并进异常消息，使 _is_content_block 能区分"内容触发"与
                # "鉴权失败"（同为 403，处理方式完全相反：前者不熔断、拆半重试）。
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        detail = e.read().decode("utf-8", "replace")[:400]
                    except Exception:
                        detail = ""
                    if detail:
                        e = RuntimeError(f"HTTP {e.code} {e.reason} | {detail}")
                _mark_dead(key_id, e)
                # 内容安全拦截要**立即抛出**，不能继续降级链：
                # 这是 prompt 内容触发的，与通道健康无关——继续试只会白等
                # 其余通道的完整超时链（实测多花 8 分钟），而且 last_err 会被
                # 后面的超时覆盖，导致调用方拿到的错误信息丢失 content_safety
                # 标记，拆半重试逻辑因此失效（本次踩坑）。
                if self._is_content_block(e):
                    raise
                last_err = e
                print("[AI] model " + m["model"] + " failed: " + str(e)[:300])
                if i == len(models) - 1:
                    raise
        # 全部模型要么熔断跳过要么失败：必须抛错，不能静默返回 "[]"
        # （2026-09-17 修：熔断 continue 会让循环到不了 last index，异常被吞，
        #  裁判因此拿到空数组当"未发现问题"，属静默失败）
        raise last_err or RuntimeError("all models unavailable (circuit-open or failed)")

    def _parse_response(self, response, original_items):
        response = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", response)
        response = re.sub(r"```json\s*", "", response)
        response = re.sub(r"```\s*$", "", response)
        data = None
        start = response.find("[")
        if start >= 0:
            depth = 0; end = start
            for i in range(start, len(response)):
                if response[i] == "[": depth += 1
                elif response[i] == "]":
                    depth -= 1
                    if depth == 0: end = i; break
            json_str = response[start:end+1]
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                last_brace = json_str.rfind("}")
                if last_brace > 0:
                    truncated = json_str[:last_brace+1]
                    if not truncated.endswith("]"): truncated += "]"
                    try: data = json.loads(truncated)
                    except: pass
        if not data:
            try: data = json.loads(response)
            except: pass
        # 2026-09-21 新增：逐条抢救截断的 JSON 数组。
        # 背景：dots3 是推理模型，reasoning 计入 max_tokens（实测 10 条批次的
        # reasoning 达 7159 字符），真实输出常在数组中途被截断——实测 10 条只
        # 写出 7 条就断在半个字符串里，整体 json.loads 必失败，全部退化成占位结果。
        # 这里按 "{" 起、配对 "}" 止逐条提取，能救回已完整输出的那几条。
        if not data:
            salvaged = self._salvage_items(response)
            if salvaged:
                print(f"[AI] JSON 整体解析失败，逐条抢救出 {len(salvaged)} 条")
                data = salvaged
        if not data:
            print("[AI] JSON parse failed, using fallback")
            return [self._fallback_result(item, response) for item in original_items]
        results = []
        for d in data:
            try:
                r = AnalysisResult(
                    intel_id=d.get("intel_id", ""),
                    macro_diagnosis=self._norm_dict(d.get("macro_diagnosis")),
                    structural_implication=self._norm_text(d.get("structural_implication")),
                    personal_action_space=self._norm_dict(d.get("personal_action_space")),
                    knowledge_links=d.get("knowledge_links", []) if isinstance(d.get("knowledge_links", []), list) else [],
                    confidence=self._norm_conf(d.get("confidence", 5)),
                    contradictions=self._norm_text(d.get("contradictions")),
                    raw_reasoning=self._norm_text(d.get("raw_reasoning"))
                )
                results.append(r)
            except Exception as e:
                print("[AI] parse item failed: " + str(e))
        while len(results) < len(original_items):
            results.append(self._fallback_result(original_items[len(results)], response))
        return results

    @staticmethod
    def _norm_text(val):
        """把模型可能返回的 dict/list 压平成文本。

        2026-09-21：实测模型会把 structural_implication 按子字段输出成
        {"accumulation_node": "...", "spatial_layer": "..."}，而 schema 要求字符串。
        下游 render_*/policy_tracker 直接对其做字符串切片，遇 dict 会 KeyError。
        这里统一压平，保住内容不丢。
        """
        if val is None:
            return ""
        if isinstance(val, str):
            return val
        return MacroAnalyzer._flat(val)

    @staticmethod
    def _flat(v):
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            return "；".join(f"{k}: {MacroAnalyzer._flat(x)}" for k, x in v.items())
        if isinstance(v, list):
            return "、".join(MacroAnalyzer._flat(x) for x in v)
        return str(v)

    @staticmethod
    def _norm_dict(val):
        """确保是 dict（下游按 key 取值，模型偶尔返回字符串或列表）。

        2026-09-21：模型常把 macro_diagnosis 直接写成一句自然语言（而非四个子键），
        下游按 accumulation_node 等 key 取值会拿不到内容。这里保留原值的同时，
        按已知子键名做一次文本回填，避免信息丢失。
        """
        if isinstance(val, dict):
            return val
        text = MacroAnalyzer._flat(val) if val is not None else ""
        if not text:
            return {}
        out = {"summary": text}
        for key in ("accumulation_node", "spatial_layer", "state_market_shift", "class_interest"):
            if key in text:
                # 从 "key: 内容" 形式里切出该键的值（到下一个键名或串尾）
                m = re.search(re.escape(key) + r"[:：]\s*(.+?)(?=(?:accumulation_node|spatial_layer|state_market_shift|class_interest)[:：]|$)", text)
                if m:
                    out[key] = m.group(1).strip(" ；;")
        return out

    @staticmethod
    def _norm_conf(val):
        """置信度归一到 1-10 整数（模型可能给字符串或越界值）。

        2026-09-21：实测模型常漏给 confidence（或给 0/空），原先默认 5 但
        抢救路径里拿到的是 None → int(None) 失败 → 落到 5；此处显式兜底。
        """
        try:
            n = float(val)
        except (TypeError, ValueError):
            return 5
        if n <= 0:
            return 5
        if n <= 1:          # 模型给了 0~1 的比例值 → 映射到 1-10
            n *= 10
        return max(1, min(10, int(round(n))))

    @staticmethod
    def _salvage_items(response):
        """从截断的 JSON 数组里逐条提取完整对象。

        按括号配对扫描，遇到配平的 {...} 就尝试解析；截断在半个对象里时，
        该对象自然被丢弃，前面的完整对象全部保住。
        字符串内的花括号需跳过（用 in_string 状态机跟踪转义）。
        """
        items = []
        depth = 0
        obj_start = -1
        in_string = False
        escaped = False
        for idx, ch in enumerate(response):
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                if depth == 0:
                    obj_start = idx
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and obj_start >= 0:
                        chunk = response[obj_start:idx + 1]
                        try:
                            obj = json.loads(chunk)
                            if isinstance(obj, dict) and obj.get("intel_id"):
                                items.append(obj)
                        except json.JSONDecodeError:
                            pass
                        obj_start = -1
        return items

    def _fallback_result(self, item, raw):
        return AnalysisResult(
            intel_id=item.get("id", ""),
            macro_diagnosis={"accumulation_node":"\u672a\u8bc6\u522b","spatial_layer":"\u672a\u8bc6\u522b","state_market_shift":"\u672a\u8bc6\u522b","class_interest":"\u672a\u8bc6\u522b"},
            structural_implication="AI \u5206\u6790\u5931\u8d25\uff0c\u9700\u4eba\u5de5\u590d\u6838",
            personal_action_space={"window_months":12,"concrete_moves":[{"action":"\u4eba\u5de5\u590d\u6838\u6b64\u60c5\u62a5","rationale":"AI\u5f02\u5e38","risk":"\u53ef\u80fd\u9057\u6f0f\u5173\u952e\u4fe1\u53f7"}],"avoid_traps":["\u76f2\u76ee\u8ddf\u98ce"],"signals_to_watch":["\u540e\u7eed\u5b98\u65b9\u6587\u4ef6","\u884c\u4e1a\u6570\u636e\u53d8\u5316"]},
            knowledge_links=[], confidence=1,
            contradictions="AI\u5206\u6790\u5f02\u5e38",
            raw_reasoning=raw[:1000]
        )


def analyze_intel(config, persona, knowledge_base, items):
    macro_context = knowledge_base.get_macro_framework_context()
    analyzer = MacroAnalyzer(config, persona, knowledge_base)
    return analyzer.analyze_batch(items, macro_context)


if __name__ == "__main__":
    import sys, argparse
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--intel", required=True)
    parser.add_argument("--persona", default="persona.md")
    parser.add_argument("--vault", default=r"D:\\Codex\u8f93\u51fa\\\u89c6\u9891\u77e5\u8bc6\u5e93")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    with open(args.persona, "r", encoding="utf-8") as f:
        persona = f.read()
    sys.path.insert(0, str(Path(__file__).parent))
    from load_knowledge import load_knowledge
    kb = load_knowledge(args.vault)
    items = []
    with open(args.intel, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line: items.append(json.loads(line))
    print("Analyzing " + str(len(items)) + " items...")
    results = analyze_intel(config, persona, kb, items)
    Path(args.output).write_text(
        "\n".join(json.dumps(asdict(r), ensure_ascii=False) for r in results) + "\n",
        encoding="utf-8")
    print("Done: " + args.output)
