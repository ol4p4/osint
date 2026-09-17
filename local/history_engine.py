r"""history_engine.py - 大历史认识档案生产管线（骨架+枝叶架构）

反幻觉反立场靠架构不靠微调：双稿对照 + 数值锚定 + 裁判核查 + 用户主编。
AI 全部走 analyze.MacroAnalyzer._call_api（硬超时/模型降级链/opencode.ai 白名单现成）；
裁判/副笔链路不注入三观（护栏沿用，保持核查客观）。

用法：
  python local/history_engine.py --outline                          # 分期表草案（总闸门）
  python local/history_engine.py --draft 00-skeleton 2              # 骨架第 2 期双稿
  python local/history_engine.py --draft 10-经济 1                  # 枝叶第 1 期（自动挂骨架）
  python local/history_engine.py --verify <drafts 文件名>           # 数值锚校验 + 幻觉裁判
  python local/history_engine.py --publish <drafts 文件名>          # 校订后转正 + index/log
  python local/history_engine.py --revision-suggest                 # 假设漂移 → 修订提示

产物：
  正式档案  D:\Codex输出\视频知识库\wiki\macro-history\
  工作区    D:\Codex输出\视频知识库\wiki\macro-history\drafts\
  （2026-09-16 目录由 history/ 更名 macro-history/：避免与对话记录/版本历史混淆，
   macro-history 取大历史观（macrohistory，布罗代尔长时段传统）之义）
"""
import argparse
import copy
import json
import re
import sys
import time as _time
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(r"D:\osint")
DATA = PROJECT / "data"
KB_WIKI = Path(r"D:\Codex输出\视频知识库\wiki")
HIST = KB_WIKI / "macro-history"
DRAFTS = HIST / "drafts"
DATA_DIR = HIST / "data"

NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d+)?)\s*(?:%|个百分点|倍|万亿|亿|万)?(?![\w.\d])")
YEAR_RE = re.compile(r"^(19|20)\d{2}$")
ANCHOR_RE = re.compile(r"【据[:：][^】]*】|【无据待查】")

MAX_MATERIAL_CHARS = 45000
PER_MATERIAL_CHARS = 6000
DRAFT_CHARS = "800-1200"
SKELETON = "00-skeleton.md"


def _log(msg):
    print(f"[HIST] {msg}")


def _load_analyzers():
    """主笔=Mimo 主链；副笔/裁判=Nemotron 优先，失败降级到其他异血统模型。
    MacroAnalyzer 构造本身不发起网络请求，_call_api 内置硬超时与降级。
    2026-09-16：nemotron 长生成偶发返回思维链（非正文），单模型链会直接产出坏稿，
    故给副笔挂降级链（排除 mimo，保证异血统对照不失效）。"""
    import yaml
    from analyze import MacroAnalyzer

    cfg = yaml.safe_load((PROJECT / "config.yaml").read_text(encoding="utf-8"))
    main = MacroAnalyzer(cfg, "", None)
    fbs = cfg.get("api", {}).get("fallback_models", []) or []
    nemotron = None
    others = []
    for fb in fbs:
        name = str(fb.get("model", ""))
        if "nemotron" in name:
            nemotron = dict(fb)
        else:
            others.append(dict(fb))
    if nemotron:
        sub_cfg = copy.deepcopy(cfg)
        sub_cfg["api"]["model"] = nemotron["model"]
        sub_cfg["api"]["fallback_models"] = others
        sub = MacroAnalyzer(sub_cfg, "", None)
    else:
        sub = main
        _log("WARN: 未找到 nemotron 副笔配置，双稿退化为同模型双稿（立场对照失效）")
    return main, sub


