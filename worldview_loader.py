# -*- coding: utf-8 -*-
r"""worldview_loader.py - 三观（世界观/人生观/价值观）加载与研判 prompt 注入
worldview.yaml 是唯一事实源（仓库根，与 persona.md 同待遇，提交 GitHub 供 CI 读取）。
纯函数无 AI 依赖；文件缺失/格式坏一律返回空串（静默降级，沿用 macro_framework 容错模式）。

设计护栏：验证裁判（verify_hypotheses / hypothesis_engine.verify_hypothesis）不注入三观——
裁判必须中立，否则校准评分（Brier）会失真。三观只影响"分析与建议"的视角。
"""
import sys
from pathlib import Path

import yaml

WORLDVIEW_FILE = Path(__file__).resolve().parent / "worldview.yaml"
MAX_PROMPT_CHARS = 800   # 保护 system prompt 预算，三观注入超长截断


def load_worldview(path=None):
    """读三观 YAML → dict；缺失/解析失败/缺 worldview 节返回 None"""
    p = Path(path) if path else WORLDVIEW_FILE
    if not p.exists():
        return None
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("worldview"), dict):
        return None
    return data


def _prop_lines(sec, key="propositions", limit=3):
    out = []
    for item in (sec.get(key) or [])[:limit]:
        s = str(item).strip()
        if s:
            out.append("- " + s)
    return out


def build_worldview_prompt(path=None):
    """三观 dict → 紧凑注入文本（≤MAX_PROMPT_CHARS）；无数据返回 ''"""
    wv = load_worldview(path)
    if not wv:
        return ""
    lines = ["【用户三观】研判必须贴合此个人视角，而非中立通用建议"]

    w = wv.get("worldview") or {}
    if w.get("summary"):
        lines.append("世界观：" + str(w["summary"]))
    lines += _prop_lines(w)

    l = wv.get("lifeview") or {}
    if l.get("summary"):
        lines.append("人生观：" + str(l["summary"]))
    lines += _prop_lines(l)

    v = wv.get("values") or {}
    parts = []
    if v.get("summary"):
        parts.append(str(v["summary"]))
    if v.get("priorities"):
        parts.append("优先 " + "＞".join(str(x) for x in v["priorities"][:4]))
    if v.get("red_lines"):
        parts.append("不可妥协：" + "、".join(str(x) for x in v["red_lines"][:4]))
    if parts:
        lines.append("价值观：" + "；".join(parts))

    dirs = [str(x).strip() for x in (wv.get("analysis_directives") or [])[:3] if str(x).strip()]
    if dirs:
        lines.append("研判指令：" + "；".join(dirs))

    text = "\n".join(lines)
    if wv.get("draft"):
        text = "（初稿，待用户校正）\n" + text
    return text[:MAX_PROMPT_CHARS]


def check_worldview(path=None):
    """结构校验 → (ok: bool, problems: list[str])"""
    wv = load_worldview(path)
    if not wv:
        return False, ["文件缺失/解析失败/缺少 worldview 节"]
    problems = []
    for key in ("worldview", "lifeview", "values"):
        sec = wv.get(key)
        if not isinstance(sec, dict) or not (
                sec.get("summary") or sec.get("propositions")
                or sec.get("priorities") or sec.get("red_lines")):
            problems.append(f"{key} 节为空")
    return (not problems), problems


def main():
    import argparse
    ap = argparse.ArgumentParser(description="三观加载器：--show 显示当前档案与注入预览；--check 结构校验")
    ap.add_argument("--show", action="store_true", help="打印当前三观 YAML + 注入预览")
    ap.add_argument("--check", action="store_true", help="结构校验")
    ap.add_argument("--file", default=None, help="指定 worldview.yaml 路径（默认仓库根）")
    args = ap.parse_args()

    target = args.file
    if args.show or not args.check:
        wv = load_worldview(target)
        if not wv:
            print(f"[WORLDVIEW] 未找到有效三观档案: {target or WORLDVIEW_FILE}")
            print("[WORLDVIEW] 录入: python local/worldview_engine.py --interactive （或 --seed 起草初稿）")
            return 1
        print("[WORLDVIEW] === 当前档案 ===")
        print(yaml.safe_dump(wv, allow_unicode=True, sort_keys=False).strip())
        print("\n[WORLDVIEW] === 注入预览（研判 prompt 实际收到） ===")
        print(build_worldview_prompt(target))
    if args.check:
        ok, problems = check_worldview(target)
        if ok:
            print("[WORLDVIEW] 校验通过")
        else:
            print("[WORLDVIEW] 校验问题: " + "；".join(problems))
        return 0 if ok else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
