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
  正式档案  D:\Codex输出\视频知识库\wiki\history\
  工作区    D:\Codex输出\视频知识库\wiki\history\drafts\
"""
import argparse
import copy
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(r"D:\osint")
DATA = PROJECT / "data"
KB_WIKI = Path(r"D:\Codex输出\视频知识库\wiki")
HIST = KB_WIKI / "history"
DRAFTS = HIST / "drafts"
DATA_DIR = HIST / "data"

NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d+)?)\s*(?:%|个百分点|倍|万亿|亿|万)?(?![\w.\d])")
YEAR_RE = re.compile(r"^(19|20)\d{2}$")
ANCHOR_RE = re.compile(r"【据[:：][^】]*】|【无据待查】")

MAX_MATERIAL_CHARS = 12000
DRAFT_CHARS = "800-1200"
SKELETON = "00-skeleton.md"


def _log(msg):
    print(f"[HIST] {msg}")


def _load_analyzers():
    """主笔=Mimo 主链；副笔/裁判=Nemotron 单模型链。
    MacroAnalyzer 构造本身不发起网络请求，_call_api 内置硬超时与降级。"""
    import yaml
    from analyze import MacroAnalyzer

    cfg = yaml.safe_load((PROJECT / "config.yaml").read_text(encoding="utf-8"))
    main = MacroAnalyzer(cfg, "", None)
    nemotron = None
    for fb in cfg.get("api", {}).get("fallback_models", []) or []:
        if "nemotron" in str(fb.get("model", "")):
            nemotron = dict(fb)
            break
    if nemotron:
        sub_cfg = copy.deepcopy(cfg)
        sub_cfg["api"]["model"] = nemotron["model"]
        sub_cfg["api"]["fallback_models"] = []
        sub = MacroAnalyzer(sub_cfg, "", None)
    else:
        sub = main
        _log("WARN: 未找到 nemotron 副笔配置，双稿退化为同模型双稿（立场对照失效）")
    return main, sub


def _materials():
    """材料包：data/ 手工表 + 失业率历史序列摘要 + 宏观快照。供 --draft/--verify 共用。"""
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
                parts.append(f"### 材料:{f.name}\n{txt[:3500]}")
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
        parts.append("\n".join(lines)[:3500])
    except Exception as e:
        _log(f"失业率序列读取失败(跳过): {e}")
    try:
        mi = json.loads((DATA / "macro_indicators.json").read_text(encoding="utf-8"))
        parts.append(f"### 材料:macro_indicators(最新快照)\n{json.dumps(mi, ensure_ascii=False)[:3500]}")
    except Exception as e:
        _log(f"宏观快照读取失败(跳过): {e}")
    pack = "\n\n".join(parts)
    if len(pack) > MAX_MATERIAL_CHARS:
        pack = pack[:MAX_MATERIAL_CHARS] + "\n(材料包截断)"
    return pack


def _material_numbers(pack):
    """材料包中出现的全部数值字符串（规范化），供数值锚比对。"""
    nums = set()
    for m in re.finditer(r"\d+(?:\.\d+)?", pack):
        nums.add(m.group(0).lstrip("0") or "0")
        nums.add(m.group(0))
    return nums


def _skeleton_text():
    f = HIST / SKELETON
    if not f.exists():
        raise FileNotFoundError(f"骨架不存在: {f}（先跑 --outline）")
    return f.read_text(encoding="utf-8")


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


def cmd_outline():
    """总闸门：只产出分期表草案，等主编逐期确认。"""
    main, _ = _load_analyzers()
    system = WRITER_RULES.format(chars="不限") + """
6. 本任务只输出「分期表」，不写叙事正文。"""
    user = f"""任务：为《中国如何走到今天》综合大历史档案起草**分期表**。

要求：
1. 分 7 期左右（参考但不限于：1978-1992 / 1992-2001 / 2001-2008 / 2008-2015 / 2015-2020 / 2020-2024 / 2024-今），主编可改。
2. 每期给出：起止年、期名、该期主导矛盾（一句话）、五个维度（经济/政治治理/社会民生/对外关系/文化思潮）各一句要点、期终标志事件。
3. 经济/社会民生要点尽量指出该期可用哪些数据序列支撑。
4. 期与期的转折逻辑（为什么这一期结束）必须一句话讲清。

