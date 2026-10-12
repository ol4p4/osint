# -*- coding: utf-8 -*-
"""
Hypothesis Engine - hypothesis generation, update, verification, reconciliation
Lifecycle: view -> decompose -> hypothesis card -> evidence update -> verify -> reconcile
"""

import json, os, re, time, yaml, uuid
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
import urllib.request, urllib.error

# ACH 段的运行护栏（2026-10-12，周循环挂死修复）
# 故障现场：JEV 断供后回退 mimo，每条要串完 dots→zen→opencode×3 的完整降级链，
# 单条最坏 585s（实测 opencode 直连 SSL 握手超时 31s+，_safe_ai_post 硬上限
# timeout+15=255s，×3 个变体）→ 30 条最坏 4.9 小时，进程被杀且日志因块缓冲全丢。
# 预算取 1200s（20 分钟）：**实测 mimo 路径 60.4s/条**（沙箱 3 条取样，
# 7 个 major 的判定 prompt 比研判大得多）→ 1200s 约消化 20 条，剩余 10 条
# 留给日常 ach_daily_batch。JEV 健康时 0.66s/条，30 条约 20s，预算形同虚设。
ACH_BUDGET_SECONDS = 1200
# 连续失败熔断阈值：与 probe_mega / analyze.CONSECUTIVE_FAIL_LIMIT 同款判据
ACH_CONSECUTIVE_FAIL_LIMIT = 3


@dataclass
class SubProposition:
    id: str
    claim: str
    indicator: str
    data_source: str
    threshold_support: str
    threshold_refute: str


@dataclass
class Hypothesis:
    id: str
    view_id: str
    title: str
    core_claim: str
    sub_propositions: List[Dict]
    confidence: float
    direction: str
    magnitude: str
    time_horizon_months: int
    created: str
    due_date: str
    status: str
    evidence_log: List[Dict] = field(default_factory=list)
    last_verified: str = ""
    verification_result: str = ""
    reconciliation_notes: str = ""

    def to_markdown(self) -> str:
        lines = [
            f"# {self.title}",
            "",
            f"**Status**: {self.status} | **Confidence**: {self.confidence:.0%} | **Due**: {self.due_date}",
            "",
            "## Core Claim",
            self.core_claim,
            "",
            f"## Direction: {self.direction} | Magnitude: {self.magnitude}",
            "",
            "## Sub-Propositions",
        ]
        for sp in self.sub_propositions:
            lines.append(f"- **{sp.get('indicator', '?')}**: {sp.get('claim', '?')}")
        if self.evidence_log:
            lines.extend(["", "## Evidence Log"])
            for ev in self.evidence_log[-5:]:
                lines.append(f"- [{ev.get('date', '?')}] {ev.get('summary', '?')}")
        return "\n".join(lines)

