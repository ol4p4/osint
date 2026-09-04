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
        for line in Path(f).read_text(encoding='utf-8', errors='replace').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                all_intel.append(json.loads(line))
            except Exception:
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
        hyps = json.loads(hyp_file.read_text(encoding='utf-8'))

    cat_stats = {}
    for i in unique:
        cat = _norm_cat(i.get('category_cn') or i.get('category') or 'other')
        cat_stats[cat] = cat_stats.get(cat, 0) + 1
        # 归一化分类写回条目（新条目缺 category_cn, JS 端筛选按该字段过滤）
        i['category_cn'] = cat

    # 源配额: 避免 Yonhap/CNBC 这种 hourly 高频源挤占 top 200 名额
    # 每个 source 最多 15 条, 然后按时间再选前 200
    PER_SOURCE_CAP = 15
    by_src = {}
    for it in unique:
        src = it.get('source_name') or '_unknown'
        by_src.setdefault(src, []).append(it)
    # 每个源截到 PER_SOURCE_CAP
    capped = []
    for src, lst in by_src.items():
        capped.extend(lst[:PER_SOURCE_CAP])
    # 重新按时间排
    capped.sort(key=lambda x: (_parse_dt(x.get('published_at')), x.get('relevance', 0)), reverse=True)
    top200 = capped[:200]

    output = {
        "generated_at": datetime.now().isoformat(),
        "intelligence": top200,
        "full_intelligence": unique,
        "intel_count": len(unique),
        "hypotheses": hyps,
        "macro": {},
        "category_stats": cat_stats,
    }
    (BASE / "dashboard_data.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8'
    )

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


def ensure_rsshub():
    """探测本地 RSSHub (localhost:1200)。2026-09-04 去掉 docker start/run 逻辑:
    本地不再拉起容器 (用户决策)——路由源走公共镜像兜底链 (slarker/rssforever),
    金十/新浪走 direct 直连, 国际源本就 scope:ci 由 CI 采集。失败不阻塞 refresh。
    """
    try:
        import urllib.request, socket
        socket.setdefaulttimeout(3)
        try:
            urllib.request.urlopen("http://localhost:1200/", timeout=3)
            return  # healthy
        except Exception:
            pass
        print("rsshub: 本机无容器, 路由源将走公共镜像兜底 (slarker/rssforever); 金十/新浪走直连")
    except Exception as e:
        print(f"rsshub: ensure failed: {e}")


def fetch_now():
    """本地 RSSHub 24h 全量拉取,append 到今日 jsonl。
    替代 CI 端 9 条金十的限流,保证本地有完整时间线。
    """
    r = subprocess.run(
        [sys.executable, str(PROJECT / "tools" / "fetch_now.py")],
        cwd=str(PROJECT), capture_output=True, text=True, timeout=300,
    )
    if r.stdout:
        # 打印关键行
        for line in r.stdout.splitlines():
            if any(k in line for k in ("[fetch_now]", "Fetched", "appended")):
                print(f"fetch_now: {line.strip()}")
    if r.returncode != 0 and r.stderr:
        print(f"fetch_now stderr: {r.stderr.strip()[:200]}")


def translate_now():
    """本地 OpenCode Zen 翻译最新 30 条未翻译条目。
    走 mimo-v2.5-free + nemotron 降级链, 6 分钟超时。
    替代依赖 CI 翻译 (CI 50 条/4h 跟不上本地 fetch_now 200+ 条/24h)。
    """
    r = subprocess.run(
        [sys.executable, str(PROJECT / "tools" / "translate_local.py")],
        cwd=str(PROJECT), capture_output=True, text=True, timeout=420,
    )
    if r.stdout:
        for line in r.stdout.splitlines():
            if any(k in line for k in ("[translate_local]", "translated", "candidates")):
                print(f"translate_now: {line.strip()}")
    if r.returncode != 0 and r.stderr:
        print(f"translate_now stderr: {r.stderr.strip()[:200]}")


if __name__ == "__main__":
    with run_logging():
        print(f"\n=== Refresh at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
        git_pull()
        sync_repo_intel()
        translate_local()
        ensure_rsshub()  # 保 RSSHub 健康(8h 滞后根因修复)
        fetch_now()       # 24h 全量本地拉(绕开 CI 9 条限流)
        translate_now()   # 本地 OpenCode Zen 翻译 (替代 CI 翻译吞吐瓶颈)
        count = rebuild_data()
        fetch_macro()
        fetch_unemployment_history()
        gen_html()
        print(f"=== Done: {count} intel ===")
