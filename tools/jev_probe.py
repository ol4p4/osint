# -*- coding: utf-8 -*-
r"""jev_probe.py - JEV 决策模型接入探针（Phase 0，2026-09-20）

目的：用历史 ACH 证据做 A/B，验证 JEV 在**中文情报判定**上的可用性，
决定走 API 还是本地复刻。方案见 docs/JEV落地方案-2026-09-20.md。

判定与旧口径的对应：
    JEV choice  →  ACH code
    consistent    →  C
    inconsistent  →  I
    neutral       →  N

关键设计（实测确认，勿改）：
  1. conf 取 probabilities 里的**原始概率**，不取 confidence 字段——
     实测验证 confidence = (P_max - 1/K)/(1 - 1/K) 只是集中度归一化，
     非正确性估计（4 组样本公式吻合，最大差 0.01）。
  2. 一次请求问全部 major 假设，保留跨假设比较能力（实测 138 处信号
     来自"未预挂载却被判 C/I"，拆成独立调用会丢失）。
  3. LR 不向模型索取，由 derive_lr(code, conf) 在代码里算（与 ach_matrix 同源）。

用法：
    python tools/jev_probe.py --sample 30          # 抽样对比
    python tools/jev_probe.py --sample 30 --dry    # 只列样本不调 API
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

PROJECT = Path(r"D:\osint")
BASE = PROJECT / "data"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "local"))

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_ALLOWED_HOST = "api.typesafe.ai"

CODE_MAP = {"consistent": "C", "inconsistent": "I", "neutral": "N"}


def _safe_jev_post(url, payload, key, timeout=120):
    """SSRF 守卫（与 analyze._safe_ai_post 同规格）：https + 白名单 + 非私有地址"""
    import ipaddress
    import socket
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") != JEV_ALLOWED_HOST:
        raise ValueError("blocked non-whitelisted JEV endpoint: " + url)
    for info in socket.getaddrinfo(parsed.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
            raise ValueError("endpoint resolves to forbidden address: " + str(ip))
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_questions(majors):
    """构造 8 个 Choice 问题（每假设一个），一次请求并行求值。

    2026-09-21 实测教训：**不要把 falsification_criteria 塞进 instructions**。
    同一证据仅改提问措辞，结论就从 inconsistent(0.77) 翻成 neutral(0.31)；
    15 条样本上，带判据版一致率 20% vs 简单版 40%——直接减半。
    原因符合官方 jaggedness 第 5 条「无关细节拉低精度」：证伪判据里的
    具体数字指标对「方向判断」是噪声。
    """
    qs = {}
    for h in majors:
        qs["h_" + h["id"]] = {
            "type": "choice",
            "instructions": "这条证据与假设「" + h.get("title", "") + "」的预期是否一致？",
            "criteria": {
                "consistent": "证据支持该假设的预期",
                "inconsistent": "证据与该假设的预期相斥",
                "neutral": "证据与该假设无关",
            },
        }
    return qs


def diagnose(state_text, majors, key, model="jev-latest"):
    """一次 JEV 调用 → {hyp_id: {"code","conf","probs"}}"""
    payload = {"model": model, "state": state_text, "questions": build_questions(majors)}
    t0 = time.time()
    d = _safe_jev_post(JEV_URL, payload, key)
    elapsed = time.time() - t0
    out = {}
    for h in majors:
        a = (d.get("answers") or {}).get("h_" + h["id"])
        if not a:
            continue
        choice = a.get("choice")
        probs = a.get("probabilities") or {}
        # conf 取所选选项的原始概率（非 confidence 字段，见文件头说明）
        out[h["id"]] = {
            "code": CODE_MAP.get(choice, "N"),
            "conf": round(float(probs.get(choice, 0.0)), 3),
            "probs": probs,
            "confidence_field": a.get("confidence"),
        }
    return out, elapsed, d.get("usage") or {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=30, help="抽样条数")
    ap.add_argument("--dry", action="store_true", help="只列样本，不调 API")
    ap.add_argument("--model", default="jev-latest")
    ap.add_argument("--out", default="", help="结果落盘路径（默认 data/jev_probe_YYYYMMDD.json）")
    args = ap.parse_args()

    from secrets_loader import get_jev_key
    from ach_matrix import derive_lr

    key = get_jev_key()
    if not key and not args.dry:
        print("[JEV-PROBE] 未找到 key（环境变量 TYPESAFE_API_KEY 或 config.local.yaml 的 jev.api_key）")
        return

    hyps = json.loads((BASE / "hypotheses" / "active_hypotheses.json").read_text(encoding="utf-8"))
    majors = [h for h in hyps if h.get("level") == "major"]
    mx = json.loads((BASE / "hypotheses" / "ach_matrix.json").read_text(encoding="utf-8"))

    # 采样：优先取有 C/I 的历史行（区分度测试），不足再补 N 行
    ci_rows = [e for e in mx["evidence"]
               if any(d.get("code") in ("C", "I") for d in (e.get("diagnosis") or {}).values())]
    n_rows = [e for e in mx["evidence"] if e not in ci_rows]
    ci_take = ci_rows[:max(args.sample * 2 // 3, 1)]
    n_take = n_rows[:max(args.sample - len(ci_take), 0)]
    sample = ci_take + n_take

    print(f"[JEV-PROBE] 历史行 {len(mx['evidence'])} 条（有 C/I 的 {len(ci_rows)} 条）")
    print(f"[JEV-PROBE] 本批采样 {len(sample)} 条（C/I {len(ci_take)} + N {len(n_take)}）")
    if args.dry:
        for e in sample[:10]:
            print("   -", e.get("summary", "")[:60])
        return

    results, agree_ci, total_ci = [], 0, 0
    code_dist = Counter()
    conf_list = []
    total_in = total_out = 0
    failed = 0
    t_start = time.time()

    for i, e in enumerate(sample, 1):
        state = "证据（" + str(e.get("date", "")) + "）：" + str(e.get("summary", ""))[:400]
        try:
            got, elapsed, usage = diagnose(state, majors, key, args.model)
        except urllib.error.HTTPError as ex:
            failed += 1
            print(f"  [{i}] HTTPError {ex.code}: {ex.read().decode('utf-8', 'replace')[:120]}")
            continue
        except Exception as ex:
            failed += 1
            print(f"  [{i}] {type(ex).__name__}: {str(ex)[:120]}")
            continue
        total_in += usage.get("input_tokens", 0)
        total_out += usage.get("output_tokens", 0)

        row = {"key": e.get("key"), "summary": e.get("summary", "")[:120], "jev": {}, "old": {}}
        for h in majors:
            hid = h["id"]
            old = (e.get("diagnosis") or {}).get(hid) or {}
            g = got.get(hid)
            if not g:
                continue
            row["jev"][hid] = {"code": g["code"], "conf": g["conf"],
                              "lr": derive_lr(g["code"], g["conf"])}
            row["old"][hid] = {"code": old.get("code"), "lr": old.get("lr")}
            code_dist[g["code"]] += 1
            if g["code"] in ("C", "I"):
                conf_list.append(g["conf"])
            # 一致率：仅在历史判 C/I 的槽位上算
            if old.get("code") in ("C", "I"):
                total_ci += 1
                if g["code"] == old.get("code"):
                    agree_ci += 1
        results.append(row)
        if i % 10 == 0:
            print(f"  [{i}/{len(sample)}] 已处理，累计 C/I 一致 {agree_ci}/{total_ci}")

    elapsed_all = time.time() - t_start
    print()
    print("=" * 56)
    print(f"[JEV-PROBE] 完成 {len(results)}/{len(sample)} 条（失败 {failed}），耗时 {elapsed_all:.1f}s"
          f"（{elapsed_all / max(len(results), 1):.2f}s/条）")
    if failed:
        print(f"[JEV-PROBE] 注意：{failed} 条失败。官方限流为动态调整（250k tok/s、1200 req/min），"
              f"失败条下轮重试即可，不影响统计口径。")
    print(f"[JEV-PROBE] tokens: in={total_in} out={total_out} | 估算成本 "
          f"${total_in / 1e6 * 0.042:.4f}")
    print()
    print("--- JEV 判定码分布 ---")
    tot = sum(code_dist.values()) or 1
    for c in ("C", "I", "N"):
        print(f"    {c}: {code_dist[c]:5d} ({code_dist[c] * 100 / tot:.1f}%)")
    print("    （历史分布参考：N 93% / C 3.6% / I 3.5%）")
    print()
    if total_ci:
        print(f"--- C/I 一致率（历史判 C/I 的 {total_ci} 个槽位）---")
        print(f"    {agree_ci}/{total_ci} = {agree_ci * 100 / total_ci:.1f}%")
        print("    决策门槛：>80% 走 API / 60-80% 混合 / <60% 走本地复刻")
    else:
        print("--- 本批无历史 C/I 槽位，无法算一致率 ---")
    if conf_list:
        conf_list.sort()
        n = len(conf_list)
        print()
        print(f"--- JEV conf 分布（C/I 判定，n={n}）---")
        print(f"    min={conf_list[0]:.3f} p50={conf_list[n // 2]:.3f} max={conf_list[-1]:.3f}")
        uniq = len(set(round(c, 2) for c in conf_list))
        print(f"    不同取值数: {uniq}  ({'有梯度 OK' if uniq > 3 else '塌缩，需减少 prompt 示例'})")

    out_path = Path(args.out) if args.out else BASE / ("jev_probe_" + time.strftime("%Y%m%d") + ".json")
    out_path.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": args.model, "n_sample": len(sample), "n_done": len(results),
        "code_dist": dict(code_dist),
        "ci_agreement": {"agree": agree_ci, "total": total_ci},
        "conf_stats": ({"min": conf_list[0], "p50": conf_list[len(conf_list) // 2],
                        "max": conf_list[-1]} if conf_list else None),
        "usage": {"input_tokens": total_in, "output_tokens": total_out},
        "results": results,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    print(f"[JEV-PROBE] 结果落盘: {out_path}")


if __name__ == "__main__":
    main()
