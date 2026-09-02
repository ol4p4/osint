# -*- coding: utf-8 -*-
r"""ach_matrix.py - PLAN-2 M1：ACH 竞争性假设矩阵（CIA Heuer 方法论）
核心转变（相对旧的逐假设 AI 拍置信度）：
  1. 验证单位 = 证据 × 全部 major 假设（一条证据同时对 8 个假设判定 C/I/N）
  2. 反驳加权：LR(似然比) 低于 1 的证据压低假设置信度——找最先被杀死的假设
  3. 贝叶斯校准：odds(H) × ΠLR → 后验，confidence 有概率语义且上限 0.95

判定码：C=一致(lr≥1.2) / I=不一致(lr≤0.8) / N=中性(lr=1.0)
LR 锚定：证据命中 indicators[].threshold_refute → lr≤0.5；threshold_support → lr≥1.5
矩阵持久化：data/hypotheses/ach_matrix.json（随周循环更新）
"""
import hashlib
import json
import time
from pathlib import Path

MAX_DIAGNOSE_PER_RUN = 20      # 每轮预算：最多诊断 20 条证据
POSTERIOR_CAP = 0.95           # 防过度自信
MATRIX_VERSION = 1

_DIM_LABELS = {
    "accumulation_node": "积累制度",
    "spatial_layer": "空间修正",
    "state_market_shift": "国家-市场",
    "class_interest": "阶级利益",
}


def evidence_key(ev):
    """证据稳定键（无全局 id 时用日期+摘要哈希）"""
    raw = str(ev.get("date", "")) + "|" + str(ev.get("summary", ""))[:80]
    return "ev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


