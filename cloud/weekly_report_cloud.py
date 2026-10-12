# -*- coding: utf-8 -*-
r"""weekly_report_cloud.py - CI 侧参谋周报生成（2026-10-12 新增）

## 为什么需要它

周报原本**只有本地一条路径**（`local/hypothesis_engine._save_ai_weekly_summary`，
由 OsintWeekly 周一 09:30 跑）。2026-10-12 实测暴露两个问题：

1. **本地周循环会在 AI 通道断供时静默挂死**——进程零 CPU / 零网络 / 零日志
   持续 5 分钟以上（120s + 180s 双窗口实测增量为 0），无声退出，**周报未产出**。
2. **同一天云端 CI 的 AI 通道是好的**——CI 用 Gemini 跑 citizen_impact 正常，
   本地 opencode/dots/zen 代理则全断。

所以让周报也上云：CI 侧生成一份，随仓库带回本地。

## 为什么不在 cloud/ 里复制一份 prompt

周报的取材逻辑（源配额 + 主题配额取样、60 席分配、反幻觉护栏、内容安全降级）
是**多轮踩坑调优的结果**（见 AGENTS.md）。复制一份必然与本地漂移，调优经验也会
分裂。因此逻辑抽在 `local/weekly_report.py`，本地与云端**共用同一套**：

    local/hypothesis_engine.py     → local/weekly_report + analyzer._call_api
    cloud/weekly_report_cloud.py   → local/weekly_report + 本文件的 call_ai

本文件只负责三件事：**取数（上周 intel + 假设树）、调 AI、写独立产物**。

## 边界（与 jev_signal_scan 同规格）

- **只读假设树**：`data/hypotheses/` 的唯一有效写入方是本地（历史教训：CI 改它
  会被 checkout 丢弃）。本脚本读 active_hypotheses.json / ach_matrix.json 出素材，
  **绝不写**，产物只落 `weekly_cloud_YYYYMMDD.md`。
- **不碰 resolutions/confidence**：周报是展示层产物，判定与校准仍在本地。
- **AI 不可达时静默跳过**，绝不失败 CI job（周报是增益，不是关键路径）。
- 幂等可重跑：同日重跑覆盖自己的产物，不动别的东西。

## 产物与消费

    weekly_cloud_YYYYMMDD.md     CI 产出（机器索引 + AI 总结）
    tools/merge_ci_weekly.py     本地按周窗口收进 data/reports/

## 用法

    python cloud/weekly_report_cloud.py --dir .            # 正常
    python cloud/weekly_report_cloud.py --dir . --dry      # 只看素材不调 AI
    python cloud/weekly_report_cloud.py --dir . --force    # 周日也强制生成
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "local"))

# 周报是散文（不是 JSON），所以不能复用 citizen_impact.call_ai——那个函数的
# 返回值检查写死了"必须含 '['"（防推理模型把思维链当正文返回），散文会被判失败。
# 这里用同一条通道链（Gemini → dots → 本机 zen → OpenCode），只改产出校验。
USER_AGENT = "osint-weekly/1.0"
AI_ALLOWED_HOST = "opencode.ai"
DOTS_BASE = "https://note3-prev-api.askdiandian.com/v1"


def _safe_post(url, payload, headers, timeout=180):
    """SSRF 守卫：仅 https + 白名单域名 + 解析结果不得为私有/环回/保留地址。

    白名单与 cloud/citizen_impact.py 同规格（opencode.ai / askdiandian），
    加 Gemini 由 local/gemini_client 自带守卫负责。
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse
    parsed = urlparse(url)
    allowed = (AI_ALLOWED_HOST, urlparse(DOTS_BASE).hostname)
    if parsed.scheme != "https" or (parsed.hostname or "") not in allowed:
        raise ValueError("blocked non-whitelisted AI endpoint: " + url)
    for info in socket.getaddrinfo(parsed.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
            raise ValueError("endpoint resolves to forbidden address: " + str(ip))
    import urllib.request
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def call_ai(system, prompt):
    """散文通道链：Gemini → dots → 本机 zen 代理 → OpenCode。

    通道顺序与 cloud/citizen_impact.call_ai 保持一致（那边是 2026-10-05 用户
    指定的顺序，且各有实测依据）。**唯一区别**：产出校验只要求"非空且不像
    思维链泄漏"，不要求 JSON。
    """
    attempts = []

    # ① Gemini（CI 上有 key；本地境内不可达，靠 reachable() 预检跳过）
    try:
        from secrets_loader import get_gemini_key
        from local.gemini_client import generate as _gemini, reachable as _g_reach
        gkey = get_gemini_key()
        if gkey and _g_reach():
            attempts.append({"provider": "gemini", "api_key": gkey})
    except Exception as ex:
        print("[WEEKLY-CI] gemini 通道跳过: " + str(ex)[:100])

    # ② dots 备援（key 在 CI Secrets 缺失则跳过；本地有）
    try:
        from secrets_loader import get_dots_key
        dkey = get_dots_key()
        if dkey:
            attempts.append({"base_url": DOTS_BASE, "model": "dots3-note-prev", "api_key": dkey})
    except Exception:
        pass

    # ③ 本机 zen 代理（CI 无 4010 端口，预检自动跳过）
    try:
        from local.zen_proxy_client import reachable as _z_reach
        if _z_reach():
            for m in ("mimo-v2.6-flash-free", "nemotron-3.5-lightning-free"):
                attempts.append({"provider": "zen_proxy", "model": m})
    except Exception:
        pass

    # ④ OpenCode 官网直连（垫底）
    try:
        from secrets_loader import get_opencode_key
        okey = get_opencode_key()
    except Exception:
        okey = ""
    import yaml
    try:
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        cfg = {}
    api_cfg = cfg.get("api", {})
    attempts.append({"base_url": api_cfg.get("base_url", "https://opencode.ai/zen/v1"),
                     "model": api_cfg.get("model", "mimo-v2.6-flash-free"),
                     "api_key": okey or api_cfg.get("api_key", "")})
    for fb in (api_cfg.get("fallback_models") or []):
        fb = dict(fb)
        fb["api_key"] = fb.get("api_key") or okey
        attempts.append(fb)

    last_err = None
    for m in attempts:
        label = m.get("provider") or m.get("model")
        try:
            if m.get("provider") == "zen_proxy":
                from local.zen_proxy_client import chat_completion as _zen_chat
                text = _zen_chat(m["model"],
                                 [{"role": "system", "content": system},
                                  {"role": "user", "content": prompt}],
                                 temperature=0.3, max_tokens=12288, timeout=180)
            elif m.get("provider") == "gemini":
                from local.gemini_client import generate as _gemini
                text = _gemini(m["api_key"], prompt, system_prompt=system,
                               timeout=240, max_tokens=12288)
            else:
                import urllib.error
                import urllib.request
                url = (m.get("base_url") or "").rstrip("/") + "/chat/completions"
                payload = json.dumps({
                    "model": m.get("model"),
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    # 推理模型 reasoning 计入 max_tokens，按 reasoning+正文双份估算
                    "max_tokens": 12288,
                }).encode("utf-8")
                headers = {"Content-Type": "application/json",
                           "Authorization": "Bearer " + (m.get("api_key") or ""),
                           "User-Agent": USER_AGENT}
                if "askdiandian" in url:
                    headers["api-key"] = m.get("api_key") or ""
                raw = _safe_post(url, payload, headers, 180)
                result = json.loads(raw)
                msg = result["choices"][0]["message"]
                text = msg.get("content", "")
                if not text:
                    for k in ("reasoning_content", "reasoning"):
                        v = msg.get(k, "")
                        if v and v.strip():
                            text = v
                            break
            if text and text.strip():
                # 反"思维链当正文"：周报正文必然含 markdown 小节标题或中文标点，
                # 纯推理泄漏通常以"首先，用户要求…"这类开头。用长度 + 无标题判据。
                t = text.strip()
                if len(t) < 120 and ("首先" in t[:40] or "用户" in t[:40]):
                    raise ValueError("looks like reasoning trace: " + t[:100])
                print("[WEEKLY-CI] 通道成功: " + str(label))
                return t
            raise ValueError("empty response")
        except Exception as ex:
            last_err = ex
            print("[WEEKLY-CI] 通道失败 %s: %s" % (label, str(ex)[:120]))
    raise last_err if last_err else RuntimeError("no AI channel available")


def load_intel_for_week(directory, week_start, week_end):
    """读窗口内每日 intel_YYYYMMDD.jsonl（CI 侧这些文件在仓库根，已 git 追踪）。

    排除 intel_raw_* / intel_final_*（不参与下游，见 AGENTS.md）。
    """
    items = []
    seen_days = 0
    day = week_start
    while day <= week_end:
        f = Path(directory) / ("intel_" + day.strftime("%Y%m%d") + ".jsonl")
        if f.exists() and f.stat().st_size > 0:
            seen_days += 1
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        try:
                            items.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except Exception:
                pass
        day += timedelta(days=1)
    print("[WEEKLY-CI] 覆盖 %d 个日文件，载入 %d 条" % (seen_days, len(items)))
    return items


def main():
    ap = argparse.ArgumentParser(description="CI 侧参谋周报生成（只读假设树）")
    ap.add_argument("--dir", default=".", help="仓库根（intel_*.jsonl 所在）")
    ap.add_argument("--out-dir", default=None, help="产物目录（默认与 --dir 相同）")
    ap.add_argument("--week-offset", type=int, default=0, help="0=上一完整周，N=再往前 N 周")
    ap.add_argument("--dry", action="store_true", help="只看素材不调 AI")
    ap.add_argument("--force", action="store_true",
                    help="非周一也生成（默认只在周一跑，避免每天刷出重复周报）")
    ap.add_argument("--min-items", type=int, default=50,
                    help="窗口内情报少于此数则不生成（防素材不足产出垃圾）")
    args = ap.parse_args()

    out_dir = Path(args.out_dir or args.dir)
    # 默认只在周一生成：周报语义是"上一完整周"，周一到周日之间素材不会变
    now = datetime.now()
    if not args.force and now.weekday() != 0:
        print("[WEEKLY-CI] 今天周%d，非周一（--force 可覆盖），跳过" % (now.weekday() + 1))
        return

    try:
        import weekly_report as wr
    except ImportError:
        from local import weekly_report as wr

    hyp_file = ROOT / "data" / "hypotheses" / "active_hypotheses.json"
    ach_file = ROOT / "data" / "hypotheses" / "ach_matrix.json"
    if not hyp_file.exists():
        print("[WEEKLY-CI] 假设树不存在，跳过: " + str(hyp_file))
        return
    try:
        hyps = json.loads(hyp_file.read_text(encoding="utf-8"))
    except Exception as ex:
        print("[WEEKLY-CI] 假设树读取失败，跳过: " + str(ex)[:120])
        return
    ach = None
    if ach_file.exists():
        try:
            ach = json.loads(ach_file.read_text(encoding="utf-8"))
        except Exception:
            ach = None
    print("[WEEKLY-CI] 假设树 %d 节点（major %d），矩阵 %s"
          % (len(hyps), sum(1 for h in hyps if h.get("level") == "major"),
             ("%d 行" % len(ach.get("evidence") or [])) if ach else "缺失"))

    week_start, week_end = wr.week_window(args.week_offset)
    items = load_intel_for_week(args.dir, week_start, week_end)
    if len(items) < args.min_items:
        print("[WEEKLY-CI] 窗口素材仅 %d 条 < 下限 %d，跳过（不产出低质周报）"
              % (len(items), args.min_items))
        return

    ctx, system, prompt = wr.build_weekly_context(
        hyps, ach_matrix=ach, intel_items=items, week_offset=args.week_offset)
    print("[WEEKLY-CI] 窗口 %s ~ %s | 窗口内 %d 条 | 取样 %d | prompt %d 字"
          % (ctx["week_start"], ctx["week_end"], ctx["week_intel_total"],
             ctx["sampled"], len(prompt)))

    if args.dry:
        print("[WEEKLY-CI] --dry 结束（未调 AI）")
        print(ctx["themes_block"][:600])
        return

    degraded = False
    try:
        summary = call_ai(system, prompt)
    except Exception as ex:
        print("[WEEKLY-CI] AI 全通道失败，跳过: " + str(ex)[:200])
        return

    body = wr.render_weekly_markdown(
        ctx, summary, degraded=degraded,
        generator="cloud/weekly_report_cloud.py（CI 侧生成）")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("weekly_cloud_" + ctx["date_str"] + ".md")
    path.write_text(body, encoding="utf-8")
    print("[WEEKLY-CI] 产物: " + path.name + "（%d 字节）" % len(body))


if __name__ == "__main__":
    main()