def _materials():
    """材料包：data/ 手工表 + 失业率历史序列摘要 + 宏观快照。供 --draft/--verify 共用。
    2026-09-16 修复：原上限 12000 字符而 data/ 表已达 23000 字符，按字母序整包截断
    把经济/社运/全球三类表全部挤出 AI 视野（V3 草案锚点名凭空捏造的根因）。
    现改为逐表限额 + 总量上限，单表超限只截该表并显式标注。"""
    parts = []
    if DATA_DIR.exists():
        for f in sorted(DATA_DIR.glob("*")):
            if f.name.upper().startswith("README") or not f.is_file():
                continue
            try:
                txt = f.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                continue
            if txt:
                if len(txt) > PER_MATERIAL_CHARS:
                    txt = txt[:PER_MATERIAL_CHARS] + "\n(本表超长截断)"
                parts.append(f"### 材料:{f.name}\n{txt}")
    try:
        uh = json.loads((DATA / "cn_unemployment_history.json").read_text(encoding="utf-8"))
        lines = ["### 材料:cn_unemployment_history（失业率序列摘要）"]
        for name, pts in (uh.get("series") or {}).items():
            seen = {}
            for p in pts:
                y = str(p.get("date", ""))[:4]
                if y and y not in seen:
                    seen[y] = p.get("value")
            compact = ", ".join(f"{y}:{v}" for y, v in sorted(seen.items())[:26])
            lines.append(f"- {name}: {compact}")
        parts.append("\n".join(lines)[:PER_MATERIAL_CHARS])
    except Exception as e:
        _log(f"失业率序列读取失败(跳过): {e}")
    try:
        mi = json.loads((DATA / "macro_indicators.json").read_text(encoding="utf-8"))
        parts.append(f"### 材料:macro_indicators(最新快照)\n{json.dumps(mi, ensure_ascii=False)[:PER_MATERIAL_CHARS]}")
    except Exception as e:
        _log(f"宏观快照读取失败(跳过): {e}")
    pack = "\n\n".join(parts)
    if len(pack) > MAX_MATERIAL_CHARS:
        _log(f"WARN 材料包总量 {len(pack)} 超上限 {MAX_MATERIAL_CHARS}，尾部截断（考虑精简 data/ 表）")
        pack = pack[:MAX_MATERIAL_CHARS] + "\n(材料包总量截断)"
    return pack


def _material_numbers(pack):
    """材料包中出现的全部数值字符串（规范化），供数值锚比对。"""
    nums = set()
    for m in re.finditer(r"\d+(?:\.\d+)?", pack):
        nums.add(m.group(0).lstrip("0") or "0")
        nums.add(m.group(0))
    return nums


def _material_names(pack):
    """材料包中实际存在的表名集合（含去扩展名/去括号注的短名），供锚点名比对。"""
    names = set()
    for m in re.finditer(r"^### 材料:(\S+)", pack, re.M):
        full = m.group(1)
        names.add(full)
        names.add(full.split(".")[0])
        short = re.sub(r"[（(].*$", "", full)
        names.add(short)
        names.add(short.replace(".md", "").replace(".json", ""))
    return names


def _bad_anchor_names(text, mat_names):
    """锚点名校验：正文【据:xx】里的 xx 必须真实存在于材料包。
    2026-09-16 新增——V3 草案出现 gdp_structure/debt_structure 等凭空锚点名，
    原校验器只查数字不查锚名，整批假锚漏网。"""
    bad = []
    for m in re.finditer(r"【据[:：]([^】]+)】", text):
        for name in re.split(r"[,，、;；\s]+", m.group(1).strip()):
            if name and name not in mat_names:
                bad.append(name)
    return sorted(set(bad))


def _skeleton_text():
    f = HIST / SKELETON
    if not f.exists():
        raise FileNotFoundError(f"骨架不存在: {f}（先跑 --outline）")
    return f.read_text(encoding="utf-8")


def _final_text(text):
    """主编定稿优先：双稿文件含「## 定稿」区时，verify/publish 只处理该区
    （否则会把 A稿/B稿/裁判清单一起当正文校验和转正）。
    2026-09-16：混合稿裁决流程需要。"""
    m = re.search(r"^## 定稿[^\n]*$", text, re.M)
    if not m:
        return text
    rest = text[m.end():]
    n = re.search(r"^## ", rest, re.M)
    return (rest[:n.start()] if n else rest).strip()


def _page_spec(page):
    """页面元信息：骨架还是枝叶、输出标题、核查级别。"""
    if page.startswith("00"):
        return {"kind": "skeleton", "title": "元叙事骨架", "level": "strict"}
    if page.startswith("10"):
        return {"kind": "branch", "title": "经济枝叶", "level": "numeric"}
    if page.startswith("20"):
        return {"kind": "branch", "title": "政治治理枝叶", "level": "strict"}
    if page.startswith("30"):
        return {"kind": "branch", "title": "社会民生枝叶", "level": "normal"}
    if page.startswith("40"):
        return {"kind": "branch", "title": "对外关系枝叶", "level": "strict"}
    if page.startswith("50"):
        return {"kind": "branch", "title": "文化思潮枝叶", "level": "culture"}
    return {"kind": "branch", "title": page, "level": "normal"}


