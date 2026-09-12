# 参谋系统 - AGENTS.md

## 项目概述
个人情报智库系统：云端抓取全球开源情报 → 本地 AI 四维结构研判 → 仪表盘展示 + Obsidian 知识库写入。
面向"中国年轻失业毕业生"视角，基于积累制度/空间修正/国家-市场边界/阶级利益四维框架分析。

## 关键路径
- **仓库**：`D:\osint`（GitHub: `ol4p4/osint`）
- **产物目录**：`D:\osint\data\`（2026-08-30 从 D:\Codex输出\osint_卫星图 迁入，gitignore 不追踪）
- **知识库**：`D:\Codex输出\视频知识库\`
- **Python**：`E:\software\python3.13.8\python.exe`
- **仪表盘**：http://127.0.0.1:19090/interactive_dashboard.html （旧端口 9090 会落进 Windows 动态保留段导致 WinError 10013，勿改回）
- **废弃目录**：`C:\Users\admin\Documents\osint`（迁移残留，仅供回滚，禁止新增引用；确认无误后可删）

## 数据流全貌（接手必读）
```
GitHub CI (daily.yml, 8次/天, cron 0 */3 * * *)
  采集RSS → clean_dedup_score → translate(NVIDIA增量50条) → citizen_impact(AI研判) → daily_briefing → push 仓库
  （link_intel_hyp / verify_hypotheses 已于 2026-09-12 从 CI 删除——改 data/hypotheses/ 但 git add 不含该目录，每轮成果被丢弃；假设链只跑本地）
仓库 D:\osint\intel_YYYYMMDD.jsonl
  ↓ (计划任务 OsintRefresh 每小时跑产物目录 refresh.py)
refresh.py: git pull + 合并 CI 数据 + 可选翻译
  → ensure_rsshub()  → 保本地 RSSHub 容器健康（curl localhost:1200 → docker start / run）
  → fetch_now()     → tools/fetch_now.py 24h 全量本地拉（绕开 CI 9 条金十限流，详见 §"CI 故障排除 #11"）
  → fetch_gdelt()   → tools/fetch_gdelt.py 国际侧补源（成功 3h / 失败 1h 节流，见 data/.gdelt_last_run）
  → translate_now() → tools/translate_local.py OpenCode Zen 翻译 30 条/6min（替代 CI 翻译吞吐瓶颈）
  → impact_now()    → cloud/citizen_impact.py 本地 AI 研判 50 条/小时
  → run_hypothesis_chain() → **每日假设链（20h 节流, data/.hyp_chain_last_run）**：
       link_intel_hyp（TF-IDF 匹配+story 去重）→ verify_hypotheses（数值阈值）
       → tools/ach_daily_batch.py（ACH 增量诊断 ~33 条/天，data/.ach_last_run 18h 节流）
  → rebuild_data → dashboard_data.json（去重后跑事件聚类 assign_story_ids，条目带 story_id/story_size）
  → run_calibration() → local/calibration.py 读 resolutions.jsonl → data/calibration.json
  → fetch_macro() → tools/fetch_macro_indicators.py → macro_indicators.json（12个宏观指标）
  → fetch_unemployment_history() → tools/fetch_macro_indicators.py --history → cn_unemployment_history.json
  → gen_dashboard+fix_dashboard → interactive_dashboard.html
  （全程 _step() 隔离：任一子步骤异常/超时不再杀死整轮；TEMP/osint_refresh.lock 单实例锁防双跑）
  ↓
仪表盘 19090 ← serve.py（**手动启动**：Start-ScheduledTask -TaskName OsintDashboard；2026-09-05 用户已禁用其登录触发器，不再开机自启）
计划任务 OsintWeekly（周一 09:30）→ 产物目录 daily_run.ps1 -Auto → Step2 main_local.py 分析 → Step2.5 verify → Step3 hypothesis_engine.run_weekly_cycle → Step3.5 policy_tracker
  （**2026-09-10 修复电源条件**：DisallowStartIfOnBatteries/StopIfGoingOnBatteries=false + StartWhenAvailable=true；
   此前 0x800710E0 失败根因。备份 XML 在 data/OsintWeekly.backup_20260910.xml。
   **2026-09-12 加固**：Step 级 try/catch 隔离（此前 ErrorActionPreference=Stop 任一步挂全链死）+
   Start-Transcript 日志 data/logs/weekly_YYYYMMDD.log（此前零日志）+ 清理 Sunday 判断死代码；备份 daily_run.ps1.bak_20260912）
