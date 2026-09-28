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
    """在 index.md 指定区段插入 - [[link]]；已存在返回 False，区段不存在则新建。

    注意：**写入前重读文件**，不依赖调用方缓存——index.md 可能被 render_wiki /
    插件 / 人工同时修改，用旧内容回写会丢链接（知识库侧体检曾发现此风险）。
    """
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


def _norm_conf(v):
    """置信度归一：假设树统一用 0-1 小数，但历史页有百分数（如 70）。
    返回 (小数, 百分数文本)。无法解析时返回 (None, "?")。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None, "?"
    if f > 1:                     # 百分数 → 小数
        f = f / 100.0
    f = max(0.0, min(1.0, f))
    pct = round(f * 100, 1)
    pct_text = str(int(pct)) if pct == int(pct) else str(pct)
    return round(f, 4), pct_text


def hypothesis_page_markdown(hyp):
    """假设的知识库页面内容（含 [[反链]] 与元数据）

    量纲约定：frontmatter 的 confidence 存 0-1 小数（与假设树一致，机器可读），
    正文展示百分数（人读）。混用会让下游把 0.92 当成 92% 或把 70 当成 7000%。
    """
    hyp_id = hyp.get("id", "hyp_unknown")
    title = hyp.get("title") or "未命名假设"
    conf_dec, conf_pct = _norm_conf(hyp.get("confidence"))
    conf_fm = conf_dec if conf_dec is not None else "?"
    due = hyp.get("due_date") or hyp.get("deadline") or "-"
    lines = [
        "---",
        f"type: hypothesis",
        f"id: {hyp_id}",
        f"title: {title}",
        f"status: {hyp.get('status', 'active')}",
        f"direction: {hyp.get('direction', '-')}",
        f"confidence: {conf_fm}",
        f"deadline: {due}",
        f"tags: [假设, {hyp.get('direction', '-')}]",
        "---",
        "",
        f"# {title}",
        "",
        f"- **方向**: {hyp.get('direction', '-')}",
        f"- **置信度**: {conf_pct}%",
        f"- **到期**: {due}",
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


def _fm_value(text, key):
    """从 frontmatter 取单值字段（用于比对，不引入 yaml 依赖）"""
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, re.M)
    return m.group(1).strip().strip("'\"") if m else None


def link_hypothesis_to_kb(hyp, vault_path=DEFAULT_VAULT):
    """假设写入知识库：wiki/hypotheses/<id>.md + index.md 链接 + log.md 记录。

    更新语义（2026-09-21 修复）：原先 `if not page.exists()` 只写一次、此后永不更新，
    导致假设的 confidence/status 变化永远不回流（假设树在变、知识库页面是静态快照）。
    现改为：**读旧页 → 比对关键字段 → 有差异才重写**（对齐 AGENTS.md
    「跨进程共享的快照文件必须校验内容，不能只信文件名」的同构思路）。
    """
    vault = Path(vault_path)
    hyp_id = hyp.get("id", "hyp_unknown")
    hyp_dir = vault / "wiki" / "hypotheses"
    hyp_dir.mkdir(parents=True, exist_ok=True)
    page = hyp_dir / f"{hyp_id}.md"

    new_content = hypothesis_page_markdown(hyp)
    dec, _ = _norm_conf(hyp.get("confidence"))
    new_conf = str(dec) if dec is not None else None
    new_status = str(hyp.get("status", "active"))
    due = str(hyp.get("due_date") or hyp.get("deadline") or "-")

    if not page.exists():
        page.write_text(new_content, encoding="utf-8")
        action = "参谋系统假设入库"
        state = "new"
    else:
        old = page.read_text(encoding="utf-8", errors="replace")
        diffs = []
        if new_conf is not None and _fm_value(old, "confidence") != new_conf:
            diffs.append(f"confidence {_fm_value(old, 'confidence')}→{new_conf}")
        if _fm_value(old, "status") != new_status:
            diffs.append(f"status {_fm_value(old, 'status')}→{new_status}")
        if _fm_value(old, "deadline") != due:
            diffs.append(f"deadline {_fm_value(old, 'deadline')}→{due}")
        if diffs:
            page.write_text(new_content, encoding="utf-8")
            action = "参谋系统假设更新"
            state = "updated(" + "; ".join(diffs) + ")"
        else:
            action = "参谋系统假设索引同步"
            state = "unchanged"

    changed = _insert_index_link(vault / "wiki" / "index.md", hyp_id, "## Hypotheses")
    # 只在页面真变化或索引真变化时写日志，避免每轮刷屏
    if state.startswith(("new", "updated")) or changed:
        _append_log(vault / "wiki" / "log.md", action, f"[[{hyp_id}]] {hyp.get('title', '')}")
    print(f"[KBLINK] {hyp_id}: page={state}, index_updated={changed}")
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
    new_content = "\n".join(lines)
    if not page.exists():
        page.write_text(new_content, encoding="utf-8")
        _insert_index_link(vault / "wiki" / "index.md", card_id, "## Views")
        _append_log(vault / "wiki" / "log.md", "参谋系统观点卡入库", f"[[{card_id}]] {card.get('title', '')}")
        state = "new"
    else:
        # 与假设页同构：内容有差异才重写（观点卡会因追问迭代而更新）
        old = page.read_text(encoding="utf-8", errors="replace")
        if old != new_content:
            page.write_text(new_content, encoding="utf-8")
            _append_log(vault / "wiki" / "log.md", "参谋系统观点卡更新", f"[[{card_id}]] {card.get('title', '')}")
            state = "updated"
        else:
            state = "unchanged"
    print(f"[KBLINK] card {card_id}: {state}")
    return page