WRITER_RULES = """你是用户的历史分析撰稿人。硬规则：
1. 事实与数字分离：正文出现的任何具体数字（百分比、规模、年份事件）必须来自【材料包】，并在句末标注【据:材料名】；材料包没有的，写【无据待查】，禁止编造。
2. 区分【事实】（有据可查）与【判断】（你的分析），判断必须显式以"判断："开头。
3. 立场显式化：分析使用用户四维框架（积累制度/空间修正/国家-市场边界/阶级利益），不使用官方宣传腔，也不用西方媒体腔，用冷静的结构分析语言。
4. 只写给定节的内容，长度{chars}字，不要展开其他节。
5. 输出纯 markdown 正文（不要 frontmatter，不要标题级 H1），各小节用 H3。"""


def _worldview_for_writer():
    """主笔注入用户三观（历史撰稿人应有用户的历史观视角）。
    2026-09-16 补设计缺口；失败降级空串。副笔/裁判不注入（保持核查客观）。"""
    try:
        sys.path.insert(0, str(PROJECT))
        from worldview_loader import build_worldview_prompt
        wv = build_worldview_prompt()
        return ("\n\n【用户三观】你的历史叙事与分析视角必须贴合此个人视角：\n" + wv + "\n") if wv else ""
    except Exception:
        return ""


def _anchor_legend(pack):
    """合法锚点名清单（注入 prompt 用）：正文【据:xx】只能用这些名字。"""
    names = sorted(n for n in _material_names(pack) if "." not in n and "（" not in n and "(" not in n)
    if not names:
        return ""
    return ("\n## 合法锚点名（【据:xx】只能用以下名字，写错即判定为编造）\n"
            + "、".join(names) + "\n")


