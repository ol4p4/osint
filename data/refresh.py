r"""refresh.py - 拉取最新情报并重建仪表盘（薄壳入口）
由计划任务 OsintRefresh 每小时调用，也可手动运行。
同步/翻译/日志逻辑在仓库 D:\osint\cloud\local_sync.py（便于 git 管理与交接）。
"""
import subprocess, sys, json, glob, os, re, time
from pathlib import Path
from datetime import datetime, timedelta, timezone

# 2026-09-04 静默化: 计划任务 OsintRefresh 已改为 pythonw 运行(无控制台),
# 子进程若不加 CREATE_NO_WINDOW, 每个控制台子程序(如 git.exe)会新建可见窗口闪屏
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

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

    # P0-3 事件聚类（同类方案调研）：同一事件多家报道 → story_id/story_size，
    # 仪表盘折叠徽章 + link_intel_hyp 证据去重。失败不阻塞 rebuild。
    try:
        sys.path.insert(0, str(PROJECT / "tools"))
        from cluster_stories import assign_story_ids
        cl_stats = assign_story_ids(unique)
        print(f"cluster: {cl_stats}")
    except Exception as e:
        print(f"cluster failed (non-blocking): {e}")

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

    # 源配额 + 相关度准入 (2026-09-04 优化):
    # ① 每源最多 15 条, 但源内改按 (相关分降序, 时间降序) 选代表——
    #    原先纯按时间取前 15, 高频源(金十日数百条)的低分新快讯把高分条目无条件挤出窗口,
    #    weights.yaml 的画像加权在窗口准入上完全失效
    # ② 全局仍按时间倒序取 top200, 时序感知不变
    # ③ 未来时间戳(源站时区错误, <2 天)在排序键上钳制到当前, 防霸屏顶置
    #    (同源内 base_score 可比: 同一 source weight 相同)
    PER_SOURCE_CAP = 15
    now_key = datetime.now().strftime('%Y-%m-%d %H:%M')

    def _rank_time(it):
        return min(_parse_dt(it.get('published_at')), now_key)

    def _rel_score(it):
        try:
            return float(it.get('final_score') or 0)
        except (TypeError, ValueError):
            return 0.0

    # 2026-09-13 筛选修复②-a: 统一量纲——本地 fetch_now/GDELT 条目此前绕过评分层
    # (只有 base_score 无时间衰减), 与 CI 条目(final_score 含衰减)两套量纲混排且本地免衰减占优。
    # 全部条目按 CI 同款公式重算"当前衰减下"的 final_score(只改内存不回写 jsonl;
    # 日期解析用 _parse_dt 覆盖 RSS 邮件日期等全格式; 历史饱和分随 168h 窗口自然出清)。
    def _decay_now(it):
        try:
            pub = datetime.strptime(_parse_dt(it.get('published_at')), '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - pub).total_seconds() / 3600
        except Exception:
            return 0.1
        if age_h < 0 or age_h >= 168:   # 未来戳/超窗: 可疑, 最低分
            return 0.1
        return max(0.5 ** (age_h / 48.0), 0.1)

    # 2026-09-13 筛选修复②-a2: 历史条目去饱和。旧曲线(kw=min(sum,1.0))产的 base_score
    # 全挤在 1.0~1.3 同分带, 保底带内部会退化回时间序。keywords_hit 里存有命中词与
    # "规则:组名"——按当前词表恢复原始加权和 raw, 重套新曲线 raw/(raw+3), 并用
    # base_old/旧kw 反推源权重。词表已删除的词贡献 0(可接受漂移), 不必等 168h 出清。
    try:
        import yaml as _yaml
        _src = _yaml.safe_load((PROJECT / 'sources.yaml').read_text(encoding='utf-8')) or {}
        _flat = _src.get('keyword_weights') or {}
        _rules = _src.get('keyword_rules') or {}
        _n2 = 0
        for it in unique:
            hits = it.get('keywords_hit') or []
            if not hits:
                continue
            raw = 0.0
            for w in hits:
                if not isinstance(w, str):
                    continue
                if w.startswith('规则:'):
                    g = _rules.get(w[3:]) or {}
                    try:
                        raw += float(g.get('weight', 0.3))
                    except (TypeError, ValueError):
                        raw += 0.3
                else:
                    raw += float(_flat.get(w, 0))
            if raw <= 0:
                continue
            base_old = float(it.get('base_score') or 0)
            kw_old = min(raw, 1.0)
            weight = base_old / kw_old if kw_old > 0 else 1.0
            kw_new = raw / (raw + 3.0)
            if len(hits) >= 8:
                kw_new *= 0.6   # 摘要/联播类天然海量命中(联播要闻21条命中~20词), 边际信息低
            it['base_score'] = round(weight * kw_new, 3)
            _n2 += 1
        if _n2:
            print(f"desaturate: {_n2} legacy items re-curved")
    except Exception as e:
        print(f"desaturate failed (non-blocking): {e}")

    for it in unique:
        try:
            _base = float(it.get('base_score') or 0)
        except (TypeError, ValueError):
            _base = 0.0
        it['final_score'] = round(min(_base * _decay_now(it), 1.0), 3)

    # 2026-09-13 筛选修复②-b: 保底带改主题配额。实测: 加性打分下联播摘要/泛宏观深度文
    # 天然多词命中(去饱和后仍 0.85+), 单主题高价值条目(失业/养老金/核电, 多为 1~2 词命中
    # 0.53~0.67)在任何总分排序里都进不了前排——纯管道修复无法达成高价值进窗目标。
    # 故按 persona.md 宏观信号清单划主题配额: 每类议题保证窗口代表席。
    # 新鲜度=72h 硬门槛; 相关性=去衰减 base_score(标题匹配); 同事件 story 最多 2 条。
    RESERVE_TOPICS = [
        ("就业与落户", 12, ["失业", "毕业生", "落户", "户籍", "招工", "裁员", "招聘", "稳就业"]),
        ("社保与养老", 8, ["养老金", "社保", "延迟退休", "养老保险", "养老金替代率"]),
        ("能源与电力", 10, ["核电", "电网", "油价", "石油", "电力", "新能源"]),
        ("贸易与供应链", 6, ["关税", "出口管制", "实体清单", "贸易摩擦"]),
        ("房地产", 4, ["房地产", "楼市", "房贷", "房价"]),
    ]
    RESERVE_WINDOW_H = 72
    STORY_CAP = 2
    _cutoff = (datetime.now(timezone.utc) - timedelta(hours=RESERVE_WINDOW_H)).strftime('%Y-%m-%d %H:%M')

    def _base_score(it):
        try:
            return float(it.get('base_score') or 0)
        except (TypeError, ValueError):
            return 0.0

    def _title_text(it):
        # 保底带匹配用标题+中文摘要（与审计基线口径一致; dry 标题常不含主题词而摘要有）
        return (str(it.get('cn_title') or '') + ' ' + str(it.get('title') or '') + ' '
                + str(it.get('cn_summary') or ''))

    reserved = []
    reserved_ids = set()
    story_seen = {}
    _reserve_stat = []
    for _topic, _quota, _words in RESERVE_TOPICS:
        _taken = 0
        for it in sorted((x for x in unique if id(x) not in reserved_ids
                          and _rank_time(x) >= _cutoff and _base_score(x) > 0
                          and any(w in _title_text(x) for w in _words)),
                         key=lambda x: (_base_score(x), _rank_time(x)), reverse=True):
            if _taken >= _quota:
                break
            sid = it.get('story_id')
            if sid:
                if story_seen.get(sid, 0) >= STORY_CAP:
                    continue
                story_seen[sid] = story_seen.get(sid, 0) + 1
            reserved.append(it)
            reserved_ids.add(id(it))
            _taken += 1
        _reserve_stat.append(f"{_topic}:{_taken}")
    print("window: reserve band " + ",".join(_reserve_stat) + f" (total {len(reserved)})")

    # ② 剩余席位: 每源 15 条代表(排除保底带已占条目), 时间排序填满 200
    by_src = {}
    for it in unique:
        if id(it) in reserved_ids:
            continue
        src = it.get('source_name') or '_unknown'
        by_src.setdefault(src, []).append(it)
    capped = []
    for src, lst in by_src.items():
        lst.sort(key=lambda x: (_rel_score(x), _rank_time(x)), reverse=True)
        capped.extend(lst[:PER_SOURCE_CAP])
    capped.sort(key=lambda x: (_rank_time(x), _rel_score(x)), reverse=True)
    top200 = reserved + capped[:max(0, 200 - len(reserved))]
    top200.sort(key=lambda x: (_rank_time(x), _rel_score(x)), reverse=True)
    print(f"window: {sum(1 for x in top200 if _rel_score(x) > 0)}/200 scored, "
          f"reserve kept {len(reserved)}")

    output = {
        "generated_at": datetime.now().isoformat(),
        "intelligence": top200,
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
    r1 = subprocess.run([sys.executable, str(PROJECT / "gen_dashboard.py")], cwd=str(PROJECT), capture_output=True, text=True, creationflags=_NO_WINDOW)
    print(f"gen_dashboard: {r1.stdout.strip()[:100]}")
    r2 = subprocess.run([sys.executable, str(PROJECT / "fix_dashboard.py")], cwd=str(PROJECT), capture_output=True, text=True, creationflags=_NO_WINDOW)
    print(f"fix_dashboard: OK")
    return r1.returncode == 0

def fetch_macro():
    """拉取宏观指标（汇率/利率/GDP等）→ data/macro_indicators.json"""
    r = subprocess.run(
        [sys.executable, str(PROJECT / "tools" / "fetch_macro_indicators.py")],
        cwd=str(PROJECT), capture_output=True, text=True, creationflags=_NO_WINDOW
    )
    if r.stdout:
        print(f"macro: {r.stdout.strip()[:200]}")
    if r.returncode != 0 and r.stderr:
        print(f"macro stderr: {r.stderr.strip()[:200]}")
    return r.returncode == 0


def run_calibration():
    """P0-1 校准评分：读 resolutions.jsonl 算 Brier/Murphy → data/calibration.json。
    纯本地计算毫秒级；resolutions 由周循环验证到期假设时写入，平时跑只是刷新。"""
    r = subprocess.run(
        [sys.executable, str(PROJECT / "local" / "calibration.py")],
        cwd=str(PROJECT), capture_output=True, text=True, creationflags=_NO_WINDOW
    )
    if r.stdout:
        print(f"calibration: {r.stdout.strip()[:200]}")
    if r.returncode != 0 and r.stderr:
        print(f"calibration stderr: {r.stderr.strip()[:200]}")
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
        cwd=str(PROJECT), capture_output=True, text=True, creationflags=_NO_WINDOW
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
        creationflags=_NO_WINDOW,
    )
    if r.stdout:
        # 打印关键行
        for line in r.stdout.splitlines():
            if any(k in line for k in ("[fetch_now]", "Fetched", "appended")):
                print(f"fetch_now: {line.strip()}")
    if r.returncode != 0 and r.stderr:
        print(f"fetch_now stderr: {r.stderr.strip()[:200]}")


def fetch_gdelt():
    """P1-5 GDELT 国际侧补源（api.gdeltproject.org，白名单在脚本内自带）。
    3h 节流防撞 GDELT 限速；失败/被阻静默跳过不阻塞 refresh，国际侧本就由 CI 兜底。"""
    r = subprocess.run(
        [sys.executable, str(PROJECT / "tools" / "fetch_gdelt.py")],
        cwd=str(PROJECT), capture_output=True, text=True, timeout=180,
        creationflags=_NO_WINDOW,
    )
    if r.stdout:
        for line in r.stdout.splitlines():
            if any(k in line for k in ("[GDELT]", "append")):
                print(f"gdelt: {line.strip()}")
    if r.returncode != 0 and r.stderr:
        print(f"gdelt stderr: {r.stderr.strip()[:200]}")


def translate_now():
    """本地 OpenCode Zen 翻译未翻译条目 (mimo-v2.5-free + nemotron 降级链)。
    2026-09-04: 翻译挪出 CI 后本地承担全部翻译吞吐, 放宽到 100 条/900s;
    替代依赖 CI 翻译 (CI 50 条/4h 跟不上本地 fetch_now 200+ 条/24h)。
    """
    r = subprocess.run(
        [sys.executable, str(PROJECT / "tools" / "translate_local.py"),
         "--max", "100", "--budget", "900"],
        cwd=str(PROJECT), capture_output=True, text=True, timeout=960,
        creationflags=_NO_WINDOW,
    )
    if r.stdout:
        for line in r.stdout.splitlines():
            if any(k in line for k in ("[translate_local]", "translated", "candidates")):
                print(f"translate_now: {line.strip()}")
    if r.returncode != 0 and r.stderr:
        print(f"translate_now stderr: {r.stderr.strip()[:200]}")


def impact_now():
    """本地 AI 研判 (citizen_impact.py: 双层身份传导 + 四维诊断)。
    2026-09-04 新增: 此前研判只在 CI 跑 (4h/轮, 每轮 480s 预算实测产出 20-40 条,
    vs 日新增 ~360 条 → 覆盖仅 8%, 仪表盘"后面不再分析"的根因)。
    本地每小时跑 50 条/900s 预算, 日吞吐上限 ~1200 条, 可追平新增。
    失败不阻塞 refresh (次日轮续跑, 未研判条目无 impact_level 自然重试)。
    """
    r = subprocess.run(
        [sys.executable, str(PROJECT / "cloud" / "citizen_impact.py"),
         "--dir", str(BASE), "--max", "50", "--budget", "900"],
        cwd=str(PROJECT), capture_output=True, text=True, timeout=960,
        creationflags=_NO_WINDOW,
    )
    if r.stdout:
        for line in r.stdout.splitlines():
            if "[IMPACT]" in line:
                print(f"impact_now: {line.strip()}")
    if r.returncode != 0 and r.stderr:
        print(f"impact_now stderr: {r.stderr.strip()[:200]}")


def run_hypothesis_chain():
    """P0/P1 假设链每日通电（2026-09-10 新增）：
    link_intel_hyp（TF-IDF 匹配+story 去重证据）→ verify_hypotheses（数值阈值）
    → ach_daily_batch（ACH 增量诊断）。此前三者只挂 CI（CI 上 Windows 绝对路径
    必然静默跳过）→ 证据链与验证链事实上停摆。20h 节流，三个子进程各自独立失败。
    """
    state = BASE / ".hyp_chain_last_run"
    if state.exists():
        try:
            age_h = (time.time() - float(state.read_text(encoding="utf-8").strip())) / 3600
            if age_h < 20:
                print(f"hyp-chain: skip (上次 {age_h:.1f}h 前 < 20h)")
                return
        except Exception:
            pass
    steps = [
        ("link", [sys.executable, str(PROJECT / "link_intel_hyp.py")], 600, ("Linked", "[TFIDF]")),
        ("verify", [sys.executable, str(PROJECT / "verify_hypotheses.py")], 600, ("指标更新",)),
        ("ach-batch", [sys.executable, str(PROJECT / "tools" / "ach_daily_batch.py")], 1600, ("[ACH-BATCH]",)),
    ]
    ok = 0
    for name, cmd, tmo, keys in steps:
        try:
            r = subprocess.run(cmd, cwd=str(PROJECT), capture_output=True, text=True,
                               timeout=tmo, creationflags=_NO_WINDOW)
            if r.stdout:
                for line in r.stdout.splitlines():
                    if any(k in line for k in keys):
                        print(f"hyp-chain[{name}]: {line.strip()[:150]}")
            if r.returncode == 0:
                ok += 1
            else:
                print(f"hyp-chain[{name}] exit={r.returncode}: {(r.stderr or '')[:150]}")
        except subprocess.TimeoutExpired:
            print(f"hyp-chain[{name}]: timeout {tmo}s (跳过, 下轮继续)")
        except Exception as e:
            print(f"hyp-chain[{name}] failed: {str(e)[:150]}")
    if ok == len(steps):
        state.write_text(str(time.time()), encoding="utf-8")
        commit_hypotheses()
    else:
        print(f"hyp-chain: {ok}/{len(steps)} 成功, 不写节流戳, 下轮重试")


def commit_hypotheses():
    """假设树自动版本化（2026-09-12 新增）：hyp_chain 全成功后 commit+push
    data/hypotheses/ 变更。.gitignore 第 17 行 !data/hypotheses/ 有意追踪假设树,
    但此前改动从不提交 → 71 节点唯一资产无版本历史, 且工作区脏文件 + 远端变更
    会让裸 git pull（local_sync.git_pull 无 stash 容错）永久失败。
    本地是假设树的唯一写入方（CI 的 link/verify 步骤已删除）；rebase/push 失败
    静默跳过留下轮重试，不阻塞 refresh 主流程。"""
    files = ["data/hypotheses/active_hypotheses.json", "data/hypotheses/ach_matrix.json"]
    try:
        subprocess.run(["git", "add", *files], cwd=str(PROJECT), capture_output=True,
                       text=True, timeout=60, creationflags=_NO_WINDOW)
        # --quiet: 有 staged 变更返回 1, 无变更返回 0
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(PROJECT),
                              capture_output=True, timeout=60, creationflags=_NO_WINDOW)
        if diff.returncode == 0:
            print("hyp-commit: 无变更, 跳过")
            return
        msg = f"auto: hypothesis chain update {time.strftime('%Y-%m-%d')}"
        c = subprocess.run(["git", "commit", "-m", msg], cwd=str(PROJECT),
                           capture_output=True, text=True, timeout=120, creationflags=_NO_WINDOW)
        if c.returncode != 0:
            print(f"hyp-commit: commit 失败: {(c.stderr or '')[:150]}")
            return
        rb = subprocess.run(["git", "pull", "--rebase", "origin", "master"], cwd=str(PROJECT),
                            capture_output=True, text=True, timeout=180, creationflags=_NO_WINDOW)
        if rb.returncode != 0:
            # 冲突时回退 rebase 中间态, 保住本地 commit 留待下轮, 避免污染后续 git_pull
            subprocess.run(["git", "rebase", "--abort"], cwd=str(PROJECT),
                           capture_output=True, timeout=60, creationflags=_NO_WINDOW)
            print(f"hyp-commit: rebase 失败(留下轮): {(rb.stderr or rb.stdout or '')[:150]}")
            return
        ph = subprocess.run(["git", "push", "origin", "master"], cwd=str(PROJECT),
                            capture_output=True, text=True, timeout=180, creationflags=_NO_WINDOW)
        print("hyp-commit: pushed" if ph.returncode == 0
              else f"hyp-commit: push 失败(留下轮): {(ph.stderr or '')[:150]}")
    except Exception as e:
        print(f"hyp-commit: failed: {str(e)[:150]}")


