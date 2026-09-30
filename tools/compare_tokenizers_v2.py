# -*- coding: utf-8 -*-
r"""compare_tokenizers_v2.py - 分词方案对比（2026-09-30，只读）

**背景**：`link_intel_hyp` 第一层用字符 2-gram TF-IDF，实测命中率 0.43%、
标注样本 2/9——中文 2-gram 产生跨词垃圾 token（"机将""将投"），稀释信号。

**论文依据**（Si et al., TACL 2023, "Sub-Character Tokenization for Chinese PLMs"）：
中文 PLM 的标准做法是**从语料学习子词词表**（SentencePiece unigram），
而非机械切分。

**关键**：本工具直接调用**生产代码** `cluster_stories._tokens`（而非本地复刻），
否则测出的数不代表实际发货的分词行为。

用法：
    python tools/compare_tokenizers_v2.py
"""
import glob
import json
import math
import re
import sys
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools"))

import cluster_stories as cs  # 生产分词器

_CJK = re.compile(r"[\u4e00-\u9fff]")
_LAT = re.compile(r"[a-zA-Z]{2,}")
_NUM = re.compile(r"\d+(?:\.\d+)?")

# 人工标注样本（真信号 → 应命中的假设）
LABELS = [
    ("台学者：台军举行实弹演习期间 解放军加强台海活动", "HM001"),
    ("美国对加拿大进口限制正式生效，以下为被禁止入境的商品", "HM101"),
    ("微软推进数据中心扩张 计划将算力扩大两倍", "HM102"),
    ("IEA: Global Coal Demand Set to Hit Record High", "HM100"),
    ("沙特称霍尔木兹海峡须恢复至“战前状态”", "HM002"),
    ("AI初创企业估值回调，投资者重新评估烧钱模式", "HM003"),
    ("三星电机将投资4.27万亿韩元扩大封装基板产能", "HM102"),
    ("Russia Tightens Secrecy On Energy Exports", "HM002"),
    ("商务部就欧盟301工具答记者问", "HM101"),
]


def load_corpus():
    files = sorted(glob.glob(str(BASE / "intel_2026092*.jsonl")))[-3:]
    items = []
    for f in files:
        if "raw" in Path(f).name or "final" in Path(f).name:
            continue
        for line in Path(f).read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return items


def item_text(it):
    return " ".join(p for p in [it.get("cn_title") or it.get("title") or "",
                                it.get("cn_summary") or it.get("summary") or ""] if p)


def build_vecs(docs, min_df=2, idf=None):
    """构建 TF-IDF 向量；传入 idf 则复用它（用于把新文档投进既有空间）。"""
    if idf is None:
        df = {}
        for toks in docs:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        n = len(docs)
        idf = {t: math.log(n / c) for t, c in df.items() if c >= min_df}
    vecs = []
    for toks in docs:
        w = {}
        for t in toks:
            i = idf.get(t)
            if i is not None:
                w[t] = w.get(t, 0.0) + i
        norm = math.sqrt(sum(v * v for v in w.values())) or 1.0
        vecs.append({t: v / norm for t, v in w.items()})
    return vecs, idf


def cosine(a, b):
    if not a or not b:
        return 0.0
    x, y = (a, b) if len(a) <= len(b) else (b, a)
    return sum(w * y.get(k, 0.0) for k, w in x.items())


def tok_2gram(text):
    """基线：机械字符 2-gram（升级前的生产行为）"""
    if not text:
        return []
    text = str(text)
    chars = _CJK.findall(text)
    toks = [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
    toks.extend(w.lower() for w in _LAT.findall(text))
    toks.extend(_NUM.findall(text))
    return toks


def tok_jieba(text):
    import jieba
    jieba.setLogLevel(60)
    return [w for w in jieba.lcut(str(text)) if len(w) >= 2]


def main():
    items = load_corpus()
    print(f"语料 {len(items)} 条")

    nodes = json.loads((BASE / "hypotheses" / "active_hypotheses.json").read_text(encoding="utf-8"))
    majors = [h for h in nodes if h.get("level") == "major"]
    print(f"major 假设 {len(majors)} 个")

    sp = cs._get_sp()
    print(f"生产分词器: {'SP-unigram' if sp else '字符2-gram(降级)'}"
          + (f" (vocab={sp.get_piece_size()})" if sp else ""))

    def hyp_text(h):
        """与 link_intel_hyp 生产代码的假设文档构成**完全一致**
        （含 falsification_criteria[:200]——漏掉它会让 claim/rationale 为空的
        节点只剩标题，测出虚假的 0.0 分）"""
        ind = " ".join(str(i.get("name", "")) for i in (h.get("indicators") or [])
                       if isinstance(i, dict))
        return " ".join([str(h.get("title", "")), str(h.get("core_claim") or ""),
                         str(h.get("rationale") or ""),
                         str(h.get("falsification_criteria") or "")[:200], ind])

    plans = [("字符2-gram(升级前)", tok_2gram),
             ("SP-unigram(生产代码)", cs._tokens),
             ("jieba词级(对照)", tok_jieba)]

    for name, tokfn in plans:
        hyp_docs = [tokfn(hyp_text(h)) for h in majors]
        intel_docs = [tokfn(item_text(it)) for it in items]
        # 标注样本投进**同一** IDF 空间（此前每条单独重建 IDF，min_df 滤掉
        # 罕见 token → 大量虚假 0.0 分，测的不是生产行为）
        label_docs = [tokfn(t) for t, _ in LABELS]
        vecs, idf = build_vecs(hyp_docs + intel_docs + label_docs)
        n_h, n_i = len(majors), len(items)
        hvs = vecs[:n_h]
        ivs = vecs[n_h:n_h + n_i]
        lvs = vecs[n_h + n_i:]
        hit = sum(1 for iv in ivs if max((cosine(iv, hv) for hv in hvs), default=0) >= 0.12)
        correct = 0
        hits_detail = []
        for (text, expect), lv in zip(LABELS, lvs):
            scored = sorted(((cosine(lv, hv), h["id"]) for hv, h in zip(hvs, majors)),
                            reverse=True)
            best = scored[0]
            if best[1] == expect:
                correct += 1
            hits_detail.append((text[:22], expect, best[1], round(best[0], 3)))
        print(f"\n[{name}]")
        print(f"  全语料命中(>=0.12): {hit} ({100 * hit / len(items):.2f}%)")
        print(f"  标注样本正确: {correct}/{len(LABELS)}")
        for t, exp, got, sc in hits_detail:
            mark = "OK " if exp == got else "MISS"
            print(f"    {mark} {t:<24} 期望{exp} 实得{got} ({sc})")


if __name__ == "__main__":
    main()