def cmd_outline():
    """总闸门：只产出分期表草案，等主编逐期确认。"""
    main, _ = _load_analyzers()
    pack = _materials()
    system = WRITER_RULES.format(chars="不限") + _worldview_for_writer() + """
6. 本任务只输出「分期表」，不写叙事正文。"""
    user = f"""任务：为《中国如何走到今天》综合大历史档案起草**分期表**。

主编指令（2026-09-16 V4，必须落实）：
A. **每维一条贯穿主线**（认识是全面而综合的，经济不是孤立发展的——五维同等承重，每格都要
   有"目标-实效对证"深度而非事件罗列）：
   经济=债务与规划线（城投/土地财政 + 五年计划目标vs实效）；政治治理=制度化承诺vs执行落差
   （考核指挥棒变迁）；社会民生=青年失业形成史（扩招→学历通胀→体制内外二元→灵活就业）；
   对外=融入体系→利用体系→体系内反噬→体系外对冲四段；文化=官方叙事vs民间思潮（三次青年
   心态大讨论为期界标志）。
B. **维度互动链清单**（分期表之后必须附）：提炼 6-8 条跨期跨维的因果链，每条一句话讲清
   传导机制，例如：土地财政(经济)→房价(社会)→婚育推迟(人口/社会)→躺平叙事(文化)；
   分税制(政治)→平台经济(经济)→化债(经济)→社保缴费可持续性(社会)。
C. 各维要点必须引用下方材料包的真实节点并标注【据:表名】；**锚点名只准用「合法锚点名」清单里的**，
   编造表名会被校验器判定为幻觉。
D. 政治维度须吸收 social movements 档案（群体性事件类型学/标志案例/治理工具）；
   **P3-P6 期政治列必须引用【据:political_movements_archive】**（如厦门 PX 2007、乌坎 2011、
   茂名 PX 2014、烂尾楼停贷潮 2022 等标志案例），不得只写"群体性事件上升"这类无锚表述。
E. **全球对照是独立输出节，不是附注**：表后除「转折逻辑」「维度互动链」外，还必须附
   「### 全球同期对照」一节（markdown 表格，列=中国分期/全球同期主要运动/时间重合但诉求结构
   不同的具体差异），覆盖 P3-P7 全部五期。**全球侧每行的运动列举必须标注【据:global_protest_comparison】**，
   中国侧对照案例标注【据:political_movements_archive】，差异分析须落到"诉求性质/组织形态/结果模式"
   三轴中的至少两轴。禁止只写中方不引全球表。
F. 文化维度须吸收词库档案【据:culture_internet_lexicon】（性别议题谱系/政治身份标签/
   民族主义变种/舆论反转机制/2026 男性议题簇）。
G. 最新数据（当期失业率等）优先锚【据:cn_unemployment_history】或【据:macro_indicators】。
H. **五年规划四步对证是全书经济叙事的主轴**（主编框架，必须落实）：每个规划期都要走
   「①规划目标 → ②政策发布 → ③落地结果 → ④成败归因」四步，归因必须连回该期主导矛盾。
   材料：【据:fyp_attribution】（四步链条+归因）、【据:fyp_target_vs_actual】（目标-实效两列）、
   【据:policy_timeline】（政策发布节点）。**特别注意"两套网格的错位"**——分期边界（政治经济阶段）
   与规划边界（五年行政周期）不对齐处即分析价值最高处，经济列须写出"该期规划是否被中断、被什么中断"。
I. **规划是闭环不是单期评估**（主编构想）：四步之后必须接第五步——**"本期遗留问题如何改写成
   下一期规划的目标/指标"**。材料：【据:fyp_cycle】（问题继承链+修正层级+复发型问题清单）。
   三条硬要求：①写清"上期问题→下期回应"的交接（如七五价格闯关失败→八五设 6% 恢复性低目标）；
   ②区分**工具层修正 vs 制度层修正**——地方债务/房地产/青年就业均属跨 6 轮/25 年复发的
   制度层问题，反复用工具层手段应对是统一机制；③**规划指标的演变史 = 上一轮问题的清单**
   （增长方式转变/就业硬指标/约束性指标/增速下调/防风险优先/不设增速，每个"首次"都对应一次失败）。

要求：
1. 分 7 期左右（1978-1992 / 1992-2001 / 2001-2008 / 2008-2015 / 2015-2020 / 2020-2024 / 2024-今），主编可改。
2. 每期给出：起止年、期名、该期主导矛盾（一句话）、五个维度各一句要点（须体现该维主线在该期的具体形态）、期终标志事件。
3. 期与期的转折逻辑必须一句话讲清。
{_anchor_legend(pack)}
## 材料包（数字与锚点的唯一合法来源）
{pack if pack else "（空：全部数字请标【无据待查】）"}

输出：markdown 表格（列=#/期次/起止/期名/主导矛盾/经济/政治治理/社会民生/对外关系/文化思潮/期终标志事件），表后附「转折逻辑」清单、「维度互动链」清单、「全球同期对照」清单共三节。"""
    _log("起草分期表（主笔 Mimo）…")
    try:
        out = main._call_api(system, user)
    except Exception as e:
        _log(f"AI 调用失败: {e}")
        (DRAFTS / "_prompt_outline.md").write_text(
            f"# 重跑提示\n\nAI 失败可稍后重跑 `--outline`。本次 prompt 已存档。\n\n## system\n\n{system}\n\n## user\n\n{user}",
            encoding="utf-8")
        return 1
    target = DRAFTS / "outline_分期表.md"
    body = (
        "---\ntitle: 分期表草案（待主编确认）\ncreated: %s\ntype: outline\nstatus: draft\n---\n\n"
        "> 主编逐期确认/修改后，把确认版贴回 `wiki/macro-history/00-skeleton.md` 的分期表并起草骨架正文。\n\n"
        % datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ) + out
    target.write_text(body, encoding="utf-8")
    _log(f"分期表草案 → {target}")
    return 0


