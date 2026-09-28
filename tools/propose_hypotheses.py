# -*- coding: utf-8 -*-
r"""propose_hypotheses.py - 从情报自动提议新假设（2026-09-28 新增）

**要解决的问题**：假设生产链在 9-14 后停摆。现有唯一入口是「views.yaml 手写
观点 → AI 拆解」，而 6 个 view 全部物化完毕 → 再没有新假设产生。情报每天进
几千条，却没有任何代码路径把「反复出现但树里没有的主题」变成假设。

**本工具做什么**（三步，全部可复核）：
  1. **发现候选主题**：对近 N 天高分情报做 TF-IDF 聚类（复用 cluster_stories
     的分词器与并查集），找出「成簇出现」的主题——一个主题被多家媒体反复报道，
     说明它有持续信息量。
  2. **排除已覆盖**：把每个候选簇与现有 75 个假设节点做 TF-IDF 余弦比对，
     相似度超过阈值的视为「树里已有」，丢弃。
  3. **AI 提议假设**：对剩下的候选簇，让 AI 按项目既有 schema 提议假设
     （core_claim / indicators / threshold_support / threshold_refute /
     falsification_criteria / deadline）。

**安全设计（关键）**：
  - **默认只提议不入库**（写 data/hypotheses/proposed_*.json），人工过目后
    再决定是否物化。这是刻意的：自动入库会让树被 AI 废话假设灌满，
    而无法证伪的假设比没有假设更糟（会污染 ACH 与校准）。
  - **提议必须可证伪**：prompt 强制要求 threshold_refute 非空且含具体年份/数值，
    产出后由 `_validate_proposal()` 做结构校验，不合格的直接丢弃并记原因。
  - **不写 confidence**：提议节点只有 base_confidence（用户可改），
    真正的置信度由 ACH/验证产生。

用法：
    python tools/propose_hypotheses.py --dry                  # 只看候选簇，不调 AI
    python tools/propose_hypotheses.py --limit 30             # 提议 30 个（推荐先小批）
    python tools/propose_hypotheses.py --limit 30 --write-tree  # 物化进树（需人工确认后）
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
HYP_FILE = BASE / "hypotheses" / "active_hypotheses.json"

sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "local"))
sys.path.insert(0, str(PROJECT / "tools"))

# 候选簇的最低规模：同主题至少这么多条情报才值得提议假设
MIN_CLUSTER_SIZE = 3
# 跨天闸门：簇内情报必须分布在至少这么多天。
# 2026-09-28 实测：首版只有规模闸门，结果候选全是「同一句话被多家媒体复述」
# （武契奇辞职 3 条同日、英央行 Ramsden 发言 4 条同日）——那是一个**事件**，
# 不是**主题**。真主题的特征是跨天反复出现（如霍尔木兹危机持续两周）。
MIN_CLUSTER_DAYS = 2
# 与现有假设的相似度超过此值视为"树里已有"，不再提议
COVERED_SIM = 0.12
# 提议节点的默认层级与验证窗口
PROPOSAL_LEVEL = "medium"
PROPOSAL_HORIZON_MONTHS = 12

# 固定栏目/模板标题的特征词（2026-09-28 实测：首版 8 个候选里 4 个是栏目名）。
# 这类标题每天重复出现，TF-IDF 必然成簇，但它们是**发布格式**不是**主题**，
# 提议假设会产出"华尔街见闻早餐会持续发布"这种废话。
TEMPLATE_MARKERS = (
    "早餐", "早报", "晚报", "日报", "周报", "汇总", "一览", "盘点", "速览",
    "提醒", "重点关注", "隔夜要闻", "daily open", "morning brief", "newsletter",
)


def _is_template_cluster(cluster):
    """簇是否由固定栏目/模板标题构成。

    判据：簇内代表标题含模板特征词的比例过半——用比例而非任一命中，
    避免把"某公司发布年报早餐会"这类真事件误杀。
    """
    titles = [it["title"].lower() for it in cluster["items"]]
    if not titles:
        return False
    hits = sum(1 for t in titles if any(m in t for m in TEMPLATE_MARKERS))
    return hits / len(titles) > 0.5


def load_existing_hyps():
    nodes = json.loads(HYP_FILE.read_text(encoding="utf-8"))
    return [n for n in nodes if n.get("status") == "active"]


def load_recent_intel(days=7, limit=None):
    """近 N 天情报，按 final_score 降序。与 probe_mega 同口径。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    lo = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    hi = now.strftime("%Y-%m-%d")

    files = sorted(BASE.glob("intel_2*.jsonl"), reverse=True)[:days + 2]
    seen, items = set(), []
    for f in files:
        if "raw" in f.name or "final" in f.name:
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                it = json.loads(line)
            except json.JSONDecodeError:
                continue
            iid = it.get("id")
            if not iid or iid in seen:
                continue
            pub = str(it.get("published_at") or "")[:10]
            if not (lo <= pub <= hi):
                continue
            seen.add(iid)
            items.append(it)

    def key(it):
        try:
            return (float(it.get("final_score") or 0), str(it.get("published_at") or ""))
        except (TypeError, ValueError):
            return (0.0, str(it.get("published_at") or ""))

    items.sort(key=key, reverse=True)
    return (items[:limit] if limit else items), lo, hi


