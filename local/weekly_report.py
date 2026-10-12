# -*- coding: utf-8 -*-
r"""weekly_report.py - 参谋周报的共享构建逻辑（2026-10-12 抽出）

## 为什么抽出来

周报原本**只有一条生成路径**：`local/hypothesis_engine._save_ai_weekly_summary`，
由本地周循环（OsintWeekly 周一 09:30）调用。它是一段 ~300 行、经多轮实测调优的
逻辑——源配额/主题配额取样、60 条席位分配、ACH 排名、反幻觉护栏、内容安全降级——
这些参数都是踩过坑才定下来的（见 AGENTS.md 各条记录）。

2026-10-12 出现的问题：**周循环在 AI 通道断供时会静默挂死**（实测零 CPU/零网络
超时 5 分钟以上，无声退出、周报未产出），而云端 CI 同一天的 AI 通道是好的。
于是决定让周报也上云。但**不能在 cloud/ 里复制一份 prompt**——两套 prompt 必然
漂移，且调优经验会分裂。所以把"取材 + 组 prompt + 渲染"抽成本模块，两边共用：

    local/hypothesis_engine.py        调用本模块，AI 用 analyzer._call_api
    cloud/weekly_report_cloud.py      调用本模块，AI 用云端的通道链

**本模块只做纯逻辑，不碰网络、不写盘**——AI 文本由调用方传入 render 函数。
这样"谁的 AI 调用成功"与"周报长什么样"解耦，两边出的周报结构完全一致。

## 契约

    ctx, system, prompt = build_weekly_context(hyps, ach_matrix, intel_items, week_offset)
    md = render_weekly_markdown(ctx, summary_text)

`ctx` 是普通 dict（可 json 序列化），含窗口/统计/各区块文本，便于调试与落盘。
"""
from datetime import datetime, timedelta

# 取样配额（2026-10-06 B 方案，实测校准：源数 3→12、主题覆盖 6 类不均 → 7 类均衡各 8~10 条）
SRC_CAP = 6      # 单源最多 6 条（60 席 / 约 12 源）
THEME_CAP = 8    # 单主题最多 8 条

# 主题判据：与取样、显示**同一套**关键词，保证口径一致。
# 2026-10-06 修：显示层原用 category_cn（源类别，46/60 条都是 finance）→
# 把配额取样刚换来的均衡又抹平了。
THEME_KWS = {
    "就业/劳动": ["就业", "失业", "毕业生", "招聘", "裁员", "工资", "劳动", "岗位"],
    "社保/民生": ["养老金", "社保", "医保", "退休", "房价", "楼市", "物价", "消费"],
    "地缘/冲突": ["冲突", "战争", "袭击", "制裁", "导弹", "停火", "大选", "选举",
               "议会", "外交", "war", "strike", "military", "summit", "ceasefire"],
    "能源/大宗": ["油价", "原油", "石油", "天然气", "OPEC", "铜", "黄金", "大宗",
               "能源", "oil", "gas", "gold", "copper", "crude"],
    "科技/AI": ["AI", "人工智能", "芯片", "半导体", "算力", "机器人", "数据中心",
              "chip", "semiconductor", "OpenAI", "Nvidia"],
    "贸易/供应链": ["关税", "贸易", "出口", "进口", "供应链", "tariff", "trade", "export"],
}

SYSTEM_PROMPT = (
    "你是一个中文政治经济分析师，面向'中国年轻失业毕业生'视角，浓缩上周情报、"
    "ACH 假设变化、给出对个人影响 + 下周预判。语气清醒、有批判性，避免空话和"
    "政治套话。不要说'我们要相信国家'这种官话。"
)


def week_window(week_offset=0):
    """上一个**完整**周窗口：上周一 00:00 ~ 上周日 23:59:59（本地时间）。

    2026-10-05 修：原 week_offset=0 取"本周一至今"，而周报在**周一生成**，
    该窗口只有几小时数据（实测 10-05 只统计到 159 条，AI 因此误判"数据严重
    不足，仅 7 条可用"；而上一完整周实际有 12102 条）。标题/正文本就写
    "上周情报总览"，窗口必须与语义一致 → 改为上一个完整周。
    """
    today_local = datetime.now()
    this_monday = (today_local - timedelta(days=today_local.weekday())
                   ).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = this_monday - timedelta(seconds=1)
    week_start = this_monday - timedelta(days=7)
    if week_offset:
        week_start -= timedelta(days=7 * week_offset)
        week_end -= timedelta(days=7 * week_offset)
    return week_start, week_end


