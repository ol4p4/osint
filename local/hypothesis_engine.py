# -*- coding: utf-8 -*-
"""
Hypothesis Engine - hypothesis generation, update, verification, reconciliation
Lifecycle: view -> decompose -> hypothesis card -> evidence update -> verify -> reconcile
"""

import json, os, re, yaml, uuid
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
import urllib.request, urllib.error


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
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        due = (datetime.now(timezone.utc) + timedelta(days=view.get("time_horizon_months", 12) * 30)).strftime("%Y-%m-%d")
        prompt = f"""Decompose this view into 2-4 verifiable sub-propositions.
View: {view["title"]}
Core claim: {view["core_claim"]}
Dimensions: {", ".join(view.get("key_dimensions", []))}
Output JSON array, each: {{"claim":"...","indicator":"measurable metric","data_source":"where to check","threshold_support":"what supports it","threshold_refute":"what refutes it"}}"""
        system = "You are a political economy analyst. Output JSON only."
        try:
            response = self.analyzer._call_api(system, prompt)
            sps = self._parse_json_array(response)
        except Exception as e:
            print(f"[ENGINE] Decompose failed: {e}")
            sps = []
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
        prompt = ("Verify this hypothesis: " + hyp.get("title","")
                  + "\nClaim: " + claim
                  + "\nConfidence: " + str(hyp.get("confidence",0.5))
                  + "\nEvidence:\n" + ev_text[:2000]
                  + "\n\nOutput JSON: {verdict: supported/partially_supported/refuted/inconclusive, new_confidence: 0-1, reasoning: ...}")
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
                if undiag:
                    print(f"[ACH] 诊断 {len(undiag)} 条证据 × {len(majors)} 个 major 假设")
                    for e in undiag:
                        try:
                            diag = ach.ai_diagnose(e, self.analyzer)
                            ach.record(e, diag)
                        except Exception as ex:
                            print("[ACH] diagnose failed: " + str(ex))
                    ach.bayesian_update(hyps)
                    ach.export_markdown(self.output_dir)
                ach.save()
        except Exception as e:
            print("[ENGINE] ACH matrix failed: " + str(e))

        # 2) 验证到期假设（engine 节点用 due_date，树节点用 deadline；空期限不验证）
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        due = []
        for h in hyps:
            if h.get("status") != "active":
                continue
            due_date = h.get("due_date") or h.get("deadline") or ""
            if due_date and due_date <= today:
                due.append(h)
        if due:
            print(f"[ENGINE] Due for verification: {len(due)}")
            for h in due:
                self.verify_hypothesis(h, self.analyzer)
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
        from datetime import datetime, timezone
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

        week_offset=0: 本周一到今天的窗口（用于周一生成复盘整周）
        week_offset=1: 上一周（周一到周日）

        周报结构：
        1. 本周大事（情报关键词，按主题分组）
        2. ACH 假设变化（排名 + 验证动态）
        3. 对个人（年轻失业毕业生）的影响
        4. 下周预判（3-5 个具体观察点）
        """
        from datetime import datetime, timezone, timedelta
        report_dir = self.output_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")

        # 计算"上周"窗口（周一 00:00 到周日 23:59 UTC）
        # 用本地时间更符合用户视角（假设用户在 UTC+8）
        today_local = datetime.now()
        # 本周一 0 点
        this_monday = today_local - timedelta(days=today_local.weekday())
        if week_offset == 0:
            week_start = this_monday.replace(hour=0, minute=0, second=0, microsecond=0)
            week_end = today_local
        else:
            last_monday = this_monday - timedelta(days=7)
            week_end_local = this_monday - timedelta(seconds=1)
            week_start = last_monday
            week_end = week_end_local
        week_start_str = week_start.strftime("%Y-%m-%d")
        week_end_str = week_end.strftime("%Y-%m-%d")
        # 标题里的"周 N"标记
        week_label = "本周（%s ~ %s）" % (week_start.strftime("%m-%d"), week_end.strftime("%m-%d"))

        # 过滤上周情报
        week_intel = []
        if intel_items:
            for it in intel_items:
                pub = (it.get("published_at") or "")[:10]
                if week_start_str <= pub <= week_end_str:
                    week_intel.append(it)
        week_intel.sort(key=lambda x: x.get("published_at", ""), reverse=True)

        # 收集假设动态
        active = [h for h in hyps if h.get("status") == "active"]
        verified = [h for h in hyps if h.get("last_verified")
                    and (h.get("last_verified") or "")[:10] >= week_start_str]
        falsified = [h for h in hyps if h.get("status") == "falsified"]
        new_hyps = [h for h in hyps
                    if h.get("created_at") and h["created_at"][:10] >= week_start_str]

        # 按主题分类上周情报
        # 优先看四维分析字段 dims/impact
        themes = {
            "经济/金融/就业": [],
            "外交/地缘/贸易": [],
            "科技/AI/产业": [],
            "社会/民生/教育": [],
            "其他": [],
        }
        for i in week_intel[:30]:
            t = i.get("cn_title") or i.get("title") or ""
            cat = (i.get("category_cn") or i.get("category") or "").lower()
            impact = i.get("graduate_impact") or i.get("impact") or ""
            score = i.get("relevance") or 0
            # 简易分类
            if any(k in cat for k in ["finance", "econ", "market", "就业", "失业"]):
                themes["经济/金融/就业"].append((score, t[:60], impact[:50]))
            elif any(k in cat for k in ["polit", "world", "diplom", "geop", "trade", "war"]):
                themes["外交/地缘/贸易"].append((score, t[:60], impact[:50]))
            elif any(k in cat for k in ["tech", "ai", "产业", "industry", "energy"]):
                themes["科技/AI/产业"].append((score, t[:60], impact[:50]))
            elif any(k in cat for k in ["society", "社会", "教育", "life", "health"]):
                themes["社会/民生/教育"].append((score, t[:60], impact[:50]))
            else:
                themes["其他"].append((score, t[:60], impact[:50]))

        theme_lines = []
        for theme, items in themes.items():
            if not items:
                continue
            # 按 score 排序取前 5
            items.sort(key=lambda x: x[0], reverse=True)
            theme_lines.append(f"### {theme}（{len(items)} 条）")
            for score, t, imp in items[:5]:
                line = f"- (R{score}) {t}"
                if imp:
                    line += f" → 个人: {imp}"
                theme_lines.append(line)
            theme_lines.append("")
        themes_block = "\n".join(theme_lines) if theme_lines else "（无）"

        # ACH 排名
        ach_section = ""
        if ach_matrix and ach_matrix.get("scoring"):
            top = sorted(ach_matrix["scoring"].items(),
                         key=lambda kv: kv[1].get("posterior", 0), reverse=True)[:5]
            rows = []
            hyp_lookup = {h["id"]: h.get("title", h["id"]) for h in hyps}
            for hid, s in top:
                rows.append(f'- {hyp_lookup.get(hid, hid)[:40]} | 后验 {s.get("posterior", 0):.2f} | 支 {s.get("support", 0)} 驳 {s.get("refute", 0)}')
            ach_section = "## ACH 排名 Top 5\n" + "\n".join(rows)

        hyp_section_lines = []
        for h in active[:8]:
            conf = h.get("confidence", 0)
            last_v = h.get("last_verified", "-")
            hyp_section_lines.append(f'- [{h.get("level", "?")}] {h.get("title", "?")} (conf {conf:.2f}, last_verified {last_v})')
        hyp_section = "## 活跃假设（前 8）\n" + "\n".join(hyp_section_lines)

        new_section = "## 上周新增假设\n" + ("\n".join(f'- {h.get("title", "?")}' for h in new_hyps[:10]) if new_hyps else "（无）")

        verif_section = "## 上周验证动态\n"
        if verified or falsified:
            for h in verified[-5:]:
                verif_section += f'- ✓ {h.get("title", "?")}: {h.get("verification_result", "?")}\n'
            for h in falsified[-5:]:
                verif_section += f'- ✗ {h.get("title", "?")}: {h.get("verification_result", "?")}\n'
        else:
            verif_section += "（无到期验证）\n"

        system = "你是一个中文政治经济分析师，面向'中国年轻失业毕业生'视角，浓缩上周情报、ACH 假设变化、给出对个人影响 + 下周预判。语气清醒、有批判性，避免空话和政治套话。不要说'我们要相信国家'这种官话。"
        prompt = f"""请基于以下材料，写一份「参谋周报」总结，分四段：