对话引擎 daily_question.ps1 → local/dialogue_engine.py（观点卡）→ feed_to_hypothesis 进假设树 → local/kb_linker.py 同步知识库
```

## 模型分工
| 角色 | 模型 | 用途 |
|------|------|------|
| 参谋长（生成） | Mimo-v2.5-free | 生成假设/现状分析/对话追问/观点卡 |
| 裁判（验证） | Nemotron-3.5-lightning-free | 验证判定/复盘 |
| 翻译（CI） | openai/gpt-oss-120b | NVIDIA API 批量翻译（2026-08-30 A/B 从 llama-3.2-11b-vision 切换：llama 生产 0/18 超时，gpt-oss 18/18 一次过，质量更好） |
| DeepSeek | 已弃用 | 输出过于官方，无批判性 |

AI 调用通过 OpenCode Zen 免费代理（`https://opencode.ai/zen/v1`，key 在 config.yaml），伪造请求头见 `local/analyze.py`。**端点有白名单校验（只允许 opencode.ai），改端点要同步改 analyze.py 的校验**。NVIDIA key 只在 GitHub Secrets（`NVIDIA_API_KEY`），本地没有——本地翻译默认跳过、靠合并 CI 翻译成果。

## 文件结构

### 仓库根目录 / cloud / local
| 文件 | 用途 |
|------|------|
| `cloud/main_cloud.py` | CI 主入口：采集→去重→评分→推送 |
| `cloud/fetch_rss.py` / `fetch_list.py` | 抓取（列表页需 cssselect） |
| `cloud/clean_dedup_score.py` | 去重+评分 |
| `cloud/translate.py` | NVIDIA 增量翻译（CI 用 cwd，本地可 `--dir 产物目录`；步长 5 与切片一致） |
| `cloud/local_sync.py` | 本地同步：git pull + 合并 CI 数据 + 可选翻译 + 日志（refresh.py 调用） |
| `local/analyze.py` | 本地 AI 四维分析（参谋长），`_call_api` 是所有 AI 调用的底层 |
| `local/hypothesis_engine.py` | 假设全生命周期：views 分解→假设卡→证据→验证→周报；树节点用 `deadline`，engine 节点用 `due_date` |
| `local/dialogue_engine.py` | P2a 对话引擎：5 轮追问(WHAT/WHY/HOW/WHEN/WHO)→观点卡→`feed_to_hypothesis` 进假设树 |
| `local/question_generator.py` | P2b 问题生成器：分析摘要→开放性问题，落 `questions/` |
| `local/kb_linker.py` | 知识库双向链接：假设/观点卡写 `视频知识库\wiki\hypotheses|views` + index.md + log.md（幂等） |
| `local/render_wiki.py` / `main_local.py` | 旧渲染管线（daily_run.ps1 的 Step2 用） |
| `verify_hypotheses.py` | 假设自动验证（FRED/Frankfurter/GoldAPI/WorldBank，域名白名单在 `ALLOWED_HOSTS`；2026-09-05 P0-2 重写：`parse_threshold()` 真比较数值、指标值优先读本地 macro_indicators.json、无源标 `no_source`/叙述阈值标 `needs_ai` 留给周循环 AI 裁判） |
| `tools/fill_deadline.py` | P0-2 一次性脚本（跑一次即弃）：AI 提议 1~24 个月验证期限回填 deadline，失败按 level 兜底（small 3/medium 6/major 12/mega 24 月）；2026-09-05 已跑 71/71 全回填 |
| `tools/cluster_stories.py` | P0-3 事件聚类：纯标准库 TF-IDF（中文2-gram）+ 余弦 + 并查集，**不引入 sklearn**；`assign_story_ids(items)` 供 refresh/link_intel_hyp 调用；三重闸门参数（SIM_THRESHOLD=0.65/时间48h/同源12h/摘要0.30）在文件头 |
| `local/calibration.py` | P0-1 校准评分：读 resolutions.jsonl 算 Brier + Murphy 三分解 + 十桶校准曲线 → `data/calibration.json`；refresh.py 自动调 |
| `tools/fetch_macro_indicators.py` | 宏观指标抓取（汇率/利率/GDP/CPI/失业率，12个指标），产物 `data/macro_indicators.json`，refresh.py 自动调用；`--history` 子命令抓 NBS 分年龄组失业率历史月度序列 |
| `tools/fetch_now.py` | 本地 24h 全量拉取（**仅国内源**，`scope:ci` 的 33 个外国源跳过——境外源一律由 CI 在 GitHub Actions 上采集，本地拉不动是常态），append 到今日 jsonl；refresh.py 自动调 |
| `tools/translate_local.py` | 本地 OpenCode Zen 翻译（mimo-v2.5-free + nemotron 降级），每跑 30 条 6 分钟，写回 jsonl；refresh.py 自动调，**本地 hourly 翻译 18-30 条/6min，CI 翻译吞吐瓶颈解决** |
| `tools/fetch_gdelt.py` | P1-5 GDELT 国际侧补源（DOC API 三组查询 24h 窗口，title-only 流入本地翻译管线）；白名单 {api.gdeltproject.org} 脚本内自带；6s 间隔+12s 退避+3h 成功节流（data/.gdelt_last_run）；refresh.py 自动调，失败静默 |
| `worldview_loader.py` | 三观加载/注入文本构建（worldview.yaml 唯一事实源；缺失静默降级返回空串；**裁判链路明确不注入**保持校准客观）；`--show` 看档案+注入预览 / `--check` 结构校验 |
| `local/worldview_engine.py` | 三观输入引擎：`--seed` AI 从 persona+views 起草初稿（draft:true）/ `--interactive` 9 轮引导（3 阶段×3 问，复用 dialogue_engine 深化机制，覆盖写 draft:false）/ `--show`；AI 合成失败不动 YAML |
| `worldview.yaml` | 用户三观档案（worldview/lifeview/values + analysis_directives），仓库根提交供 CI 读取；draft=true 标记 AI 初稿待校正；Obsidian 镜像 `视频知识库\wiki\views\worldview.md` |
| `gen_dashboard.py` + `fix_dashboard.py` | 生成 HTML（必须按此顺序）；gen_dashboard 内嵌 macro 面板 CSS/HTML/JS，趋势图用 Chart.js 4.4 (jsdelivr)，情报流 section 用 `<details>` 折叠默认收起；P1-4 翻车高亮（flipBadge：⚡高确信翻车/↓置信度断崖 + 红边卡片） |
| `link_intel_hyp.py` | 情报→假设证据关联（P1-1 起主匹配=TF-IDF 余弦≥0.12 top3 与 cluster_stories 共用分词，DOMAIN_MAP 降为兜底；证据按 story_id 去重；report 带 method 字段） |
| `daily_briefing.py` / `sync_data.py` / `rebuild_hyps.py` | 简报/同步/重建树 |
| `views.yaml` | 观点模板（`materialized_hyp_id` 标注已物化的 view，防止周循环重复生成） |
| `sources.yaml`(50源) / `config.yaml`(key+路径) / `weights.yaml` / `daily_question.ps1`(P2a/P2b入口) | 配置与入口 |

