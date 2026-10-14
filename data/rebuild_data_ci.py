#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""rebuild_data_ci.py - CI 侧 dashboard_data 现算（2026-10-14）

## 为什么需要它

看板生成（gen_dashboard.py）读 `data/dashboard_data.json`，而该文件**只有本地
rebuild_data 会产出**——CI 上 `sync_data.py` 硬编码读 `D:\osint\data\...`，
从 2026-09-10 起一直在静默失败（`|| echo skip` 吞掉），CI 从未有过可用的
dashboard_data。翻译上云后 CI 已有全量翻译情报，只差这一步就能独立生成看板。

## 与本地 rebuild_data 的关系（刻意精简，不是复制）

保留（看板渲染必需）：id 去重、脏日期清洗、事件聚类、统一衰减 final_score、
每源配额准入、Top200、intel_top（24h 高分）、category_stats、假设树挂载。

省略（本地画像深度逻辑，CI 无必要）：
  - 主题保底带（RESERVE_TOPICS：本地 persona 画像的窗口准入，偏本地阅读偏好）
  - 历史条目去饱和重算（本地有 9 万条存量需要重套新曲线；CI 只算增量窗口）

## 输出

写到 `--out`（CI 默认 `dashboard_data.json` 到工作区根）。结构同本地：
`{generated_at, intelligence, intel_top, intel_count, hypotheses, macro, category_stats}`
—— gen_dashboard.py 零改动可读。
"""
import glob
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PER_SOURCE_CAP = 15
TOP_N = 200
TOP24_SOURCE_CAP = 8
TOP24_MIN_SCORE = 0.5


def _norm_cat(c):
    c = str(c or '').lower()
    known = ('energy', 'geopolitics', 'macro', 'finance', 'east_asia', 'tech',
             'public_health', 'trade', 'society')
    for k in known:
        if k in c:
            return k
    return 'other'


def _parse_dt(s):
    """条目时间 → 'YYYY-MM-DD HH:MM'（RSS 邮件日期等多格式兼容，同本地 rebuild）。"""
    s = str(s or '')
    m = re.match(r'(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})', s)
    if m:
        return m.group(1) + ' ' + m.group(2)
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return '1970-01-01 00:00'


def _clean_date(e):
    """脏日期过滤：未来时间戳 / 2020 以前 → False（同本地 rebuild 的闸门口径）。"""
    dt = _parse_dt(e.get('published_at'))
    try:
        d = datetime.strptime(dt, '%Y-%m-%d %H:%M')
    except ValueError:
        return False
    if d > datetime.now() + timedelta(days=2):
        return False
    return d.year >= 2020


def _decay_now(it):
    """CI 同款统一衰减（本地 fetch 条目无 final_score，这里全量现算）。"""
    try:
        pub = datetime.strptime(_parse_dt(it.get('published_at')),
                                '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - pub).total_seconds() / 3600
    except Exception:
        return 0.1
    if age_h < 0 or age_h >= 168:
        return 0.1
    return max(0.5 ** (age_h / 48.0), 0.1)


def _rel_score(it):
    """优先取已有 final_score（CI 条目评分层写过），否则现算衰减。"""
    try:
        v = float(it.get('final_score') or 0)
        if v > 0:
            return v
    except (TypeError, ValueError):
        pass
    return _decay_now(it)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="CI 侧 dashboard_data 现算")
    parser.add_argument('--dir', default='.', help='intel_2*.jsonl 所在目录')
    parser.add_argument('--hyp', default=str(ROOT / 'data' / 'hypotheses' / 'active_hypotheses.json'),
                        help='假设树路径（CI checkout 里 data/hypotheses/ 随 git）')
    parser.add_argument('--out', default='dashboard_data.json', help='输出路径')
    args = parser.parse_args()

    files = sorted(glob.glob(str(Path(args.dir) / 'intel_2*.jsonl')))
    if not files:
        print('[rebuild-ci] no intel files, skip')
        sys.exit(1)

    all_intel = []
    for f in files:
        try:
            for line in Path(f).read_text(encoding='utf-8', errors='replace').splitlines():
                line = line.strip()
                if line:
                    try:
                        all_intel.append(json.loads(line))
                    except Exception:
                        pass
        except OSError as e:
            print(f'[rebuild-ci] read {f} failed: {e}')

    # 去重 + 脏日期清洗（同本地）
    seen = set()
    unique = []
    dirty = 0
    for e in all_intel:
        eid = e.get('id', '')
        if eid and eid not in seen:
            if not _clean_date(e):
                dirty += 1
                continue
            seen.add(eid)
            unique.append(e)
    if dirty:
        print(f'[rebuild-ci] cleaned {dirty} dirty-date entries')

    # 事件聚类（story_id/story_size，看板折叠徽章用；失败不阻塞）
    try:
        sys.path.insert(0, str(ROOT / 'tools'))
        from cluster_stories import assign_story_ids
        stats = assign_story_ids(unique)
        print(f'[rebuild-ci] cluster: {stats}')
    except Exception as e:
        print(f'[rebuild-ci] cluster failed (non-blocking): {e}')

    # 分类归一 + 统计
    cat_stats = {}
    for i in unique:
        cat = _norm_cat(i.get('category_cn') or i.get('category') or 'other')
        cat_stats[cat] = cat_stats.get(cat, 0) + 1
        i['category_cn'] = cat

    # 每源配额准入 + Top200（同本地逻辑：源内按分数选代表，全局时间倒序）
    now_key = datetime.now().strftime('%Y-%m-%d %H:%M')

    def _rank_time(it):
        return min(_parse_dt(it.get('published_at')), now_key)

    def _per_source_top(pool):
        per_src = {}
        out = []
        for it in sorted(pool, key=lambda x: (_rank_time(x), _rel_score(x)), reverse=True):
            s = it.get('source_name') or it.get('source') or '_unknown'
            if per_src.get(s, 0) >= PER_SOURCE_CAP:
                continue
            per_src[s] = per_src.get(s, 0) + 1
            out.append(it)
        return out

    ranked = _per_source_top(unique)
    ranked.sort(key=lambda x: (_rank_time(x), _rel_score(x)), reverse=True)
    top200 = ranked[:TOP_N]
    print(f'[rebuild-ci] top200 from {len(unique)} unique '
          f'({len(set((i.get("source_name") or i.get("source") or "?") for i in unique))} sources)')

    # intel_top：24h 内 final_score >= 0.5，每源最多 8 条（右栏"24h 重要情报"）
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M')
    hi = [x for x in unique if _rank_time(x) >= cutoff and _rel_score(x) >= TOP24_MIN_SCORE]
    hi.sort(key=lambda x: (_rel_score(x), _rank_time(x)), reverse=True)
    intel_top = []
    per_src = {}
    for x in hi:
        s = x.get('source_name') or x.get('source') or '_unknown'
        if per_src.get(s, 0) >= TOP24_SOURCE_CAP:
            continue
        per_src[s] = per_src.get(s, 0) + 1
        intel_top.append(x)
        if len(intel_top) >= 60:
            break
    print(f'[rebuild-ci] intel_top: {len(intel_top)} 条 24h 高分')

    # 假设树（CI checkout 的 data/hypotheses/ 随 git，本地是唯一写入方——这里只读）
    hyps = []
    hyp_path = Path(args.hyp)
    if hyp_path.exists():
        try:
            hyps = json.loads(hyp_path.read_text(encoding='utf-8'))
        except Exception as e:
            print(f'[rebuild-ci] hypotheses parse failed: {e}')

    output = {
        'generated_at': datetime.now().isoformat(),
        'intelligence': top200,
        'intel_top': intel_top,
        'intel_count': len(unique),
        'hypotheses': hyps,
        'macro': {},
        'category_stats': cat_stats,
    }
    out_path = Path(args.out)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    cn = sum(1 for i in unique if i.get('cn_title'))
    print(f'[rebuild-ci] Data: {len(unique)} intel, {len(hyps)} hyps, {cn} CN-titled '
          f'-> {out_path} ({out_path.stat().st_size} bytes)')


if __name__ == '__main__':
    main()
