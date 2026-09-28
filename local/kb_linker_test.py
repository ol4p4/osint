# -*- coding: utf-8 -*-
r"""kb_linker_test.py - kb_linker 更新回流桩测（2026-09-21）

验证核心修复：假设页原先「一次写入永不更新」，现在 confidence/status 变化会回流。
用临时目录，不碰真实知识库。

跑法：python D:\osint\local\kb_linker_test.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from kb_linker import link_hypothesis_to_kb, _insert_index_link  # noqa: E402

PASS, FAIL = [], []


def t(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ← {detail}" if detail and not cond else ""))


def mk_vault(tmp):
    """建最小 vault 骨架"""
    v = Path(tmp)
    (v / "wiki" / "hypotheses").mkdir(parents=True, exist_ok=True)
    (v / "wiki" / "index.md").write_text("# Wiki 索引\n\n## Hypotheses\n\n", encoding="utf-8")
    (v / "wiki" / "log.md").write_text("# Wiki 操作日志\n", encoding="utf-8")
    return str(v)


print("=" * 60)
print("kb_linker 更新回流桩测")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmp:
    vault = mk_vault(tmp)

    base_hyp = {
        "id": "hyp_test01",
        "title": "测试假设",
        "status": "active",
        "confidence": 0.6,
        "due_date": "2027-01-01",
        "direction": "toward",
        "core_claim": "这是核心主张",
    }

    # ---------- 1. 首次写入 ----------
    print("\n[1] 首次写入")
    p = link_hypothesis_to_kb(base_hyp, vault_path=vault)
    t("页面已创建", p.exists())
    text1 = p.read_text(encoding="utf-8")
    t("frontmatter confidence 为小数 0.6", "confidence: 0.6" in text1, text1[:200])
    t("正文显示百分数 60%", "**置信度**: 60%" in text1, text1[:400])
    t("含 title 字段", "title: 测试假设" in text1)
    t("含 tags 字段", "tags: [假设, toward]" in text1)
    t("含 deadline 字段", "deadline: 2027-01-01" in text1)
    t("上级链接指向假设树", "[[参谋系统假设树]]" in text1)
    t("index.md 已登记", "[[hyp_test01]]" in (Path(vault) / "wiki" / "index.md").read_text(encoding="utf-8"))

    # ---------- 2. 内容不变时幂等（不重写） ----------
    print("\n[2] 内容不变 → 幂等")
    mtime1 = p.stat().st_mtime_ns
    link_hypothesis_to_kb(base_hyp, vault_path=vault)
    t("文件未被重写（mtime 不变）", p.stat().st_mtime_ns == mtime1)

    # ---------- 3. confidence 变化 → 回流（核心修复） ----------
    print("\n[3] confidence 变化 → 页面更新（原 bug：永不更新）")
    hyp2 = dict(base_hyp, confidence=0.85)
    link_hypothesis_to_kb(hyp2, vault_path=vault)
    text2 = p.read_text(encoding="utf-8")
    t("frontmatter 更新为 0.85", "confidence: 0.85" in text2, text2[:200])
    t("正文更新为 85%", "**置信度**: 85%" in text2, text2[:400])
    t("页面确实被重写", p.stat().st_mtime_ns != mtime1)

    # ---------- 4. status 变化 → 回流 ----------
    print("\n[4] status 变化 → 页面更新")
    hyp3 = dict(base_hyp, confidence=0.85, status="falsified")
    link_hypothesis_to_kb(hyp3, vault_path=vault)
    text3 = p.read_text(encoding="utf-8")
    t("status 更新为 falsified", "status: falsified" in text3, text3[:200])

    # ---------- 5. 百分数输入被归一 ----------
    print("\n[5] 百分数输入自动归一为小数")
    hyp4 = dict(base_hyp, id="hyp_test02", confidence=70)
    p4 = link_hypothesis_to_kb(hyp4, vault_path=vault)
    text4 = p4.read_text(encoding="utf-8")
    t("confidence: 70 → 0.7", "confidence: 0.7" in text4, text4[:200])
    t("正文显示 70%", "**置信度**: 70%" in text4, text4[:400])

    # ---------- 6. deadline 变化 → 回流 ----------
    print("\n[6] deadline 变化 → 页面更新")
    hyp5 = dict(base_hyp, confidence=0.85, status="falsified", due_date="2028-06-30")
    link_hypothesis_to_kb(hyp5, vault_path=vault)
    text5 = p.read_text(encoding="utf-8")
    t("deadline 更新为 2028-06-30", "deadline: 2028-06-30" in text5, text5[:200])

    # ---------- 7. index 写入前重读（防丢链接） ----------
    print("\n[7] index.md 写入前重读 → 不丢第三方链接")
    idx = Path(vault) / "wiki" / "index.md"
    # 模拟第三方（插件/人工）插入的链接
    idx.write_text(idx.read_text(encoding="utf-8") + "- [[第三方加的页]]\n", encoding="utf-8")
    link_hypothesis_to_kb(dict(base_hyp, id="hyp_test03", confidence=0.5), vault_path=vault)
    idx_text = idx.read_text(encoding="utf-8")
    t("第三方链接未被覆盖", "[[第三方加的页]]" in idx_text, idx_text[-300:])
    t("新链接已加入", "[[hyp_test03]]" in idx_text)

    # ---------- 8. 签名兼容（worldview_engine 的私有 import） ----------
    print("\n[8] 签名兼容（保护 worldview_engine 的私有 import）")
    import inspect
    from kb_linker import DEFAULT_VAULT, _append_log  # noqa: F401
    sig = inspect.signature(link_hypothesis_to_kb)
    t("link_hypothesis_to_kb 签名不变", list(sig.parameters) == ["hyp", "vault_path"], list(sig.parameters))
    t("DEFAULT_VAULT 仍可 import", isinstance(DEFAULT_VAULT, str))
    t("_append_log 仍可 import", callable(_append_log))
    t("_insert_index_link 仍可 import", callable(_insert_index_link))

print()
print("=" * 60)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
print("=" * 60)
sys.exit(1 if FAIL else 0)