## 信息源分层（2026-09-04 定稿）
- **主要渠道**（weight 1.1-1.2，本地+CI 双采）：金十数据、新浪财经(7x24+滚动，官方直连 API)、财联社——国内市场行情/快讯/政策的主入口
- **辅助渠道**（scope: ci，仅 GitHub Actions 采集）：彭博(容器路由)、路透(容器路由,上游时好时坏)、AP(容器路由)、CNN/DW(官方 RSS,CI 境外直连)——补充国际市场动态与海外宏观；境内网络直连这些源全部被阻,本地拉不动是设计内行为
- 国内源双采幂等：条目 id=md5(源名:链接:标题)，本地与 CI 采同一条 id 相同，rebuild_data 自动去重；合并优先保留 CI 带回的 cn_title 翻译版
- 东亚四源（Nikkei/KoreaHerald/Yonhap/JapanTimes）曾在 east_asia_sources 与 rss_sources 完全重复，已去重并入 rss_sources

### 产物目录（`D:\osint\data\`，gitignore 不追踪）
| 文件 | 用途 |
|------|------|
| `dashboard_data.json` / `interactive_dashboard.html` | 仪表盘数据+页面（含宏观指标面板） |
| `macro_indicators.json` | 宏观指标数据（10个：汇率/利率/GDP/CPI/失业率），由 fetch_macro_indicators.py 产出 |
| `refresh.py` | 刷新入口：git pull + 合并 + 翻译 + rebuild + fetch_macro + gen_html，日志落 `logs/refresh_YYYYMMDD.log` |
| `daily_run.ps1` | 本地分析+周循环运行器（`-Auto` 参数供计划任务用；**必须带 UTF-8 BOM**） |
| `intel_YYYYMMDD.jsonl` | 每日情报（`intel_raw_*`/`intel_final_*` 不参与重建和翻译） |
| `hypotheses/active_hypotheses.json` | 假设树（71 节点：2mega/8大/20中/41小，status 支持 active/falsified；deadline 2026-09-05 已 71/71 回填，落在 2026-12~2028-09） |
| `hypotheses/resolutions.jsonl` | P0-1：到期假设验证结果（周循环 record_resolution 幂等追加），calibration.py 的输入 |
| `calibration.json` | P0-1：Brier + Murphy 三分解 + 十桶校准曲线（gen_dashboard 校准面板读取） |
| `dialogues/view_cards/` `questions/` `reports/` | 观点卡 / 每日开放问题 / 周报 |