class HypothesisEngine:
    def __init__(self, config, kb, analyzer):
        self.config = config
        self.kb = kb
        self.analyzer = analyzer
        paths = config.get("paths", {})
        self.output_dir = Path(paths.get("output_dir", r"D:\osint\data"))
        self.hyp_dir = self.output_dir / "hypotheses"
        self.hyp_dir.mkdir(parents=True, exist_ok=True)
        self.hyp_file = self.hyp_dir / "active_hypotheses.json"
        self.hyp_history = self.hyp_dir / "hypothesis_history.jsonl"

    def load_views(self, views_path):
        return yaml.safe_load(Path(views_path).read_text(encoding="utf-8"))

    def load_active_hypotheses(self):
        if self.hyp_file.exists():
            return json.loads(self.hyp_file.read_text(encoding="utf-8"))
        return []

    def save_active_hypotheses(self, hyps):
        self.hyp_file.write_text(json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")

    def generate_hypotheses_from_views(self, views):
        existing = self.load_active_hypotheses()
        existing_view_ids = {h.get("view_id") for h in existing}
        new_hyps = []
        for view in views:
            if view["id"] in existing_view_ids:
                continue
            hyps = self._decompose_view(view)
            new_hyps.extend(hyps)
        if new_hyps:
            existing.extend(new_hyps)
            self.save_active_hypotheses(existing)
            print(f"[ENGINE] Generated {len(new_hyps)} hypotheses from {len(views)} views")
        return existing

    def _decompose_view(self, view):
        """view → 2-4 个可验证子命题。

        ⚠️ 2026-09-21 修复**时间基准缺陷**：原 prompt 未告知模型"今天是几号"，
        AI 只能凭训练数据的时间感写年份，实测产出的证伪判据全部指向 2023-2025
        （75 个节点里 51 个即 68% 已过期），与代码计算的 due_date（2027-2028）
        完全脱节——验证时按判据走永远已过期，按 due_date 走判据内容对不上时间。
        修复：把当前日期与目标验证窗口显式写进 prompt。
        """
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        horizon_months = view.get("time_horizon_months", 12)
        due = (now + timedelta(days=horizon_months * 30)).strftime("%Y-%m-%d")
        due_year = (now + timedelta(days=horizon_months * 30)).year
        prompt = f"""Decompose this view into 2-4 verifiable sub-propositions.

**Current date: {today}** (year {now.year})
**Verification deadline: {due}** — all thresholds must refer to this window.

View: {view["title"]}
Core claim: {view["core_claim"]}
Dimensions: {", ".join(view.get("key_dimensions", []))}

Output JSON array, each: {{"claim":"...","indicator":"measurable metric","data_source":"where to check","threshold_support":"what supports it","threshold_refute":"what refutes it"}}

CRITICAL requirements for thresholds:
- Every threshold MUST be checkable between {today} and {due}. Never use dates before {now.year}.
- State the year explicitly in each threshold, e.g. "by {due_year}, X exceeds Y".
- Use concrete numbers with units and a named data source.
- Do NOT invent statistics; if a baseline value is unknown, phrase the threshold relatively
  (e.g. "grows more than 20% from {now.year} level") instead of citing a fabricated figure."""
        system = ("You are a political economy analyst. Output JSON only. "
                  "You are working in the year " + str(now.year) +
                  "; never propose thresholds dated before that.")
        try:
            response = self.analyzer._call_api(system, prompt)
            sps = self._parse_json_array(response)
        except Exception as e:
            print(f"[ENGINE] Decompose failed: {e}")
            sps = []

        # 时间闸门（2026-09-21）：证伪判据里的年份若早于今年，说明模型又凭训练数据
        # 猜了过期日期——这类判据在验证时永远"已过期"，等于没有证伪能力。
        # 不静默接受：记录告警，并保留供人工复核（宁可标注也不要假的可证伪性）。
        stale = _stale_years(sps, now.year)
        if stale:
            print(f"[ENGINE] ⚠️ 时间闸门：{view['title'][:24]} 的判据含过期年份 {stale}"
                  f"（今年 {now.year}）——已记录 stale_thresholds 待复核")

        hyp = {
            "id": "hyp_" + uuid.uuid4().hex[:8],
            "view_id": view["id"],
            "title": view["title"],
            "core_claim": view["core_claim"],
            "sub_propositions": sps,
            "confidence": view.get("confidence_initial", 0.5),
            "direction": "toward",
            "magnitude": "pending analysis",
            "time_horizon_months": view.get("time_horizon_months", 12),
            "created": today,
            "due_date": due,
            "status": "active",
            "evidence_log": [],
            "last_verified": "",
            "verification_result": "",
            "reconciliation_notes": "",
            # 生成时即从子命题的反驳阈值回填，验证闭环和知识库页面立即可用
            "falsification_criteria": "；".join(
                sp.get("threshold_refute", "") for sp in sps
                if isinstance(sp, dict) and sp.get("threshold_refute"))[:300],
        }
        if stale:
            hyp["stale_thresholds"] = stale
        self._save_hypothesis_markdown(hyp)
        return [hyp]

    def _parse_json_array(self, text):
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*$", "", text)
        start = text.find("[")
        if start >= 0:
            depth = 0; end = start
            for i in range(start, len(text)):
                if text[i] == "[": depth += 1
                elif text[i] == "]":
                    depth -= 1
                    if depth == 0: end = i; break
            return json.loads(text[start:end+1])
        return json.loads(text)

    @staticmethod
    def _stale_years(sps, current_year):
        """扫描子命题里的年份，返回早于今年的年份列表（去重升序）。

        用途：生成时的时间闸门。AI 未被告知当前日期时会凭训练数据写 2023-2025，
        这类判据在验证时永远"已过期"，等于没有证伪能力（实测存量 68% 中招）。
        返回非空即告警——宁可显式标注，也不要假的可证伪性。
        """
        found = set()
        for sp in sps:
            if not isinstance(sp, dict):
                continue
            for v in sp.values():
                # 不用 \b：中文语境下「到2024年底」的年份两侧是汉字，
                # \b 依赖的 word boundary 不成立，会整条漏掉（实测踩坑）
                for y in re.findall(r"(20[1-9]\d)", str(v)):
                    if int(y) < current_year:
                        found.add(int(y))
        return sorted(found)

    def _save_hypothesis_markdown(self, hyp):
        md_dir = self.output_dir / "wiki" / "hypotheses"
        md_dir.mkdir(parents=True, exist_ok=True)
        md_path = md_dir / (hyp["id"] + ".md")
        lines = [
            "# " + hyp.get("title", "?"),
            "",
            "Status: " + hyp.get("status", "?") + " | Confidence: " + str(hyp.get("confidence", "?"))
            + " | Due: " + (hyp.get("due_date") or hyp.get("deadline") or "-"),
            "",
            "## Core Claim",
            hyp.get("core_claim") or hyp.get("rationale") or "",
            "",
            "## Sub-Propositions",
        ]
        for sp in hyp.get("sub_propositions", []):
            c = sp.get("claim", "?")
            ind = sp.get("indicator", "?")
            lines.append("- " + c)
            lines.append("  - Indicator: " + ind)
            lines.append("  - Support: " + sp.get("threshold_support", "?"))
            lines.append("  - Refute: " + sp.get("threshold_refute", "?"))
        md_path.write_text("\n".join(lines), encoding="utf-8")
        # 知识库双向链接：同步写入 视频知识库 wiki/hypotheses + index.md + log.md
        try:
            from kb_linker import link_hypothesis_to_kb
            link_hypothesis_to_kb(hyp)
        except Exception as e:
            print("[ENGINE] kb link failed: " + str(e))
    def update_evidence(self, hyp_id, evidence_entry):
        hyps = self.load_active_hypotheses()
        for h in hyps:
            if h["id"] == hyp_id:
                h["evidence_log"].append(evidence_entry)
                self.save_active_hypotheses(hyps)
                return True
        return False

    def get_due_hypotheses(self):
        """到期假设：engine 节点用 due_date，假设树节点用 deadline；空期限不参与"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hyps = self.load_active_hypotheses()
        out = []
        for h in hyps:
            if h.get("status") != "active":
                continue
            due = h.get("due_date") or h.get("deadline") or ""
            if due and due <= today:
                out.append(h)
        return out

    def get_all_active(self):
        hyps = self.load_active_hypotheses()
        return [h for h in hyps if h["status"] == "active"]



    def verify_hypothesis(self, hyp, ai_analyzer):
        claim = hyp.get("core_claim") or hyp.get("rationale") or hyp.get("title", "")
        ev_text = "\n".join([e.get("summary","") for e in hyp.get("evidence_log",[])])
        # P0-2: 把 verify_hypotheses.py 抓到的指标当前值+阈值喂给裁判，
        # 让叙述式阈值（needs_ai）也能对照真实数值判定
        ind_lines = []
        for ind in (hyp.get("indicators") or []):
            if isinstance(ind, dict) and ind.get("name"):
                cur = ind.get("current_value")
                ind_lines.append("- " + ind["name"] + ": 当前值=" + (str(cur) if cur is not None else "无数据")
                                 + " | 支持阈值=" + (ind.get("threshold_support") or "-")
                                 + " | 证伪阈值=" + (ind.get("threshold_refute") or "-"))
        prompt = ("Verify this hypothesis: " + hyp.get("title","")
                  + "\nClaim: " + claim
                  + "\nConfidence: " + str(hyp.get("confidence",0.5))
                  + "\nEvidence:\n" + ev_text[:2000]
                  + ("\n\nIndicator readings (compare values against thresholds where given):\n"
                     + "\n".join(ind_lines) if ind_lines else "")
                  + "\n\nScore this hypothesis 1-20 (integer): 1-6 = contradicted by evidence (refuted),"
                    " 7-12 = cannot judge yet (inconclusive), 13-16 = partially supported, 17-20 = strongly supported."
                    " Be critical; verification is about falsification, not confirmation."
                  + "\n\nOutput JSON: {score: 1-20, verdict: supported/partially_supported/refuted/inconclusive, new_confidence: 0-1, reasoning: ...}")
        system = "You are the verification judge. Be objective and critical."
        try:
            response = ai_analyzer._call_api(system, prompt)
            import re as _re
            response = _re.sub(r"```json\s*", "", response)
            response = _re.sub(r"```\s*$", "", response)
            start = response.find("{")
            if start >= 0:
                depth = 0; end = start
                for i in range(start, len(response)):
                    if response[i] == "{": depth += 1
                    elif response[i] == '}':
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                result = json.loads(response[start:end+1])
            else:
                result = json.loads(response)
        except Exception as e:
            print("[ENGINE] Verify failed: " + str(e))
            result = {"verdict": "inconclusive", "new_confidence": hyp.get("confidence", 0.5), "reasoning": str(e)}
        # P1-2（学 LLM-as-a-Verifier）：1-20 连续分确定性映射 verdict，消除离散档平局问题；
        # score 缺失/非法时尊重模型自报 verdict（向后兼容）
        score = result.get("score")
        try:
            score = max(1, min(20, int(score)))
        except (TypeError, ValueError):
            score = None
        if score is not None:
            result["verdict"] = ("supported" if score >= 17 else
                                 "partially_supported" if score >= 13 else
                                 "inconclusive" if score >= 7 else "refuted")
        hyp["verify_score"] = score
        hyp["confidence"] = result.get("new_confidence", hyp["confidence"])
        hyp["verification_result"] = result.get("verdict", "inconclusive")
        hyp["last_verified"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hyp["reconciliation_notes"] = result.get("reasoning", "")
        # 验证闭环：refuted → falsified（其余 verdict 保持 active 等待更多证据）
        if result.get("verdict") == "refuted":
            hyp["status"] = "falsified"
        # falsification_criteria 空缺时从指标/子命题的反驳阈值回填
        if not hyp.get("falsification_criteria"):
            refutes = []
            for ind in hyp.get("indicators", []) or []:
                if isinstance(ind, dict) and ind.get("threshold_refute"):
                    refutes.append(str(ind["threshold_refute"]))
            for sp in hyp.get("sub_propositions", []) or []:
                if isinstance(sp, dict) and sp.get("threshold_refute"):
                    refutes.append(str(sp["threshold_refute"]))
            if refutes:
                hyp["falsification_criteria"] = "；".join(refutes[:3])
        return hyp

    def record_resolution(self, hyp, pre_conf):
        """P0-1 校准前置：到期假设的验证结果落 resolutions.jsonl（按 hyp_id 幂等追加）。
        confidence_at_deadline 记录验证前的置信度（系统持有的'预测'），供 calibration.py 算 Brier。"""
        res_file = self.output_dir / "hypotheses" / "resolutions.jsonl"
        res_file.parent.mkdir(parents=True, exist_ok=True)
        seen = set()
        if res_file.exists():
            for line in res_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        seen.add(json.loads(line).get("hyp_id"))
                    except Exception:
                        pass
        if hyp.get("id") in seen:
            return
        verdict = hyp.get("verification_result", "inconclusive")
        outcome = {"supported": "confirmed", "partially_supported": "partial",
                   "refuted": "refuted"}.get(verdict, "inconclusive")
        try:
            pre_conf = round(float(pre_conf or 0.5), 3)
        except (TypeError, ValueError):
            pre_conf = 0.5
        entry = {
            "hyp_id": hyp.get("id"), "title": hyp.get("title", ""), "level": hyp.get("level"),
            "resolved_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "deadline": hyp.get("due_date") or hyp.get("deadline") or "",
            "outcome": outcome, "verdict": verdict,
            "confidence_at_deadline": pre_conf,
            "confidence_after": hyp.get("confidence", 0.5),
            "verify_score": hyp.get("verify_score"),
            "judged_by": "ai_referee",
            "reasoning": (hyp.get("reconciliation_notes") or "")[:200],
        }
        with res_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        hyp["resolved_at"] = entry["resolved_at"]  # 节点标记：到期过滤据此跳过（幂等）
        print("[ENGINE] Resolution recorded: " + str(hyp.get("id")) + " -> " + outcome)

    def _ai_channel_alive(self, timeout=60):
        """AI 通道预检（2026-10-12，周循环挂死修复）：真发一次最小请求。

        动机：JEV 不可用时，ACH 段只能靠 analyzer（mimo）。若它也是死的，
        30 条证据会**逐条**白撞完整降级链（单条最坏 585s）——与其让每条各撞
        一次，不如先花 1 次调用确认通道，不通就整段跳过（下轮重试，日常增量
        由 ach_daily_batch 消化）。这与 AGENTS.md「available 这类能力探针必须
        实探，只看配置会把"配置了但死了"判成可用」是同一条判据。

        返回 True/False，任何异常都视为不可用（预检本身不该抛错）。
        """
        try:
            out = self.analyzer._call_api("你是一个测试助手。", "回答：ok",
                                          timeout=timeout)
            ok = bool(out and str(out).strip())
            print("[ACH] AI 通道预检: " + ("可用" if ok else "返回空"))
            return ok
        except Exception as ex:
            print("[ACH] AI 通道预检失败: " + str(ex)[:120])
            return False

    def run_weekly_cycle(self, intel_items=None):
        """每周循环：补生成缺失的 view 假设 → 验证到期假设 → 保存 → 周报。
        证据追加由 link_intel_hyp.py 每日负责，这里不再写无信息量的汇总证据。"""
        print("[ENGINE] === Weekly Hypothesis Cycle ===")
        hyps = self.load_active_hypotheses()
        # 1) 补齐未物化的 view（view_id 或 materialized_hyp_id 已存在则跳过，避免重复生成）
        try:
            views = self.load_views(r"D:\osint\views.yaml") or []
        except FileNotFoundError:
            print("[ENGINE] views.yaml not found, skip generation")
            views = []
        existing_view_ids = {h.get("view_id") for h in hyps if h.get("view_id")}
        existing_ids = {h.get("id") for h in hyps}
        new_hyps = []
        for view in views:
            if view.get("id") in existing_view_ids:
                continue
            if view.get("materialized_hyp_id") and view["materialized_hyp_id"] in existing_ids:
                continue
            new_hyps.extend(self._decompose_view(view))
        if new_hyps:
            hyps.extend(new_hyps)
            print(f"[ENGINE] Generated {len(new_hyps)} hypotheses from views")
        # 2') ACH 竞争性假设矩阵（PLAN-2 M1）：证据诊断 + 贝叶斯置信度更新
        try:
            majors = [h for h in hyps if h.get("level") == "major"]
            if majors:
                from ach_matrix import ACHMatrix
                ach = ACHMatrix(self.output_dir / "hypotheses" / "ach_matrix.json", majors)
                undiag = ach.find_undiagnosed()
                # 2026-09-21: 优先走 JEV 决策模型（0.66s/条 vs mimo 45s/条），失败回退
                jev = None
                try:
                    from jev_client import JevClient
                    _j = JevClient(caller="weekly_cycle")
                    if _j.available:
                        jev = _j
                        print("[ACH] 决策层: " + _j.describe())
                except Exception as _ex:
                    print("[ACH] JEV 不可用: " + str(_ex)[:80])
                if undiag:
                    # 批上限（2026-10-10）：原先无上限，积压 120 条 × mimo 53s/条
                    # ≈ 106 分钟，周循环被 ACH 拖死（其余步骤全被推迟）。日常增量
                    # 由 refresh 每轮的 ach_daily_batch（20h 节流）负责，这里只做
                    # 周度收尾：上限 30 条 ≈ 27 分钟，其余留给日常批消化。
                    undiag = undiag[:30]
                    # 2026-10-12 三重护栏（本轮挂死修复）——
                    # 故障现场：JEV 451 断供 → 回退 mimo → 每条要串完
                    # dots→zen→opencode×3 的完整降级链，单条最坏 585s（实测
                    # opencode 直连 SSL 握手超时 31s+，_safe_ai_post 硬上限
                    # timeout+15=255s ×3 变体），30 条 = 最坏 4.9 小时。进程最终
                    # 被杀，且 stdout 块缓冲导致日志全丢（已在 daily_run.ps1 用
                    # PYTHONUNBUFFERED 修），表现为"周循环静默挂死"。
                    # 判据（AGENTS.md）：AI 分析量必须与通道吞吐匹配——
                    # 条数 ÷ 吞吐 × 单条耗时 先算一遍再上线。
                    budget_deadline = time.time() + ACH_BUDGET_SECONDS
                    consec_fail = 0
                    done = 0
                    # ① 通道预检：先花 1 次调用确认通道真的能用，别让 30 条各自白撞。
                    #    JEV 不可用时 analyzer（mimo）是唯一指望，它挂了就整段跳过。
                    if jev is None and not self._ai_channel_alive():
                        print("[ACH] AI 通道预检失败，本轮跳过 ACH（下轮重试；"
                              "日常增量由 ach_daily_batch 消化）")
                        undiag = []
                    if undiag:
                        print(f"[ACH] 诊断 {len(undiag)} 条证据 × {len(majors)} 个 major 假设"
                              f"（周度上限 30，预算 {ACH_BUDGET_SECONDS}s，积压由日常批消化）")
                    for e in undiag:
                        # ② 墙钟预算：超时即停，已诊断的照常落盘
                        if time.time() > budget_deadline:
                            print("[ACH] 预算用尽，剩余 %d 条下轮继续"
                                  % (len(undiag) - done))
                            break
                        try:
                            diag = ach.ai_diagnose(e, self.analyzer, jev=jev)
                            ach.record(e, diag, model=("jev" if jev is not None else "mimo"))
                            done += 1
                            consec_fail = 0
                        except Exception as ex:
                            if jev is not None:
                                try:
                                    diag = ach.ai_diagnose(e, self.analyzer, jev=None)
                                    ach.record(e, diag, model="mimo")
                                    done += 1
                                    consec_fail = 0
                                    print("[ACH] JEV 失败已回退 mimo: " + str(ex)[:70])
                                    continue
                                except Exception as ex2:
                                    ex = ex2
                            consec_fail += 1
                            print("[ACH] diagnose failed（连续 %d/%d）: %s"
                                  % (consec_fail, ACH_CONSECUTIVE_FAIL_LIMIT, str(ex)[:100]))
                            # ③ 连续失败熔断：与 probe_mega / analyze 同款判据。
                            #    单条失败可能是内容问题，连续多条说明通道整体不可用，
                            #    继续跑只是把剩余条目逐个喂给死链白等。
                            if consec_fail >= ACH_CONSECUTIVE_FAIL_LIMIT:
                                print("[ACH] 连续 %d 条失败，判定通道不可用，提前退出"
                                      "（已诊断 %d 条已保留，剩余下轮）"
                                      % (consec_fail, done))
                                break
                    if done:
                        ach.bayesian_update(hyps)
                        ach.sensitivity_analysis()  # P0-4：逐条证据中性化看排名稳定性
                        ach.export_markdown(self.output_dir)
                ach.save()
        except Exception as e:
            print("[ENGINE] ACH matrix failed: " + str(e))

        # 2) 验证到期假设（engine 节点用 due_date，树节点用 deadline；空期限不验证）
        # 2026-09-10 幂等修复：resolutions.jsonl 里已有记录的节点不再重判——
        # 此前一个到期节点会每周被 AI 重判一次并覆盖 confidence_at_deadline 存档的预测
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        resolved_ids = set()
        res_file = self.output_dir / "hypotheses" / "resolutions.jsonl"
        if res_file.exists():
            for line in res_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        resolved_ids.add(json.loads(line).get("hyp_id"))
                    except Exception:
                        pass
        due = []
        for h in hyps:
            if h.get("status") != "active":
                continue
            if h.get("id") in resolved_ids:
                continue  # 已 resolve：结果已存档，不再重判
            due_date = h.get("due_date") or h.get("deadline") or ""
            if due_date and due_date <= today:
                due.append(h)
        if due:
            print(f"[ENGINE] Due for verification: {len(due)}")
            for h in due:
                pre_conf = h.get("confidence", 0.5)  # 验证前置信度 = 校准用的'预测'
                self.verify_hypothesis(h, self.analyzer)
                self.record_resolution(h, pre_conf)
                print("  " + h.get("title", "?") + ": " + str(h.get("verification_result", "")))
        # 3) 保存同一个列表（修复原版保存错误对象导致验证结果不落盘的 bug）
        self.save_active_hypotheses(hyps)
        active = [h for h in hyps if h.get("status") == "active"]
        self._save_weekly_report(active)
        print(f"[ENGINE] Active hypotheses: {len(active)}")

        # 4) 周日或显式 weekly-summary 触发 AI 周报浓缩
        ach_matrix_path = self.output_dir / "hypotheses" / "ach_matrix.json"
        ach_data = None
        if ach_matrix_path.exists():
            try:
                import json as _json
                ach_data = _json.loads(ach_matrix_path.read_text(encoding="utf-8"))
            except Exception:
                ach_data = None
        # 2026-09-12 修复：删除此处局部 `from datetime import datetime, timezone`——
        # 函数内 import 使 datetime 成为局部变量，导致 377 行先访问时抛 UnboundLocalError，
        # 周循环每次必死在验证到期假设之前（8-31 后引入，9-07/9-14 周报缺失的真正根因之一）。
        # 全局第 10 行已有同名 import，此处直接使用。
        today = datetime.now(timezone.utc)
        is_monday = today.weekday() == 0  # 0=Mon
        # 环境变量 OSINT_WEEKLY=1 强制出周报（不强制也行，因为周一就够）
        if is_monday:
            try:
                self._save_ai_weekly_summary(hyps, ach_matrix=ach_data, intel_items=intel_items, week_offset=0)
            except Exception as e:
                print("[ENGINE] AI weekly summary step failed: " + str(e))

        return active

    def _save_weekly_report(self, active_hyps):
        report_dir = self.output_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        report_path = report_dir / ("hypothesis_weekly_" + date_str + ".md")
        lines = ["# Hypothesis Weekly Report - " + date_str, ""]
        for h in active_hyps:
            due = h.get("due_date") or h.get("deadline") or "-"
            conf = h.get("confidence", 0)
            try:
                conf_str = str(round(float(conf), 2))
            except (TypeError, ValueError):
                conf_str = str(conf)
            lines.append("- " + h.get("title", "?") + " | confidence: " + conf_str
                         + " | due: " + due
                         + " | last_verified: " + (h.get("last_verified") or "-"))
        lines.extend(["", "---", "Generated by Hypothesis Engine"])
        report_path.write_text("\n".join(lines), encoding="utf-8")
        print("[ENGINE] Weekly report: " + str(report_path))

    def _save_ai_weekly_summary(self, hyps, ach_matrix=None, intel_items=None, week_offset=0):
        """AI 浓缩的每周总结。在周一或显式调用时执行。

        week_offset=0: 上一个**完整**周（上周一 00:00 ~ 上周日 23:59:59）
        week_offset=N: 再往前推 N 周

        周报结构：
        1. 本周大事（情报关键词，按主题分组）
        2. ACH 假设变化（排名 + 验证动态）
        3. 对个人（年轻失业毕业生）的影响
        4. 下周预判（3-5 个具体观察点）

        2026-10-05 修：原 week_offset=0 取"本周一至今"，而周报在**周一生成**，
        该窗口只有几小时数据（实测 10-05 只统计到 159 条，AI 因此误判"数据严重
        不足，仅 7 条可用"；而上一完整周实际有 12102 条）。标题/正文本就写
        "上周情报总览"，窗口必须与语义一致 → 改为上一个完整周。
        """
        from datetime import datetime, timezone
        # 2026-10-12 重构：取材/组 prompt/渲染抽到 local/weekly_report.py，
        # 与云端 cloud/weekly_report_cloud.py 共用同一套（配额取样、反幻觉护栏、
        # 内容安全降级都在那边）。本方法只负责「本地的 AI 调用 + 落盘」，
        # 避免两份 prompt 各自漂移。行为等价性已用同周数据 A/B 对照验证。
        try:
            import weekly_report as _wr          # 与调用方同目录（local/ 已在 sys.path）
        except ImportError:
            from local import weekly_report as _wr

        report_dir = self.output_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)

        ctx, system, prompt = _wr.build_weekly_context(
            hyps, ach_matrix=ach_matrix, intel_items=intel_items, week_offset=week_offset)
        date_str = ctx["date_str"]

        degraded = False
        try:
            summary = self.analyzer._call_api(system, prompt)
        except Exception as e:
            # 2026-10-08：内容安全拦截的降级（补录 09-07 周报时实测踩到）。
            # dots 网关对 prompt 做内容审查，而周报 prompt 会把 60 条情报标题
            # 原样塞进去——实测 08-31~09-06 那周有 145 条涉政条目（领导人活动等），
            # 整体送审被 403 `governance.content_safety_input_rejected`，
            # **整份周报的 AI 总结因此退化成占位文本**。
            # 与 analyze._analyze_single_batch 的拆半隔离同源问题，但周报 prompt
            # 是一个整体（不是可拆的条目列表），所以改用**降级重试**：
            # 去掉逐条标题（保留统计/ACH/假设动态），只泄露聚合信息，实测可通过。
            is_block = False
            try:
                is_block = MacroAnalyzer._is_content_block(e)
            except Exception:
                is_block = ("content_safety" in str(e).lower()
                            or "safety system" in str(e).lower())
            if is_block:
                print("[ENGINE] AI weekly summary 被内容安全拦截，降级为无逐条标题版重试")
                try:
                    summary = self.analyzer._call_api(system, _wr.safe_prompt_for(ctx))
                    degraded = True
                except Exception as e2:
                    print("[ENGINE] AI weekly summary 降级重试仍失败: " + str(e2)[:160])
                    summary = "（AI 周报生成失败，请看 hypothesis_weekly_*.md 了解活跃假设列表）"
            else:
                print("[ENGINE] AI weekly summary failed: " + str(e))
                summary = "（AI 周报生成失败，请看 hypothesis_weekly_*.md 了解活跃假设列表）"

        body = _wr.render_weekly_markdown(
            ctx, summary, degraded=degraded,
            generator="HypothesisEngine._save_ai_weekly_summary")
        summary_path = report_dir / ("weekly_" + date_str + ".md")
        summary_path.write_text(body, encoding="utf-8")
        print("[ENGINE] AI weekly summary: " + str(summary_path))
        return summary_path