输出：markdown 表格（列=#/期次/起止/期名/主导矛盾/经济/政治治理/社会民生/对外/文化/期终标志事件），表后附"转折逻辑"清单。"""
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
        "> 主编逐期确认/修改后，把确认版贴回 `wiki/history/00-skeleton.md` 的分期表并起草骨架正文。\n\n"
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
        _log("材料包为空：往 wiki/history/data/ 放数据表后重试（数值锚会全部失败）")
    period_hint = f"骨架对应分期见下方材料。本节只写分期表第 {sec} 期相关内容。" if spec["kind"] == "skeleton" \
        else f"枝叶章节编号严格对齐骨架分期：本节只写骨架分期表中第 {sec} 期，期名与主导矛盾必须引用骨架原文。"
    system = WRITER_RULES.format(chars=DRAFT_CHARS)
    user = f"""## 任务
为《{spec["title"]}》起草第 {sec} 节。

## 硬性结构要求
{period_hint}

## 骨架（00-skeleton，分期与主导矛盾以此为准）
{skel[:6000]}

## 材料包（数字唯一合法来源）
{pack if pack else "（空：全部数字请标【无据待查】）"}

## 输出
单节正文，{DRAFT_CHARS} 字。"""
    drafts = []
    for role, az in (("主笔Mimo", main), ("副笔Nemotron", sub)):
        _log(f"起草 {page} 第{sec}节（{role}）…")
        try:
            drafts.append((role, az._call_api(system, user)))
        except Exception as e:
            _log(f"{role} 失败: {e}")
    if not drafts:
        (DRAFTS / f"_prompt_{page}_sec{sec}.md").write_text(
            f"# 重跑提示\n\n两稿均失败（429/超时）可稍后重跑同命令。\n\n## user\n\n{user}", encoding="utf-8")
        return 1
    diff = ""
    if len(drafts) == 2:
        try:
            diff = sub._call_api(
                "你是审稿裁判。只输出两稿的实质性分歧清单：事实冲突/框架差异/评价差异逐条列出（每条一行，注明哪稿更强），没有分歧写「无实质分歧」。不评文风。",
                f"## A稿（主笔）\n{drafts[0][1]}\n\n## B稿（副笔）\n{drafts[1][1]}")
        except Exception as e:
            diff = f"（分歧比对失败: {e}）"
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
    """数值锚校验（全书）+ Nemotron 幻觉裁判（strict 级逐节，其余抽查首节）。"""
    src = DRAFTS / fname
    if not src.exists():
        _log(f"文件不存在: {src}")
        return 1
    text = src.read_text(encoding="utf-8")
    spec = _page_spec(fname.split("__")[0])
    loose = _unanchored_numbers(text, _material_numbers(_materials()))
    _, sub = _load_analyzers()
    sections = [s for s in re.split(r"\n(?=#{2,3} )", text) if s.strip()]
    report = [f"# 核查报告：{fname}", f"- 生成: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
              f"- 页面级别: {spec['level']}", ""]
    report.append("## 数值锚校验\n")
    report.append(f"未锚数字 {len(loose)} 处：" + (", ".join(loose[:60]) if loose else "无（全部数字有锚/豁免）"))
    judge_scope = sections if spec["level"] == "strict" else sections[:1]
    report.append("\n## 幻觉裁判（Nemotron）\n")
    for sec in judge_scope:
        head = sec.splitlines()[0][:40]
        try:
            verdict = sub._call_api(
                "你是事实核查裁判，不持立场。检查给定历史文本：1) 内部矛盾 2) 与公认史实明显冲突的断言 3) 无出处却断言为事实的句子。逐条列出并引用原句；都无则写「未发现问题」。不评价观点与立场。",
                f"{sec[:4000]}")
            report.append(f"### {head}\n\n{verdict}\n")
        except Exception as e:
            report.append(f"### {head}\n\n（裁判失败: {e}）\n")
    out = src.with_name(src.stem.replace("_draft", "") + "_verify.md")
    out.write_text("\n".join(report), encoding="utf-8")
    _log(f"核查报告 → {out}（未锚 {len(loose)} 处，裁判 {len(judge_scope)} 节）")
    return 0


def cmd_publish(fname):
    """转正：drafts → history/，更新 REVISIONS/index/log（幂等）。"""
    src = DRAFTS / fname
    if not src.exists():
        _log(f"文件不存在: {src}")
        return 1
    text = src.read_text(encoding="utf-8")
    base = fname.split("__")[0]
    page = HIST / f"{base}.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if page.exists():
        old = page.read_text(encoding="utf-8")
        page.write_text(old + f"\n\n---\n\n# 修订 {now}\n\n{text}", encoding="utf-8")
        action = "追加修订"
    else:
        page.write_text(
            f"---\ntitle: {base}\ncreated: {now}\nupdated: {now}\ntype: history\nstatus: published\nrelated_hyps: []\n---\n\n{text}",
            encoding="utf-8")
        action = "新建"
    rev = HIST / "REVISIONS.md"
    with rev.open("a", encoding="utf-8") as f:
        f.write(f"- {now} {base}: {action}（{fname}）\n")
    for rel, marker, line in (
            (KB_WIKI / "index.md", "history/", f"- [历史脉络档案](history/README.md) — 大历史认识：骨架+枝叶+事件卡\n"),
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