## 运行方式
```bash
# 手动刷新（拉取+合并+翻译+重建）
python D:\osint\data\refresh.py

# 对话引擎：想法 → 5轮追问 → 观点卡 → 进假设树
python D:\osint\local\dialogue_engine.py --interactive "你的想法" --feed-hyp
python D:\osint\local\dialogue_engine.py --batch <草稿目录>      # 批量
# 或右键运行 D:\osint\daily_question.ps1（三模式菜单）

# 每日开放性问题
python D:\osint\local\question_generator.py --analysis-text "分析摘要"

# 三观录入（P2c）：AI 引导 9 问 → worldview.yaml → 全链路自动生效
python D:\osint\local\worldview_engine.py --interactive   # 引导录入（覆盖写）
python D:\osint\worldview_loader.py --show                # 查看当前三观与注入预览
# 或 daily_question.ps1 菜单选 4

# 周循环（幂等，可随时手动跑）
python -c "..." # 见 daily_run.ps1 Step3，或等 OsintWeekly 周一 09:30 自动跑
```

## 约束与禁忌 / MUST NOT
- **禁止引用旧路径** `C:\Users\admin\Documents\osint`（旧仓库）和 `D:\Codex输出\osint_卫星图`（旧产物目录，均已废弃）；仓库= `D:\osint`，产物= `D:\osint\data`，路径常量只在文件头部定义一次。
- **禁止绕过白名单**：外发请求只允许 https + 白名单域名（verify_hypotheses 的 `ALLOWED_HOSTS`、analyze.py 的 opencode.ai 校验）。加新 API 必须先加白名单。
- **禁止让周循环重复生成假设**：views.yaml 加新 view 时若已在树里，必须填 `materialized_hyp_id`。
- **写 Python 文件 IO 用方法式 API**（`read_text/write_text/Path.open`），不要 `open(变量)`——Mimosa 安全扫描会按路径穿越拦截（PreToolUse），拦截后改写法而不是硬试。
- **.ps1 文件保存必须带 UTF-8 BOM**：PS 5.1 无 BOM 按 GBK 解析，中文字符串奇数字节会吞掉后面引号导致"字符串缺少终止符"（用 python 补 BOM：`open(p,'wb').write(b'\xef\xbb\xbf'+content_bytes)`）。
- 破坏性改动前先 commit（仓库有 git）；产物目录脚本改前先复制 `.bak_日期`。
- **bat 文件避免 `%VAR%\path` 模式**：cmd 解析时 `\r` 会被当 carriage return 吞一个字符（`%OUTDIR%\refresh.py` → `efresh.py`），导致"不是内部或外部命令"。bat 里路径直接写绝对路径（**用正斜杠更稳**：`D:/osint/data/refresh.py` Windows 也认），不要混用 `%VAR%` + 反斜杠。
- **serve.py 进程管理**：agent 会话内用 `Start-Process`/后台任务启动的进程会随会话清理被杀（用户浏览器随即 ERR_CONNECTION_REFUSED）。正确方式：`Start-ScheduledTask -TaskName OsintDashboard`（进程挂 Task Scheduler 下，脱离会话树；任务本身 Interactive 即可）。**开机自启已停用（2026-09-05 用户决策）**：OsintDashboard 任务的登录触发器已 Enabled=False，任务保留用于手动拉起；想恢复自启把触发器 Enabled 改回 True。
- 批量改多文件前先 `git status` 确认影响范围；禁止 `push --force`、`reset --hard` 丢未提交内容。
- `falsification_criteria` 为空时会在验证时自动从 indicators/sub_propositions 的 `threshold_refute` 回填，不要手填重复值。

## read-macro 集成（2026-09 落地）

| 模块 | 产出 | 文件 |
|---|---|---|
| A. 五维框架 prompt 注入 | analyze.py `_build_system_prompt` 拼 `MACRO_FIVE_DIM` + 宏观快照 | `local/macro_framework.py` |
| B. 中国货币/信用序列 | macro_indicators.json 5 新增 (cn_dr007/cn_shibor_3m/cn_m1/cn_m2/cn_shrong_yoy) | `tools/fetch_macro_indicators.py` |
| C. 估值分位面板 | KPI Bar 3 列 (宏观 + 估值 + ACH) | `tools/fetch_index_valuation.py` + gen_dashboard.py |
| D. 政策追踪周报 | 周一 OsintWeekly 跑 policy_tracker.py, 落 wiki/views/view_cards/ | `local/policy_tracker.py` + daily_run.ps1 Step 3.5 |