def find_candidate_clusters(items, min_size=MIN_CLUSTER_SIZE):
    """用 TF-IDF + 并查集找「成簇出现」的主题（复用 cluster_stories）。

    返回 [{"tokens": [...], "items": [...], "size": n}]，按簇规模降序。
    只用标题文本——正文噪声大（行情播报模板句）会串簇。
    """
    import cluster_stories as cs
    from link_intel_hyp import build_tfidf_vectors

    texts, toks_list = [], []
    for it in items:
        t = (it.get("cn_title") or it.get("title") or "").strip()
        if len(t) < 8:
            continue
        texts.append(t)
        toks_list.append(cs._tokens(t))
    if len(texts) < min_size:
        return []

    vecs = build_tfidf_vectors(toks_list)
    # 并查集：相似度超阈值则合并
    parent = list(range(len(texts)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # 倒排索引剪枝：只比较共享高权重 token 的对（全量 O(n²) 在数千条时太慢）
    posting = {}
    for i, v in enumerate(vecs):
        top = sorted(v.items(), key=lambda x: -x[1])[:cs.SIG_TOKENS]
        for t, _ in top:
            posting.setdefault(t, []).append(i)

    for t, idxs in posting.items():
        if len(idxs) > cs.MAX_POSTING or len(idxs) < 2:
            continue
        for a_i in range(len(idxs)):
            for b_i in range(a_i + 1, len(idxs)):
                i, j = idxs[a_i], idxs[b_i]
                va, vb = vecs[i], vecs[j]
                aa, bb = (va, vb) if len(va) <= len(vb) else (vb, va)
                dot = sum(w * bb.get(k, 0.0) for k, w in aa.items())
                if dot >= cs.SIM_THRESHOLD:
                    union(i, j)

    groups = {}
    for i in range(len(texts)):
        groups.setdefault(find(i), []).append(i)

    clusters = []
    for root, idxs in groups.items():
        if len(idxs) < min_size:
            continue
        # 跨天闸门：同日被多家媒体复述 = 一个事件，不是持续主题
        days = {str(items[i].get("published_at") or "")[:10] for i in idxs}
        days.discard("")
        if len(days) < MIN_CLUSTER_DAYS:
            continue
        # 簇主题词 = 簇内平均权重最高的 token
        agg = {}
        for i in idxs:
            for t, w in vecs[i].items():
                agg[t] = agg.get(t, 0.0) + w
        top_tokens = [t for t, _ in sorted(agg.items(), key=lambda x: -x[1])[:8]]
        clusters.append({
            "size": len(idxs),
            "day_count": len(days),
            "tokens": top_tokens,
            "items": [{"title": texts[i][:100],
                       "date": str(items[i].get("published_at") or "")[:10],
                       "source": items[i].get("source_name", "")} for i in idxs[:5]],
        })
    clusters.sort(key=lambda c: -c["size"])
    return clusters


def filter_uncovered(clusters, hyps, sim_threshold=COVERED_SIM):
    """把与现有假设高度重合的簇剔除（树里已经有的主题不必再提议）。"""
    import cluster_stories as cs
    from link_intel_hyp import build_tfidf_vectors

    hyp_texts = [(h.get("title", "") + " " + (h.get("core_claim") or "")
                  + " " + (h.get("rationale") or "")[:200]) for h in hyps]
    hyp_toks = [cs._tokens(t) for t in hyp_texts]

    cl_texts = [" ".join(c["tokens"]) + " " + " ".join(i["title"] for i in c["items"])
                for c in clusters]
    cl_toks = [cs._tokens(t) for t in cl_texts]

    all_toks = hyp_toks + cl_toks
    vecs = build_tfidf_vectors(all_toks)
    hyp_vecs = vecs[:len(hyp_toks)]
    cl_vecs = vecs[len(hyp_toks):]

    out = []
    for c, cv in zip(clusters, cl_vecs):
        best_sim, best_hyp = 0.0, None
        for hv, h in zip(hyp_vecs, hyps):
            if not hv or not cv:
                continue
            a, b = (cv, hv) if len(cv) <= len(hv) else (hv, cv)
            dot = sum(w * b.get(k, 0.0) for k, w in a.items())
            if dot > best_sim:
                best_sim, best_hyp = dot, h
        c["covered_sim"] = round(best_sim, 4)
        c["covered_by"] = best_hyp.get("id") if best_hyp else None
        c["covered_title"] = (best_hyp.get("title") if best_hyp else None)
        if best_sim < sim_threshold:
            out.append(c)
    return out


def build_proposal_prompt(clusters, sample_items=None):
    """让 AI 按项目既有 schema 提议假设。强制可证伪。

    **2026-09-28 设计转向**：首版靠 TF-IDF 聚类找候选主题，实测三处失效——
      ① 固定栏目成簇（华尔街见闻早餐）→ 加栏目过滤
      ② 同一事件被多家复述成簇（武契奇辞职）→ 加跨天闸门
      ③ 剩下来的仍是「美元指数播报」这类**数据流水**，不是主题
    根因：TF-IDF 只能测「词面相似」，测不出「是否值得建假设」。
    现在改为**双通道**：聚类结果只作为「热点提示」喂给 AI，真正的主题识别
    由 AI 从高分情报样本里做——它有能力区分「事件」「数据流水」「持续议题」。
    """
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    due_year = (now + timedelta(days=PROPOSAL_HORIZON_MONTHS * 30)).year

    blocks = []
    for i, c in enumerate(clusters, 1):
        titles = "\n".join(f"    - [{it['date']}] {it['title']}" for it in c["items"])
        blocks.append(f"{i}. 主题词: {', '.join(c['tokens'])}\n   代表情报:\n{titles}")

    hotspot_block = chr(10).join(blocks) if blocks else "（本期无聚类热点）"

    sample_block = ""
    if sample_items:
        lines = []
        for it in sample_items[:120]:
            t = (it.get("cn_title") or it.get("title") or "").strip()
            if len(t) < 10:
                continue
            lines.append(f"  [{str(it.get('published_at') or '')[:10]}] {t[:90]}")
        sample_block = chr(10).join(lines)

    return f"""你在为一个个人情报分析系统提议**可验证的新假设**。

**Current date: {today}** (year {now.year})

系统已有的 75 个假设覆盖这些方向：地缘冲突（台海/美伊/俄乌）、能源转型与供应格局、
AI 算力与成本、东亚社保趋同、青年就业、贸易摩擦、房地产、十五五产业政策。

**任务**：从下面的近期高分情报里，找出**现有假设未覆盖、且值得建立可验证假设**的主题。

### 通道一：自动聚类的热点（可能有参考价值，也可能是噪声）
{hotspot_block}

### 通道二：近期高分情报样本（更全面的语料）
{sample_block if sample_block else "（未提供样本）"}

**什么值得提议**（务必区分）：
- ✅ **持续议题**：跨天反复出现、有演化趋势、能被未来情报证伪的现象
  （如"某类政策转向正在发生""某国正在为 X 做准备"）
- ❌ **单一事件**：某公司发布产品、某人辞职、某次会议召开——是事实通报，不是假设
- ❌ **数据流水**：指数涨跌、汇率播报、每日行情——没有可证伪的断言
- ❌ **固定栏目**：早报/汇总/一览——发布格式，不是主题

输出 JSON 数组，每个元素：
{{
  "title": "假设标题（中文，30 字以内，陈述句）",
  "core_claim": "核心断言（一句话，说明什么会发生/正在发生）",
  "rationale": "为什么这么判断（基于上面哪些情报）",
  "indicators": [{{"name":"可量化指标","source":"数据来源","threshold_support":"支持阈值","threshold_refute":"证伪阈值"}}],
  "falsification_criteria": "什么情况下这个假设被证伪（必须具体）",
  "time_horizon_months": <验证窗口月数，6-24 之间的整数>,
  "base_confidence": <先验置信度 0-1>
}}

**硬性要求**（不满足会被程序丢弃）：
1. `falsification_criteria` 必须非空，且包含**具体年份或数值**——
   "情况好转"这类无法检验的表述会被拒绝。
2. `indicators` 至少 1 个，每个都要有 `threshold_refute`（证伪阈值）。
3. 所有年份必须是 {now.year} 或之后——**禁止引用 {now.year} 之前的年份作为验证时点**。
4. 不要编造统计数字；若基线未知，用相对表述（如"较 {now.year} 水平增长 20% 以上"）。
5. 假设必须**可被未来的情报证伪**——如果无论发生什么它都成立，不要提。
6. **宁缺勿滥**：如果语料里没有值得建假设的持续议题，返回空数组 `[]`。
   提出 1 个扎实的假设，胜过 10 个"某事件发生了"式的伪假设。"""


def _validate_proposal(p):
    """结构校验：不合格的提议丢弃并记原因（防 AI 产出无法证伪的废话假设）。

    2026-09-28 补强：首版只查 falsification_criteria 含年份/数值，
    实测漏过一条——rationale 里写"中东LNG占全球约45%"这种**编造的基线数字**
    （项目无此数据源），而假设的指标阈值正是建立在该数字上。
    现在对 indicators 的阈值也做「无据数字」检查：threshold_support/refute
    里出现具体百分比时，必须能追溯到项目已有数据源（macro_indicators /
    index_valuation / cn_unemployment_history 等），否则标记可疑。
    """
    reasons, warnings = [], []
    if not isinstance(p, dict):
        return False, ["不是对象"]
    for field in ("title", "core_claim", "falsification_criteria"):
        if not str(p.get(field) or "").strip():
            reasons.append(f"缺 {field}")

    fc = str(p.get("falsification_criteria") or "")
    # 必须含年份或数值
    has_year = bool(re.search(r"20\d{2}", fc))
    has_num = bool(re.search(r"\d+(?:\.\d+)?\s*(%|亿|万|倍|个|次|美元|元)", fc))
    if not (has_year or has_num):
        reasons.append("falsification_criteria 无具体年份或数值（无法检验）")

    # 年份不得过期（AGENTS.md 记录过的时间基准缺陷）
    this_year = datetime.now().year
    stale = [int(y) for y in re.findall(r"(20\d{2})", fc) if int(y) < this_year]
    if stale and not has_num:
        reasons.append(f"证伪判据含过期年份 {stale[:2]}")

    inds = p.get("indicators") or []
    if not isinstance(inds, list) or not inds:
        reasons.append("缺 indicators")
    else:
        ok_ind = [i for i in inds if isinstance(i, dict)
                  and i.get("name") and i.get("threshold_refute")]
        if not ok_ind:
            reasons.append("indicators 无 threshold_refute")

        # 无据数字检查：阈值里的具体数值必须带可核实的来源
        # 已知数据源白名单（项目实际抓取/归档的）
        known_sources = ("IEA", "BP", "EIA", "NBS", "国家统计局", "人社部", "财政部",
                         "发改委", "WorldBank", "世界银行", "FRED", "OECD", "SIPRI",
                         "Wind", "Bloomberg", "路透", "Reuters", "海关", "交易所",
                         "BIMCO", "上海航运", "MSCI", "IMF", "Crisis Group", "UN",
                         "央行", "统计局", "能源局", "工信部", "国务院")
        for ind in ok_ind:
            src = str(ind.get("source") or "")
            if not any(s.lower() in src.lower() for s in known_sources):
                warnings.append(f"指标「{str(ind.get('name'))[:20]}」来源未在已知白名单: {src[:40]}")

    try:
        c = float(p.get("base_confidence", 0.5))
        if not (0 < c < 1):
            reasons.append(f"base_confidence 越界: {c}")
    except (TypeError, ValueError):
        reasons.append("base_confidence 非数值")

    # warnings 不拒绝，但随提议落盘供人工过目
    p["_source_warnings"] = warnings
    return (not reasons), reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="取样语料天数窗口")
    ap.add_argument("--sample", type=int, default=1500, help="参与聚类的最大情报数")
    ap.add_argument("--limit", type=int, default=0, help="最多提议几个假设（0=全部候选）")
    ap.add_argument("--dry", action="store_true", help="只看候选簇，不调 AI")
    ap.add_argument("--write-tree", action="store_true",
                    help="把通过校验的提议物化进假设树（默认只落提议文件）")
    args = ap.parse_args()

    hyps = load_existing_hyps()
    print(f"[PROPOSE] 现有活跃假设 {len(hyps)} 个")

    items, lo, hi = load_recent_intel(days=args.days, limit=args.sample)
    print(f"[PROPOSE] 语料窗口 {lo} ~ {hi}，取 {len(items)} 条")

    clusters = find_candidate_clusters(items)
    print(f"[PROPOSE] 发现候选簇 {len(clusters)} 个（规模 >= {MIN_CLUSTER_SIZE}）")

    before = len(clusters)
    clusters = [c for c in clusters if not _is_template_cluster(c)]
    if before != len(clusters):
        print(f"[PROPOSE] 滤除固定栏目簇 {before - len(clusters)} 个"
              f"（早餐/汇总/一览等发布格式，非主题）")

    uncovered = filter_uncovered(clusters, hyps)
    print(f"[PROPOSE] 剔除已覆盖后剩 {len(uncovered)} 个（相似度阈值 {COVERED_SIM}）")

    if args.limit:
        uncovered = uncovered[:args.limit]

    # 双通道：聚类热点可能为空/噪声，AI 仍需从高分样本里识别主题
    sample = [it for it in items[:200]
              if len((it.get("cn_title") or it.get("title") or "").strip()) >= 10]

    if args.dry:
        print("\n候选热点（未调 AI）:")
        for i, c in enumerate(uncovered[:25], 1):
            print(f"  {i:2d}. size={c['size']:3d} days={c.get('day_count')} "
                  f"覆盖度={c['covered_sim']:.3f} 最近似={c.get('covered_by')} "
                  f"| {', '.join(c['tokens'][:6])}")
            for it in c["items"][:2]:
                print(f"        [{it['date']}] {it['title'][:70]}")
        print(f"\n[PROPOSE] 高分样本 {len(sample)} 条将一并喂给 AI（通道二）")
        print("[PROPOSE] 去掉 --dry 即调 AI 提议")
        return

    from analyze import MacroAnalyzer
    from load_knowledge import load_knowledge
    import yaml

    kb = load_knowledge(r"D:\Codex输出\视频知识库")
    try:
        kb.load_all()
    except Exception:
        pass
    config = yaml.safe_load((PROJECT / "config.yaml").read_text(encoding="utf-8"))
    analyzer = MacroAnalyzer(config, "", kb)

    prompt = build_proposal_prompt(uncovered, sample_items=sample)
    system = "You are a rigorous intelligence analyst. Propose only falsifiable hypotheses."
    print(f"[PROPOSE] 调用 AI 提议（prompt {len(prompt)} 字符）...")
    try:
        raw = analyzer._call_api(system, prompt, timeout=240)
    except Exception as ex:
        print(f"[PROPOSE] AI 调用失败: {str(ex)[:150]}")
        Path(BASE / "hypotheses" / "proposed_prompt_failed.md").write_text(
            prompt, encoding="utf-8")
        print("[PROPOSE] prompt 已落盘供重跑")
        return

    # 解析（复用括号配对抢救思路：截断时保住前面完整的对象）
    text = re.sub(r"```json\s*", "", raw)
    text = re.sub(r"```\s*$", "", text)
    start = text.find("[")
    parsed = []
    if start >= 0:
        depth, obj_start = 0, None
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                if depth == 0:
                    obj_start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and obj_start is not None:
                    try:
                        parsed.append(json.loads(text[obj_start:i + 1]))
                    except json.JSONDecodeError:
                        pass
                    obj_start = None

    print(f"[PROPOSE] AI 返回 {len(parsed)} 条提议，开始校验...")
    accepted, rejected = [], []
    for p in parsed:
        ok, reasons = _validate_proposal(p)
        # cluster_index 为可选（双通道后 AI 可能基于样本而非聚类提议）
        ci = p.get("cluster_index")
        src = None
        if isinstance(ci, int) and 0 < ci <= len(uncovered):
            src = uncovered[ci - 1]
        rec = {"proposal": p, "cluster": src, "reasons": reasons}
        (accepted if ok else rejected).append(rec)

    print(f"[PROPOSE] 通过 {len(accepted)} 条 / 拒绝 {len(rejected)} 条")
    for r in rejected:
        print(f"  拒绝: {str(r['proposal'].get('title'))[:40]} <- {'; '.join(r['reasons'])}")

    now_s = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    out_file = BASE / "hypotheses" / f"proposed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_file.write_text(json.dumps({
        "at": now_s, "window": f"{lo} ~ {hi}", "samples": len(items),
        "clusters_found": len(clusters), "uncovered": len(uncovered),
        "accepted": accepted, "rejected": rejected,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[PROPOSE] 提议已落盘: {out_file}")

    if accepted:
        print("\n=== 通过的提议 ===")
        for r in accepted:
            p = r["proposal"]
            print(f"\n[{p.get('title')}]  先验={p.get('base_confidence')} "
                  f"窗口={p.get('time_horizon_months')}月")
            print(f"  断言: {str(p.get('core_claim'))[:110]}")
            print(f"  证伪: {str(p.get('falsification_criteria'))[:110]}")
            for w in (p.get("_source_warnings") or []):
                print(f"  ⚠️ {w}")

    if args.write_tree and accepted:
        nodes = json.loads(HYP_FILE.read_text(encoding="utf-8"))
        existing_ids = {n.get("id") for n in nodes}
        added = 0
        for i, r in enumerate(accepted, 1):
            p = r["proposal"]
            hid = "hyp_prop_" + datetime.now().strftime("%Y%m%d") + f"_{i:02d}"
            if hid in existing_ids:
                continue
            horizon = p.get("time_horizon_months") or PROPOSAL_HORIZON_MONTHS
            due = (datetime.now(timezone.utc)
                   + timedelta(days=int(horizon) * 30)).strftime("%Y-%m-%d")
            nodes.append({
                "id": hid,
                "title": str(p.get("title"))[:60],
                "level": PROPOSAL_LEVEL,
                "direction": "toward",
                "confidence": float(p.get("base_confidence", 0.5)),
                "base_confidence": float(p.get("base_confidence", 0.5)),
                "status": "active",
                "core_claim": p.get("core_claim"),
                "rationale": p.get("rationale"),
                "indicators": p.get("indicators") or [],
                "falsification_criteria": p.get("falsification_criteria"),
                "deadline": due,
                "deadline_source": "ai_proposed",
                "created": now_s,
                "source": "propose_hypotheses",
                "evidence_log": [],
            })
            added += 1
        HYP_FILE.write_text(json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[PROPOSE] 已物化 {added} 个节点进假设树（level={PROPOSAL_LEVEL}）")


if __name__ == "__main__":
    main()