def cmd_draft(page, sec):
    """双稿起草一节 + 副笔列分歧。骨架先于枝叶。"""
    spec = _page_spec(page)
    skel = _skeleton_text()
    if spec["kind"] == "branch" and "| # |" in skel and "待定 | 待定 |" in skel:
        _log("骨架分期表仍是占位（未确认锁定）——先确认分期表并起草骨架，枝叶再开工。")
        return 1
    main, sub = _load_analyzers()
    pack = _materials()
    if not pack:
        _log("材料包为空：往 wiki/macro-history/data/ 放数据表后重试（数值锚会全部失败）")
    period_hint = f"骨架对应分期见下方材料。本节只写分期表第 {sec} 期相关内容。" if spec["kind"] == "skeleton" \
        else f"枝叶章节编号严格对齐骨架分期：本节只写骨架分期表中第 {sec} 期，期名与主导矛盾必须引用骨架原文。"
    # 经济枝叶强制四步对证（主编框架）：规划→政策发布→落地结果→成败归因
    if page.startswith("10"):
        period_hint += """
本节为经济枝叶，必须走**规划闭环**结构（主编框架，五步）：
① 上期遗留问题（上期归因如何变成本期议程？【据:fyp_cycle】问题继承链）
② 本期规划目标（【据:fyp_target_vs_actual】）
③ 对应政策发布节点（【据:policy_timeline】/【据:lgfai_debt_timeline】）
④ 落地结果（达成/超额/落空的实测口径）
⑤ 成败归因 + 本期遗留问题（【判断】性质，显式标注，连回该期主导矛盾；【据:fyp_attribution】有归因参照）
另须显式写出两件事：
- "该期规划是否被中断、被什么中断"——分期边界（政治经济阶段）与规划边界（五年行政周期）的错位处即分析价值最高处；
- 本期问题属**工具层修正**（改政策工具/考核指标）还是**制度层修正**（触及财税/土地/户籍基础制度）——
  地方债务/房地产/青年就业等跨 6 轮、25 年复发的问题，反复用工具层手段应对是统一机制。"""
    # 三观只注入主笔：副笔须保持"异血统"对照，注入同一视角会让双稿退化为同视角重复
    system_main = WRITER_RULES.format(chars=DRAFT_CHARS) + _worldview_for_writer()
    system_sub = WRITER_RULES.format(chars=DRAFT_CHARS)
    user = f"""## 任务
为《{spec["title"]}》起草第 {sec} 节。

## 硬性结构要求
{period_hint}

## 骨架（00-skeleton，分期与主导矛盾以此为准）
{skel[:6000]}

{_anchor_legend(pack)}
## 材料包（数字与锚点的唯一合法来源）
{pack if pack else "（空：全部数字请标【无据待查】）"}

## 输出
单节正文，{DRAFT_CHARS} 字。"""
    drafts = []
    for role, az, sys_prompt in (("主笔Mimo", main, system_main), ("副笔Nemotron", sub, system_sub)):
        _log(f"起草 {page} 第{sec}节（{role}）…")
        try:
            # 主/副笔链都含推理型模型（nemotron 长生成实测需 198s+），统一给 360s：
            # 2026-09-16 修——主笔曾设 180s，mimo 429 降级到 nemotron 时必然超时，
            # 表现为"主笔全链失败"，实际是超时太短。
            drafts.append((role, az._call_api(sys_prompt, user, timeout=360)))
        except Exception as e:
            _log(f"{role} 失败: {e}")
    if not drafts:
        (DRAFTS / f"_prompt_{page}_sec{sec}.md").write_text(
            f"# 重跑提示\n\n两稿均失败（429/超时）可稍后重跑同命令。\n\n## user\n\n{user}", encoding="utf-8")
        return 1
    diff = ""
    if len(drafts) == 2:
        judge_sys = ("你是审稿裁判。只输出两稿的实质性分歧清单：事实冲突/框架差异/评价差异逐条列出"
                     "（每条一行，注明哪稿更强），没有分歧写「无实质分歧」。不评文风。")
        judge_user = f"## A稿（主笔）\n{drafts[0][1]}\n\n## B稿（副笔）\n{drafts[1][1]}"
        for attempt in (1, 2):
            try:
                diff = sub._call_api(judge_sys, judge_user, timeout=360)
                break
            except Exception as e:
                _log(f"裁判第 {attempt} 次失败: {e}")
                if attempt == 2:
                    diff = f"（分歧比对失败两次: {e}）\n\n> 裁判不可用，请主编人工对照两稿分歧。"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    out = DRAFTS / f"{page}__sec{sec}_{stamp}_draft.md"
    parts = [f"# {page} 第{sec}节 双稿（{stamp}）\n"]
    for role, txt in drafts:
        parts.append(f"\n---\n## {role} 稿\n\n{txt}\n")
    if diff:
        parts.append(f"\n---\n## 裁判分歧清单\n\n{diff}\n")
    parts.append("\n---\n> 主编裁决后把定稿贴进本文件顶部「定稿」区，再跑 --verify。")
    out.write_text("\n".join(parts), encoding="utf-8")
    _log(f"双稿 → {out}（{len(drafts)} 稿）")
    return 0


