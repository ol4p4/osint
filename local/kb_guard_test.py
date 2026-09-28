# -*- coding: utf-8 -*-
r"""kb_guard_test.py - kb_guard 桩测（边界用例，对齐 AGENTS.md「桩测 8 组边界」惯例）

跑法：python D:\osint\local\kb_guard_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from kb_guard import (  # noqa: E402
    normalize_keywords, normalize_confidence, normalize_summary,
    check_frontmatter, check_size, check_links, guard_page,
)

PASS, FAIL = [], []


def t(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ← {detail}" if detail and not cond else ""))


print("=" * 60)
print("kb_guard 桩测")
print("=" * 60)

# ---------- 1. normalize_keywords ----------
print("\n[1] normalize_keywords —— 修「长句混进关键词」")
long_sentence = "毕业生群体面临更激烈竞争，60%企业未完成招聘目标意味着机会分布碎片化"
out = normalize_keywords(["青年失业", long_sentence, "AI", "青年失业", "  空格  ", "", "扩招"])
t("丢弃超长句（12 字上限）", long_sentence not in out, out)
t("去重（青年失业 只出现一次）", out.count("青年失业") == 1, out)
t("去空白", "空格" in out, out)
t("保留正常词", {"青年失业", "AI", "扩招"} <= set(out), out)
t("空输入不报错", normalize_keywords(None) == [])
t("非字符串项被跳过", normalize_keywords([1, None, "AI"]) == ["AI"])
t("条数上限 20", len(normalize_keywords([f"k{i}" for i in range(50)])) == 20)
t("正好 12 字保留", normalize_keywords(["一二三四五六七八九十一二"]) == ["一二三四五六七八九十一二"])
t("13 字丢弃", normalize_keywords(["一二三四五六七八九十一二三"]) == [])

# ---------- 2. normalize_confidence ----------
print("\n[2] normalize_confidence —— 统一量纲")
t("百分数 70 → 0.7", normalize_confidence(70) == 0.7, normalize_confidence(70))
t("小数 0.92 不变", normalize_confidence(0.92) == 0.92)
t("字符串 '80' → 0.8", normalize_confidence("80") == 0.8)
t("1 → 1.0（边界，按小数）", normalize_confidence(1) == 1.0, normalize_confidence(1))
t("0 → 0.0", normalize_confidence(0) == 0.0)
t("None → None", normalize_confidence(None) is None)
t("'abc' → None", normalize_confidence("abc") is None)
t("150 → 1.5 越界 → None", normalize_confidence(150) is None, normalize_confidence(150))
t("负数 → None", normalize_confidence(-5) is None)

# ---------- 3. normalize_summary ----------
print("\n[3] normalize_summary")
t("压平换行", normalize_summary("第一行\n第二行") == "第一行 第二行")
t("截断超长", len(normalize_summary("啊" * 500)) == 120)
t("非字符串 → 空串", normalize_summary(None) == "")

# ---------- 4. check_frontmatter ----------
print("\n[4] check_frontmatter —— 必填字段 + 值域")
good_hyp = """---
id: hyp_test
title: 测试假设
status: active
confidence: 0.8
---

# 测试假设
"""
ok, r = check_frontmatter(good_hyp, "hypothesis")
t("完整 hypothesis 通过", ok, r)

missing = """---
id: hyp_test
title: 测试假设
---

# 测试假设
"""
ok, r = check_frontmatter(missing, "hypothesis")
t("缺 status/confidence 被拒", not ok and len(r) == 2, r)

bad_conf = good_hyp.replace("confidence: 0.8", "confidence: 150")
ok, r = check_frontmatter(bad_conf, "hypothesis")
t("confidence 越界被拒", not ok, r)

no_fm = "# 没有 frontmatter\n正文"
ok, r = check_frontmatter(no_fm, "hypothesis")
t("无 frontmatter 被拒", not ok, r)

# ---------- 5. check_size ----------
print("\n[5] check_size —— 计数式判据（不用极值）")
t("199 行通过", check_size("\n" * 198 + "x")[0], check_size("\n" * 198 + "x")[2])
t("200 行通过（边界）", check_size("\n" * 199 + "x")[0])
ok, r, n = check_size("\n" * 200 + "x")
t("201 行被拒（边界）", not ok and n == 201, r)

# ---------- 6. check_links ----------
print("\n[6] check_links —— 悬空链接（含行内代码豁免）")
idx = ({}, {}, {})
t("无链接通过", check_links("没有链接", index=idx)[0])
ok, r = check_links("见 [[不存在的页]]", index=idx)
t("悬空链接被拒", not ok, r)
t("行内代码里的链接豁免", check_links("格式是 `[[目标]]`", index=idx)[0])
t("围栏代码块豁免", check_links("```\n[[目标]]\n```", index=idx)[0])

# 用真实 vault 索引验证别名解析
real = ({}, {"参谋系统假设树": Path("x")}, {})
t("basename 命中", check_links("见 [[参谋系统假设树]]", index=real)[0])
real_alias = ({}, {}, {"AI产品开发与开源": Path("x")})
t("alias 命中", check_links("见 [[AI产品开发与开源]]", index=real_alias)[0])

# ---------- 7. guard_page ----------
print("\n[7] guard_page —— 总闸门（单页隔离，不抛异常）")
ok, r = guard_page(good_hyp + "\n[[参谋系统假设树]]\n", "hypothesis", index=real)
t("合格页通过", ok, r)
ok, r = guard_page(missing + "\n[[不存在的页]]\n", "hypothesis", index=idx)
t("多项问题一次报全（>=3 条）", not ok and len(r) >= 3, r)
t("不抛异常（坏输入也返回）", isinstance(guard_page("", "hypothesis", index=idx), tuple))

# ---------- 8. 真实知识库抽查 ----------
print("\n[8] 真实知识库抽查")
vault = Path(r"D:\Codex输出\视频知识库")
if vault.exists():
    sample = vault / "wiki" / "hypotheses" / "H001.md"
    if sample.exists():
        text = sample.read_text(encoding="utf-8")
        ok, r = check_frontmatter(text, "hypothesis")
        t("H001.md frontmatter 合规", ok, r)
        conf_line = [x for x in text.splitlines() if x.startswith("confidence:")]
        t("H001.md confidence 是小数", conf_line and float(conf_line[0].split(":")[1]) <= 1, conf_line)
else:
    print("  (跳过：知识库路径不存在)")

print()
print("=" * 60)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
print("=" * 60)
sys.exit(1 if FAIL else 0)
