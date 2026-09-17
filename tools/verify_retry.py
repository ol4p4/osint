# -*- coding: utf-8 -*-
"""verify_retry.py — 增量补跑核查报告中的失败节（只重试失败的，不动已通过的）

背景：免费 AI 通道（OpenCode/OpenRouter/NVIDIA）在长 prompt 下偶发超时，
整轮重跑会随机换一批节失败，永远凑不齐。本脚本读现有 _verify.md，
只对「裁判失败」的节重跑，把结果原地更新——多跑几轮即可全绿。

用法：
  python tools/verify_retry.py <draft文件名> [最大轮数]
"""
import re
import sys
from pathlib import Path

PROJECT = Path(r"D:\osint")
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "local"))
import history_engine as he  # noqa: E402

JUDGE_SYS = (
    "你是事实核查裁判，不持立场。检查给定历史文本，只查这三类问题：\n"
    "1) 内部矛盾（前后说法冲突）\n"
    "2) 与公认史实明显冲突的断言（时间/数字/事件性质错误）\n"
    "3) 无出处却断言为事实的**具体数字或具体事件细节**\n"
    "重要排除项（不要列为问题）：\n"
    "- 以「判断：」开头的分析性段落——这是作者显式标注的判断，不是事实断言\n"
    "- 概括性叙述（如'地方依赖土地出让获取财源'）——属公认背景，不要求逐句锚\n"
    "- 【据:xxx】标签本身的形式（内部代码标签是本文档的既定引用格式）\n"
    "逐条列出并引用原句；都无则写「未发现问题」。不评价观点与立场。"
)


def main(fname, max_rounds=6):
    src = he.DRAFTS / fname
    if not src.exists():
        print(f"文件不存在: {src}")
        return 1
    raw = src.read_text(encoding="utf-8")
    text = he._final_text(raw)
    if text is raw:
        print("未检测到「定稿」区——将校验全文")
    sections = [s for s in re.split(r"\n(?=#{2,4} )", text) if s.strip()]
    rep_path = src.with_name(src.stem.replace("_draft", "") + "_verify.md")
    if not rep_path.exists():
        print(f"核查报告不存在，请先跑 --verify: {rep_path}")
        return 1

    _, sub = he._load_analyzers()
    for rnd in range(1, max_rounds + 1):
        rep = rep_path.read_text(encoding="utf-8")
        # 找出失败的节标题
        fails = []
        for m in re.finditer(r"^### (.+)$", rep, re.M):
            head = m.group(1)
            seg = rep[m.end():]
            nxt = re.search(r"^### ", seg, re.M)
            body = seg[:nxt.start()] if nxt else seg
            if "裁判失败" in body:
                fails.append(head)
        if not fails:
            print(f"全部通过（第 {rnd} 轮检查）")
            return 0
        print(f"[轮 {rnd}] 待补 {len(fails)} 节: {[f[:20] for f in fails]}")
        for head in fails:
            # 找对应正文节
            target = None
            for s in sections:
                first = s.splitlines()[0][:40]
                if first == head[:40]:
                    target = s
                    break
            if not target:
                print(f"  跳过 {head[:24]}（正文中未找到对应节）")
                continue
            try:
                verdict = sub._call_api(JUDGE_SYS, target[:2500], timeout=600)
                # 原地替换该节结论
                pat = re.compile(r"(^### " + re.escape(head) + r"\n\n).*?(?=\n### |\Z)",
                                 re.M | re.S)
                rep = pat.sub(lambda m: m.group(1) + verdict.strip() + "\n", rep)
                rep_path.write_text(rep, encoding="utf-8")
                print(f"  ✓ {head[:24]}（{len(verdict)} 字符）")
            except Exception as e:
                print(f"  ✗ {head[:24]}: {str(e)[:80]}")
    rep = rep_path.read_text(encoding="utf-8")
    remaining = rep.count("裁判失败")
    print(f"完成 {max_rounds} 轮，仍失败 {remaining} 节")
    return 0 if remaining == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    sys.exit(main(sys.argv[1], rounds))
