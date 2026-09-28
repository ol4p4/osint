# -*- coding: utf-8 -*-
r"""kb_guard.py - 知识库写入闸门（纯函数层，无副作用）

设计原则（对齐 AGENTS.md 既有约束）：
  - 「AI 输出必须过规范化层再喂下游」→ 本模块就是知识库写入的规范化层，
    与 analyze.py 的 _norm_text/_norm_dict/_norm_conf 同构：独立成层，各调用点自行 import。
  - 「批处理必须能隔离坏元素」→ 所有检查函数返回 (ok, reasons) 而非抛异常，
    调用方记告警后继续下一项，不让一页坏内容拖垮整轮。
  - 「新鲜度/合法性闸门一律用计数而非取极值」→ 行数/长度判据全部用计数。
  - 「写 Python 文件 IO 用方法式 API」→ 本模块只读，用 Path.read_text。

用法：
    from kb_guard import (
        normalize_keywords, normalize_confidence,
        check_frontmatter, check_links, check_size,
        vault_link_index, guard_page,
    )

    ok, reasons = guard_page(text, page_type="hypothesis")
    if not ok:
        print("跳过写入：", reasons)
"""
import re
from pathlib import Path

# 知识库根（与 kb_linker.DEFAULT_VAULT 一致）
DEFAULT_VAULT = r"D:\Codex输出\视频知识库"

# 页面行数上限（知识库 SCHEMA.md 规定 200 行）
MAX_LINES = 200
# 关键词单项长度上限（超过就不是关键词，是句子）
MAX_KEYWORD_CHARS = 12
# 关键词条数上限
MAX_KEYWORDS = 20
# summary/一句话结论长度上限
MAX_SUMMARY_CHARS = 120

LINK_RE = re.compile(r"\[\[([^\]\[]+)\]\]")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
FENCE_RE = re.compile(r"^```.*?^```", re.S | re.M)

# 各页面类型的必填 frontmatter 字段
REQUIRED_FIELDS = {
    "hypothesis": ["id", "title", "status", "confidence"],
    "view-card": ["id", "created"],
    "concept": ["title", "created", "type"],
    "baseline": ["date", "type"],
}

# confidence 合法值域（0-1 小数）
CONF_MIN, CONF_MAX = 0.0, 1.0


# ---------- 规范化（修 AI 输出污染） ----------

