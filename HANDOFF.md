# osint 参谋系统 - 项目交接

## 项目结构

```
D:\osint\                        # git 仓库, GitHub: ol4p4/osint
├── AGENTS.md                    # 活文档 (90 行, 项目惯例, 必读)
├── config.yaml                  # 公共配置 (无 key, 在 git)
├── config.local.yaml            # 私有配置 (api_key, gitignore)
├── views.yaml                   # 6 个 view 模板 (周循环物化)
├── weights.yaml                 # 源权重
│
├── cloud\                       # CI 用 (GitHub Actions 跑)
│   ├── main_cloud.py            # CI 入口
│   ├── fetch_rss.py             # RSS 抓取
│   └── translate.py             # NVIDIA 翻译 (50 条/批)
│
├── local\                       # 本地用 (AI 分析 + 假设树)
│   ├── analyze.py               # MacroAnalyzer, 4 维框架
│   ├── macro_framework.py       # 五维框架常量 (read-macro 下沉)
│   ├── hypothesis_engine.py     # 假设生命周期 (decompose / verify / ACH)
│   ├── ach_matrix.py            # ACH 矩阵 (CIA Heuer)
│   ├── dialogue_engine.py       # 5 轮追问 → view_card
│   ├── policy_tracker.py        # 周政策追踪
│   ├── kb_linker.py             # 写 wiki vault
│   └── main_local.py            # 本地主入口
│
├── data\                        # 90% gitignore, 跑 refresh.py 重建
│   ├── refresh.py               # 主刷新入口 (OsintRefresh 计划任务调)
│   ├── serve.py                 # HTTP server (19090)
│   ├── daily_run.ps1            # 周一 OsintWeekly
│   ├── watchdog.ps1             # OsintWatchdog (6h 检查 CI)
│   ├── intel_YYYYMMDD.jsonl     # 每日累积 (gitignore)
│   ├── dashboard_data.json      # rebuild_data 产物 (gitignore)
│   ├── macro_indicators.json    # 17 指标 (gitignore)
│   ├── cn_unemployment_history.json
│   ├── index_valuation.json     # 估值快照
│   ├── hypotheses/              # active_hypotheses.json + ach_matrix.json (均入库)
│   ├── dialogues/view_cards/    # 观点卡目录
│   └── logs/                    # refresh_*.log + watchdog_*.log
│
├── tools\                       # 数据抓取工具
│   ├── fetch_macro_indicators.py # 17 宏观指标
│   ├── fetch_index_valuation.py  # 3 指数 PE 估值
│   ├── fetch_now.py             # 46 源 24h RSS (42 rss + 4 east_asia, 以 sources.yaml 实际为准)
│   ├── translate_local.py       # 本地 OpenCode Zen 翻译
│   └── find_surrogate.py        # 调试工具
│
├── gen_dashboard.py            # dashboard HTML 生成器
├── fix_dashboard.py            # dashboard 后处理
└── secrets_loader.py            # api_key 多源读取
```

**外部资源 (不在 git 仓库里, 单独打包)**:
```
D:\Codex输出\视频知识库\          # Obsidian KB vault, 44 个概念 .md
C:\Users\admin\.zcode\cli\plugins\cache\  # read-macro / firecrawl 插件
E:\software\python3.13.8\        # Python 解释器
```

---

## 数据流 (一句话)

CI 抓 RSS → git push → OsintRefresh 计划任务 git pull + 抓本地 RSS + 翻译 + 写 jsonl → gen_dashboard.py 生成 HTML → serve.py 端口 19090 serve → 浏览器看

---

## 已知问题