## 1. 本周大事（300 字内）
按主题归纳上周最值得关注的 3-5 个事件。每事件 1-2 句：是什么 + 为什么重要。
不要逐条复述情报列表，要识别**模式**（如"多家央行同步加息"、"AI 芯片订单集中爆发"）。

## 2. 假设验证动态（150 字内）
ACH 排名前 5 中哪些被强化/削弱？新出现的反常点是什么？本周验证活动有何结论？

## 3. 对个人（年轻失业毕业生）的影响（200 字内）
结合双层身份：
- 第一层（普通公民）：宏观叙事如何映射到日常
- 第二层（年轻失业毕业生）：就业/学历/房产/婚育/社保的具体影响
要具体、可操作、避免空话。给出 1-2 个本周值得自己跟进的具体动作。

## 4. 下周预判（200 字内）
3-5 个具体观察点，标注"高/中/低确定性"。例如：
- 央行议息（高，确定性最高）
- 财报季 AI 板块表现（中）
- 国内某政策窗口期（中）
- 边缘/高赔率事件（低）
每点 1 句说明触发条件。

---

## 上周情报（已按主题分类，按相关度排序）

{themes_block}

## 上周情报总条数：{len(week_intel)}（活跃过滤后）

{ach_section}

{hyp_section}

{new_section}

