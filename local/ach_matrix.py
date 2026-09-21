# -*- coding: utf-8 -*-
r"""ach_matrix.py - PLAN-2 M1：ACH 竞争性假设矩阵（CIA Heuer 方法论）
核心转变（相对旧的逐假设 AI 拍置信度）：
  1. 验证单位 = 证据 × 全部 major 假设（一条证据同时对 8 个假设判定 C/I/N）
  2. 反驳加权：LR(似然比) 低于 1 的证据压低假设置信度——找最先被杀死的假设
  3. 贝叶斯校准：odds(H) × ΠLR → 后验，confidence 有概率语义且上限 0.95

判定码：C=一致(lr≥1.2) / I=不一致(lr≤0.8) / N=中性(lr=1.0)

2026-09-20 LR 推导下沉：模型只报 code + conf(把握度 0~1)，LR 由 derive_lr() 在代码里算。
  动因：实测 388 条证据的 LR 分布严重塌缩——C 有 76/111 集中在 1.5、I 有 46/108 集中在 0.5，
  即模型在照抄 prompt 里的示例值，而非做概率推理（官方 JEV 文档亦明言"模型不是计算器"）。
  改造后 conf 是模型的自然输出（"你有多确定"），LR 是代码的确定性函数，可复算、可调参。
  兼容：老行只有 lr 没有 conf，record() 回退读 lr；新行同时存 code/conf/lr，
  日后调 LR_C_STRENGTH/LR_I_STRENGTH 可直接用存量 conf 重算，无需重跑 AI。
矩阵持久化：data/hypotheses/ach_matrix.json（随周循环更新）
"""
import hashlib
import json
import time
from pathlib import Path

MAX_DIAGNOSE_PER_RUN = 20      # 每轮预算：最多诊断 20 条证据
POSTERIOR_CAP = 0.95           # 防过度自信
POSTERIOR_FLOOR = 0.05         # 2026-09-16: 后验下限——LR 复利(20h/轮)会把连吃 I 的假设
                               # 打到 0.0（实测 HM100 支17/驳44 → 0.0），0 表示"证据方向
                               # 极不利"而非"绝对不可能"，保留 5% 供后续证据翻案
MATRIX_VERSION = 1

# 2026-09-12 诊断准入：只消化 link_intel_hyp 标记为 ach_eligible 的证据
# （TF-IDF 命中 / DOMAIN 高分），存量弱证据不再进队列——否则诊断速度(40/天)
# 永远追不上历史灌入量(数千条)。翻 True 可回退全量消化存量。
LEGACY_DEFAULT_ELIGIBLE = False

# 2026-09-20 LR 推导锚点：conf(把握度 0~1) → LR
#   LR = 1 + conf * (STRENGTH - 1)，conf=1 达最大强度，conf=0 退化为中性 1.0
#   取值参照改造前实测分布（C 主峰 1.5 / I 主峰 0.5），使新旧口径可比，
#   历史后验不会因本次改动整体漂移。
LR_C_STRENGTH = 1.5   # C 的最大似然比：conf=1 → lr=1.5
LR_I_STRENGTH = 0.5   # I 的最小似然比：conf=1 → lr=0.5
LR_CONF_FLOOR = 0.0   # conf 下限（模型可表达"几乎不相关"）
LR_CONF_CAP = 1.0     # conf 上限（不放大到 2.0——过度自信是历史教训，见 POSTERIOR_CAP）


def derive_lr(code, conf, strength_c=LR_C_STRENGTH, strength_i=LR_I_STRENGTH):
    """由判定码 + 把握度推导似然比（确定性函数，可复算可调参）。

    code=C → LR ∈ [1.0, strength_c]，conf 越大越接近上限
    code=I → LR ∈ [strength_i, 1.0]，conf 越大越接近下限
    code=N → 恒 1.0（中性证据不参与贝叶斯更新）

    把握度低时自动向 1.0（中性）收缩，避免"低把握 + 极端 LR"污染后验。
    conf 无法解析时按 0.5 处理（调用方 record() 对"字段缺失"另有更保守的
    回退：走旧格式读 lr，都没有则 1.0，即不更新后验）。
    """
    if code == "N":
        return 1.0
    try:
        c = float(conf)
    except (TypeError, ValueError):
        c = 0.5   # 缺省：中等把握
    c = max(LR_CONF_FLOOR, min(LR_CONF_CAP, c))
    if code == "C":
        return round(1.0 + c * (strength_c - 1.0), 3)
    if code == "I":
        return round(1.0 - c * (1.0 - strength_i), 3)
    return 1.0


