# -*- coding: utf-8 -*-
r"""kb_linker.py - 知识库双向链接（P2 待续事项：wiki/hypotheses 与 index.md/log.md 同步）
假设/观点卡写入 视频知识库(D:\Codex输出\视频知识库) 并更新 index.md 的 Hypotheses 区与 log.md。
幂等：页面已存在则只确保索引链接存在，不重复写。
"""
import re
from datetime import datetime
from pathlib import Path

DEFAULT_VAULT = r"D:\Codex输出\视频知识库"


def _insert_index_link(index_file, link_text, section="## Hypotheses"):
    """在 index.md 指定区段插入 - [[link]]；已存在返回 False，区段不存在则新建"""
    if not index_file.exists():
        return False
    content = index_file.read_text(encoding="utf-8")
    if f"[[{link_text}]]" in content:
        return False
    lines = content.split("\n")
    sec = -1
    for i, line in enumerate(lines):
        if section in line:
            sec = i
            break
    link_line = f"- [[{link_text}]]"
    if sec < 0:
        lines += ["", section, link_line]
    else:
        insert_at = sec + 1
        while insert_at < len(lines) and lines[insert_at].strip() and not lines[insert_at].startswith("##"):
            insert_at += 1
        lines.insert(insert_at, link_line)
    index_file.write_text("\n".join(lines), encoding="utf-8")
    return True


def _append_log(log_file, action, details):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n- {now} | {action} | {details}"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(entry)


def hypothesis_page_markdown(hyp):
    """假设的知识库页面内容（含 [[反链]] 与元数据）"""
    hyp_id = hyp.get("id", "hyp_unknown")
    title = hyp.get("title", "未命名假设")
    conf = hyp.get("confidence", "?")
    try:
        conf = str(round(float(conf), 2))
    except (TypeError, ValueError):
        pass
    due = hyp.get("due_date") or hyp.get("deadline") or "-"
    lines = [
        "---",
        f"type: hypothesis",
        f"id: {hyp_id}",
        f"status: {hyp.get('status', 'active')}",
        f"direction: {hyp.get('direction', '-')}",
        f"confidence: {conf}",
        f"due: {due}",
        "---",
        "",
        f"# {title}",
        "",
        "## 核心主张",
        hyp.get("core_claim") or hyp.get("rationale") or "（待补充）",
        "",
        "## 反驳标准",
        hyp.get("falsification_criteria") or "（待补充）",
    ]
    for ind in hyp.get("indicators", []) or []:
        if isinstance(ind, dict):
            lines.append(f"- 指标：{ind.get('name', '?')}（{ind.get('source', '?')}）"
                         f" 支持：{ind.get('threshold_support', '-')} / 反驳：{ind.get('threshold_refute', '-')}")
    if hyp.get("verification_result"):
        lines += ["", "## 最近验证",
                  f"- 结果：{hyp['verification_result']}（{hyp.get('last_verified') or '-'}）"]
    ev = hyp.get("evidence_log") or []
    if ev:
        lines += ["", "## 证据（最近5条）"]
        for e in ev[-5:]:
            if isinstance(e, dict):
                lines.append(f"- [{e.get('date', '?')}] {e.get('summary', '')}")
    lines += [
        "",
        "## 关联",
        "- 上级：[[参谋系统假设树]]",
        f"- 情报来源目录：intel_YYYYMMDD.jsonl",
    ]
    return "\n".join(lines)


def link_hypothesis_to_kb(hyp, vault_path=DEFAULT_VAULT):
    """假设写入知识库：wiki/hypotheses/<id>.md + index.md 链接 + log.md 记录。
    返回页面路径。幂等：页面已存在则只确保索引链接。"""
    vault = Path(vault_path)
    hyp_id = hyp.get("id", "hyp_unknown")
    hyp_dir = vault / "wiki" / "hypotheses"
    hyp_dir.mkdir(parents=True, exist_ok=True)
    page = hyp_dir / f"{hyp_id}.md"
    if not page.exists():
        page.write_text(hypothesis_page_markdown(hyp), encoding="utf-8")
        action = "参谋系统假设入库"
    else:
        action = "参谋系统假设索引同步"
    changed = _insert_index_link(vault / "wiki" / "index.md", hyp_id, "## Hypotheses")
    _append_log(vault / "wiki" / "log.md", action, f"[[{hyp_id}]] {hyp.get('title', '')}")
    print(f"[KBLINK] {hyp_id}: page={'new' if action.endswith('入库') else 'exists'}, index_updated={changed}")
    return page


def link_view_card_to_kb(card, vault_path=DEFAULT_VAULT):
    """观点卡入库：wiki/views/view_cards/<id>.md + index.md Views 区 + log.md"""
    vault = Path(vault_path)
    card_id = card.get("id", "view_unknown")
    d = vault / "wiki" / "views" / "view_cards"
    d.mkdir(parents=True, exist_ok=True)
    page = d / f"{card_id}.md"
    lines = [
        "---",
        "type: view-card",
        f"id: {card_id}",
        f"created: {card.get('created', '-')}",
        "---",
        "",
        f"# 观点卡：{card.get('title', '?')}",
        "",
        "## 核心观点",
        card.get("core_claim", ""),
        "",
        "## 依据",
        card.get("evidence_basis", "（未提供）"),
        "",
        "## 可验证指标",
    ]
    for k in card.get("verifiable_indicators", []):
        lines.append(f"- {k}")
    lines += [
        "",
        "## 反驳标准",
        card.get("refute_criteria", "（未提供）"),
        "",
        "## 对个人的影响",
        card.get("personal_impact", "（未提供）"),
        "",
        "## 关联",
        "- [[参谋系统假设树]]",
    ]
    if not page.exists():
        page.write_text("\n".join(lines), encoding="utf-8")
        _insert_index_link(vault / "wiki" / "index.md", card_id, "## Views")
        _append_log(vault / "wiki" / "log.md", "参谋系统观点卡入库", f"[[{card_id}]] {card.get('title', '')}")
    print(f"[KBLINK] card {card_id}: {'new' if page.exists() else 'exists'}")
    return page