class ACHMatrix:
    def __init__(self, matrix_file, majors):
        self.file = Path(matrix_file)
        self.majors = majors                      # major 假设节点引用（dict 列表）
        self.data = self._load()
        self._sync_rows()

    # ---------- 持久化 ----------
    def _load(self):
        if self.file.exists():
            try:
                return json.loads(self.file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"version": MATRIX_VERSION, "evidence": [], "diagnosis": {}}

    def save(self):
        from datetime import datetime, timezone
        self.file.parent.mkdir(parents=True, exist_ok=True)
        # ISO8601 UTC, 前端可解析
        self.data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.data["updated"] = self.data["updated_at"][:16].replace("T", " ")  # 兼容老字段
        self.data["hypotheses"] = [
            {"id": h["id"], "title": h.get("title", ""), "prior": h.get("base_confidence") or h.get("confidence", 0.5)}
            for h in self.majors]
        self.file.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")

    def _sync_rows(self):
        """假设集变化时补齐每条证据的判定槽位（新假设默认 N 中性）"""
        known = {h["id"] for h in self.majors}
        for ev in self.data["evidence"]:
            for hid in known - set(ev.get("diagnosis", {})):
                ev["diagnosis"][hid] = {"code": "N", "lr": 1.0, "note": "新假设，默认中性"}

    # ---------- 证据发现 ----------
    def find_undiagnosed(self):
        """major 节点 evidence_log 里还没进矩阵的证据"""
        diagnosed = {ev["key"] for ev in self.data["evidence"]}
        out = []
        for h in self.majors:
            for ev in h.get("evidence_log", []) or []:
                if not isinstance(ev, dict) or not ev.get("summary"):
                    continue
                key = evidence_key(ev)
                if key not in diagnosed:
                    out.append({"key": key, "hyp_id": h["id"], "ev": ev})
        # 去重（同证据挂在多个假设下）
        seen, uniq = set(), []
        for e in out:
            if e["key"] not in seen:
                seen.add(e["key"])
                uniq.append(e)
        return uniq[:MAX_DIAGNOSE_PER_RUN]

    # ---------- AI 诊断 ----------
    def ai_diagnose(self, evidence_entry, analyzer):
        """一次 AI 调用：单条证据 × 全部 major 假设 → 判定码 + LR + 理由"""
        hyp_list = [{"id": h["id"], "title": h.get("title", ""),
                     "falsification": (h.get("falsification_criteria") or "")[:150]}
                    for h in self.majors]
        system = ("你是情报分析教员，教授 CIA 的 ACH（竞争性假设分析）方法。"
                  "对一条证据，逐个假设判定：C=证据与假设预期一致；I=证据与假设预期相斥（证伪信号，最重要）；"
                  "N=无关。并给出似然比 LR=P(证据|假设)/P(证据|非假设)。证伪优先，诊断性优先。")
        prompt = (
            "证据：\n" + json.dumps({"date": evidence_entry["ev"].get("date"),
                                     "summary": evidence_entry["ev"].get("summary", "")[:300],
                                     "source": evidence_entry["ev"].get("source", "")}, ensure_ascii=False)
            + "\n\n竞争假设列表(JSON)：\n" + json.dumps(hyp_list, ensure_ascii=False)
            + "\n\n严格只输出 JSON 数组（不要 markdown）："
              '[{"hyp_id":"原id","code":"C|I|N","lr":1.0,"note":"一句诊断理由(≤40字)"}]'
              "\n注意：lr 范围 0.3~2.0；C 取 >1.2，I 取 <0.8，N 取 1.0 左右。"
              "若证据命中某假设的证伪判据(falsification)，该假设必须判 I 且 lr≤0.5。")

        response = analyzer._call_api(system, prompt)
        import re
        response = re.sub(r"```json\s*", "", response)
        response = re.sub(r"```\s*$", "", response)
        start = response.find("[")
        if start < 0:
            raise ValueError("AI 未返回 JSON 数组")
        depth = 0
        for i in range(start, len(response)):
            if response[i] == "[":
                depth += 1
            elif response[i] == "]":
                depth -= 1
                if depth == 0:
                    return json.loads(response[start:i + 1])
        raise ValueError("AI 输出 JSON 不完整")

    def record(self, evidence_entry, diagnosis):
        """写入/更新一条证据的矩阵行"""
        known = {h["id"] for h in self.majors}
        row = {"key": evidence_entry["key"], "date": evidence_entry["ev"].get("date", ""),
               "summary": evidence_entry["ev"].get("summary", "")[:120], "diagnosis": {}}
        for d in diagnosis:
            if isinstance(d, dict) and d.get("hyp_id") in known:
                code = d.get("code", "N")
                lr = d.get("lr", 1.0)
                try:
                    lr = max(0.3, min(2.0, float(lr)))
                except (TypeError, ValueError):
                    lr = 1.0
                if code not in ("C", "I", "N"):
                    code = "N"
                row["diagnosis"][d["hyp_id"]] = {"code": code, "lr": lr, "note": str(d.get("note", ""))[:60]}
        for hid in known - set(row["diagnosis"]):
            row["diagnosis"][hid] = {"code": "N", "lr": 1.0, "note": "未判定"}

        for i, ev in enumerate(self.data["evidence"]):
            if ev["key"] == row["key"]:
                self.data["evidence"][i] = row
                return
        self.data["evidence"].append(row)

    # ---------- 贝叶斯更新 ----------
    def bayesian_update(self, hyps):
        """按矩阵重算每个 major 假设的后验置信度并写回 confidence"""
        hyp_by_id = {h["id"]: h for h in hyps}
        scores = {}
        for h in self.majors:
            prior = float(h.get("base_confidence") or h.get("confidence") or 0.5)
            odds = prior / max(1 - prior, 0.01)
            support = refute = 0
            for ev in self.data["evidence"]:
                d = ev.get("diagnosis", {}).get(h["id"])
                if not d:
                    continue
                odds *= d.get("lr", 1.0)
                if d["code"] == "C":
                    support += 1
                elif d["code"] == "I":
                    refute += 1
            posterior = odds / (1 + odds)
            posterior = min(posterior, POSTERIOR_CAP)
            scores[h["id"]] = {"posterior": round(posterior, 3), "support": support,
                               "refute": refute}
            target = hyp_by_id.get(h["id"])
            if target:
                target["confidence"] = round(posterior, 3)
        self.data["scoring"] = scores
        return scores

    # ---------- 导出 ----------
    def export_markdown(self, out_dir):
        """Obsidian 风格矩阵周报：假设 × 最近证据的 C/I/N 表 + 排名"""
        out = Path(out_dir) / "reports"
        out.mkdir(parents=True, exist_ok=True)
        recent = self.data["evidence"][-25:]
        scoring = self.data.get("scoring", {})
        lines = ["# ACH 竞争性假设矩阵（" + time.strftime("%Y-%m-%d") + "）", "",
                 "> 判定：C=一致 / I=不一致(证伪信号) / N=中性；置信度=贝叶斯后验（上限0.95）", ""]
        ranked = sorted(scoring.items(), key=lambda kv: -kv[1]["posterior"])
        lines.append("## 排名（后验置信度）")
        for i, (hid, s) in enumerate(ranked, 1):
            title = next((h.get("title", hid) for h in self.majors if h["id"] == hid), hid)
            lines.append(f"{i}. **{title}** — {s['posterior']:.2f}（支持{s['support']}/反驳{s['refute']}）")
        lines += ["", "## 诊断矩阵（最近 %d 条证据）" % len(recent), "",
                  "| 证据 | " + " | ".join(h.get("title", "")[:8] for h in self.majors) + " |",
                  "|---|" + "---|" * len(self.majors)]
        for ev in recent:
            row = [ev.get("summary", "")[:28]]
            for h in self.majors:
                d = ev.get("diagnosis", {}).get(h["id"], {})
                row.append(d.get("code", "·") + str(d.get("lr", "")))
            lines.append("| " + " | ".join(row) + " |")
        p = out / ("ach_matrix_" + time.strftime("%Y%m%d") + ".md")
        p.write_text("\n".join(lines), encoding="utf-8")
        print("[ACH] matrix report: " + str(p))
        return p