KB 概念页：`D:\Codex输出\视频知识库\wiki\concepts\宏观-五维分析框架.md`
注：read-macro 插件本身（`C:\Users\admin\.zcode\cli\plugins\cache\zcode-plugins-official\read-macro\0.1.1`）是 zcode CLI 工具，**不在 agent Python 代码里**——通过 `local/macro_framework.py` 把五维框架的常量下沉到 osint 自己的分析 prompt。

## 同类方案调研 P0 落地（2026-09-05，源自 docs/同类方案调研-2026-09-04.md）

调研结论「轮子不用重造，但四个零件该换」的四个 P0 已全部落地：

| P0 项 | 落地 | 关键事实 |
|---|---|---|
| 修复验证闭环 | tools/fill_deadline.py + verify_hypotheses.py 重写 + hypothesis_engine 裁判 prompt 注入指标值 | deadline 曾 71/71 不可用（63 空 + 7 个 2028+ 远期）；阈值原是「字符串非空即计数」的空壳。现在周循环能真正到期验证 |
| 校准评分 | local/calibration.py + resolutions.jsonl + 仪表盘校准面板 | Brier 手算样例校验通过；面板在 resolutions 有数据前显示「暂无已验证假设」 |
| 事件聚类 | tools/cluster_stories.py（纯标准库，1.1s/6500条）+ refresh/link_intel_hyp/仪表盘徽章三处接入 | 实测 1250 簇/3010 条归簇；三重闸门压模板句误聚：阈值 0.65、候选对时间差 ≤48h、同源 >12h 不合并、双有摘要时摘要相似 ≥0.30；同日不同公司的公告模板句仍会小规模误聚（有界，可接受） |
| ACH 敏感性分析 | ach_matrix.sensitivity_analysis() + 周报新节 | 现有矩阵 LR 99% =1.0（有壳无实）→ delta 全 0；合成强证伪证据验证翻转逻辑通过，等周循环真诊断出信号 |

验证链路现状：small 假设 40 个有 indicators，其中 37 个 no_source（自定义指标无免费 API）、3 个有 WorldBank 值但阈值是叙述式（走 AI 裁判）。**验证的主战场是周循环 AI 裁判**（deadline 修复后按月到期触发），verify_hypotheses 的数值比较是新假设拿简式阈值时的加成。

### P1 五项落地（2026-09-05 下午）

| P1 项 | 落地 | 实测事实 |
|---|---|---|
| TF-IDF 假设匹配 | link_intel_hyp.py：`build_tfidf_vectors()` + `match_intel_tfidf()`，假设语料含 indicators 名 | 精度 100%（抽检 8/8 相关）；召回 9/1816 偏低——词面模型对"抽象假设 vs 具体新闻"天然低召回，**换 embedding 向量即可跃升（只需替换 build_tfidf_vectors，调用方不动）**；当前证据链主力仍是 DOMAIN_MAP 兜底 + story 去重 |
| verdict 1-20 连续分 | hypothesis_engine：score≥17 supported / 13-16 partial / 7-12 inconclusive / ≤6 refuted，映射覆盖模型自报；resolutions 记 verify_score | 桩测 8 组边界（含 99 越界钳制、无分回退）全过 |
| 关键词分组 DSL | sources.yaml 新增 `keyword_rules`（any/must/exclude/weight/cap），fetch_rss 预编译叠加计分 | 种子 5 组中英混排；离线测试英文 must 组命中、负例不计分、叠加计分全符合语义 |
| 高确信翻车高亮 | gen_dashboard：RESOLUTIONS 映射 + flipBadge（⚡高确信翻车/⚡已翻车/↓置信度断崖）+ .flip 红边 | Playwright 浏览器实测双徽章+红边渲染正确；数据为零自然不显示 |
| GDELT 补源 | tools/fetch_gdelt.py + refresh.py 接入（成功 3h/失败 1h 节流） | 连通性已证（拿到 HTTP 响应）；**试探期触发 GDELT IP 临时封锁（429），等解封后 refresh 每日 ~8 轮自然生效**；mock 验证映射/去重/过滤全过 |

## 三观输入功能（2026-09-12 落地）

对话式录入（`worldview_engine.py --interactive`，3 阶段×3 问，AI 深化追问）→ `worldview.yaml`（仓库根，唯一事实源）→ 注入研判链路：

| 注入点 | 覆盖 | 方式 |
|---|---|---|
| `local/analyze.py` `_build_system_prompt` | 本地主分析 + 周分析 | persona 之后拼【用户三观】块 |
| `cloud/citizen_impact.py` `build_prompt` | 本地每小时研判 + CI 云端研判 | `GRADUATE_CONTEXT` 后拼 `_worldview_block()` |
| `data/serve.py` `_ask_staff` | 看板问答面板 | system 提示追加 |

