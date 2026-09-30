# -*- coding: utf-8 -*-
r"""train_sp_tokenizer.py - 从项目语料训练 SentencePiece unigram 词表（2026-09-30）

**依据**（Si et al., TACL 2023, "Sub-Character Tokenization for Chinese PLMs",
Tsinghua + Huawei）：中文 PLM 的标准做法是**从语料学习子词词表**
（SentencePiece unigram / BPE），而非机械字符切分。词表由「子字 token +
字 token + 词组 token」混合构成。

**为什么需要**：`cluster_stories._tokens` 原用字符 2-gram，产生跨词垃圾 token
（"机将""将投"），稀释 TF-IDF 信号。实测对比（9 条人工标注）：
  2-gram 2/9 → jieba 3/9 → SP-unigram 4/9；全语料命中 0.43% → 2.22%。

**何时重训**：语料显著变化时（如新增领域、源结构大改）。词表 8000 词，
覆盖通用中文新闻足够；日常无需重训。

用法：
    python tools/train_sp_tokenizer.py            # 用近 7 天语料训练
    python tools/train_sp_tokenizer.py --days 14  # 更大窗口
"""
import argparse
import glob
import json
import sys
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
MODEL_PREFIX = BASE / ".sp_unigram"
TRAIN_CORPUS = BASE / ".sp_train_corpus.txt"

VOCAB_SIZE = 8000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="训练语料窗口天数")
    args = ap.parse_args()

    try:
        import sentencepiece as spm
    except ImportError:
        print("[SP-TRAIN] 未安装 sentencepiece——请先 pip install sentencepiece")
        return 1

    files = sorted(glob.glob(str(BASE / "intel_2*.jsonl")), reverse=True)[:args.days + 2]
    lines = []
    for f in files:
        name = Path(f).name
        if "raw" in name or "final" in name:
            continue
        for line in Path(f).read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = " ".join(p for p in [d.get("cn_title") or d.get("title") or "",
                                     d.get("cn_summary") or d.get("summary") or ""] if p)
            t = t.replace("\n", " ").strip()
            if len(t) >= 10:
                lines.append(t)

    if len(lines) < 500:
        print(f"[SP-TRAIN] 语料仅 {len(lines)} 行，不足 500——跳过（保留现有词表）")
        return 1

    TRAIN_CORPUS.write_text("\n".join(lines), encoding="utf-8")
    print(f"[SP-TRAIN] 训练语料 {len(lines)} 行 -> {TRAIN_CORPUS.name}")

    spm.SentencePieceTrainer.train(
        input=str(TRAIN_CORPUS), model_prefix=str(MODEL_PREFIX),
        vocab_size=VOCAB_SIZE, model_type="unigram",
        character_coverage=1.0, num_threads=4,
        minloglevel=2,   # 静默 EM 迭代日志
    )
    print(f"[SP-TRAIN] 词表已保存: {MODEL_PREFIX}.model (vocab={VOCAB_SIZE})")

    # 训练语料是中间产物，训完即删（词表本身已足够）
    try:
        TRAIN_CORPUS.unlink()
        print("[SP-TRAIN] 已清理训练语料中间文件")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