def _unanchored_numbers(text, mat_nums):
    """数值锚校验：返回未锚数字清单（启发式，最终裁决在主编）。"""
    loose = []
    for m in NUM_RE.finditer(text):
        val = m.group(1)
        if YEAR_RE.match(val) or re.match(r"^P\d+$", val):
            continue
        head = text[max(0, m.start() - 40):m.start()]
        tail = text[m.end():m.end() + 40]
        if "无据待查" in head or "无据待查" in tail:
            continue
        if ANCHOR_RE.search(tail[:40]):
            continue
        if val in mat_nums or val.lstrip("0") in mat_nums:
            continue
        loose.append(m.group(0))
    return loose


def cmd_verify(fname):
    """数值锚校验（全书）+ Nemotron 幻觉裁判（strict 级逐节，其余抽查首节）。
    文件含「## 定稿」区时只校验定稿（主编裁决后的正文）。"""
    src = DRAFTS / fname
    if not src.exists():
        _log(f"文件不存在: {src}")
        return 1
    raw = src.read_text(encoding="utf-8")
    text = _final_text(raw)
    if text is not raw:
        _log("检测到「定稿」区——只校验定稿正文（忽略 A/B 稿与裁判清单）")
    spec = _page_spec(fname.split("__")[0])
    pack = _materials()
    loose = _unanchored_numbers(text, _material_numbers(pack))
    bad_anchors = _bad_anchor_names(text, _material_names(pack))
    _, sub = _load_analyzers()
    # 分节支持 H2-H4（定稿正文常用 #### 作维度小节）
    sections = [s for s in re.split(r"\n(?=#{2,4} )", text) if s.strip()]
    report = [f"# 核查报告：{fname}", f"- 生成: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
              f"- 页面级别: {spec['level']}", ""]
    report.append("## 数值锚校验\n")
    report.append(f"未锚数字 {len(loose)} 处：" + (", ".join(loose[:60]) if loose else "无（全部数字有锚/豁免）"))
    report.append("\n## 锚点名校验\n")
    report.append(f"材料包中不存在的锚点名 {len(bad_anchors)} 个："
                  + (", ".join(bad_anchors[:60]) if bad_anchors else "无（全部锚点可溯源）"))
    judge_scope = sections if spec["level"] == "strict" else sections[:1]
    report.append("\n## 幻觉裁判\n")
    # 串行裁判（2026-09-17 三轮调优结论）：NVIDIA glm 端点对并发敏感——
    # 6 并发全超时、2 并发仍大量超时，串行最稳。为控制总时长：
    # 输入截到 1800 字 + 单节超时 200s，6 节最坏 20 分钟，正常 3-6 分钟。
    for sec in judge_scope:
        head = sec.splitlines()[0][:40]
        try:
            verdict = sub._call_api(
                "你是事实核查裁判，不持立场。检查给定历史文本，只查这三类问题：\n"
                "1) 内部矛盾（前后说法冲突）\n"
                "2) 与公认史实明显冲突的断言（时间/数字/事件性质错误）\n"
                "3) 无出处却断言为事实的**具体数字或具体事件细节**\n"
                "重要排除项（不要列为问题）：\n"
                "- 以「判断：」开头的分析性段落——这是作者显式标注的判断，不是事实断言\n"
                "- 概括性叙述（如'地方依赖土地出让获取财源'）——属公认背景，不要求逐句锚\n"
                "- 【据:xxx】标签本身的形式（内部代码标签是本文档的既定引用格式）\n"
                "逐条列出并引用原句；都无则写「未发现问题」。不评价观点与立场。",
                f"{sec[:1800]}", timeout=200)
            report.append(f"### {head}\n\n{verdict}\n")
        except Exception as e:
            report.append(f"### {head}\n\n（裁判失败: {e}）\n")
    out = src.with_name(src.stem.replace("_draft", "") + "_verify.md")
    out.write_text("\n".join(report), encoding="utf-8")
    _log(f"核查报告 → {out}（未锚 {len(loose)} 处，裁判 {len(judge_scope)} 节）")
    return 0