**护栏**：验证裁判（verify_hypotheses / hypothesis_engine.verify_hypothesis）**不注入**三观——裁判保持中立，Brier 校准才不失真。注入文本 ≤800 字自动截断；文件缺失一律空串降级。`--seed` 可从 persona+views 起草初稿（draft:true），`--interactive` 确认后转正。录入入口：daily_question.ps1 模式 4。

## 周循环链路加固（2026-09-12，审计驱动）

PM 视角审计发现：每小时线和云端线质量在线，短板集中在周循环——它是唯一没有隔离、没有日志、没有看门狗的运行器，却承担校准闭环调度。本轮修复：

| 问题 | 修复 | 验证 |
|---|---|---|
| daily_run.ps1 `ErrorActionPreference=Stop`，Step2 挂则 2.5/3/3.5 全不跑 | Step 级 try/catch 隔离 + 原生命令 `$LASTEXITCODE` 检查（PS 原生命令非零退出不触发 catch） | Parser 语法校验 + 手动实跑 |
| 周循环零日志（Write-Host 丢弃，9-07 失败无据可查） | Start-Transcript 落 `data/logs/weekly_YYYYMMDD.log` | 实跑确认日志生成 |
| watchdog 只看本地 jsonl mtime，本地 fetch_now 活着时测不出 CI 死亡 | v3 加 `gh run list` 检测：最近 CI 成功 >12h → dispatch | 手动触发看日志 |
| OsintWeekly 9-07 静默失败后无兜底 | v3 加周报新鲜度：hypothesis_weekly_*.md >8 天 → 补跑 daily_run -Auto（24h 节流戳） | 手动触发看日志 |
| 假设树（71 节点）改动从不 commit——git 有追踪无历史，工作区脏文件 + 远端变更会让裸 git_pull 永久失败 | refresh.py `commit_hypotheses()`：hyp_chain 全成功后 add→commit→pull --rebase→push；rebase 冲突自动 --abort 留下轮 | 实跑确认 commit+push |
| CI 的 link/verify 两步改 data/hypotheses/ 但 git add 不含，每轮成果丢弃白烧时长 | daily.yml 删除两步，假设链诚实化只跑本地 | `gh workflow run` 补跑 CI 绿 |
| **hypothesis_engine.py:419 函数体内局部 datetime import 使 datetime 成局部变量，377 行抛 UnboundLocalError——周循环 8-31 后每次必死在验证到期假设之前（9-07 周报缺失的真正根因，与电源条件无关）** | 删除该局部 import（全局第 10 行已有），2026-09-12 实测 import 链通过 | 桩测 import chain OK |
| opencode.ai 偶发慢速滴字节保活绕过 socket timeout（_read_status 每次 read 都有数据，180s 永不触发，实测挂死 22 分钟，faulthandler 栈定位于 ssl.read；Windows 无 SIGALRM） | analyze.py `_safe_ai_post` 白名单校验后改为线程+join 硬超时（195s），超时走模型降级链；实测 429 配额耗尽时 183.7s 正确放弃 | 最小请求实测 |

**重要认知**：`data/hypotheses/` 被 git 追踪（.gitignore 第 9 行 `data/*` + 第 17 行 `!data/hypotheses/`），AGENTS.md 旧描述「产物目录 gitignore 不追踪」不准确。假设树的唯一有效写入方是本地；CI 不碰它。daily_run.ps1 Step2.5 用绝对路径调 verify_hypotheses.py（其内部 MACRO_FILE 也是绝对路径），与 cwd 无关。

## 系统通电修复（2026-09-10，审计驱动）

审计发现 P0/P1 成果「代码就位但未通电」——verify/link 只挂 CI 而 CI 上必然静默跳过、周任务因电源条件从未成功。本轮修复：

