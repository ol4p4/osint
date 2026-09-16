# 补齐骨架各期缺失的单稿（配额限流导致批量跑时部分期只拿到一稿）
# 用法：python tools/fill_missing_drafts.py [期号...]  （不带参数=自动检测全部缺口）
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(r"D:\osint")
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "local"))
import history_engine as he  # noqa: E402


def _existing(sec):
    """返回该期已有稿件 {角色: 文本}，从最近的双稿文件里取。"""
    files = sorted(he.DRAFTS.glob(f"00-skeleton__sec{sec}_*_draft.md"))
    got = {}
    for f in files:
        t = f.read_text(encoding="utf-8")
        for role in ("主笔Mimo", "副笔Nemotron"):
            m = re.search(rf"^## {role} 稿\n(.*?)(?=\n## |\Z)", t, re.M | re.S)
            if m and m.group(1).strip():
                got[role] = m.group(1).strip()
    return got, files


def _prompt(sec, pack):
    skel = he._skeleton_text()
    period_hint = f"骨架对应分期见下方材料。本节只写分期表第 {sec} 期相关内容。"
    system = he.WRITER_RULES.format(chars=he.DRAFT_CHARS)
    user = f"""## 任务
为《元叙事骨架》起草第 {sec} 节。

## 硬性结构要求
{period_hint}

## 骨架（00-skeleton，分期与主导矛盾以此为准）
{skel[:6000]}

{he._anchor_legend(pack)}
## 材料包（数字与锚点的唯一合法来源）
{pack if pack else "（空：全部数字请标【无据待查】）"}

## 输出
单节正文，{he.DRAFT_CHARS} 字。"""
    return system, user


def main(secs):
    main_az, sub_az = he._load_analyzers()
    pack = he._materials()
    for sec in secs:
        got, files = _existing(sec)
        missing = [r for r in ("主笔Mimo", "副笔Nemotron") if r not in got]
        if not missing:
            print(f"[P{sec}] 两稿齐全，跳过")
            continue
        system, user = _prompt(sec, pack)
        for role in missing:
            az = main_az if role == "主笔Mimo" else sub_az
            print(f"[P{sec}] 补 {role} …")
            try:
                # 两条链都含推理模型，统一 360s（主笔曾 180s 导致降级后必超时）
                got[role] = az._call_api(system, user, timeout=360)
                print(f"[P{sec}] {role} OK（{len(got[role])} 字符）")
            except Exception as e:
                print(f"[P{sec}] {role} 失败: {e}")
        if len(got) == 2:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
            out = he.DRAFTS / f"00-skeleton__sec{sec}_{stamp}_draft.md"
            parts = [f"# 00-skeleton 第{sec}节 双稿（{stamp}，补齐合并）\n"]
            for role in ("主笔Mimo", "副笔Nemotron"):
                parts.append(f"\n---\n## {role} 稿\n\n{got[role]}\n")
            # 裁判分歧
            try:
                diff = sub_az._call_api(
                    "你是审稿裁判。只输出两稿的实质性分歧清单：事实冲突/框架差异/评价差异逐条列出"
                    "（每条一行，注明哪稿更强），没有分歧写「无实质分歧」。不评文风。",
                    f"## A稿（主笔）\n{got['主笔Mimo']}\n\n## B稿（副笔）\n{got['副笔Nemotron']}",
                    timeout=360)
                parts.append(f"\n---\n## 裁判分歧清单\n\n{diff}\n")
            except Exception as e:
                parts.append(f"\n---\n## 裁判分歧清单\n\n（比对失败: {e}）\n")
            parts.append("\n---\n> 主编裁决后把定稿贴进本文件顶部「定稿」区，再跑 --verify。")
            out.write_text("\n".join(parts), encoding="utf-8")
            print(f"[P{sec}] 合并双稿 → {out.name}")
            # 删掉旧的单稿文件
            for f in files:
                if f != out:
                    f.unlink()
                    print(f"[P{sec}] 清理 {f.name}")
        else:
            print(f"[P{sec}] 仍缺 {[r for r in ('主笔Mimo','副笔Nemotron') if r not in got]}，稍后重试")
    return 0


if __name__ == "__main__":
    args = [int(x) for x in sys.argv[1:]] or [3, 4, 5, 6, 7]
    sys.exit(main(args))