def _step(fn, name):
    """子步骤隔离：任一步异常/超时不再杀死整轮 refresh（9-05 实测 13 轮死 5 轮）"""
    try:
        return fn()
    except subprocess.TimeoutExpired:
        print(f"[STEP] {name}: timeout, skip (下轮继续)")
    except Exception as e:
        print(f"[STEP] {name} failed: {str(e)[:200]}")
    return None


if __name__ == "__main__":
    # 单实例锁：与 OsintWatchdog 共用的 TEMP/osint_refresh.lock（lock 内 2h 视为在跑）
    import os as _os, tempfile as _tempfile
    _lock = Path(_tempfile.gettempdir()) / "osint_refresh.lock"
    if _lock.exists():
        try:
            _age_h = (time.time() - _lock.stat().st_mtime) / 3600
        except OSError:
            _age_h = 0
        if _age_h < 2:
            print(f"another refresh is running (lock age {_age_h:.1f}h), exit")
            sys.exit(0)
    _lock.write_text(str(_os.getpid()), encoding="utf-8")
    try:
        with run_logging():
            print(f"\n=== Refresh at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
            _step(git_pull, "git_pull")
            _step(sync_repo_intel, "sync_repo_intel")
            _step(translate_local, "translate_local")
            _step(ensure_rsshub, "ensure_rsshub")  # 保 RSSHub 健康(8h 滞后根因修复)
            _step(fetch_now, "fetch_now")       # 24h 全量本地拉(绕开 CI 9 条限流)
            _step(fetch_gdelt, "fetch_gdelt")   # P1-5 GDELT 国际侧补源(节流, 失败静默)
            _step(translate_now, "translate_now")   # 本地 OpenCode Zen 翻译
            _step(impact_now, "impact_now")      # 本地 AI 研判
            _step(run_hypothesis_chain, "hyp_chain")  # 每日假设链(link→verify→ACH)
            count = _step(rebuild_data, "rebuild_data")
            _step(run_calibration, "calibration")
            _step(fetch_macro, "fetch_macro")
            _step(fetch_unemployment_history, "unrate_history")
            _step(gen_html, "gen_html")
            print(f"=== Done: {count} intel ===")
    finally:
        try:
            _lock.unlink()
        except OSError:
            pass