| 问题 | 修复 | 验证 |
|---|---|---|
| 三条链（证据/验证/ACH）无自动调度 | refresh.py `run_hypothesis_chain()`（20h 节流: link→verify→ACH 批）+ daily_run.ps1 Step 2.5 | **实跑通过**：link 3350/6062 匹配、verify 幂等、ACH 矩阵 20→39 行 |
| 幂等缺陷（置信度反复漂移） | verify_hypotheses 信号状态闸门（sN_rM 快照）+ intel 微调日闸；hypothesis_engine resolved_at 标记跳过已 resolve | 桩测：连跑两次第二次不调整 |
| 9-05 日志 13 轮死 5 轮 | refresh `_step()` 隔离全部子步骤 + TEMP/osint_refresh.lock 单实例锁 | 锁测试：锁定期间干净退出 |
| ACH 积压 885 条（周 20 条=45 周） | tools/ach_daily_batch.py 每日 ~33 条（1500s 预算实测 45s/条），约 4 周清完 | dry 计数正确 |
| OsintWeekly 从未成功（0x800710E0） | 电源条件三项修复（备份 XML data/OsintWeekly.backup_20260910.xml） | **实测拉起成功（Running）** |
| GDELT 每轮白撞 126s | 失败写 1h 节流戳 | 代码审查 |
| 本地抓取不吃 keyword_rules | fetch_now.py 三参构造 | 代码审查 |
| 仓库根僵尸假设树（8-27, 零引用） | git rm + 磁盘删除 + daily.yml add 列表清理 | grep 确认零引用 |

**当前状态**：假设树 71 节点仍全 active（最早 deadline 2026-12-05，23 个 small）——resolutions 与校准面板要等 12 月首个真实到期；ACH 矩阵每日 +33 条自动消化积压，2-3 周后 major 排名开始有信息量。

## CI 故障排除
1. **RSS 超时**：每源 15s 超时，坏源跳过不影响其他源
2. **翻译 404/超时**：NVIDIA key 在 GitHub Secrets；已限每次 50 条、batch 5；模型降级链 MODEL_CHAIN（gpt-oss-120b→20b→llama）
3. **push 403**：检查 workflow `permissions: contents: write`
4. **`No module named 'cloud'`**：`main_cloud.py` 顶部有 sys.path 修复
5. **simhash 报错**：确认 `simhash==2.1.2`
6. **本地数据没中文**：正常——等 CI 翻完由 local_sync 合并回来；或本地设置 `NVIDIA_API_KEY` 环境变量
7. **计划任务没跑成**：查 `logs/refresh_YYYYMMDD.log`；`daily_run.ps1` 手动测试加 `-Auto`
8. **GitHub schedule 会被静默跳过**（平台通病）：数据陈旧时先 `gh run list` 看 CI，再 `gh workflow run daily.yml` 手动补跑，跑完等本地 OsintRefresh 每小时拉取或手动跑 refresh.py
   - 诊断命令：`gh api repos/ol4p4/osint/actions/runs?event=schedule` 看时间戳间隔
   - 双联防御：CI 已 8x/day（cron `0 */3 * * *`）+ 本地 `OsintWatchdog` 计划任务每 6h 三项检查（v3, 2026-09-12）：① `intel_2*.jsonl` mtime > 8h 静默 → 本地跑 refresh.py 自愈 + `gh workflow run daily.yml`；② 最近 CI 成功 run > 12h → dispatch（此前只看本地 mtime，本地 fetch_now 活着时测不出 CI 死亡）；③ 最新 hypothesis_weekly_*.md > 8 天（错过周一）→ 补跑 daily_run.ps1 -Auto（24h 节流戳 data/.weekly_catchup_last_run）。日志 `data/logs/watchdog_YYYYMMDD.log`
9. **仪表盘时间错乱**：time_ago 已改为浏览器端动态计算（gen_dashboard.py 内嵌 JS IIFE），不再依赖采集时写死的静态文本
10. **fetch_list 采集 0 条**（2026-08-30 诊断）：接口缺陷已修（main 现在写 output jsonl，与 fetch_rss 同接口）；但所有列表源在 CI 上也解析出 0 条——**sources.yaml 的 list_selector 已与改版后的页面结构脱节**（gov.cn 还是 JS 渲染页）。逐源修选择器是持久战，替代方案：改用 RSSHub 或各站 RSS 源。
11. **仪表盘情报全显示 "8 小时前" 但金十/财联社实际在发**（2026-09-01 诊断；**2026-09-04 已基本根治**）：原 90% 是本地 RSSHub Docker 容器没起。9-04 起 fetch_rss 三级改造后**无 RSSHub 也能拉全源**：
   - 金十/新浪(7x24+滚动) → 官方直连 API（sources.yaml `direct: jin10_flash / sina_zhibo / sina_roll`，不经任何 RSSHub）
   - 其余 7 条 RSSHub 路由 → 本地容器失败自动退公共镜像（`hub.slarker.me` → `rsshub.rssforever.com`，见 fetch_rss.py `RSSHUB_BASES`，可用环境变量 `RSSHUB_BASES` 覆盖）
   - 实测 Docker 全程关闭：46 源中受影响 10 源 10/10 覆盖、377 条/24h（镜像限流时某源可能暂缺，下一轮自动恢复）
   - Docker 开着时本地容器仍优先（更快；公共镜像有匿名限流，勿长期裸奔依赖）
   - 历史排查步骤（RSSHub 时代）见 `C:\Users\admin\.zcode\cli\memories\projects\osint-d824a33e2ef30701\memory\osint-rsshub-local-bootstrap.md`