def filter_week_intel(intel_items, week_start, week_end):
    """按发布时间过滤出窗口内的情报"""
    lo = week_start.strftime("%Y-%m-%d")
    hi = week_end.strftime("%Y-%m-%d")
    out = []
    for it in (intel_items or []):
        pub = (it.get("published_at") or "")[:10]
        if lo <= pub <= hi:
            out.append(it)
    out.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return out


def intel_rank(it):
    """重要度排序键。relevance 是死字段（恒 0，见 AGENTS.md 筛选算法修复节），
    所以用 final_score，回退 base_score。"""
    for k in ("final_score", "base_score"):
        v = it.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return 0.0


def theme_of(it):
    t = (it.get("cn_title") or it.get("title") or "")
    for th, ws in THEME_KWS.items():
        if any(w in t for w in ws):
            return th
    return "其他"


def _is_column(it):
    """固定栏目条判定（多主题打包，占席位不提供独立信息）。
    判据 import 自生产模块；缺失时降级为"都不是栏目条"。"""
    try:
        from local.intel_gate import is_column_item
        return bool(is_column_item(it))
    except Exception:
        return False


def sample_intel(week_intel):
    """源配额 + 主题配额两轮取样（第一轮严格配额，第二轮放宽填满 60 席）。

    2026-10-06（B 方案）：纯按分数取样会**严重偏科**——实测上周 14860 条取前 60，
    来源 100% 集中在金十/新浪/财联社 3 个高频源，主题 72% 是金融/美联储，
    地缘（加沙停火、巴西大选）与科技（诺贝尔奖、英伟达）一条都进不来。
    原因：高频快讯源权重高（1.2）+ 发得多，天然霸榜。
    """
    ranked = sorted(week_intel, key=intel_rank, reverse=True)
    pool = [x for x in ranked if not _is_column(x)]
    # 去重（同标题前 24 字符）
    seen, dedup = set(), []
    for x in pool:
        k = (x.get("cn_title") or x.get("title") or "")[:24]
        if k and k in seen:
            continue
        seen.add(k)
        dedup.append(x)

    chosen, out = set(), []
    src_cnt, th_cnt = {}, {}
    for strict in (True, False):
        for x in dedup:
            if len(out) >= 60:
                break
            if id(x) in chosen:
                continue
            s = x.get("source_name") or x.get("source") or "_unknown"
            th = theme_of(x)
            if strict and (src_cnt.get(s, 0) >= SRC_CAP or th_cnt.get(th, 0) >= THEME_CAP):
                continue
            chosen.add(id(x))
            out.append(x)
            src_cnt[s] = src_cnt.get(s, 0) + 1
            th_cnt[th] = th_cnt.get(th, 0) + 1
        if len(out) >= 60:
            break
    return out


def build_themes_block(sampled):
    """按主题分组渲染（每主题最多 5 条，按分数降序）"""
    themes = {k: [] for k in THEME_KWS}
    themes["其他"] = []
    for i in sampled[:60]:
        t = i.get("cn_title") or i.get("title") or ""
        impact = i.get("graduate_impact") or i.get("impact") or ""
        themes[theme_of(i)].append((intel_rank(i), t[:60], impact[:50]))

    lines = []
    for theme, items in themes.items():
        if not items:
            continue
        items.sort(key=lambda x: x[0], reverse=True)
        lines.append("### %s（%d 条）" % (theme, len(items)))
        for score, t, imp in items[:5]:
            line = "- (R%s) %s" % (score, t)
            if imp:
                line += " → 个人: " + imp
            lines.append(line)
        lines.append("")
    return "\n".join(lines) if lines else "（无）"