def normalize_keywords(items, max_chars=MAX_KEYWORD_CHARS, limit=MAX_KEYWORDS):
    """把 AI 产出清洗成真正的关键词列表。

    修的问题：render_wiki.py 曾把 macro_diagnosis 的**长句值**（如
    「毕业生群体面临更激烈竞争，60%企业未完成招聘目标意味着机会分布碎片化」）
    塞进 frontmatter 的 `关键词` 字段。

    规则：去重、去空白、丢弃超长项（长句不是关键词）、限制条数、保持首次出现顺序。
    """
    out, seen = [], set()
    for x in items or []:
        if not isinstance(x, str):
            continue
        s = x.strip().strip("，。；,;.")
        if not s or len(s) > max_chars:
            continue                      # 超长 = 句子，丢弃（宁可缺不可错）
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def normalize_confidence(v):
    """置信度归一为 0-1 小数。返回 None 表示无法解析。

    假设树统一用小数，历史页有百分数（70）；混用会让下游把 0.92 读成 0.92%。
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f > 1:                             # 百分数 → 小数
        f = f / 100.0
    if f < CONF_MIN or f > CONF_MAX:
        return None
    return round(f, 4)


def normalize_summary(s, max_chars=MAX_SUMMARY_CHARS):
    """一句话总结：压平换行、截断超长、去空白。"""
    if not isinstance(s, str):
        return ""
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_chars]


# ---------- 校验（写入前闸门） ----------

def parse_frontmatter(text):
    """解析 YAML frontmatter，返回 (dict, body)。解析失败返回 ({}, text)。"""
    m = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n?", text, re.S)
    if not m:
        return {}, text
    fm, key = {}, None
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- ") and key:
            fm.setdefault(key, [])
            if isinstance(fm[key], list):
                fm[key].append(line.lstrip()[2:].strip().strip("'\""))
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            key, v = k.strip(), v.strip()
            if not v:
                fm[key] = []
            elif v.startswith("[") and v.endswith("]"):
                fm[key] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
            else:
                fm[key] = v.strip("'\"")
    return fm, text[m.end():]


def check_frontmatter(text, page_type):
    """检查必填字段与字段值域。返回 (ok, reasons)。"""
    reasons = []
    fm, _ = parse_frontmatter(text)
    if not fm:
        return False, ["缺少 frontmatter"]

    for f in REQUIRED_FIELDS.get(page_type, []):
        if f not in fm or fm[f] in ("", [], None):
            reasons.append(f"frontmatter 缺字段：{f}")

    # confidence 量纲与值域
    if "confidence" in fm and fm["confidence"] not in ("", None):
        if normalize_confidence(fm["confidence"]) is None:
            reasons.append(f"confidence 非法（应为 0-1 小数）：{fm['confidence']}")
    return (not reasons), reasons


def check_size(text, max_lines=MAX_LINES):
    """检查行数上限。返回 (ok, reasons, lines)。"""
    lines = text.count("\n") + 1
    if lines > max_lines:
        return False, [f"超出行数上限：{lines} > {max_lines}"], lines
    return True, [], lines


def strip_code(text):
    """去掉围栏代码块与行内代码——里面的 [[x]] 是示例，不是链接。"""
    return INLINE_CODE_RE.sub("", FENCE_RE.sub("", text))


def check_links(text, index=None, vault=None):
    """检查 [[链接]] 是否都能解析到实际文件。返回 (ok, reasons)。

    index 为 vault_link_index() 的返回值；不传则现场构建（慢，避免在大循环里这么做）。
    """
    if index is None:
        index = vault_link_index(vault or DEFAULT_VAULT)
    by_path, by_base, by_alias = index
    reasons = []
    for m in LINK_RE.finditer(strip_code(text)):
        target = m.group(1).split("|")[0].strip()
        if not target:
            continue
        if not _resolve(target, by_path, by_base, by_alias):
            reasons.append(f"悬空链接：[[{target}]]")
    return (not reasons), reasons


def _resolve(target, by_path, by_base, by_alias):
    """路径 → basename → alias；先试完整目标（文件名可能含 #），再试去锚点。"""
    def try_one(x):
        if not x:
            return None
        if x in by_path:
            return by_path[x]
        if x + ".md" in by_path:
            return by_path[x + ".md"]
        base = Path(x).name
        if base in by_base:
            return by_base[base]
        return by_alias.get(x)

    return try_one(target) or try_one(target.split("#")[0].strip())


def vault_link_index(vault=None):
    """构建链接解析索引：(按路径, 按basename, 按alias)。

    按 AGENTS.md「AI 分析量必须与通道吞吐匹配」的同类考虑：索引**每次进程启动构建一次**
    并在调用方缓存，不要在每页校验时重建（167 页 × 全库扫描 = O(n²)）。

    注意：by_base 存**单值**（首个命中），basename 歧义由知识库侧 wiki_lint.py 单独报告——
    这里只需保证「能否解析」的判断正确，不承担歧义检测职责。
    """
    vault = Path(vault or DEFAULT_VAULT)
    by_path, by_base, by_alias = {}, {}, {}
    if not vault.exists():
        return by_path, by_base, by_alias
    for p in vault.rglob("*.md"):
        if ".git" in p.parts or ".obsidian" in p.parts:
            continue
        try:
            r = p.relative_to(vault).as_posix()
        except ValueError:
            continue
        by_path[r] = p
        by_path[r[:-3]] = p
        by_base.setdefault(p.stem, p)
        fm, _ = parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        aliases = fm.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        for a in aliases:
            if a:
                by_alias[a] = p
    return by_path, by_base, by_alias


def guard_page(text, page_type, index=None, max_lines=MAX_LINES):
    """写入前的总闸门：一次跑完 frontmatter + 行数 + 链接三项。

    返回 (ok, reasons)。**不抛异常**——调用方记告警后继续，实现单页隔离。
    """
    reasons = []
    ok_fm, r_fm = check_frontmatter(text, page_type)
    ok_sz, r_sz, _ = check_size(text, max_lines)
    ok_lk, r_lk = check_links(text, index=index)
    reasons += r_fm + r_sz + r_lk
    return (not reasons), reasons
