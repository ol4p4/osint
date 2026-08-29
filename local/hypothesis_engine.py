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
            f"",
            f"**Status**: {self.status} | **Confidence**: {self.confidence:.0%} | **Due**: {self.due_date}",
            f"",
            f"## Core Claim",
            f"{self.core_claim}",
            f"",
            f"## Direction: {self.direction} | Magnitude: {self.magnitude}",
            f"",
            f"## Sub-Propositions",
        ]
        for sp in self.sub_propositions:
            lines.append(f"- **{sp.get(chr(39)+chr(39), sp.get(chr(34)+chr(34), chr(63)))}**: {sp.get(chr(39)+chr(39)+chr(99)+chr(108)+chr(97)+chr(105)+chr(109), sp.get(chr(99)+chr(108)+chr(97)+chr(105)+chr(109), chr(63)))}")
        if self.evidence_log:
            lines.extend(["", "## Evidence Log"])
            for ev in self.evidence_log[-5:]:
                lines.append(f"- [{ev.get(chr(100)+chr(97)+chr(116)+chr(101), chr(63))}] {ev.get(chr(115)+chr(117)+chr(109)+chr(109)+chr(97)+chr(114)+chr(121), chr(63))}")
        return chr(10).join(lines)

class HypothesisEngine:
    def __init__(self, config, kb, analyzer):
        self.config = config
        self.kb = kb
        self.analyzer = analyzer
        paths = config.get("paths", {})
        self.output_dir = Path(paths.get("output_dir", r"D:\Codex输出\osint_卫星图"))
        self.hyp_dir = self.output_dir / "hypotheses"
        self.hyp_dir.mkdir(parents=True, exist_ok=True)
        self.hyp_file = self.hyp_dir / "active_hypotheses.json"
        self.hyp_history = self.hyp_dir / "hypothesis_history.jsonl"

    def load_views(self, views_path):
        with open(views_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def load_active_hypotheses(self):
        if self.hyp_file.exists():
            with open(self.hyp_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def save_active_hypotheses(self, hyps):
        with open(self.hyp_file, "w", encoding="utf-8") as f:
            json.dump(hyps, f, ensure_ascii=False, indent=2)

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
            "reconciliation_notes": ""
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
            "# " + hyp["title"],
            "",
            "Status: " + hyp["status"] + " | Confidence: " + str(round(hyp["confidence"],2)) + " | Due: " + hyp["due_date"],
            "",
            "## Core Claim",
            hyp["core_claim"],
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
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    def update_evidence(self, hyp_id, evidence_entry):
        hyps = self.load_active_hypotheses()
        for h in hyps:
            if h["id"] == hyp_id:
                h["evidence_log"].append(evidence_entry)
                self.save_active_hypotheses(hyps)
                return True
        return False

    def get_due_hypotheses(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hyps = self.load_active_hypotheses()
        return [h for h in hyps if h["status"] == "active" and h["due_date"] <= today]

    def get_all_active(self):
        hyps = self.load_active_hypotheses()
        return [h for h in hyps if h["status"] == "active"]



    def verify_hypothesis(self, hyp, ai_analyzer):
        ev_text = "\n".join([e.get("summary","") for e in hyp.get("evidence_log",[])])
        prompt = "Verify this hypothesis: " + hyp.get("core_claim","") + "\nConfidence: " + str(hyp.get("confidence",0.5)) + "\nEvidence: " + ev_text[:2000] + "\n\nOutput JSON: {verdict: supported/partially_supported/refuted/inconclusive, new_confidence: 0-1, reasoning: ...}"
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
        return hyp

    def run_weekly_cycle(self, intel_items=None):
        print("[ENGINE] === Weekly Hypothesis Cycle ===")
        views = self.load_views(r"D:\osint\views.yaml")
        hyps = self.generate_hypotheses_from_views(views)
        active = self.get_all_active()
        print("[ENGINE] Active hypotheses: " + str(len(active)))
        if intel_items:
            for h in active:
                h["evidence_log"].append({"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "source": "daily_intel", "summary": "Daily update: " + str(len(intel_items)) + " items"})
            self.save_active_hypotheses(hyps)
        due = self.get_due_hypotheses()
        if due:
            print("[ENGINE] Due for verification: " + str(len(due)))
            for h in due:
                verified = self.verify_hypothesis(h, self.analyzer)
                print("  " + verified["title"] + ": " + verified["verification_result"])
            self.save_active_hypotheses(hyps)
        self._save_weekly_report(active)
        return active

    def _save_weekly_report(self, active_hyps):
        report_dir = self.output_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        report_path = report_dir / ("hypothesis_weekly_" + date_str + ".md")
        lines = ["# Hypothesis Weekly Report - " + date_str, ""]
        for h in active_hyps:
            lines.append("- " + h["title"] + " | confidence: " + str(round(h["confidence"],2)) + " | due: " + h["due_date"])
        lines.extend(["", "---", "Generated by Hypothesis Engine"])
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("[ENGINE] Weekly report: " + str(report_path))