def build_ach_section(ach_matrix, hyps):
    if not (ach_matrix and ach_matrix.get("scoring")):
        return ""
    top = sorted(ach_matrix["scoring"].items(),
                 key=lambda kv: kv[1].get("posterior", 0), reverse=True)[:5]
    lookup = {h["id"]: h.get("title", h["id"]) for h in hyps}
    rows = []
    for hid, s in top:
        rows.append("- %s | 后验 %.2f | 支 %s 驳 %s"
                    % (lookup.get(hid, hid)[:40], s.get("posterior", 0),
                       s.get("support", 0), s.get("refute", 0)))
    return "## ACH 排名 Top 5\n" + "\n".join(rows)


def build_hyp_sections(hyps, week_start):
    """活跃假设 / 上周新增 / 上周验证动态 三段"""
    active = [h for h in hyps if h.get("status") == "active"]
    lo = week_start.strftime("%Y-%m-%d")
    verified = [h for h in hyps if h.get("last_verified")
                and (h.get("last_verified") or "")[:10] >= lo]
    falsified = [h for h in hyps if h.get("status") == "falsified"]
    new_hyps = [h for h in hyps
                if h.get("created_at") and h["created_at"][:10] >= lo]

    lines = []
    for h in active[:8]:
        try:
            conf = "%.2f" % float(h.get("confidence", 0))
        except (TypeError, ValueError):
            conf = str(h.get("confidence", "-"))
        lines.append("- [%s] %s (conf %s, last_verified %s)"
                     % (h.get("level", "?"), h.get("title", "?"), conf,
                        h.get("last_verified", "-")))
    hyp_section = "## 活跃假设（前 8）\n" + "\n".join(lines)

    new_section = "## 上周新增假设\n" + (
        "\n".join("- %s" % h.get("title", "?") for h in new_hyps[:10]) if new_hyps else "（无）")

    verif_section = "## 上周验证动态\n"
    if verified or falsified:
        for h in verified[-5:]:
            verif_section += "- ✓ %s: %s\n" % (h.get("title", "?"),
                                                h.get("verification_result", "?"))
        for h in falsified[-5:]:
            verif_section += "- ✗ %s: %s\n" % (h.get("title", "?"),
                                                h.get("verification_result", "?"))
    else:
        verif_section += "（无到期验证）\n"

    return active, verified, falsified, new_hyps, hyp_section, new_section, verif_section


def build_prompt(ctx):
    """组 prompt。反幻觉护栏与要求段完整保留（见 AGENTS.md 9-16 记录）。"""
    return """请基于以下材料，写一份「参谋周报」总结，分四段：

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

## 上周情报总条数：{total}（活跃过滤后）

{ach_section}

{hyp_section}

{new_section}

{verif_section}

---

要求：
- 直接给结论，不要列点列举所有数据
- 对"中国年轻失业毕业生"视角要具体到行为层面（投简历方向、技能学习、避免的坑）
- 语气：参谋而非导师，敢说"这周最重要的信号是 X"
- 字数：每段严格控制在提示字数内，总长 800-1000 字
- 反幻觉（2026-09-16 加，9-14 周报曾把 0 条情报硬解读为"空白本身是最强信号"）：
  若上周情报总条数为 0 或极少（<10），第 1 节必须如实写"本周无足够情报数据，以下判断仅基于假设矩阵存量"，禁止把数据缺失解读为任何"信号"；第 4 节预判须明确标注"数据不足，仅供参考"；绝不编造未在材料中出现的事件或数据""".format(
        themes_block=ctx["themes_block"],
        total=ctx["week_intel_total"],
        ach_section=ctx["ach_section"],
        hyp_section=ctx["hyp_section"],
        new_section=ctx["new_section"],
        verif_section=ctx["verif_section"],
    )


