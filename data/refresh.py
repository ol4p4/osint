r"""refresh.py - 拉取最新情报并重建仪表盘（薄壳入口）
由计划任务 OsintRefresh 每小时调用，也可手动运行。
同步/翻译/日志逻辑在仓库 D:\osint\cloud\local_sync.py（便于 git 管理与交接）。
"""
import subprocess, sys, json, glob, os, re
from pathlib import Path
from datetime import datetime, timedelta

PROJECT = Path(r"D:\osint")
BASE = Path(r"D:\osint\data")
sys.path.insert(0, str(PROJECT))
from cloud.local_sync import run_logging, git_pull, sync_repo_intel, translate_local

def git_pull():
    r = subprocess.run(["git", "pull", "origin", "master"], cwd=str(PROJECT), capture_output=True, text=True)
    print(f"Git pull: {r.stdout.strip()[:100]}")
    return r.returncode == 0

def _clean_date(e):
    """脏日期过滤：解析出日期后，明显未来(>2天)或早于2020的条目丢弃；解析失败保守保留"""
    pa = str(e.get('published_at', ''))
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', pa)
    if not m:
        return True
    try:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return True
    return not (d > datetime.now() + timedelta(days=2) or d.year < 2020)


def _norm_cat(raw):
    """分类归一化：复合键取主类、中文键映射标准键、非法值落 other"""
    raw = str(raw or 'other').strip().lower()
    if '/' in raw:
        raw = raw.split('/')[0]
    canon = {
        '国际政治': 'geopolitics', '地缘政治': 'geopolitics',
        '金融市场': 'finance', '金融': 'finance',
        '能源安全': 'energy', '能源': 'energy',
        '宏观经济': 'macro', '宏观': 'macro',
        '社会': 'social', '就业': 'social',
        '贸易': 'trade', '科技': 'tech', '技术': 'tech',
        '东亚': 'east_asia', '中国': 'east_asia',
    }
    raw = canon.get(raw, raw)
    return raw if re.match(r'^[a-z_]{2,20}$', raw) else 'other'


def rebuild_data():
    """从所有 intel jsonl 重建 dashboard_data.json"""
    # 只读 intel_2*.jsonl（按日期命名的最终文件），排除 intel_raw_*/intel_final_*
    intel_files = sorted(glob.glob(str(BASE / "intel_2*.jsonl")))
    all_intel = []
    for f in intel_files:
        with open(f, 'r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    all_intel.append(json.loads(line))
                except:
                    pass

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
        print(f"Cleaned {dirty} dirty-date entries (future/older than 2020)")

    # 2026-08-30 修复: 旧排序按 relevance 降序取 Top200, 但新条目无 relevance 字段(=0)
    # 全部被挤出 Top200, 仪表盘永远看不到新信息。改为发布时间降序(新->旧), 同时间按相关度
    def _parse_dt(s):
        s = str(s or '')
        m = re.match(r'(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})', s)
        if m:
            return m.group(1) + ' ' + m.group(2)
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(s).strftime('%Y-%m-%d %H:%M')
        except Exception:
            return '1970-01-01 00:00'
    unique.sort(key=lambda x: (_parse_dt(x.get('published_at')), x.get('relevance', 0)), reverse=True)

    hyp_file = BASE / "hypotheses" / "active_hypotheses.json"
    hyps = []
    if hyp_file.exists():
        with open(hyp_file, 'r', encoding='utf-8') as f:
            hyps = json.load(f)

    cat_stats = {}
    for i in unique:
        cat = _norm_cat(i.get('category_cn') or i.get('category') or 'other')
        cat_stats[cat] = cat_stats.get(cat, 0) + 1
        # 归一化分类写回条目（新条目缺 category_cn, JS 端筛选按该字段过滤）
        i['category_cn'] = cat

    output = {
        "generated_at": datetime.now().isoformat(),
        "intelligence": unique[:200],
        "full_intelligence": unique,
        "intel_count": len(unique),
        "hypotheses": hyps,
        "macro": {},
        "category_stats": cat_stats,
    }
    with open(BASE / "dashboard_data.json", 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    cn_count = sum(1 for i in unique if i.get('cn_title'))
    print(f"Data: {len(unique)} intel, {len(hyps)} hyps, {cn_count} CN-titled")
    return len(unique)

def gen_html():
    """运行 gen_dashboard.py + fix_dashboard.py"""
    r1 = subprocess.run([sys.executable, str(PROJECT / "gen_dashboard.py")], cwd=str(PROJECT), capture_output=True, text=True)
    print(f"gen_dashboard: {r1.stdout.strip()[:100]}")
    r2 = subprocess.run([sys.executable, str(PROJECT / "fix_dashboard.py")], cwd=str(PROJECT), capture_output=True, text=True)
    print(f"fix_dashboard: OK")
    return r1.returncode == 0

def fetch_macro():
    """拉取宏观指标（汇率/利率/GDP等）→ data/macro_indicators.json"""
    r = subprocess.run(
        [sys.executable, str(PROJECT / "tools" / "fetch_macro_indicators.py")],
        cwd=str(PROJECT), capture_output=True, text=True
    )
    if r.stdout:
        print(f"macro: {r.stdout.strip()[:200]}")
    if r.returncode != 0 and r.stderr:
        print(f"macro stderr: {r.stderr.strip()[:200]}")
    return r.returncode == 0


def fetch_unemployment_history():
    """拉取 NBS 分年龄组失业率历史月度序列 → data/cn_unemployment_history.json
    NBS 每月19日发布上月数据,财新20日左右转载。节流策略:同月内只跑一次。
    """
    hist_file = BASE / "cn_unemployment_history.json"
    # 月度节流: 上次成功抓取是当前月则跳过
    if hist_file.exists():
        try:
            prev = json.loads(hist_file.read_text(encoding="utf-8"))
            last_upd = prev.get("updated_at", "")
            if last_upd:
                last_month = last_upd[:7]  # YYYY-MM
                if last_month == datetime.now().strftime("%Y-%m"):
                    # 同月, 但允许在每月20日后重抓(NBS 通常19日发布, 保险起见 21+)
                    day = datetime.now().day
                    if day < 21:
                        print(f"unrate-history: skip (already fetched {last_month}, day {day}<21)")
                        return True
        except Exception:
            pass
    r = subprocess.run(
        [sys.executable, str(PROJECT / "tools" / "fetch_macro_indicators.py"), "--history"],
        cwd=str(PROJECT), capture_output=True, text=True
    )
    if r.stdout:
        print(f"unrate-history: {r.stdout.strip()[:300]}")
    if r.returncode != 0 and r.stderr:
        print(f"unrate-history stderr: {r.stderr.strip()[:200]}")
    return r.returncode == 0


if __name__ == "__main__":
    with run_logging():
        print(f"\n=== Refresh at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
        git_pull()
        sync_repo_intel()
        translate_local()
        count = rebuild_data()
        fetch_macro()
        fetch_unemployment_history()
        gen_html()
        print(f"=== Done: {count} intel ===")