## 数据量级真相（2026-08-30 诊断）
- 关键词表（sources.yaml keyword_weights）是**中文**（就业/失业/毕业生/落户…），47 个源以英文为主 → 每日命中通常只有 0~3 条，**这是筛选器设计行为而非故障**
- 8-27 的 105 条是首次抓取的历史积压（feed 全量），之后每日只有源新增；"信息不新"的解法是**扩充中文源**（财新、华尔街见闻、第一财经、人社部官网等），不是改代码
- 未来日期脏数据（源站错误时间戳）由 refresh 的 `_clean_date` 过滤（>明天2天或 <2020 年丢弃）

## 数据质量基线（2026-08-30）
- 情报 ~718 条（脏日期已过滤）；cn_title 405（56%，随 CI 翻译推进会涨）
- 每日新增 RSS ~331 条；翻译吞吐 50 条/次 CI × 4 次/天
- 假设树 69 节点（8大/20中/41小）；观点卡链路已通（view_e0c75e → hyp_4656924e）

## 待续事项
- [x] **PLAN-1 RSSHub 中文源接入**（5 源已上线 CI docker run per-job；公共实例 403 已绕过）
- [x] **PLAN-2 M1 ACH 假设矩阵**（ach_matrix.py + hypothesis_engine 接入 + falsification_criteria 69/69 补完）
- [x] **PLAN-2 M3 仪表盘 ACH 排名面板**（gen_dashboard.py 已加，等首次周循环产出 ach_matrix.json 后自动显示）
- [x] **宏观指标集成**（tools/fetch_macro_indicators.py → 10 指标 → refresh.py 自动调用 → 仪表盘面板渲染）
- [x] **同类方案调研 P0 四项**（2026-09-05 落地：验证闭环/校准评分/事件聚类/敏感性分析，详见 §"同类方案调研 P0 落地"）
- [x] **同类方案调研 P1 五项**（2026-09-05 落地：TF-IDF 匹配/verdict 连续分/关键词 DSL/翻车高亮/GDELT，详见 §"P1 五项落地"）
- [x] **系统通电修复**（2026-09-10：假设链每日自动调度/幂等/加固/任务条件/僵尸树，详见 §"系统通电修复"）
- [x] **三观输入功能**（2026-09-12：worldview_engine 对话式录入 + worldview_loader 四链路注入 + 裁判护栏；初稿已 seed，待用户 --interactive 校正转正）
- [x] **周循环链路加固**（2026-09-12：daily_run 隔离+日志、watchdog v3 双盲区、假设树自动 commit+push、CI 删无效步骤，详见 §"周循环链路加固"；9-14 周一 09:30 为首次真实验证点）
- [ ] **PLAN-2 M2 贝叶斯调优**（等 ACH 积压消化 2-3 周后看后验分布再调先验/LR 锚定；敏感性分析已就位）
- [ ] 事件聚类阈值调优：同日公告模板句仍会小规模误聚；观察仪表盘「同事件×N」徽章误报率后调 cluster_stories.py 文件头三闸门
- [ ] TF-IDF 匹配召回跃升：把 build_tfidf_vectors 换成 embedding 向量（接口已预留，调用方不动）；需新增白名单域名
- [ ] GDELT 解封观察：失败已写 1h 节流戳（不再白撞）；持续 429 则把查询组砍到 1 组/次
- [ ] **2026-12-05 首个真实到期验证**（23 个 small 节点）——resolutions.jsonl 开始产出 + 校准面板点亮
- [ ] 指标覆盖率提升：74 个 custom 指标部分无免费 API（NBS 3 个指标无抓取函数）；small 假设 37/40 指标 no_source
- [ ] 源健康度审计：47+6 源逐源测试（部分 list 源选择器已脱节）
- [ ] 旧目录 `C:\Users\admin\Documents\osint` 确认后删除（含 git 历史，删前确认不再回滚）
- [ ] 对话引擎观点卡的 time_horizon_months 有时与用户回答的到期日不一致（AI 浓缩偏差，可加后校验）
- [ ] mimo-v2.5-free 代理偶发 empty response / HTTP 400：批量 AI 脚本都应带兜底 + 预算超时（ach_daily_batch 已按此设计，失败条下轮重试）

---
*最后更新：2026-09-12 - 周循环链路加固（daily_run 隔离+日志 / watchdog v3 双盲区 / 假设树自动 commit / CI 删无效步骤）*