### 部署类
1. **data/* 默认 gitignore** - 假设树/数据文件不在 git, 新机器从 0 累积需 2 周达到 2400+ 条
2. **RSSHub 容器常被 Docker 重启杀掉** - refresh.py 有 `ensure_rsshub()` 自动 docker start/run 恢复
3. **OsintRefresh 计划任务偶发 0x800703EE 取消** - 1h 间隔有概率, 15min 间隔未再发生
4. **fetch_now 串行 49 源 2-3 分钟** - 并发改造在 backlog

### 安全类 (Mimosa 钩子)
5. **`open(变量)` / `urlopen` / `Request` 直连** 全部被拦 - 必须用 `Path.read_text/write_text` + `_get()` 白名单
6. **`.ps1` 文件保存必须带 UTF-8 BOM** - python 加: `open(p,'wb').write(b'\xef\xbb\xbf'+content_bytes)`
7. **`subprocess.run` 命令注入** 风险 - 别拼 shell 字符串, 用参数列表

### 数据类
8. **NBS 官方 API 100% 403** - 走 Trading Economics 镜像兜底, 但 TE 月级数据滞后 1 月
9. **multpl.com HTML 表格 JS 渲染** - urllib 拿不到, 走 firecrawl MCP 抓 markdown 再解析
10. **GuruFocus 免费层只给最近 14 月** - 历史百分位算 10y 实际只能拿 14 个点
11. **M1/M2 同比口径缺失** - TE 端只给"绝对值余额", 不是"同比" (用 cn_dr007 + cn_shibor_3m 方向判断货币-信用象限替代)
12. **WorldBank indicator code 部分无效** (如 `FM.LBL.BMNY.GD.ZG` 报 120 invalid value)

### 配置/资源类
13. **OpenCode Zen key 只能在 config.local.yaml 或环境变量** - config.yaml 留空 (曾因 git 历史泄露过 key 撤了)
14. **NVIDIA key 在 GitHub Secrets** - 本地没有, CI 翻译才用
15. **firecrawl Python SDK 需 API key** - agent 走 zcode MCP 走自带的 key, Python 代码不直接调
16. **Dashboard 上 point time_ago 在我浏览器 dynamic 算** - 不依赖 fetch_now 写入的静态文本

### 翻译类
17. **CI 翻译 50 条/批 6x/day 跟不上本地 fetch_now 增量** - 本地用 OpenCode Zen 兜底 (`tools/translate_local.py` 30 条/6 min)
18. **mimo-v2.5-free 高峰 503** - 已有降级链 nemotron-3.5-lightning-free

### 历史遗留
19. **C:\Users\admin\Documents\osint 旧目录** - 9-01 之前用过, 9-01 后迁到 D:\osint, 旧目录已废弃
20. **Plan v1 read-macro 集成** 9-02 落地 (4 模块, commit 804d9f4 / f9ee413 / a4aaa7f), 已写进 AGENTS.md
21. **Dashboard 9-03 重设计三连击** (commit ab4c967 / 17a7f1d / 5308230) - 估值曲线 / change_bp 派生 / dark mode toggle

---

## 运行态 (2026-09-04 自动化重建后)

**此前 OsintRefresh 计划任务从未存在过** (历史刷新全是手动跑), 已于 9-04 重建:

| 计划任务 | 触发 | 动作 | 权限 |
|---|---|---|---|
| `OsintRefresh` | 登录 + 每小时 (PT1H, 3650天) | `E:\software\python3.13.8\python.exe D:\osint\data\refresh.py` (cwd=data) | Interactive / Limited |
| `OsintDashboard` | 登录自启 (替代已删除的 Startup bat) | 同 python 跑 `serve.py` :19090 | Interactive / Limited |
| `OsintWatchdog` | 每 6h (原有) | `data/watchdog.ps1` v2: stale 时**先本地 refresh 自愈**再 dispatch CI, 日志按日轮转 `watchdog_YYYYMMDD.log`, TEMP 锁防并发 | Interactive / Limited |
| `OsintWeekly` | 周一 09:30 (原有) | `daily_run.ps1 -Auto` (⚠️ 至 9-04 从未运行过, 首跑建议手动验证) | Interactive / Limited |

注意:
- **解释器必须用 `E:\software\python3.13.8\python.exe`** (9-04 已补装 pyyaml 6.0.3; 链路只需 feedparser+yaml, 均已就位)
- CI 并没有停 (此前"3 天无 auto commit"是本地没 pull 的假象); CI 停更时 watchdog 本地自愈兜底
- RSSHub 依赖 Docker Desktop 运行; Docker 不在时 fetch_now 直接打源站, 本地 24h 拉取降级可用 (实测 46 源 207 条)
- 假设树 71 节点但 evidence 全空 / 0 falsified (link_intel_hyp 结果未写回), 验证闭环仍待接
- `cn_m1_yoy`/`cn_m2_yoy` 存的是余额绝对值而非同比, 下游勿按同比解读
- `refresh.py` 9-04 修复: 删除遮蔽 import 的本地 git_pull (改用 local_sync 带超时版); open() 改 Path 方法式 API