{verif_section}

---

要求：
- 直接给结论，不要列点列举所有数据
- 对"中国年轻失业毕业生"视角要具体到行为层面（投简历方向、技能学习、避免的坑）
- 语气：参谋而非导师，敢说"这周最重要的信号是 X"
- 字数：每段严格控制在提示字数内，总长 800-1000 字"""

        try:
            summary = self.analyzer._call_api(system, prompt)
        except Exception as e:
            print("[ENGINE] AI weekly summary failed: " + str(e))
            summary = "（AI 周报生成失败，请看 hypothesis_weekly_*.md 了解活跃假设列表）"

        out = ["# 参谋周报 - " + date_str, ""]
        out.append(f"> {week_label} | 视角：公民 + 年轻失业毕业生 | 来源：ACH 矩阵 + 假设验证 + 情报")
        out.append("")
        out.append("## AI 总结")
        out.append("")
        out.append(summary)
        out.append("")
        out.append("---")
        out.append("")
        out.append("## 机器索引")
        out.append("")
        out.append(f"### 上周情报总览（{week_start_str} ~ {week_end_str}）")
        out.append("")
        out.append(f"- 总条数：{len(week_intel)}")
        out.append(f"- 活跃假设：{len(active)} | 已验证：{len(verified)} | 已证伪：{len(falsified)} | 新增：{len(new_hyps)}")
        out.append("")
        out.append("### 按主题分类")
        out.append("")
        out.append(themes_block)
        out.append("")
        out.append(ach_section)
        out.append("")
        out.append(hyp_section)
        out.append("")
        out.append(new_section)
        out.append("")
        out.append(verif_section)
        out.append("")
        out.append("---")
        out.append("Generated by HypothesisEngine._save_ai_weekly_summary")

        summary_path = report_dir / ("weekly_" + date_str + ".md")
        summary_path.write_text("\n".join(out), encoding="utf-8")
        print("[ENGINE] AI weekly summary: " + str(summary_path))
        return summary_path