def _ach_eligible(ev):
    """证据是否值得 AI 诊断：新条目看 ach_eligible 标记，存量条目按 LEGACY 开关"""
    if "ach_eligible" in ev:
        return bool(ev.get("ach_eligible"))
    if LEGACY_DEFAULT_ELIGIBLE:
        try:
            return float(ev.get("relevance") or 0) >= 0.4
        except (TypeError, ValueError):
            return False
    return False

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
                ev["diagnosis"][hid] = {"code": "N", "lr": 1.0, "conf": 1.0, "note": "新假设，默认中性"}

    # ---------- 证据发现 ----------
    def find_undiagnosed(self, limit=None, newest_first=True):
        """major 节点 evidence_log 里还没进矩阵的证据。
        limit: 覆盖 MAX_DIAGNOSE_PER_RUN 的每轮预算（每日批用大值）
        newest_first: 新证据优先（每日增量场景），False 则按日志顺序（历史积压场景）
        2026-09-12 诊断准入：只取 _ach_eligible 的强证据（见文件头）"""
        diagnosed = {ev["key"] for ev in self.data["evidence"]}
        out = []
        for h in self.majors:
            for ev in h.get("evidence_log", []) or []:
                if not isinstance(ev, dict) or not ev.get("summary"):
                    continue
                key = evidence_key(ev)
                if key not in diagnosed and _ach_eligible(ev):
                    out.append({"key": key, "hyp_id": h["id"], "ev": ev})
        # 去重（同证据挂在多个假设下）
        seen, uniq = set(), []
        for e in out:
            if e["key"] not in seen:
                seen.add(e["key"])
                uniq.append(e)
        if newest_first:
            uniq.sort(key=lambda e: str(e["ev"].get("date", "")), reverse=True)
        return uniq[:(limit if limit else MAX_DIAGNOSE_PER_RUN)]

    # ---------- AI 诊断 ----------
    def ai_diagnose(self, evidence_entry, analyzer, jev=None):
        """单条证据 × 全部 major 假设 → 判定码 + 把握度 + 理由

        2026-09-20 起模型只报 code + conf，不再自报 LR——LR 由 derive_lr() 推导。
        保持"一次调用看全部假设"的跨假设比较能力（实测 138 处 C/I 判给了
        TF-IDF 未预挂载的假设，拆成独立调用会丢失这部分信号）。

        2026-09-21 起优先走 JEV（决策专用模型，实测 0.66s/条 vs mimo 45s/条）：
          jev 参数传入 JevClient 实例则走 JEV；未传/不可用则回退 analyzer（mimo）。
          JEV 无 note 字段（架构上不生成文本），note 留空——下游不消费（见 record()）。
        """
        if jev is not None and getattr(jev, "available", False):
            return self._diagnose_jev(evidence_entry, jev)
        return self._diagnose_llm(evidence_entry, analyzer)

    def _diagnose_jev(self, evidence_entry, jev):
        """JEV 路径：两段式（Noul 议题门控 → Choice 方向判断）

        2026-09-21 二次实测：单段式「预期是否一致」有系统性误判——JEV 把
        「铁路客运创新高」「原油跳水」「A股高开」这类与议题无关的内容判成
        inconsistent（字面理解为"不支持该假设"），噪声被大量吸入导致后验塌缩。
        改为两段式后，门控先滤掉无关内容（实测噪声相关性 0.01~0.04）。
        """
        ev = evidence_entry["ev"]
        state = "证据（" + str(ev.get("date", "")) + "）：" + str(ev.get("summary", ""))[:400]
        got = jev.gate_and_diagnose(state, self.majors)
        if not got:
            raise ValueError("JEV 未返回任何判定")
        return [{"hyp_id": hid, "code": v["code"], "conf": v["conf"], "note": "",
                 "gate": v.get("gate")} for hid, v in got.items()]

    def _diagnose_llm(self, evidence_entry, analyzer):
        """通用大模型路径（mimo）：索取 code + conf + note，JSON 解析容错"""
        hyp_list = [{"id": h["id"], "title": h.get("title", ""),
                     "falsification": (h.get("falsification_criteria") or "")[:150]}
                    for h in self.majors]
        system = ("你是情报分析教员，教授 CIA 的 ACH（竞争性假设分析）方法。"
                  "对一条证据，逐个假设判定：C=证据与假设预期一致；I=证据与假设预期相斥（证伪信号，最重要）；"
                  "N=无关。证伪优先，诊断性优先。"
                  "注意：你只负责判断方向与把握度，不要做任何数值计算。")
        prompt = (
            "证据：\n" + json.dumps({"date": evidence_entry["ev"].get("date"),
                                     "summary": evidence_entry["ev"].get("summary", "")[:300],
                                     "source": evidence_entry["ev"].get("source", "")}, ensure_ascii=False)
            + "\n\n竞争假设列表(JSON)：\n" + json.dumps(hyp_list, ensure_ascii=False)
            + "\n\n严格只输出 JSON 数组（不要 markdown）："
              '[{"hyp_id":"原id","code":"C|I|N","conf":0.8,"note":"一句诊断理由(≤40字)"}]'
              "\nconf = 你对这个判定码的把握度，0~1 的小数："
              "0.9+=证据明确指向该方向；0.6~0.8=较有把握；0.3~0.5=勉强相关/倾向性判断；"
              "≤0.2=几乎无法判断（这种情况应改判 N）。"
              "请给出真实区分度，不要所有条目都给同一个数值。"
              "若证据命中某假设的证伪判据(falsification)，该假设必须判 I 且 conf≥0.8。")

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

    def record(self, evidence_entry, diagnosis, model=None):
        """写入/更新一条证据的矩阵行。

        LR 来源优先级（2026-09-20）：
          1) 有 conf → derive_lr(code, conf) 代码推导（新口径）
          2) 无 conf 但有 lr → 沿用模型自报值并钳制（旧口径，兼容历史/外部调用）
        同时落盘 code/conf/lr 三字段：日后调 LR_*_STRENGTH 可用存量 conf 重算 LR，
        无需重跑 AI。

        **判定记忆（2026-09-21）**：row 带 `model`（判定来源）+ `diagnosed_at`（时间），
        并把每次判定追加到 data/hypotheses/diagnosis_log.jsonl（append-only）。
        动机：此前 437 行判定无来源、无时间戳、无历史——是"静态快照"而非"可追溯过程"，
        导致重跑时无法对比新旧、无法回滚单条、无法区分 JEV 与 mimo 的判定。
        """
        known = {h["id"] for h in self.majors}
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        row = {"key": evidence_entry["key"], "date": evidence_entry["ev"].get("date", ""),
               "summary": evidence_entry["ev"].get("summary", "")[:120], "diagnosis": {},
               "model": model or "unknown", "diagnosed_at": now}
        for d in diagnosis:
            if isinstance(d, dict) and d.get("hyp_id") in known:
                code = d.get("code", "N")
                if code not in ("C", "I", "N"):
                    code = "N"
                if d.get("conf") is not None:
                    conf = d.get("conf")
                    try:
                        conf = max(LR_CONF_FLOOR, min(LR_CONF_CAP, float(conf)))
                    except (TypeError, ValueError):
                        conf = 0.5
                    lr = derive_lr(code, conf)
                else:
                    # 旧格式回退：模型自报 lr
                    conf = None
                    try:
                        lr = max(0.3, min(2.0, float(d.get("lr", 1.0))))
                    except (TypeError, ValueError):
                        lr = 1.0
                entry = {"code": code, "lr": lr, "note": str(d.get("note", ""))[:60]}
                if conf is not None:
                    entry["conf"] = round(conf, 3)
                # 两段式门控的相关性概率（仅 JEV 路径有）——留痕供日后调阈值
                if d.get("gate") is not None:
                    try:
                        entry["gate"] = round(float(d["gate"]), 3)
                    except (TypeError, ValueError):
                        pass
                row["diagnosis"][d["hyp_id"]] = entry
        for hid in known - set(row["diagnosis"]):
            row["diagnosis"][hid] = {"code": "N", "lr": 1.0, "conf": 1.0, "note": "未判定"}

        prev = None
        for i, ev in enumerate(self.data["evidence"]):
            if ev["key"] == row["key"]:
                prev = ev
                self.data["evidence"][i] = row
                break
        else:
            self.data["evidence"].append(row)

        self._append_diagnosis_log(row, prev)

    def _append_diagnosis_log(self, row, prev):
        """追加式诊断日志（append-only，永不覆盖）——判定记忆的追溯载体。

        记录每次判定的来源/时间/判定码，prev 为被覆盖的上一版（含来源）。
        落盘 data/hypotheses/diagnosis_log.jsonl，供：
          - 重跑前后对比（同一 key 的判定演化）
          - 区分 JEV / mimo 的判定
          - 出问题回滚单条
        失败静默——记账不该阻断主流程。
        """
        try:
            log = self.file.parent / "diagnosis_log.jsonl"
            rec = {
                "at": row.get("diagnosed_at"),
                "key": row.get("key"),
                "summary": (row.get("summary") or "")[:80],
                "model": row.get("model"),
                "codes": {hid: d.get("code") for hid, d in (row.get("diagnosis") or {}).items()},
                "prev_model": (prev or {}).get("model"),
                "prev_codes": ({hid: d.get("code") for hid, d in (prev.get("diagnosis") or {}).items()}
                               if prev else None),
            }
            with log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ---------- 贝叶斯更新 ----------
    def bayesian_update(self, hyps):
        """按矩阵重算每个 major 假设的后验置信度并写回 confidence。
        先验恒取 base_confidence（用户/AI 设定的原始锚点）——不用 confidence 兜底，
        否则上一轮被压低的后验会变成下一轮的先验，LR 复利恶性循环（9-16 诊断）。"""
        hyp_by_id = {h["id"]: h for h in hyps}
        scores = {}
        for h in self.majors:
            prior = float(h.get("base_confidence") or 0.5)
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
            posterior = max(min(posterior, POSTERIOR_CAP), POSTERIOR_FLOOR)
            scores[h["id"]] = {"posterior": round(posterior, 3), "support": support,
                               "refute": refute}
            target = hyp_by_id.get(h["id"])
            if target:
                target["confidence"] = round(posterior, 3)
        self.data["scoring"] = scores
        return scores

    # ---------- 敏感性分析（P0-4：学 ArkhamMirror / Heuer 方法论收尾步） ----------
    def sensitivity_analysis(self):
        """逐条证据把 LR 中性化(置1重算其余证据的后验)，看 Top-1 是否翻转、后验变化多大。
        诊断性最高的证据 = 移除后排名/后验变动最大的那条。
        Heuer：ACH 的价值在于告诉分析师**下一步该去找什么证据**。
        须在 bayesian_update() 之后调用（依赖 self.data['scoring']）。"""
        base = {hid: s.get("posterior", 0.5)
                for hid, s in (self.data.get("scoring") or {}).items()}
        if not base:
            return {}
        top1 = max(base, key=lambda k: base[k])
        results = []
        for ev in self.data["evidence"]:
            neutral = {}
            for h in self.majors:
                prior = float(h.get("base_confidence") or 0.5)  # 与 bayesian_update 同源：恒取 base_confidence
                odds = prior / max(1 - prior, 0.01)
                for e2 in self.data["evidence"]:
                    if e2.get("key") == ev.get("key"):
                        continue  # 本条证据中性化（LR 不参与）
                    d = e2.get("diagnosis", {}).get(h["id"])
                    if d:
                        odds *= d.get("lr", 1.0)
                neutral[h["id"]] = min(odds / (1 + odds), POSTERIOR_CAP)
            new_top1 = max(neutral, key=lambda k: neutral[k])
            deltas = {hid: base.get(hid, 0.0) - neutral[hid] for hid in neutral}
            moved = max(deltas, key=lambda k: abs(deltas[k]))
            results.append({
                "key": ev.get("key"),
                "summary": (ev.get("summary") or "")[:80],
                "date": ev.get("date", ""),
                "top1_flip": new_top1 != top1,
                "new_top1": new_top1,
                "top1_delta": round(deltas.get(top1, 0.0), 3),
                "max_abs_delta": round(abs(deltas[moved]), 3),
                "moved_hyp": moved,
            })
        results.sort(key=lambda r: (-r["max_abs_delta"], not r["top1_flip"]))
        out = {"top1": top1, "flips": sum(1 for r in results if r["top1_flip"]),
               "n_evidence": len(results), "items": results[:10],
               "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self.data["sensitivity"] = out
        return out

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

        # P0-4 敏感性分析节：哪条证据决定排名、哪个结论最脆弱
        sens = self.data.get("sensitivity")
        if sens and sens.get("items"):
            top1_title = next((h.get("title", sens["top1"]) for h in self.majors
                               if h["id"] == sens["top1"]), sens["top1"])
            lines += ["", "## 敏感性分析（逐条证据中性化后的排名稳定性）", "",
                      "当前 Top-1：**" + top1_title + "**；移除单条证据后排名翻转 "
                      + str(sens.get("flips", 0)) + " 次（共 " + str(sens.get("n_evidence", 0)) + " 条证据）。", "",
                      "| 证据 | 移除后 Top-1 变为 | 翻转 | 最大单假设后验变化 |",
                      "|---|---|---|---|"]
            for r in sens["items"][:5]:
                t = next((h.get("title", r["new_top1"])[:16] for h in self.majors
                          if h["id"] == r["new_top1"]), r["new_top1"])
                lines.append("| " + r.get("summary", "")[:28] + " | " + t + " | "
                             + ("⚠️ 翻转" if r.get("top1_flip") else "否")
                             + " | " + format(r.get("max_abs_delta", 0), "+.3f") + " |")
            lines += ["", "> 下一步该找的证据：对 Top-1 后验变化最大的证据做补充核实；"
                      "若移除某条证据即翻盘，说明当前结论最脆弱。"]

        p = out / ("ach_matrix_" + time.strftime("%Y%m%d") + ".md")
        p.write_text("\n".join(lines), encoding="utf-8")
        print("[ACH] matrix report: " + str(p))
        return p