def cmd_publish(fname):
    """转正：drafts → macro-history/，更新 REVISIONS/index/log（幂等）。
    文件含「## 定稿」区时只转正定稿（主编裁决后的正文）。"""
    src = DRAFTS / fname
    if not src.exists():
        _log(f"文件不存在: {src}")
        return 1
    raw = src.read_text(encoding="utf-8")
    text = _final_text(raw)
    if text is not raw:
        _log("检测到「定稿」区——只转正定稿正文（忽略 A/B 稿与裁判清单）")
    base = fname.split("__")[0]
    page = HIST / f"{base}.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if page.exists():
        old = page.read_text(encoding="utf-8")
        page.write_text(old + f"\n\n---\n\n# 修订 {now}\n\n{text}", encoding="utf-8")
        action = "追加修订"
    else:
        page.write_text(
            f"---\ntitle: {base}\ncreated: {now}\nupdated: {now}\ntype: macro-history\nstatus: published\nrelated_hyps: []\n---\n\n{text}",
            encoding="utf-8")
        action = "新建"
    rev = HIST / "REVISIONS.md"
    with rev.open("a", encoding="utf-8") as f:
        f.write(f"- {now} {base}: {action}（{fname}）\n")
    for rel, marker, line in (
            (KB_WIKI / "index.md", "macro-history/", f"- [历史脉络档案](macro-history/README.md) — 大历史认识：骨架+枝叶+事件卡\n"),
            (KB_WIKI / "log.md", now, f"- {now} 参谋系统 history_engine：{base} {action}（双稿+数值锚+裁判核查）\n")):
        try:
            content = rel.read_text(encoding="utf-8")
            if marker not in content:
                rel.write_text(content.rstrip() + "\n" + line, encoding="utf-8")
                _log(f"更新 {rel.name}")
        except FileNotFoundError:
            _log(f"跳过 {rel}（不存在）")
    _log(f"{action} → {page}")
    return 0


def cmd_revision_suggest():
    """假设置信度漂移 >15pp → 提示对应脉络页修订（只提示不代写）。"""
    f = DATA / "hypotheses" / "active_hypotheses.json"
    hyps = json.loads(f.read_text(encoding="utf-8"))
    if isinstance(hyps, dict):
        hyps = hyps.get("hypotheses") or hyps.get("nodes") or []
    pages = [p for p in HIST.glob("*.md") if p.name[0].isdigit()]
    drift = []
    for h in hyps:
        try:
            d = float(h.get("confidence", 0)) - float(h.get("base_confidence", 0))
        except (TypeError, ValueError):
            continue
        if abs(d) >= 0.15:
            drift.append((h.get("id", "?"), h.get("title", "?"), h.get("level", "?"), round(d, 2)))
    _log(f"置信度漂移 ≥15pp 的假设 {len(drift)} 个：")
    for hid, title, lv, d in drift:
        _log(f"  {hid} [{lv}] {title}: {d:+.2f}")
    if pages:
        linked = {}
        for p in pages:
            m = re.search(r"related_hyps:\s*\[([^\]]*)\]", p.read_text(encoding="utf-8")[:600])
            if m and m.group(1).strip():
                linked[p.name] = [x.strip() for x in m.group(1).split(",")]
        hit = {p: [d for d in drift if d[0] in ids] for p, ids in linked.items()}
        for p, ds in hit.items():
            if ds:
                _log(f"  → 建议修订 {p}（关联假设变动: {', '.join(x[1] for x in ds)}）")
    if not pages:
        _log("尚无脉络页定稿（related_hyps 未挂接）——档案成型后本提示才有落点")
    return 0


def main():
    ap = argparse.ArgumentParser(description="大历史认识档案生产管线")
    ap.add_argument("--outline", action="store_true", help="分期表草案（总闸门）")
    ap.add_argument("--draft", nargs=2, metavar=("PAGE", "SEC"), help="起草某页某节（双稿）")
    ap.add_argument("--verify", metavar="FILE", help="数值锚校验 + 幻觉裁判")
    ap.add_argument("--publish", metavar="FILE", help="校订后转正 + index/log")
    ap.add_argument("--revision-suggest", action="store_true", help="假设漂移 → 修订提示")
    args = ap.parse_args()
    DRAFTS.mkdir(parents=True, exist_ok=True)
    if args.outline:
        return cmd_outline()
    if args.draft:
        return cmd_draft(args.draft[0], args.draft[1])
    if args.verify:
        return cmd_verify(args.verify)
    if args.publish:
        return cmd_publish(args.publish)
    if args.revision_suggest:
        return cmd_revision_suggest()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
