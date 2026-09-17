# -*- coding: utf-8 -*-
"""extract_fyp_tables.py — 从五年规划纲要官方 PDF 提取「主要指标」专栏表

背景：主编指出「核实应该下载原文，尤其五年规划」。此前材料表用的是二手汇总
（搜索摘要/媒体转述），曾因此产生 4 处史实错误。本脚本从发改委官网 PDF 原文
提取指标表，作为一手权威锚。

用法：python tools/extract_fyp_tables.py
产物：wiki/macro-history/sources/ 下的 *_指标提取.txt
"""
import re
import sys
from pathlib import Path

from pypdf import PdfReader

SOURCES = Path(r"D:\Codex输出\视频知识库\wiki\macro-history\sources")

# 指标专栏的定位关键词（纲要里叫「专栏N 主要指标」或「主要指标」）
KEYWORDS = ("主要指标", "专栏")


def extract(pdf_path: Path):
    reader = PdfReader(str(pdf_path))
    print(f"=== {pdf_path.name}：{len(reader.pages)} 页 ===")
    hits = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception as e:
            print(f"  第 {i+1} 页解析失败: {e}")
            continue
        if any(k in text for k in KEYWORDS) and re.search(r"指标|预期性|约束性", text):
            hits.append((i + 1, text))
    print(f"  命中指标页: {[h[0] for h in hits]}")
    return hits


def main():
    if not SOURCES.exists():
        print(f"源目录不存在: {SOURCES}")
        return 1
    pdfs = sorted(SOURCES.glob("*.pdf"))
    if not pdfs:
        print("未找到 PDF")
        return 1
    for pdf in pdfs:
        hits = extract(pdf)
        if not hits:
            continue
        out = pdf.with_name(pdf.stem + "_指标提取.txt")
        parts = [f"# {pdf.name} 指标表提取（原文一手数据）\n"]
        for pno, text in hits:
            # 清理多余空白
            clean = re.sub(r"[ \t]+", " ", text)
            clean = re.sub(r"\n{3,}", "\n\n", clean)
            parts.append(f"\n{'='*60}\n## 第 {pno} 页\n{'='*60}\n{clean.strip()}\n")
        out.write_text("\n".join(parts), encoding="utf-8")
        print(f"  → {out.name}（{sum(len(p) for _, p in hits)} 字符）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