def build_weekly_context(hyps, ach_matrix=None, intel_items=None, week_offset=0):
    """构建周报全部素材与 prompt。返回 (ctx, system, prompt)。

    ctx 为可 json 序列化的 dict，含窗口/统计/各区块文本与渲染所需字段。
    """
    week_start, week_end = week_window(week_offset)
    week_intel = filter_week_intel(intel_items, week_start, week_end)
    sampled = sample_intel(week_intel)
    themes_block = build_themes_block(sampled)

    # 内容安全降级版 prompt 要用（去掉逐条标题，保留聚合统计）
    active, verified, falsified, new_hyps, hyp_section, new_section, verif_section = \
        build_hyp_sections(hyps, week_start)

    ctx = {
        # 文件名日期用 UTC——与 engine 原实现一致（backfill_weekly 也按 UTC 找产物）。
        # 注意窗口仍按本地时间算（周一是本地语义），两者混用是既有行为，勿"顺手统一"。
        "date_str": datetime.utcnow().strftime("%Y%m%d"),
        "week_start": week_start.strftime("%Y-%m-%d"),
        "week_end": week_end.strftime("%Y-%m-%d"),
        "week_start_md": week_start.strftime("%m-%d"),
        "week_end_md": week_end.strftime("%m-%d"),
        "week_intel_total": len(week_intel),
        "themes_block": themes_block,
        "ach_section": build_ach_section(ach_matrix, hyps),
        "hyp_section": hyp_section,
        "new_section": new_section,
        "verif_section": verif_section,
        "n_active": len(active),
        "n_verified": len(verified),
        "n_falsified": len(falsified),
        "n_new": len(new_hyps),
        "sampled": len(sampled),
    }
    return ctx, SYSTEM_PROMPT, build_prompt(ctx)


def safe_prompt_for(ctx):
    """内容安全拦截时的降级 prompt：去掉逐条情报标题，只保留统计与矩阵。

    2026-10-08 实测：dots 网关对 prompt 做内容审查，周报 prompt 会把 60 条情报标题
    原样塞进去——08-31~09-06 那周有 145 条涉政条目，整体送审被 403
    `governance.content_safety_input_rejected`，**整份周报的 AI 总结退化成占位文本**。
    周报 prompt 是整体不可拆（不是可二分隔离的条目列表），所以用降级而非拆半。
    """
    ctx2 = dict(ctx)
    ctx2["themes_block"] = ("（逐条情报标题因内容安全策略省略；"
                            "以下结论基于上方统计与假设矩阵）")
    return build_prompt(ctx2)


def render_weekly_markdown(ctx, summary_text, degraded=False, generator=None):
    """把 AI 文本 + ctx 渲染成最终 markdown。

    generator: 页脚署名。默认按 ctx.get("generator") 取，缺省写本模块名——
    本地与云端调用方各自标明来源，便于事后判断"这份周报是谁生成的"。
    """
    out = ["# 参谋周报 - " + ctx["date_str"], ""]
    out.append("> 上周（%s ~ %s） | 视角：公民 + 年轻失业毕业生 | 来源：ACH 矩阵 + 假设验证 + 情报"
               % (ctx["week_start_md"], ctx["week_end_md"]))
    out.append("")
    out.append("## AI 总结")
    out.append("")
    if degraded:
        out.append("（注：本周情报标题因第三方内容安全策略未能送入 AI，"
                   "以下总结基于统计与假设矩阵。）")
        out.append("")
    out.append(summary_text)
    out.append("")
    out.append("---")
    out.append("")
    out.append("## 机器索引")
    out.append("")
    out.append("### 上周情报总览（%s ~ %s）" % (ctx["week_start"], ctx["week_end"]))
    out.append("")
    out.append("- 总条数：%s" % ctx["week_intel_total"])
    out.append("- 活跃假设：%s | 已验证：%s | 已证伪：%s | 新增：%s"
               % (ctx["n_active"], ctx["n_verified"], ctx["n_falsified"], ctx["n_new"]))
    out.append("")
    out.append("### 按主题分类")
    out.append("")
    out.append(ctx["themes_block"])
    out.append("")
    out.append(ctx["ach_section"])
    out.append("")
    out.append(ctx["hyp_section"])
    out.append("")
    out.append(ctx["new_section"])
    out.append("")
    out.append(ctx["verif_section"])
    out.append("")
    out.append("---")
    out.append("Generated by " + (generator or "weekly_report.render_weekly_markdown"))
    return "\n".join(out)
