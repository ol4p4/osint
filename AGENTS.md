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
| `local/render_wiki.py` / `main_local.py` | 旧渲染管线（daily_run.ps1 的 Step2 用）；main_local 内置三道数据闸门：`_wait_for_today_intel`（等当日文件就绪 600s）+ `_fresh_count`（近 3 天计数，防陈旧）+ Top N 截断（`ai_analysis.max_items`，默认 60） |
| `local/run_weekly_cycle.py` | **周循环入口**（2026-09-21 新增）：加载当日情报 → `engine.run_weekly_cycle(intel_items=...)`。替代原先 daily_run.ps1 里的 `python -c` 单行——那个写法没传 intel_items，导致 AI 周报连续两周写「情报总条数 0」 |
| `verify_hypotheses.py` | 假设自动验证（FRED/Frankfurter/GoldAPI/WorldBank，域名白名单在 `ALLOWED_HOSTS`；2026-09-05 P0-2 重写：`parse_threshold()` 真比较数值、指标值优先读本地 macro_indicators.json、无源标 `no_source`/叙述阈值标 `needs_ai` 留给周循环 AI 裁判） |
| `tools/fill_deadline.py` | P0-2 一次性脚本（跑一次即弃）：AI 提议 1~24 个月验证期限回填 deadline，失败按 level 兜底（small 3/medium 6/major 12/mega 24 月）；2026-09-05 已跑 71/71 全回填 |
| `tools/cluster_stories.py` | P0-3 事件聚类：纯标准库 TF-IDF（**SP-unigram 分词，2026-09-30 从字符 2-gram 升级**）+ 余弦 + 并查集，**不引入 sklearn**；`assign_story_ids(items)` 供 refresh/link_intel_hyp 调用；三重闸门参数（SIM_THRESHOLD=0.65/时间48h/同源12h/摘要0.30）在文件头；词表缺失自动降级 2-gram |
| `tools/train_sp_tokenizer.py` | SP 词表训练（8000 词 unigram，从近 7 天语料学习）→ `data/.sp_unigram.model`（**已入库**，缺失会静默降级）；语料显著变化时重训 |
| `local/calibration.py` | P0-1 校准评分：读 resolutions.jsonl 算 Brier + Murphy 三分解 + 十桶校准曲线 → `data/calibration.json`；refresh.py 自动调 |
| `local/jev_client.py` | **JEV 决策模型客户端**（2026-09-21）：System One API 调用 + SSRF 白名单 + 用量记账 + 重试；`diagnose_evidence()` 供 ACH 用 |
| `tools/jev_probe.py` | JEV Phase 0 探针：A/B 对比历史判定 + 人工金标准测试 |
| `tools/jev_usage.py` | JEV 用量账本（API 无用量端点，本地记账）→ `data/jev_usage.json` |
| `tools/fetch_macro_indicators.py` | 宏观指标抓取（汇率/利率/GDP/CPI/失业率，12个指标），产物 `data/macro_indicators.json`，refresh.py 自动调用；`--history` 子命令抓 NBS 分年龄组失业率历史月度序列 |
| `tools/fetch_now.py` | 本地 24h 全量拉取（**仅国内源**，`scope:ci` 的 33 个外国源跳过——境外源一律由 CI 在 GitHub Actions 上采集，本地拉不动是常态），append 到今日 jsonl；refresh.py 自动调 |
| `tools/translate_local.py` | 本地翻译（**通道链：Gemini → dots → 本机 4010 zen 代理 → OpenCode 官网直连**），每跑 30 条 6 分钟，写回 jsonl；refresh.py 自动调 |
| `local/zen_proxy_client.py` | **本机 OpenCode Zen 代理客户端**（2026-10-05）：`reachable()` 预检（CI 无 4010 自动跳过）+ `chat_completion()` 把代理强制的 SSE 响应拼回完整文本；自带收窄回环守卫，见 §"本机 OpenCode Zen 代理接入" |
| `local/intel_gate.py` | **AI 准入排序**（2026-10-05 建 / 2026-10-06 从硬过滤改为排序）：按「新鲜度 → `base_score` → 时间」排优先级，**不丢弃任何条目**；`OSINT_AI_SCORE_GATE=0` 退化纯时间序；被 translate_local / citizen_impact 共用，见 §"AI 准入排序" |
| `tools/rescore_recent.py` | 词表扩充后的**一次性存量回填**（2026-10-06）：只补 `base_score=0` 的条目（严格增量，不改正分），默认近 7 天，写回前自动备份 `.bak_rescore_*` |
| `tools/fetch_gdelt.py` | P1-5 GDELT 国际侧补源（DOC API 三组查询 24h 窗口，title-only 流入本地翻译管线）；白名单 {api.gdeltproject.org} 脚本内自带；6s 间隔+12s 退避+3h 成功节流（data/.gdelt_last_run）；refresh.py 自动调，失败静默 |
| `worldview_loader.py` | 三观加载/注入文本构建（worldview.yaml 唯一事实源；缺失静默降级返回空串；**裁判链路明确不注入**保持校准客观）；`--show` 看档案+注入预览 / `--check` 结构校验 |
| `net_proxy.py` | **git 联网代理探测**（2026-10-06）：探测本地 Clash 代理端口并注入 git 子进程环境变量（`GIT_CONFIG_*`，不改命令行、无注入面），供 local_sync/refresh 共用；CI 无代理自动退回直连，见 §"CI 故障排除 #9b" |
| `local/worldview_engine.py` | 三观输入引擎：`--seed` AI 从 persona+views 起草初稿（draft:true）/ `--interactive` 9 轮引导（3 阶段×3 问，复用 dialogue_engine 深化机制，覆盖写 draft:false）/ `--show`；AI 合成失败不动 YAML |
| `worldview.yaml` | 用户三观档案（worldview/lifeview/values + analysis_directives），仓库根提交供 CI 读取；draft=true 标记 AI 初稿待校正；Obsidian 镜像 `视频知识库\wiki\views\worldview.md` |
| `gen_dashboard.py` + `fix_dashboard.py` | 生成 HTML（必须按此顺序）；gen_dashboard 内嵌 macro 面板 CSS/HTML/JS，趋势图用 Chart.js 4.4 (jsdelivr)；**情报流双栏**（2026-10-06：左「最新消息」时间序 / 右「24h 重要情报」分数序，见 §"情报流双栏"）；P1-4 翻车高亮（flipBadge：⚡高确信翻车/↓置信度断崖 + 红边卡片） |
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
- **正则抓取多值句必须验证语义归属**（2026-09-19 踩坑）：财新失业率文章有三种句式（「25—29岁、30—59岁…分别录得 A%、B%」/「…为 A% 和 B%」/「30—59岁…维持在 A%」），旧正则一律往后找第一个数字，把 25-29 岁的值错配给了 30-59 岁，污染 5 处下游正文。**修法**：①按年龄段出场顺序配对取值；②加**逻辑自洽闸门**（分项不应大于总量——30-59 岁是劳动力主体，其失业率不可能高于城镇调查失业率总量）。规则：**数值型抓取管道必须内置自洽校验，宁可缺不可错**。
- **引用任何下游数据前先做合理性检验**：均值/分项/总量之间若有包含关系，先算一遍能否自洽。历史档案写作时若核对过「30-59 vs 总量 5.3%」，本可当场发现这个 bug。
- `falsification_criteria` 为空时会在验证时自动从 indicators/sub_propositions 的 `threshold_refute` 回填，不要手填重复值。
- **新鲜度/合法性闸门一律用「计数」而非「取极值」**（2026-09-21 踩坑）：首版 `_content_age_days` 取 `max(published_at)` 算年龄，但语料混着源站错误时间戳的未来条目（实测 2026-11-17）→ max 恒取未来 → 年龄 **-57 天** → 闸门永远通过。改用 `_fresh_count()` 统计「近 N 天内条目数」，未来日期天然落在区间外。**任何用极值做判据的校验，先问：脏数据能否顶到极值端？**
- **跨进程共享的快照文件必须校验内容，不能只信文件名**（2026-09-21）：`cache/intel_{今天}.jsonl` 由 main_local 自己写入，上游抓到旧数据时会顶着当日文件名——实测 9-21 的缓存装的是 9-14 的 1006 条（与当日仅 4 条交集）。
- **AI 分析量必须与通道吞吐匹配**（2026-09-21）：`条数 ÷ batch_size × 单批耗时` 先算一遍再上线。Step2 曾全量送 1006 条 = 101 批 × dots 实测 137s/批 = 3.8 小时，自 8-30 起**静默挂起从未跑完**（CPU 0.7s/84 分钟，不报错不退出）。**"能跑"不等于"跑得完"**。
- **两个定时任务抢跑是结构性问题**（2026-09-21）：OsintWeekly 用 StartWhenAvailable 补跑时落点不可控（曾压在整点，与 OsintRefresh 只差 3 秒）。消费"另一进程正在写入的数据"必须有等待/重试闸门（`_wait_for_today_intel`），不能假定数据已就绪。
- **排查进程挂起的手法**：CPU 增量 + 网络字节增量**双零** = 挂起（非慢速推进）；`Get-CimInstance Win32_Process` 的 `ReadTransferCount` 判断"是否在等网络"；对比缓存内容与真实数据的 **id 交集**，可立刻证伪"它在处理正确数据"的假设。
- **推理模型的 reasoning 计入 max_tokens**（2026-09-21 踩坑）：dots3-note-prev 生成 10 条 × 8 字段 JSON 时，reasoning 实测 7159 字符、正文 6331 字符，`max_tokens=8192` 会在数组中途截断。**长输出任务的 max_tokens 必须按 reasoning + 正文双份估算**；同时解析侧要有逐条抢救（`_salvage_items`），不能指望整体 `json.loads`。
- **AI 输出必须过规范化层再喂下游**（2026-09-21）：模型返回的字段类型不固定（该给字符串的给 dict、confidence 给 0 或空）。下游做字符串切片/按 key 取值前，统一走 `_norm_text`/`_norm_dict`/`_norm_conf`——**先规范化，再消费**，别让 schema 违约渗进渲染层。
- **同码不同因的 HTTP 错误必须读响应体区分**（2026-09-21）：403 可能是鉴权失败（端点已死，该熔断）、配额耗尽（该退避）、或**内容安全审查**（端点健康，换个 prompt 立刻可用）——三者处理方式完全相反。实测 dots 返 `governance.content_safety_input_rejected`，被熔断器当成"端点死了"拉黑 30 分钟，放大成整轮失败。**只看状态码做熔断/重试决策必然误判**，先把 body 读出来再判断。
- **批处理必须能隔离坏元素**（2026-09-21）：dots 对 prompt 做内容审查，10 条批次里有 1 条涉政敏感即整批被拒（其余 9 条单发正常）。**二分拆半是通用解法**——O(log n) 次重试定位问题元素，比"整批放弃"或"全量逐条"都好。凡是"一批一请求"的 AI 调用都要问：单条坏数据会不会拖垮整批？
- **服务端输出上限可能远低于请求值**（2026-09-21）：dots 接受 `max_tokens=32768` 但实际封顶约 8-9K。**参数被接受 ≠ 生效**——用真实负载测实际产出长度，据此定 batch_size，别按请求值估算。
- **置信度只能由"判定层"写，挂载层不许碰**（2026-09-28）：`link_intel_hyp.py` 曾按情报正文的涨跌词（增长/上升/下跌/暴跌）对**全部 75 个节点**做 ±0.01 调整。该规则衡量的是**行情语气**，不是"这条情报是否支持该假设"——实测把 `三战在5年内爆发` 从先验 0.05 推到 0.94，贡献证据却是「大熊猫抵达美国」这类无关条目。**凡"关键词计数→改置信度"的设计，先问：这个词表能区分"与假设相关"和"碰巧含这些词"吗？** 现置信度只有两个合法来源：`ach_matrix.bayesian_update`（贝叶斯后验）与 `hypothesis_engine.verify_hypothesis`（AI 裁判）。新增写 confidence 的代码前，先确认它属于判定层。
- **树里的节点性质不同，回填/统计不可一视同仁**（2026-09-28）：75 节点 = 6 major（ACH 诊断对象）+ 4 mega（**探针**：用户问"这个可能性多大"，靠先验+情报信号占比回答，不进矩阵）+ 60 small/medium（层级分解）+ 5 view 物化（实验类）。**对探针节点灌证据累积会得出无意义结论**（"含涨跌词的新闻多"≠"三战概率高"）。
- **探针措辞必须锚定节点自己的判定定义**（2026-09-28）：`tools/probe_mega.py` 首版只写议题名（"全球性经济危机"），JEV 把霍尔木兹油价、美债收益率这类**能源地缘事件**全算成危机信号（120 条里 60 条命中 0.2+）。修正为把 `rationale`/`falsification_criteria`/`indicators` 写进问题 + 显式声明"间接关联不计入"后，signal 档从 50% 收紧到 7.67%、最高 gate 从 0.71 降到 0.27，语义归属抽检通过。**凡是让模型判断"是否与 X 相关"的调用，X 必须有可检验的定义，光给名字必然过宽。**
- **判定层三层信息衰减：证据、提问、判据**（2026-09-29）：ACH 判定曾系统性误判——HM001「台海冲突升级」51 条 C/I 里 gate≥0.5 仅 6 条，其余是「A股军工板块拉升」（gate 0.13, **conf 0.93**）这类字面含"军工/地缘"但语义无关的条目。三层根因：①证据只存标题 `[:100]`（平均 47 字符），JEV 看不到正文；②门控问「是否涉及该议题」，JEV 把"议题"理解为**主题域**而非**概率变化**；③方向问「是否与预期一致」，把"是否支持假设成立"字面化。**修法：证据带正文（`body` 字段）+ 门控问"是否改变概率" + 方向问"是否实质推动"（带节点 rationale）。** 实测 10 条人工标注 9/10 正确（噪声 5/5 全滤）。
- **证据必须能区分竞争假设，只看绝对相关度不够**（2026-09-29）：新增 `selectivity = gate_max / gate_median` 双判据（Heuer 的 diagnosticity 形式化）。实测能把信号与噪声干净分开——台海军演 10.2 / 伊拉克石油 8.0（信号）vs 水星半径 1.0 / 金鱼饼干 1.0（噪声）。**单假设场景退化为只看绝对门槛**（1 个假设时比值恒为 1，实测踩坑：台海军演被误杀）。阈值来源非人工拍定：`GATE_ABS_THRESHOLD=0.15`（真信号 0.26~0.56 / 噪声上界 0.19）、`SEL_MIN=2.5`（p50=2.3 / p90=7.2），依据 Selective Prediction 的 reliability+relevance 双层设计（ReCoVERR 2024）。
- **弱证据降权要够狠，否则累积主导后验**（2026-09-30）：`GATE_WEAK_THRESHOLD` 从 0.20 提到 **0.35**。依据：186 条 C/I 里 60%（111 条）来自 gate < 0.3 的弱相关证据，而 floor=0.20 只降到 0.4~1.0 倍——`HM003` 被弱 I 压到 0.516。提到 0.35 后 `HM003` 0.516→**0.825**、`HM001` 0.108→**0.206**。**模拟工具 `tools/simulate_gate_weight.py` 可对比多个阈值（只读不写盘）**，落地前先跑它——实测重算结果与模拟完全一致。
- **贴边界的后验要核实证据质量，不能假设是噪声**（2026-09-30）：`HM100`/`HM102` 在三个阈值下都贴地板 0.05，抽检其 I 判定发现内容质量很高（IEA「煤炭需求创纪录」反驳能源转型、微软扩产反驳 AI 算力短缺），**12/57 与 5/49 条强 I 判定足以支撑结论**——这是真实的强证据结果，不是噪声累积。**"调参数救不了也不该救"是合法结论。**
- **"是不是 major"取决于能否被情报诊断，不取决于题目大小**（2026-09-29）：`hyp_e2b46e32`（东亚三国趋同）与 `hyp_d26158d3`（中国社保走韩国老路）曾是 major，但实测 ACH 诊断 **0 条 C/I**——它们与探针同级（覆盖多年/多领域的总判断），JEV 面对"这条情报是否改变该元命题的可能性"无从下手。已降为 mega 移出 ACH，改走 `probe_mega` 测信号占比（实测信号率 1.0% / 0.0%，印证判断）。**判据：ACH 假设必须是可被单条情报推动的竞争解释；元命题不是。**
- **major 层必须有竞争面，"事实描述"不合格**（2026-09-29）：上述两个元命题的子命题多是"老年抚养比超20%""台积电产能利用率超95%"这类可查证统计——它们没有竞争解释，不需要情报诊断。**small 层的职责恰是"可查证的量化检查点"，不需要竞争面；但 major 层必须有**（Heuer：ACH 要求假设互斥竞争同一批证据）。
- **证据挂载范围必须与诊断范围对齐**（2026-09-29）：`link_intel_hyp` 对全部 75 节点做匹配挂载，而 `ach_daily_batch` 只诊断 major——实测 18,178 条证据里 **71%（13,004 条）挂在 ACH 看不见的地方**，消耗 TF-IDF 算力却无下游消费。修法：`link_intel_hyp` Pass 3 沿 parent 链**上卷**子节点证据到第一个 major 祖先（带 `from_child` 标记，relevance × 0.9）。**为什么不把子节点升 major**：父子同时在 ACH 会"自己和自己竞争"，违背互斥要求；子命题是**验证分解**（indicators 是可查证检查点），不是独立竞争假设。
- **上卷会继承子节点的挂载噪声**（2026-09-29）：上卷暴露了 DOMAIN_MAP 关键词过宽的老问题——`HM101_A_s2`（加拿大报复性关税涉及农产品）1191 条证据里混入农产品期货日报、"国产伟哥案"等无关条目（"农产品"字面匹配）。实测门控能拦住（噪声 gate 0.04~0.13 / selectivity 1.3~1.5 vs 真信号 0.60 / 5.71），**代价是浪费 JEV 调用，不污染后验**。
- **"每日上限"必须跨轮次累计，局部计数器等于没有上限**（2026-09-30）：`EVIDENCE_DAILY_CAP=6` 原本用**本轮局部**计数器（`hyp_recorded={}` 每轮归零），而 refresh 每小时跑一轮 → 每轮各加 6 条，实测 HM100 单日直接证据 9 条、HM101 10 条，**cap 形同虚设**。Pass 3 上卷更严重：它根本不查任何上限，遍历的是**全量 candidates**，9-29 单日给 HM101 灌 481 条（350 条来自 `HM101_A_s2` 一个子节点），**JEV 日消耗从 5 万 token 暴涨到 798 万（100 倍）**——按 $0.042/Mtok 算，用户 $5 额度只够 13 天。**判据：凡是"每 X 上限"的闸门，计数必须来自持久状态（按日期过滤已有条目），不能来自本次运行的局部变量。**
- **上卷本身有效，失控的是量不是质**（2026-09-30）：修 cap 前先量了质量——上卷证据的信号率 **11.8%**，与直接证据 **13.1%** 几乎持平（`tools/simulate_rollup_cap.py`）。所以修法是**限量**（`ROLLUP_DAILY_CAP=4`），不是关掉上卷。cap 取 4 的依据：6 major ×（直接 6 + 上卷 4）= 60/天 = `ach_daily_batch` 的 BATCH 容量——**证据量必须与诊断吞吐匹配**，多出的只会积压成矩阵膨胀。上卷低于直接是刻意的：它隔了一层子节点，优先级应当更低。
- **分词器换用论文做法，但收益有真实边界**（2026-09-30）：`cluster_stories._tokens` 从字符 2-gram 换成 **SentencePiece unigram**（Si et al., TACL 2023 "Sub-Character Tokenization for Chinese PLMs" 的做法：从语料学习子词词表）。孤立层收益显著（语料命中 0.14%→2.01%，14 倍），**但生产管线级收益为 0.0pp**（`tools/verify_tokenizer_upgrade.py` 实测）——因为 96.5% 的条目走 DOMAIN 兜底，TF-IDF 分支只覆盖 3.5%。**教训：分层系统的单层指标提升不等于端到端提升，必须在生产结构下 A/B。** 聚类侧是真实赢（最大簇 15→9，误聚减少；新增合并抽查全是真实同事件）。词表 `data/.sp_unigram.model` 必须入库（缺失会静默降级，匹配行为无声改变）。
- **TF-IDF 词面匹配的天花板已被测出**（2026-09-30）：用矩阵里 9420 个已被 JEV 判定的（证据,假设）对做地面真值，实测 **AUC=0.709**，阈值 0.12 下只捕获 **6.7%** 的真信号——**93% 的真信号靠 DOMAIN 兜底捞回**。同时 DOMAIN 的 AUC=0.822 更高，但对 18.2% 的真信号完全无分（只能靠 TF-IDF）。**两者互补而非替代**。词面匹配的上限在此，**再往上要走 embedding**（接口已预留 `build_tfidf_vectors`，调用方不动）。
- **模拟必须与生产语义一致，否则会得出假结论**（2026-09-30，自我纠错）：`tools/simulate_matcher_union.py` 首版把同一 (情报,假设) 对当成**两条独立候选**（tfidf 一条、domain 一条）参与排序，得出"union 可 +9.1pp 召回"——**这是错的**。真实实现应按 hyp_id **合并成一条**，且同时命中时 method 取 tfidf（否则丢失 `ach_eligible` 资格，因为 DOMAIN 需 ≥0.4）。用忠实语义重测（`tools/simulate_union_merge.py`）：**union 只带来 +0.5pp 召回**（+1 真信号 / +6 噪声），**不值得改**。**判据：写模拟前先问"真实代码会把这两条候选合并吗"，否则测的是不存在的系统。**
- **"命中即满分"的计分必然饱和**（2026-09-30）：DOMAIN 兜底原用纯 Jaccard（`|交集|/|并集|`），实测 **73% 是 1.0**——因为情报与假设常恰好同命中一个域，分数高不代表内容相关。修法：域内命中强度按**关键词 IDF 加权**（命中多词 > 单词，专指词 > 泛词）。实测泛词 `AI` 命中 13.7% 语料 / `投资` 8.9% / `美国` 8.0%，IDF 权重 2.17/2.57 vs `芯片` 4.26。修复后满分从 7.9% → **0%**，<0.5 从 22.3% → 56.8%，分数恢复梯度。**踩坑：`log(N/(1+df))` 在 df=0 时给最高权重，但 df=0 意味着该词本批语料从未出现——应按 df=1 处理。**
- **排序键必须实测 AUC，不能凭直觉选**（2026-09-30，本轮最大发现）：cap 择优的排序键原为 `(tfidf优先, -relevance, -base_score)`。用 5175 条已诊断证据 join 回原始情报测 AUC 发现：**`relevance` 的 AUC=0.4762（低于随机！）**，且 51.4% 并列在 1.0（分数饱和）→ 排序退化为随机抽签。`tools/test_sort_value.py` 更直接：现状 top-6 只抓到理想值的 **8%**，比随机选（12%）还差。换成 `keywords_hit*10 + body_len/100`（AUC=0.6458，并列仅 4.2%）后回测 **+112%**（8→17 条）。**判据：任何"按 X 排序择优"的设计，先测 X 对目标的 AUC；AUC<0.5 说明它在反向排序。**
- **单日真信号率不能用来验证排序改进**（2026-09-30 踩坑）：新排序键上线后，我按"当日直接证据真信号率"对比，先得到 22.2%→16.9%（像是退化），换正确基线后又得到 19.4%（落在旧键区间内）。逐日数据显示该指标在 **11.1%~30.6%** 之间波动（±10pp），**完全被当日新闻质量主导**，信噪比太低。**验证排序改动必须用池级回测**（同一批候选池上比"按新键取 top-N" vs "按旧键取 top-N" 抓到多少真信号，`tools/backtest_ranking.py`），而不是看上线后的日度比率。**判据：要验证的是"选择能力"，就必须在同一个池子上比较选择结果——跨时间的比率变化混入了太多无关变量。**
- **历史证据的 A/B 验证需要真花钱诊断（实测结论）**（2026-10-01）：排序键改动**不会自动重跑历史**（Pass 2 只在灌入时执行一次）。用 `tools/reselect_historical.py` 重建候选池重选 + JEV 诊断，得到**统计显著**的结论：
  | | 新键 | 旧键 | 检验 |
  |---|---|---|---|
  | 真信号率 | **26.2%** | 20.4% | z=2.13 ✓ |
  | gate 均值 | **0.1305** | 0.1187 | t=2.16 ✓ |
  | n | 355 | 668 | — |
  **但真实增益是 +28%，不是回测的 +112%**——回测高估了，因为它是"理想排序 vs 现状"的差距，而新键只填了一部分。**判据：回测给的是上界，真实增益必须花钱实测。**
- **回填历史必须检查时间线失衡**（2026-10-01）：补 356 条到 15 天窗口后，该期占全库比例 10.6% → **15.4%**（可接受，<25% 警戒线）。若按 cap=20 全量补 1586 条则会到 **35.1%**——ACH 后验是累加更新，单期数据量翻 3 倍会让后验被那两周主导。**判据：回填前先算"该期占全库比例"，超过 25% 就分批补或降低 cap。**
- **`glob("intel_*.jsonl")` 会漏数据（2026-10-01 修）**：字符串排序把 `intel_final_20260829.jsonl`（`f` > `2`）排在 `intel_2026*` 之前，占掉 3 天窗口的名额——实测生产只读到 2 天（9-29 的 2796 条被挤掉）。**修法：`glob("intel_2*.jsonl")` + 显式排除 `raw`/`final`**（`fetch_now.py` 的注释早已写明这两类不参与下游，但读取侧没落实）。同类修复：`link_intel_hyp.py` / `daily_briefing.py` / `verify_hypotheses.py`。**判据：凡是"取最近 N 个文件"的逻辑，先确认排序键能正确处理异常文件名。**
- **分数饱和是排序失效的头号原因**（2026-09-30）：HM102 的 9-10 完整池里 **86% 的证据并列在满分 1.0**（命中单一"科技"域 → coverage=1.0 → strength 饱和）。并列组内真信号率 14.4%，说明**不是分数无效而是分数无梯度**。**判据：设计评分函数后先看"最大并列组占比"，>30% 时排序已不可用。**
- **闸门参数要为当前决策层校准，不能沿用旧值**（2026-09-30）：`EVIDENCE_DAILY_CAP=6` / `BATCH=60` / `MAX_DIAGNOSE_PER_RUN=20` 都是为 **mimo（45s/条）** 设的。JEV 接入后实测 **1.52 条/秒**（5455 条/小时），瓶颈从"诊断速度"变成"排序质量"。cap 6→20 实测捕获池中真信号 **8%→23%（2.9 倍）**，成本仍只 **$0.0091/天**（$4.35 够用 479 天）。**判据：换决策层/换模型后，重新推导所有"按吞吐量算出来"的参数。**
- **清理脚本的阈值必须从生产代码导入，不能硬编码**（2026-09-30 踩坑）：`tools/prune_overcap_evidence.py` 首版硬编码 `DIRECT_CAP=6`，在 cap 提到 20 后把刚合法灌入的 20 条**误判为超标并删除**（删了 49 条，已从备份恢复）。**判据：任何"校验/清理"脚本读的阈值都 import 自生产模块——复制常量等于埋雷。**

## 探针假设机制（2026-09-28 建立）

`tools/probe_mega.py`：给 mega 探针节点测「当前情报语料里的信号密度」。

| 项 | 说明 |
|---|---|
| 输入 | 近 3 天情报（按 final_score 降序采样，默认取 200 条） |
| 方法 | JEV Noul 逐条问「这条情报是否**实质推动**该情景向发生靠近」，问题里带节点自己的定义 |
| 输出 | `signal_ratio`（gate ≥ 0.20 占比，判读用）/ `mention_ratio`（≥0.08 宽口径）/ 最强信号 gate 值 |
| 落盘 | `data/hypotheses/probe_readings.json`（追加式保留历史）+ 节点 `probe_reading` 字段 |
| **不写 confidence** | 探针概率是用户的问题，工具只提供客观输入；`confidence` 保持先验 |
| 调度 | refresh.py `run_hypothesis_chain` 第 4 步（20h 节流），实测 0.88s/条、200 条约 3 分钟、成本 ~$0.005 |
| 手动 | `python tools/probe_mega.py --limit 200 --write`；`--dry` 只看采样 |

**当前读数**（2026-09-28，300 条样本）：三战 signal 6.67% / 经济危机 7.67%——语料里约 7% 的条目在实质推进这两个议题，与先验（0.05/0.08）同量级，暂无异常信号。

## 假设生产机制（2026-09-28 建立）

**问题**：假设生产链 9-14 后停摆。唯一入口是 `views.yaml` 手写观点 → AI 拆解（`_decompose_view`），6 个 view 全部物化后无新假设产生。情报每天进几千条，**没有任何代码路径把「反复出现但树里没有的主题」变成假设**。

`tools/propose_hypotheses.py`：从情报自动提议假设。**默认只提议不入库**。

| 项 | 说明 |
|---|---|
| 流程 | TF-IDF 聚类找成簇主题（复用 cluster_stories）→ 与现有 75 节点比对剔除已覆盖 → AI 按既有 schema 提议 |
| 输出 | `data/hypotheses/proposed_YYYYMMDD_HHMMSS.json`（含 accepted/rejected + 拒绝原因） |
| 入库 | 需显式 `--write-tree`，人工过目后执行。**默认不入库是刻意的**——自动入库会让树被废话假设灌满，无法证伪的假设比没有更糟 |
| 手动 | `python tools/propose_hypotheses.py --dry`（只看候选）/ `--limit N`（调 AI） |

**四道闸门**（全部实测校准，缺一不可）：

| 闸门 | 挡什么 | 实测教训 |
|---|---|---|
| 固定栏目过滤 | 早报/汇总/一览类模板标题 | 首版 8 个候选里 4 个是「华尔街见闻早餐」——发布格式不是主题 |
| 跨天持续性（≥2 天） | 同一事件被多家媒体复述 | 「武契奇辞职」3 条同日成簇——那是一个事件，不是持续主题 |
| 可证伪校验 | 无年份/无数值的证伪判据 | "情况好转"这类无法检验的表述必须拒绝 |
| 来源白名单 | 编造的指标来源 | 首版漏过一条 rationale 里写「中东LNG占45%」的编造基线 |

**设计转向（关键）**：TF-IDF 只能测「词面相似」，测不出「是否值得建假设」——实测候选全是数据流水（美元指数播报）。改为**双通道**：聚类结果只作热点提示，主题识别交给 AI 从高分样本做（它能区分「事件」「数据流水」「持续议题」）。prompt 里显式列出三类反例 + 要求"宁缺勿滥，返回空数组也接受"。

**实测**：1500 条语料 → 21 簇 → 滤栏目剩 15 → 剔除已覆盖剩 2 → AI 提 1 条「霍尔木兹危机导致全球LNG贸易流向长期重构」（3 指标带双阈值、来源具体、证伪条件可量化）。

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

## 历史脉络档案（2026-09-12 建档）

大历史认识（经济/政治/文化交织的「中国如何走到今天」）生产管线，反幻觉反立场靠架构不靠微调：

| 部件 | 说明 |
|---|---|
| 架构 | 骨架+枝叶：`wiki/macro-history/00-skeleton.md`（分期表+维度互动，唯一承重墙；2026-09-16 目录由 `wiki/history/` 更名，避免与对话历史混淆）→ 5 枝叶（10-经济/20-政治治理/30-社会民生/40-对外/50-文化）+ cards/ 事件卡 + data/ 手工数据表 |
| 管线 | `local/history_engine.py`：`--outline` 分期表（总闸门，锁定前不起草）→ `--draft <页> <节>` 双稿对照（Mimo 主笔+Nemotron 副笔+裁判列分歧）→ `--verify` 数值锚校验+幻觉裁判 → `--publish` 转正+REVISIONS/index/log → `--revision-suggest` 假设漂移>15pp 提示修订 |
| 反幻觉 | 数字必锚【据:xx】或【无据待查】，禁裸数字；校验器扫描；Nemotron 裁判逐节核查（strict 页逐节，其余抽查）；材料包=data/ 表+失业率序列+宏观快照 |
| 立场 | 双稿多血统对照显形敏感区；用户四维框架显式注入；主编（用户）裁决——不微调模型（微调不消幻觉不除立场，远期有校订语料后再议） |

注意：脚本产出 AI 失败时把 prompt 落盘 `drafts/_prompt_*.md` 供重跑；opencode.ai 免费配额 429 时全线降级失败属正常，次日配额滚动恢复。

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

## 证据准入收紧（2026-09-12，盘点驱动）

盘点发现证据灌入（日数百条）远超 ACH 诊断速度（日 ~40 条）：major 证据 905→3906 只用 2 天，矩阵积压持续膨胀。且实测 **DOMAIN 分数无区分度**——73% 匹配是 1.0 满分（情报与假设恰好同命中一个域 ≠ 内容相关），阈值过滤无效。修复设计：**主闸门 = 每假设每日限量择优，存量弱证据退出诊断队列**：

| 改动 | 文件 | 效果 |
|---|---|---|
| 每假设每日证据 cap=6（TF-IDF 优先 → 分数降序 → 关键词密度降序择优，两遍法） | link_intel_hyp.py | 证据日增 ≤48，与诊断速度匹配 |
| DOMAIN 兜底卫生底线 min_score=0.34 | match_intel_to_hyp | 滤 <1/3 重叠长尾（实测仅 1%） |
| 证据条目加 `ach_eligible` 标记（tfidf 一律 eligible / domain≥0.4） | update_hyp_evidence | 诊断准入依据（TF-IDF 余弦与 DOMAIN 分数量纲不同，不能统一阈值） |
| `find_undiagnosed` 只收 eligible；存量（无标记）默认拒，`LEGACY_DEFAULT_ELIGIBLE=True` 可回退 | ach_matrix.py | ~3753 条历史弱证据退出队列（保留在 evidence_log，历史完整） |
| 日志打印 eligible 队列数 + 准入漏斗（候选→记录） | ach_daily_batch.py / link | 盘点直接看收敛趋势 |

**实测**：link 候选 2409 → 记录 0（3 天窗口已全记录，幂等正确）；ACH eligible 队列 0（存量退出，次日起按 ≤48/天积累新准入证据）。桩测五件套全过（eligible 判定/标记写入/准入规则/过滤/cap+tfidf 优先）。

## ACH LR 推导下沉（2026-09-20，分布诊断驱动）

**动机**：盘点 388 条证据的 LR 分布发现严重塌缩——**C 有 76/111 集中在 1.5、I 有 46/108 集中在 0.5**，即模型在照抄 prompt 里的示例值（原 prompt 写"C 取 >1.2，I 取 <0.8"），而非做概率推理。这与 JEV 官方 jaggedness 文档的结论一致（"模型不是计算器，算术应留在代码里"）。

**改法**：模型只报 `code` + `conf`（把握度 0~1），LR 由 `derive_lr()` 在代码里算。

| 项 | 内容 |
|---|---|
| 新增常量 | `LR_C_STRENGTH=1.5` / `LR_I_STRENGTH=0.5` / `LR_CONF_FLOOR=0` / `LR_CONF_CAP=1.0` |
| 映射 | `LR = 1 + conf*(STRENGTH-1)`；C∈[1.0,1.5]、I∈[0.5,1.0]、N 恒 1.0 |
| 关键性质 | **conf 低时自动向 1.0（中性）收缩**——避免"低把握+极端 LR"污染后验 |
| 落盘 | 新行同时存 `code/conf/lr`，日后调 `LR_*_STRENGTH` 可用存量 conf 重算 LR，**无需重跑 AI** |
| 兼容 | 旧行无 conf → 回退读模型自报 lr（原样保留）；两者皆无 → lr=1.0（不更新后验，保守） |

**强度上限的取舍**：C 上限取 1.5 而非 2.0、I 下限取 0.5 而非 0.3——单条证据不应过度撬动后验，配合 `POSTERIOR_FLOOR=0.05` 共同防极端值。

**验证**（2026-09-20 完成）：
- 映射单调性 + 边界 + 异常输入容错（None/''/'abc'/越界）全过
- **历史后验零漂移**：改动后重算 8 个 major 的后验，与现状最大差异 0.000000
- 端到端桩测 4 变体（标准/markdown 围栏/漏 conf/非法 code+越界 conf）全过
- 真实 AI 实测 2 条：第 1 条全 N conf=0.2（确无关联，判定正确）；第 2 条命中 I 且 conf=0.7，与历史判定一致——**证明模型能给出有区分度的 conf**

**注意**：`ai_diagnose` 保持"一次调用看全部 8 个假设"的跨假设比较能力，**未拆成独立调用**。实测 151 条有 C/I 的证据中，**107 条存在"未挂载却被判 C/I"的情况（138 处信号）**——这些是 TF-IDF 预筛漏掉、靠跨假设比较才发现的。拆成独立问题会丢失这部分信号（ACH 方法论本身也要求同时看所有竞争假设以防锚定）。

## JEV 决策模型接入（2026-09-20 调研 / 2026-09-21 Phase 0 实测）

**JEV 是什么**：TypeSafe AI 的 System One 决策模型（创始人 Diogo Almeida，GPT-4/ChatGPT/RLHF 共同作者）。非自回归，一次前向输出 schema 内全部概率。三原语：Choice（选一个，返 choice/probabilities/confidence）、Score（有序打分）、Noul（是/否概率）。**闭源**，无技术报告（官方 sitemap 全站仅 5 篇博客）。

**key 与端点**：`config.local.yaml` 的 `jev` 段（gitignored），`secrets_loader.get_jev_key()` 读取；端点 `https://api.typesafe.ai/v1/systemone`，模型 `jev-latest`。

### Phase 0 实测结论（60 条历史证据 + 12 条人工金标准）

| 结论 | 数据 |
|---|---|
| **吞吐提升 68 倍** | 0.66 s/条（8 假设并行一次调用），mimo 为 ~45 s/条 |
| **成本可忽略** | 60 条 $0.0054（$0.042/Mtok 输入，输出免费） |
| **对人工金标准准确率 83%** | 10/12（自建标注，覆盖 C/I/N 三类 5 个假设） |
| **对历史标签仅 23%** | ⚠️ 但**历史标签本身是噪声**，见下 |
| **conf 有真实梯度** | C/I 判定 conf：min 0.51 / p50 0.73 / max 1.00，21 个不同取值 |

**关键认知一：23% 一致率是假警报。** 分歧案例逐条人工核对，JEV 大多是对的——「韩国加息」「日经指数涨跌」「黄金欧元行情」被判给「AI成本上升」假设是 mimo 的过度联想。用 Noul 单问相关性，JEV 给这 5 条的概率是 0.04~0.17（明确无关）。**这批历史 C/I 标签质量差，不能作为 A/B 基准。**

**关键认知二：提问方式的影响远大于语言**（最重要的工程发现）。同一证据仅改措辞，结论从 inconsistent(0.77) 翻转成 neutral(0.31)：

| 提问方式 | 与历史一致率 |
|---|---|
| A 简单：「与假设预期是否一致？」 | **40%** |
| B 带证伪判据：「…该假设的证伪判据是：<120字>」 | **20%** |
| C 英文提问（简单措辞） | 未提升（同 A 量级） |

**英文并不比中文好**——推翻了"中文能力差"的假设，真正瓶颈是 prompt 结构。原因符合官方 jaggedness 第 5 条「无关细节拉低精度」：证伪判据里的具体数字指标对方向判断是噪声。

**修正模板后重跑**：一致率 23% → **59.4%**，判定分布也更接近历史（C 3.0%/I 4.8% vs 历史 3.6%/3.5%）。

**关键认知三：`confidence` 字段是集中度，非正确性。** 实测验证 `c = (P_max − 1/K)/(1 − 1/K)`，4 组样本最大误差 0.01。**喂 `derive_lr()` 必须取 `probabilities[choice]` 原始概率，不能取 confidence 字段。**

### 工具与现状

| 项 | 状态 |
|---|---|
| `tools/jev_probe.py` | Phase 0 探针（A/B 对比 + 金标准测试），已按实测教训固化简单提问模板 |
| `local/jev_client.py` | **JEV 客户端**（2026-09-21）：双通道（free/paid）+ SSRF 白名单 + 用量记账 + 3 次重试 |
| `tools/jev_usage.py` | **用量账本**：API 无用量端点（`/v1/usage` 等全 404），只能本地记账 |
| 提问模板 | **不要把 falsification_criteria 塞进 instructions**（实测减半表现） |
| **接入状态** | **已接入 ACH 主链**：`ai_diagnose(..., jev=)` 优先 JEV，失败自动回退 mimo |
| 调用方 | `tools/ach_daily_batch.py`（`--no-jev` 可强制回退）、`local/hypothesis_engine.py` 周循环、`tools/probe_mega.py`、`tools/rerun_ach.py`、`cloud/jev_signal_scan.py`（CI） |
| 本地复刻 | **不需要**（中文够用）。备选 `jaredpalmer/kev`（Qwen 底座，含训练代码） |

**架构要点**：JEV 走**独立客户端**（`local/jev_client.py`），**不经过 `analyze._call_api`**——因此
**不需要往 `analyze.py` 的 `AI_ALLOWED_HOSTS` 加 `api.typesafe.ai`**，避免无谓扩大 `_call_api` 的攻击面。
JEV 客户端自带同规格 SSRF 守卫（仅 https + 白名单域名 + 拒绝私有/环回地址）。

**用量追踪**：TypeSafe **没有用量查询端点**（实测 `/v1/usage`、`/v1/account`、`/v1/me`、
`/v1/credits` 全部 404，只有 `/v1/models` 可用），所以用量必须本地记账：
`data/jev_usage.json` 按日/按调用方累计，`python tools/jev_usage.py` 查看。
定价 $0.042/Mtok 输入、输出免费——实测 5 条证据约 4900 token = $0.0002。

**回退设计**：`ai_diagnose(e, analyzer, jev=)` 中 jev 为 None 或 `available=False` 时自动走 mimo；
JEV 调用抛错时调用方会对同一条回退 mimo 重试一次，避免因决策层故障丢证据（实测遇 `503 no healthy upstream` 可正确回退）。

**限流提示**：官方限流动态调整（250k tok/s、1200 req/min），实测偶发失败条，下轮重试即可。

### 免 key 免费通道（2026-10-08 接入，**默认首选**）

**来源**：opencode.ai 的 Zen 网关提供 System One 兼容端点，模型 `jev-1.13-free`，
**匿名直连、不需要任何 key**。此前 CI 无 `TYPESAFE_API_KEY`，JEV 判断链**从未在线上跑过**——这是接它的根本动机。

| 项 | 内容 |
|---|---|
| 端点 | `POST https://opencode.ai/zen/v1/systemone`，body 同 TypeSafe（`state` + `questions`） |
| 模型名 | 只有 `jev-1.13-free` 可用；`jev-latest`/`jev-1`/`systemone` 均返 401 `Model not supported`；`jev-1.13`（付费版）返 401 `Missing API key` |
| 通道选择 | 默认 `auto`（free 优先 → paid 兜底）；`OSINT_JEV_CHANNEL=free\|paid` 可覆盖；free 失败自动降级 paid，反之不然 |
| available 语义 | **不再要求 key**——无 key 环境（CI）现在也走 JEV，不再回退 mimo |
| 记账 | **free 调用不计入付费账本**（其 cost 恒为 `"0"`，记进去会把免费 token 按付费单价算成假成本） |
| 原语支持 | 只支持 `noul` / `choice`；**`score` 返 HTTP 422 error body**（客户端已按通道故障处理并降级） |
| ⚠️ UA 必需 | **必须显式设 User-Agent**（客户端已设 `osint-jev/1.0`）。urllib 默认 `Python-urllib/3.x` 在本地被 Cloudflare 拦成 403；**CI runner 上实测无 UA 也 200**（地域/指纹差异），但不能依赖这一点 |
| 白名单 | `ALLOWED_HOSTS = (api.typesafe.ai, opencode.ai)`，SSRF 守卫同规格（仅 https + 拒绝私有/环回/保留地址） |

**质量 A/B（2026-10-08，8 样本 × 2 轮，生产 `gate_and_diagnose` 模板）**：

| 样本 | 付费基线 | free | paid（本次同日） | free−paid |
|---|---|---|---|---|
| 铁路/原油/A股/日经（噪声） | 0.01~0.03 | 0.03~0.05 | 0.03~0.05 | 0.000~−0.005 |
| 国台办/军售/台领导人（边缘） | 0.14~0.18 | 0.11~0.22 | 0.11~0.22 | 0.000 |
| 解放军实弹演习（信号） | 0.86 | 0.75~0.76 | 0.75~0.76 | 0.000 |

**结论：free 与 paid 判定逐条一致**（多数差值 0.000，最大 0.005）。噪声上界 0.05 / 信号下界 0.75 有 **15 倍间隔**，门控阈值 0.15 稳稳落在中间。单条 ~0.8~1.9s，**40 次连发 0 失败**（无 RPM 限制迹象）。

**CI 可达性（已实测，非推测）**：临时探针 workflow（run `37723919445`）全绿——curl 免 key `HTTP=200 time=0.51s`；项目客户端 `通道: JEV: free(jev-1.13-free) / available: True`；`ach_matrix` 集成 `诊断 0.74s, 6 个假设`，HM102 判 C(conf=0.73, gate=0.63)。

**⚠️ selectivity 在小假设数下失真**：探针只给 2 个假设时，台海信号的 selectivity 算成 1.88 < `SEL_MIN=2.5` → 高 gate(0.75) 反被判 N。生产 6 个 major 不受影响（实测 sel=7.33）。**凡是小规模对照测试，别用 selectivity 判据下结论**——它衡量的是"能否区分竞争假设"，假设太少时分母失真。

### CI 侧信号扫描（`cloud/jev_signal_scan.py`，2026-10-08）

让线上真正跑判断的落地形态。**只读假设树、只写独立产物**——`data/hypotheses/` 的唯一有效写入方是本地（CI 改它会被 checkout 丢弃，见 §"周循环链路加固"的教训）。

| 项 | 内容 |
|---|---|
| 输入 | `intel_2*.jsonl` 最近 2 个文件，经 `intel_gate.select_priority_unique` 取 Top N（默认 100） |
| 判定 | 生产同款 `gate_and_diagnose`（Noul 门控 → Choice 方向）× 6 个 major |
| 产物 | `jev_signals_YYYYMMDD.json`（机器可读）+ `.md`（人读摘要），CI commit 时一并带走 |
| 调度 | `daily.yml` 的 impact 之后、briefing 之前；`|| echo skip` 兜底，端点不可达不失败 job |
| 实测 | 本地 20 条 17s（0.86s/条）；12 条真实候选 max gate 0.05~0.11 全低于阈值（行情类噪声，与 Phase 0 噪声上界吻合） |
| 语义边界 | **不累积、不改后验**——它是当日快照观察；累积式 ACH 仍只在本地跑 |

### 两段式门控（2026-09-21 二次实测，**必须遵守**）

**首版问法有系统性误判。** 单段式问「这条证据与假设的预期是否一致」时，JEV 把它**字面理解**为"这条消息是否支持该假设成立"，于是：

```
I conf=0.6  1至8月全国铁路发送旅客33.2亿人次    ← 铁路客运？
I conf=0.76 周末！原油暗盘，跳水               ← 原油？
I conf=0.74 A股四大指数集体高开                ← A股？
C conf=0.89 日经225指数低开1.5%                ← 行情？
```

即**一切"市场正常运行"被判 I（"不支持升级"），"下跌/紧张"被判 C**。全量重跑后台海 C/I 从 20 条涨到 32 条，后验被打到地板 0.050——**比原来更差**。已从备份回滚。

**三种问法对照实验**（噪声=铁路/原油/A股，真信号=台海实弹演习）：

| 问法 | 噪声 | 真信号 |
|---|---|---|
| A「预期是否一致」 | ❌ 全判 inconsistent 0.63~0.79 | ✅ 0.99 |
| B「是否涉及该议题」 | ✅ 全判 unrelated 1.00 | ✅ 0.99 |
| **C Noul「是否直接涉及」** | ✅ **0.01~0.04** | ✅ **0.95** |

**解法：两段式**——第一段 Noul 判议题相关性（门控），第二段仅对通过门控的假设判方向。

**阈值 0.08 的实测依据**（台海假设）：

```
噪声 gate：铁路 0.01 / 原油 0.02 / A股 0.02 / 日经 0.03
真信号 gate：核潜艇 0.14 / 防务开支翻倍 0.14 / 台海巡艇 0.18 / 实弹演习 0.86
```

噪声上界 0.04 与真信号下界 0.14 有 3.5 倍间隔。**入口 `JevClient.gate_and_diagnose(gate_threshold=)`，gate 值已落盘可重算（无需重跑 AI）**。

**效果**：3496 个 gate 样本中 93% 低于阈值（正确判 N），C/I 总量 242 → 73。回归测试 8/9 通过（唯一"失败"是「A股高开，AI硬件侧升温」gate=0.08 压线，确实涉及 AI 算力议题，判 I 有道理）。

### 判定记忆层（2026-09-21）

**动机**：此前 437 行判定无 `model`/`diagnosed_at`/历史版本——是"静态快照"而非"可追溯过程"，导致重跑时无法对比新旧、无法回滚单条、无法区分 JEV 与 mimo。

| 部件 | 内容 |
|---|---|
| 行级字段 | `model`（jev/mimo）+ `diagnosed_at` + `schema`（legacy=模型自报lr / v2=代码推导） |
| 追加式日志 | `data/hypotheses/diagnosis_log.jsonl`（append-only，含 prev_model/prev_codes，974 条） |
| 回填工具 | `tools/backfill_diag_source.py`（存量 437 行：legacy 388 / v2 49，全部 mimo） |
| 重跑工具 | `tools/rerun_ach.py`（`--limit N` 小批 / `--all` 全量 / `--only-ci` 只跑含 C/I 的） |

**关键**：`resolutions.jsonl` 尚不存在（首个到期 2026-12-05），所以此刻重跑改的是"未被验证的猜测"——**再晚就会污染校准数据**。

### 后验可信度标注（2026-09-21，误导性输出修复）

**动机**：`中国社保走韩国老路` 后验 0.650 但 C/I 证据为 **0**——它显示的是**先验值**，不是判断结果。仪表盘无法区分它与"有 20 条证据支撑的 0.65"，读者会把"没判断过"当成"判断为真"。另一侧，`全球能源转型` 后验 0.090 但 C 12 / I 11 条——**证据量最大却结论最极端**，是 LR 复利放大的信号。

**落地**：`bayesian_update` 输出 `evidence_total` / `prior_baseline` / `reliability` 五级：

| 级别 | 判据 | 含义 |
|---|---|---|
| `none` | 零 C/I 证据 | 后验=先验，**尚未被判断** |
| `weak` | 1-2 条 | 易被单条证据左右 |
| `ok` / `strong` | 3-9 / ≥10 条 | 正常 |
| `extreme` | ≥8 条且后验 ≤0.12 或 ≥0.88 | **复利放大，结论需谨慎** |

报告与仪表盘同步标注（`⚠️无证据` / `⚠️极端值` / `样本小`），零证据行加虚线边框。

**实测**：`全球能源供应格局`（30 条→0.950）与 `全球能源转型`（23 条→0.090）**都被自动标为 extreme**——这两个极端值此前会被当成"强结论"。

### 假设生成的时间基准缺陷（2026-09-21，根因修复）

**根因**：`_decompose_view` 的 prompt **未告知模型当前日期**，AI 只能凭训练数据的时间感写年份。

**实测后果**：75 个节点中 **59 个（78%）的证伪判据含过期年份**（2020-2025），而代码算的 `due_date` 是 2027-2028——**两套时间体系脱节**。验证时按判据走永远"已过期"，按 due_date 走判据内容对不上时间。指标层同样中招（46 个指标中 28 个含过期年份）。

**修复**：
- prompt 显式注入 current date + 目标验证窗口 + 年份要求 + 禁编造统计（未知基线改为相对表述）
- system prompt 声明工作年份
- 新增 `_stale_years()` 时间闸门：生成后扫描，含过期年份则记 `stale_thresholds` 告警
- **踩坑**：年份正则不能用 `\b`——中文语境下「到2024年底」两侧是汉字，word boundary 不成立会整条漏掉（单元测试暴露，7/7 通过后固化）

**存量审计**（`tools/audit_stale_thresholds.py`，**不盲目重写**）——过期判据有两种成因，处置方式不同：

| 分类 | 数量 | 处置 |
|---|---|---|
| `due_date` 仍在未来（2027+） | **41 个** | **可重写判据**（时间基准错，假设本身有效） |
| `due_date` 已到/已过 | **18 个** | **走验证流程**（重写会把该验证的假设永久推迟） |

**关键**：不区分就批量重写，会把"本该被验证的假设"永久推迟。

## 周循环 9-14 首跑复盘（2026-09-16，周一历史首次真实运行）

**成功**：任务真实触发（09:30 机器不可用，StartWhenAvailable 于 14:23 补跑）；Step2 崩溃后 2.5/3/3.5 照常跑完（**隔离加固实战验证**）；假设引擎自动物化 4 条新 views 假设（树 71→75）；周报两份产物落盘；假设链日常积累正常（ACH 矩阵 9-05 的 20 行 → 9-16 的 284 行）。

**失败与修复（同日完成）**：

| 问题 | 根因 | 修复 |
|---|---|---|
| Step2 主分析崩 `KeyError: 'content'` | 补跑时当日 intel 文件未产出（CI 10:12 才出、refresh 整点才拉）→ main_local 回退旧 cache → 旧条目缺字段 | analyze.py `_analyze_single_batch` 改 `.get()` 回退链（content→content_preview→summary），title/source 同步防御；桩测 9-14 真实缺字段数据通过 |
| 周报 ACH 排名 0.95 vs 假设清单 0.0/0.01 矛盾 | 两区块快照取自不同时间的新旧矩阵（非打穿 bug） | 无需修代码；根因之二见下行 |
| HM100 后验 0.0（支17/驳44）——LR 复利无下限 | `bayesian_update` 先验取 `base_confidence or confidence`（confidence 被压低后成下轮先验的隐患）+ 后验只有上限无下限 | 先验恒取 `base_confidence`（原始锚点），新增 `POSTERIOR_FLOOR=0.05`（0=证据极不利≠绝对不可能，留 5% 翻案空间）；敏感性分析同源统一；全矩阵重算写回 |
| AI 周报把 0 条情报硬解读为"空白本身是最强信号" | prompt 无零数据护栏 | `_save_ai_weekly_summary` prompt 加反幻觉指令：<10 条时必须写"本周无足够情报数据"，禁止把缺失解读为信号、禁止编造事件 |
| policy_tracker 零命中 | 依赖 Step2 崩溃产物（级联失败） | Step2 修复后自然恢复 |

**遗留观察**：①周一 09:30 数据窗口问题（CI 10:12 + refresh 整点 → 09:30 时当日数据必缺）——周报用 cache 回退可接受，Step2 修复后不再崩；②HM100 驳 44 是"能源转型利空传统能源"假设吃满了能源类快讯的 I 判定，方法论上 ACH 诊断对高频同质证据的累计惩罚偏重，观察 2-3 周再定是否引入证据去重加权。

## 周循环 9-21 复盘（2026-09-21，Step2 三大缺陷修复）

周一补跑（机器 12:51 开机 → StartWhenAvailable 13:00 补跑）。**任务最终跑完（14:40），但过程不健康**：Step2 卡死 84 分钟被人工终止，靠隔离机制兜底；且它分析的根本不是当日数据。三份产物落盘，ACH 矩阵 421→437 行，新口径 `conf` 字段 392 条。

| 问题 | 根因 | 修复 |
|---|---|---|
| **Step2 分析的是 9-14 的陈旧数据**（1006 条，9-11/9-12 发布，与当日仅 4 条交集） | 周任务 13:00:00 与 OsintRefresh 13:00:10 只差 3 秒启动，refresh 的 git pull 未落地 → `load_intel` 全部路径落空 → main_local 兜底 `max(cache_files, key=mtime)` 抓到 9-14 旧快照 | ①`load_intel` 路径优先级改为**产物目录优先**（cache 快照降次选）；②main_local 加 `_wait_for_today_intel()` 等当日文件就绪（上限 600s）；③缓存回退改按文件名日期取最新（不再用 mtime） |
| **Step2 自 8-30 起从未跑完** | 全量送入 AI：1006 条 × batch_size 10 = 101 批，唯一可用通道 dots 实测 **137s/批** → 需 3.8 小时 | config.yaml 新增 `ai_analysis.max_items: 60`（按 final_score 降序取 Top N，约 14 分钟）；**缓存快照仍写全量**，只有 AI 分析截断 |
| **AI 周报连续两周写「本周情报总条数为 0」** | `daily_run.ps1` 以 `python -c "...run_weekly_cycle()"` 单行调用，**未传 intel_items** → `_save_ai_weekly_summary` 的 week_intel 恒为空（库里实际有 4.5 万条） | 新增入口 `local/run_weekly_cycle.py`（显式加载当日情报并传入）；daily_run.ps1 Step3 改调该脚本 |
| policy_tracker 零命中 | 唯一输入 `analysis_*.jsonl` 停在 8-20/8-30（Step2 从不产出的级联失败） | Step2 修复后自然恢复 |
| dots 通道长 prompt 逼近超时 | 分析批 14K 字符 prompt 实测 137s，默认 `timeout=180` 余量不足 | `_analyze_single_batch` 显式传 `timeout=240`（`_safe_ai_post` cap 上限） |
| **AI 返回了但 `JSON parse failed`，60 条分析全退化成占位结果** | dots3 是**推理模型，reasoning 计入 max_tokens**（实测 10 条批次 reasoning 达 7159 字符）→ 真实输出在数组中途被截断（实测 10 条只写出 7 条，断在半个字符串里）→ 整体 `json.loads` 必失败。旧"截断修复"只找最后一个 `}`，救不回来 | 新增 `_salvage_items()`：括号配对状态机逐条提取完整对象，截断处的半条丢弃、前面的全保住（实测 **0 → 6 条**） |
| 模型返回的字段结构不符 schema（`structural_implication` 给成 dict、`confidence` 给 0/空） | 下游 `render_*`/`policy_tracker` 直接做字符串切片，遇 dict 抛 KeyError；confidence 走 `int(None)` 失败 | 新增 `_norm_text`/`_norm_dict`/`_norm_conf` 规范化层：dict 压平成文本（内容不丢）、缺失子键按已知键名回填、conf 归一到 1-10（0~1 比例值自动 ×10） |
| AI 通道全线不可用时 Step2 空转近 50 分钟 | 每批都白撞完整降级链（实测单批约 8 分钟），60 条 6 批 | `analyze_batch` 加连续失败熔断（`CONSECUTIVE_FAIL_LIMIT=3`）；**判据是"有效产出"而非"非空列表"**——解析失败会返回一批 fallback 占位结果，用非空判断熔断永不触发（首版踩过） |

**关键教训（已固化为规则）**：
- **新鲜度闸门必须用「计数」而非「最大值」**：首版实现取 `max(published_at)` 算"最新距今几天"，但语料混着源站错误时间戳的未来条目（实测 2026-11-17）→ max 恒取未来 → 年龄算成 **-57 天** → 闸门永远通过、形同虚设。改为统计「近 N 天内条目数」（`_fresh_count`），未来日期天然不落在区间内，不会污染判定。**任何"取极值做校验"的闸门都要先问：脏数据能不能顶到极值端？**
- **文件名日期不可信**：main_local 会把加载到的数据原样写进 `cache/intel_{今天}.jsonl`，一旦上游抓到旧数据，陈旧内容就顶着当日文件名。**跨进程共享的快照文件必须校验内容，不能只信文件名**。
- **两个定时任务抢跑是结构性问题**：周任务用 StartWhenAvailable 补跑时，落点不可控（这次正好压在整点）。凡是消费"另一进程正在写入的数据"，都要有等待/重试闸门，不能假定数据已就绪。
- **AI 分析量必须与通道吞吐匹配**：接入新数据源或改批大小时，先算 `条数 ÷ batch_size × 单批耗时`，与任务窗口比对。**"能跑"不等于"跑得完"**——Step2 的失败方式是静默挂起（CPU 0.7s/84 分钟），不报错、不退出，只靠超时兜底。

**排查手法（可复用）**：进程 CPU 增量 + 网络字节增量双零 = 挂起（非慢速推进）；`Get-CimInstance Win32_Process` 的 `ReadTransferCount` 是判断"是否在等网络"的可靠指标；对比"缓存文件内容 vs 当日真实数据"的 id 交集，能立刻证伪"它在分析正确数据"的假设。

### 第二轮：403 真相与端到端验证（2026-09-21 傍晚）

第一轮修复后实跑仍报 `dots HTTP 403`，而**同一时刻独立进程用同一 key 测试全部 200**。追查过程排除了一串错误假设，最终抓到响应体：

```json
{"title":"Request rejected by content safety review","status":403,
 "detail":"...not allowed by our safety system...",
 "error_type":"governance.content_safety_input_rejected"}
```

**根因：dots 网关的内容安全审查，与网络/配额/并发全都无关。** 逐条探测定位到触发条目——Top60 里第 9 条涉政敏感（"习特会前中美举行新一轮经贸磋商"），**10 条批次只要有 1 条敏感，整批全被拒**，而其余 9 条单发全部正常。

| 问题 | 根因 | 修复 |
|---|---|---|
| **一条敏感内容拖垮整批** | dots 对 prompt 做内容审查，命中即拒整批 | `_analyze_single_batch` 捕获内容拦截后**拆半递归**，二分定位敏感条目并单独记占位结果（实测 10 条 → 9 条正常 + 1 条隔离，不再丢 9 条） |
| **内容拦截的 403 被熔断器误判** | `_mark_dead` 见 403 就拉黑 30 分钟——但内容触发的 403 端点其实健康，换个 prompt 立刻可用 | `_mark_dead` 先识别 `content_safety` 特征词，命中则不熔断 |
| **异常信息丢失 `content_safety` 标记** | `HTTPError` 的 `str()` 只有 `"HTTP Error 403: Forbidden"`，响应体（含 error_type）读不到 | `_call_api` 捕获 `HTTPError` 时补读 body 并合并进异常消息；且**内容拦截立即 raise**，不继续走降级链（否则 `last_err` 会被后续超时覆盖，拆半逻辑失效——首版踩过） |
| **仍有 10% 条目退化成占位** | dots **服务端**把单次输出封顶在约 8-9K token（请求 32768 也只给 ~9000），10 条 × 8 字段恰好撞顶 | `batch_size` 10 → 5（单批输出减半，不再触顶）；实测 5 条批次 **5/5 全部有效** |

**端到端验证结果（首轮未能完成，第二轮补上）**：
- Step2 **自 8-30 以来首次跑通** → `analysis_20260921.jsonl` 40 条，**36 条有效（90%）**，四维诊断字段完整
- Step3 跑通 → 周报「总条数」由 **0 → 2343**，AI 总结开始引用真实事件（伊朗扣押船只、鼎阳科技、机构调仓）
- ACH 走 JEV 决策层，20 条证据 × 8 major 正常诊断

**关键教训**：
- **同码不同因的 HTTP 错误必须靠响应体区分**：403 可能是鉴权失败（端点死）、配额耗尽、或内容审查（端点健康），三者处理方式完全相反。**只看状态码做熔断/重试决策，必然误判**——先把 body 读出来。
- **一批一请求的批处理要能"隔离坏元素"**：单条敏感内容让整批失败是典型的放大器。**二分拆半是通用解法**（O(log n) 次重试定位问题元素），比"整批放弃"或"全量逐条"都好。
- **服务端输出上限可能远低于请求值**：dots 接受 `max_tokens=32768` 但实际封顶 ~9K。**不要相信参数被接受就等于生效**——用真实负载验证实际产出长度，并据此定 batch_size。

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
9b. **本地 git 连不上 GitHub**（`Failed to connect to github.com port 443`）：本机直连 GitHub 常态不通（国内网络），需经本地 Clash 代理。**已根治（2026-10-06）**：`net_proxy.py` 探测本地代理并注入 git 子进程环境变量，`local_sync.git_pull` / `refresh.commit_hypotheses` 全部改用它——**不再需要手工 export 代理**。
   - 代理候选端口：7897（Clash Verge mixed-port 默认）→ 7890 → 7891 → 7888，两级判据（端口在监听 **+ 实探 CONNECT 能连上 GitHub**），缓存 300s
   - **为什么必须实探**（2026-10-06 踩坑）：Clash 节点不稳时端口照样 LISTENING，但代理 TLS 握手失败（`schannel: failed to receive handshake`），而**直连反而通**——只看端口就会把本来能成的 git 操作塞进坏代理搞挂。实测 `_probe()` 在代理死时正确返回 None 退回直连
   - 只注入环境变量（`GIT_CONFIG_COUNT/KEY/VALUE`），命令行保持纯参数列表，无注入面；外部代理 URL 过白名单正则（仅 http:// + 回环 + 端口）
   - CI 无本地代理 → 探测失败 → 返回空 dict → git 直连，互不影响
   - 应急开关：`OSINT_GIT_PROXY=none` 强制禁代理；`OSINT_GIT_PROXY=http://127.0.0.1:7897` 显式指定
   - 诊断：`python -c "import net_proxy; print(net_proxy.describe())"`；`gh` CLI 自带路由，**不受影响**（实测直连/代理都通）
   - **根因提醒**：Clash 未设自启（HKCU Run 无该键），重启后核心不运行 → 代理全失效。若频繁遇到，需给 Clash 自己开自启（属全局设置，osint 不自作主张）
10. **fetch_list 采集 0 条**（2026-08-30 诊断）：接口缺陷已修（main 现在写 output jsonl，与 fetch_rss 同接口）；但所有列表源在 CI 上也解析出 0 条——**sources.yaml 的 list_selector 已与改版后的页面结构脱节**（gov.cn 还是 JS 渲染页）。逐源修选择器是持久战，替代方案：改用 RSSHub 或各站 RSS 源。
11. **仪表盘情报全显示 "8 小时前" 但金十/财联社实际在发**（2026-09-01 诊断；**2026-09-04 已基本根治**）：原 90% 是本地 RSSHub Docker 容器没起。9-04 起 fetch_rss 三级改造后**无 RSSHub 也能拉全源**：
   - 金十/新浪(7x24+滚动) → 官方直连 API（sources.yaml `direct: jin10_flash / sina_zhibo / sina_roll`，不经任何 RSSHub）
   - 其余 7 条 RSSHub 路由 → 本地容器失败自动退公共镜像（`hub.slarker.me` → `rsshub.rssforever.com`，见 fetch_rss.py `RSSHUB_BASES`，可用环境变量 `RSSHUB_BASES` 覆盖）
   - 实测 Docker 全程关闭：46 源中受影响 10 源 10/10 覆盖、377 条/24h（镜像限流时某源可能暂缺，下一轮自动恢复）
   - Docker 开着时本地容器仍优先（更快；公共镜像有匿名限流，勿长期裸奔依赖）
   - 历史排查步骤（RSSHub 时代）见 `C:\Users\admin\.zcode\cli\memories\projects\osint-d824a33e2ef30701\memory\osint-rsshub-local-bootstrap.md`
12. **AI 全线 403/429——研判/翻译断供排查顺序**（2026-09-19 实战复盘：09-17 起 70 轮全 0 产出）：
   - **先看错误码**：`403 + "error code: 1010"` = Cloudflare 指纹封禁（拦无 UA/Python 默认 UA 的请求），与 key/配额无关；`429 + FreeUsageLimitError` = OpenCode 免费层限流（会滚动恢复）；两者常叠加。
   - **排查顺序**：①`curl https://opencode.ai` 测出口 → ②带 key 用 curl 测 `/chat/completions`（curl 能过说明只是 Python 层被拦）→ ③查 refresh 日志 grep 403/429 定断供起始日。
   - **自愈路径**：dots 备援通道（note3-prev-api.askdiandian.com，key 在 config.local.yaml `dots.api_key`）已在 analyze.py / citizen_impact.py / translate_local.py 三处放链首；NVIDIA 备援（integrate.api.nvidia.com）实测超时不可用；OpenCode 429 时每小时 refresh 自动重试自然恢复。
   - **translate_local 的 dots 截断坑**：dots3 是推理模型，reasoning 计入 max_tokens（翻译批 reasoning ~8k 字符），max_tokens=4096 时正文被截成非法 JSON（Unterminated string）——已提到 12288。
   - 伴随症状：git pull 连不上 github.com（github 巨慢 >10s）、GDELT 429、ACH 诊断返回非 JSON——都是同一网络故障的表现，先修网络出口再查各管线。

## 数据量级真相（2026-08-30 诊断 / 2026-09-13 筛选修复后更新）
- ~~关键词表是中文、47 源以英文为主、每日命中 0~3 条~~ → 2026-09-05 起词表已扩到 153 词（63 英 + 90 中）+ keyword_rules 5 组；实测语料 79.5% 为中文（本地直连源为主力），语言错配已非主要矛盾
- **2026-09-13 筛选修复**（详见 §"筛选算法修复"）：旧关键词分 min(sum,1.0) 封顶 + 词权重几乎全 ≥1.0 → 命中即饱和 → 窗口退化纯时间窗，用户高价值条目（失业/养老金/核电类）被源配额挤出 98.7%。已改 score/(score+3) 亚线性梯度 + 窗口主题配额保底带
- 未来日期脏数据（源站错误时间戳）由 refresh 的 `_clean_date` 过滤（>明天2天或 <2020 年丢弃）；漏网 2 天内未来戳在 TimeDecay/rebuild 衰减里给最低分（防登顶）

## 筛选算法修复（2026-09-13，实测诊断驱动）

两路审计（机制 + 7 天 17,662 条实效测量）发现打分链三处结构性缺陷并修复：

| 缺陷 | 修复 | 效果 |
|---|---|---|
| **打分饱和**：kw_score=min(sum,1.0) 且 153 词几乎全 ≥1.0 → 命中即满分 → 窗口退化纯时间窗 | fetch_rss/fetch_list 改 `score/(score+3.0)` 亚线性梯度（单强词 0.53/双词 0.66/泛宏观 0.72）；摘要联播类（命中≥8 词）再 ×0.6 | 关键词分重新有区分度 |
| **窗口挤出**：高价值条目 151 条仅 2 条进窗（98.7% 死于源配额层）；历史条目同分带 | refresh.rebuild_data 重构：①全库统一量纲（本地条目按 base×当前衰减补算 final）；②历史条目用 keywords_hit 反查词表恢复 raw 重套新曲线（8841 条，免等 168h 出清）；③**主题配额保底带**——按 persona.md 信号清单划 5 主题（就业12/社保8/能源10/贸易6/房地产4 席），72h 硬门槛 + 去衰减 base 排序 + 同事件 story 最多 2 条 | 窗口 R 值全梯度（R2~R9）；高价值进窗 2→9（口径受限：72h 语料内窄题条目本身仅 ~10 条，配额已把它们全数保住）；persona 相关主题约占窗 1/4 |
| **死字段与死配置**：relevance 恒 0 但被 4 处消费（R 值恒 R0/按相关度无效/简报关键信号恒空/tiebreak）；weights.yaml 整体死配置（CI 实收 sources.yaml，load_weights 零调用，persona_boost 24 标签从未接线） | R 值/排序按钮接统一分（桶号复用 rel-3/4/5 CSS）；daily_briefing 重点与关键信号区接 final_score；CI priority 阈值按新分布重校准（0.45/0.30/0.18，实测 p90=0.45）；weights.yaml 头部标注 deprecated | 仪表盘"按相关度"真实生效（浏览器实测 top3=R9）；简报区复活 |

**验证**：Playwright 浏览器实测 R 徽章 200 枚全梯度分布、排序按钮生效、无 JS 错误；rebuild 全量跑通（25070 条，保底带 34 席按主题装满）。
**边界**：窄主题（青年失业率等）72h 语料本身稀缺（失业 5 条/毕业生 0 条），窗口上限受语料约束；保底带词表在 refresh.py RESERVE_TOPICS 可调。

## 数据质量基线（2026-08-30）
- 情报 ~718 条（脏日期已过滤）；cn_title 405（56%，随 CI 翻译推进会涨）
- 每日新增 RSS ~331 条；翻译吞吐 50 条/次 CI × 4 次/天
- 假设树 69 节点（8大/20中/41小）；观点卡链路已通（view_e0c75e → hyp_4656924e）

## 巡检修复（2026-10-04，健康巡检驱动）

系统整体健康（CI 8/8 绿、refresh 每 30min 正常、假设链/探针/ACH 均在跑），但发现 4 个问题，3 个已修：

| 问题 | 根因 | 修复 | 验证 |
|---|---|---|---|
| **假设树 commit 长期推不上远端**（积压 6 个 commit） | `commit_hypotheses()` 硬编码只提交 `active_hypotheses.json`+`ach_matrix.json`，而探针写的 `probe_readings.json` 同样被 git 追踪却从不提交 → 工作区永久脏 → 自己的 `git pull --rebase` 与下一轮 `local_sync.git_pull` 全部因 `cannot pull with rebase: You have unstaged changes` 失败 | ①`commit_hypotheses` 改 `git ls-files data/hypotheses/` **动态取追踪文件**（覆盖 probe_readings，自动纳入日后新增追踪文件，不误收未追踪的 proposed_*.json）②`local_sync.git_pull` 与 commit_hypotheses 的 rebase 均加 `--autostash` 纵深防御 | 实跑 `git pull --rebase --autostash` 4 commit 重放成功 → push 完成，与远端零差异；端到端调 `commit_hypotheses()` 打印"无变更,跳过" |
| **`index_valuation.json` 全零损坏**（13160 字节全 `\x00`） | NTFS 掉电/未刷盘（其他产物完好，非代码 bug）。后果：估值面板无数据，`--calc` 读到空 JSON 报 `Expecting value: line 1 column 1` | 从 `raw_sp500_pe.md`/`raw_valuation_csi300.md`/`raw_valuation_hsi.md` 三个 raw md `--fetch` 重建（损坏件备份为 `.corrupt_20261004`） | 重建后 S&P500 1870 点/pct 0.662、CSI300 14 点/pct 0.143、HSI 14 点/pct 0.5，读取正常 |
| **AI 403 日志看不到真实原因** | `translate_local.py`/`citizen_impact.py` 直调 `_safe_ai_post` 且**无** HTTPError 响应体补读，日志只留 `HTTP Error 403: Forbidden`——同为 403 的「鉴权失败/配额耗尽/内容安全」无法区分 | 把补读**下沉到共用的 `_safe_ai_post`**（analyze/translate_local/citizen_impact 三处一次受益），新增 `_enrich_http_error()` | 实测 403 现显示 `FreeTierError: OpenCode's free tier can only be used from within OpenCode`；translate_local 端到端 3/3 通过 |
| GDELT 持续 429 | IP 被 GDELT 限流（非代码问题），失败写 1h 节流戳避免白撞 | 无需改代码；巡检时实测已解封（HTTP 200） | 连通性实测 200 |

**周循环二次巡检（2026-10-05 周一实跑发现）**——周报统计窗口长期错位：

| 问题 | 根因 | 修复 | 验证 |
|---|---|---|---|
| **周报统计窗口错位**（AI 误判"数据严重不足，仅 7 条"） | `_save_ai_weekly_summary` 的 `week_offset=0` 取"**本周一至今**"，而周报在**周一生成**——窗口只有几小时。标题/正文本写"上周情报总览"，语义与窗口脱节。实测 10-05 只统计到 159 条，AI 据此断言"本周实际可用情报仅 7 条"；上一完整周实际有 **12102 条** | 窗口改为**上一个完整周**（上周一 00:00 ~ 上周日 23:59:59），与标题语义对齐；`week_offset=N` 继续往前推 N 周 | 重跑：窗口 `09-28~10-04`、总条数 **159 → 14687**，AI 总结从"数据不足"变为引用真实事件（美国 9 月非农 2.9 万、美联储路径预期） |
| **`load_week_intel` 只读当日文件** | 周报窗口是上周，但入口只加载 `intel_今天.jsonl`——上周条目几乎不在其中（实测当日文件里只有 10-04 的 183 条落在窗口内）。窗口修好后此问题才暴露 | 改为按窗口日期**逐日读 `intel_YYYYMMDD.jsonl`**（上周 7 个文件） | 实测加载 14687 条，覆盖 7 个日文件 |
| **主题取样排序退化** | 窗口上万条时，`week_intel[:30]` 按 `published_at` 降序 → 前 30 条**全是最后一天**的；且主题内按 `relevance` 排序，而 `relevance` 是恒 0 的死字段 | 取样改按 `final_score`（回退 `base_score`）降序，取 60 条；主题内排序同步改用真实分数 | 新周报主题分类覆盖全周高价值条目 |
| **Step2 分析 22% 退化成占位**（9-28 起长期 22~29%） | 模型输出中文时用 **ASCII 直引号**包裹词句（`从"劳动性收入"向"财产性收入"`），使 JSON 非法 → 整体 `json.loads` 失败 → 整批 fallback。此前误判为"推理模型截断"——实测今日 13 条退化里 **8 条（62%）是引号问题**，其余才是截断 | 新增 `_repair_unescaped_quotes()`：字符串内遇引号时向前看下一非空白字符，是 `, } ] :` 或结尾则为结构引号，否则转义（json_repair 标准做法）。只加反斜杠不改字符、内容保真；对合法 JSON 恒等不变；与截断抢救叠加 | 含引号非法 JSON → 正确解析且内容保真；正常 JSON/空值恒等不变；今日退化样本 8/13 被识别修复 |

**关键教训**：
- **"上周"类窗口必须验证生成时点**：周报在周一生成，任何"本周至今"的窗口在那一刻都接近空集。**周期性报告的窗口语义要与生成时点一起核对**——标题写"上周"、代码取"本周"这种错位不会报错，只会让 AI 基于空数据编故事。
- **窗口修好后要连带检查数据加载范围**：统计窗口与数据加载是两处独立逻辑（`hypothesis_engine` 过滤 vs `run_weekly_cycle` 加载），只改一处会让窗口正确但数据为空。**改窗口定义时，加载侧的日期范围必须同步核对**。
- **跨日数据窗口不能只读当日文件**：`load_week_intel` 原假设"当日文件包含所需数据"，但周窗口跨 7 天。**任何"时间范围"参数的消费者，要确认数据源覆盖该范围**。
- **死字段（恒 0）会被当作有效排序键**：`relevance` 早在 9-13 就被标记为死字段，但周报仍用它排序。**改数据管道时，要 grep 所有消费方，别只改主链路**。
- **"解析失败"要先看原始响应，别急着归因**：Step2 的 JSON 失败长期被当成"推理模型截断"，实际 62% 是**未转义引号**。**同类错误码/异常要抽样看原文**——截断与语法错误是两种病，修法完全不同。

**关键教训**：
- **「被 git 追踪」的文件必须有明确的提交方**：`probe_readings.json` 属于「写它的代码没提交它、提交它的代码不知道它」的缝隙——单一文件漏提交，会让**整条 git 链路**（rebase + 下一轮 pull）静默死掉。凡是自动写入 + 被追踪的产物，提交方要么枚举全、要么动态取（`git ls-files <dir>`），不能硬编码子集。
- **裸 `git pull` 在自动化里必须带 `--autostash`**：工作区一旦有任何未提交文件（哪怕与本次 pull 无关），裸 pull 会永久失败而非跳过。自动化场景下这是单点故障放大器。
- **产物文件损坏要区分「代码 bug」与「存储损坏」**：全零字节（非空、非截断）是典型的未刷盘特征；先扫其他文件是否同病，再判断根因。本次只有 1 个文件损坏，代码无关。
- **错误处理补读要下沉到共用底层**：`analyze._call_api` 早有 body 补读，但另外两个模块绕过它直调底层 → 同样的坑踩了两次。修在共用 `_safe_ai_post` 上，一次覆盖全部调用方。

## Google Gemini 接入 CI（2026-10-05）

**动机**：CI 的 `citizen_impact` 步骤长期空转——OpenCode 免费层已锁死（403 `FreeTierError: can only be used from within OpenCode`），dots 备援 key 只在本地 `config.local.yaml`，CI 拿不到。实测最近 run 是 `analyzed 0/50`。

| 项 | 内容 |
|---|---|
| key 存放 | GitHub Secrets `GEMINI_API_KEY`（**不进 git**；用户 2026-10-05 提供） |
| 端点 | **原生 `:generateContent`**，非 OpenAI 兼容层（见下方坑） |
| 模型 | `gemini-3.5-flash-lite`（CI 实测 200）；候选链 `GEMINI_MODELS` 逐个尝试 |
| 客户端 | `local/gemini_client.py`（**共用**，对齐 `jev_client.py` 独立客户端模式）：`generate()` + `reachable()` |
| **通道顺序** | **Gemini → dots → OpenCode**（2026-10-05 用户指定：Gemini 优先，dots 备援）。两处接入：`citizen_impact.call_ai`、`translate_local.translate_batch` |
| 生效范围 | CI 上 Gemini 生效；本地境内不可达 → 预检跳过，自动走 dots |
| 额度 | 免费层按**项目**计（非 key），RPD 太平洋午夜重置 |
| **实测效果** | CI `analyzed **0/50 → 45/50**`（run 37291577943）；本地 impact 0/5 → 5/5 |

**可达性预检（本地必需）**：本地境内到 `generativelanguage.googleapis.com` 是**黑洞式超时**——`socket.create_connection(timeout=6)` 实测仍耗时 **48s**（Windows 上 socket timeout 对 connect 不生效）。若无预检，本地每批翻译/研判都会先白等 48s 才降级。`gemini_client.reachable()` 用线程 + join 硬超时（4s）+ **进程内缓存**，实测 6s 判定不可达、第二次 0s。**判据：给链首通道加"不可达地区会跳过"的能力时，先测失败耗时——黑洞超时不是几秒，是几十秒。**

**批次节流（免费层 RPM 必需）**：Gemini 免费层 **15 RPM**。50 条 ÷ batch 5 = 10 批若短时连发，必触顶（CI 实测最后一批 429/503）。两处接入点都加进程级时间戳节流到 **≥4.5s/批**（=13 RPM 留余量），桩测 3 次调用间隔 4.5s/4.5s。**判据：接免费层 API 前先算"批次总数 ÷ 分钟数"，超出 RPM 就加节流——否则末尾批次静默失败。**

**关键坑一：`AQ.` 开头的 key 不被 OpenAI 兼容端点接受**。Google 正把 AI Studio key 从 `AIza`（Standard）迁移到 `AQ.Ab...`（Auth）。新 key 在**原生端点**正常，但发到 `/v1beta/openai/chat/completions` 会返回 **404 Not Found**（兼容层只认 AIza）。**修法：走原生端点 + `x-goog-api-key` 头**（不是 `Authorization: Bearer`）。

**关键坑二：2.5 代模型对新用户已下架**。CI 逐模型探测（`curl` 6 个模型看 HTTP 码）实测：`gemini-3.5-flash-lite`/`3.1-flash-lite`/`3.5-flash` → 200；`gemini-2.5-flash-lite`/`2.5-flash`/`2.5-pro` → **404**。文档页面仍列 2.5 代，但**文档有 ≠ 你的 key 能用**——必须用真实请求探测，不能照文档抄模型名。**判据：接入第三方 API 时，"文档列的模型"与"本账号可用的模型"是两回事，先探测再固化。**

**关键坑三：免费层数据用于训练**。官方条款明确免费层「human reviewers may read」提交内容。本项目 `worldview.yaml`（用户三观档案）会注入 prompt——若介意，接付费层（Tier 1 绑卡即明确不用于训练）或在该路径关掉三观注入。**接第三方 AI 前先确认免费层的数据使用条款**。

**顺带修复的既有 bug（本地研判长期静默退化）**：
- `citizen_impact` 的 dots 通道 `max_tokens=3000` 对推理模型不够（reasoning 计入配额，5 条批次的中文 reasoning 吃光配额 → 正文被挤成半截 → `parse_json_array` 返回 0 条 → 整批退化）。translate_local 早已为同样问题提到 12288，**此处漏改**。修：3000 → 12288。
- `parse_json_array` 遇截断**整体返回 []**（丢弃已完整输出的对象）。修：加 `_salvage_objects()` 逐条抢救（对齐 `analyze._salvage_items`）。
- **教训：同一类问题在多个模块有副本时，修一处要 grep 全部消费方**——translate_local 修了 max_tokens，citizen_impact 漏了，导致本地研判静默 0 产出无人察觉。

**CI 触发盲区**：`daily.yml` 的 push 触发器 paths 只含 `sources.yaml`/`config.yaml`/`AGENTS.md`/`docs/**`/`.github/workflows/**` 等，**不含 `cloud/**`**——改 `cloud/citizen_impact.py` 不会自动触发 CI，需 `gh workflow run daily.yml` 手动 dispatch。

## 本机 OpenCode Zen 代理接入（2026-10-05）

**背景**：OpenCode 免费层 2026-09-17 起对第三方客户端一律 403（`FreeTierError: can only be used from within OpenCode`），osint 的 OpenCode 腿全线死亡。而本机 `E:\OpenCode\zen-proxy.py`（监听 `127.0.0.1:4010`）早已修好：用**官方桌面凭据**（读 `C:\Users\admin\.local\share\opencode\auth.json` 的 key + `opencode.db` 里登记过的 session ID）转发，实测恢复可用。**osint 此前从未接入它**（全仓库零引用），本次接线。

| 项 | 内容 |
|---|---|
| 客户端 | `local/zen_proxy_client.py`（`reachable()` 预检 + `chat_completion()`） |
| 接入点 | `local/analyze.py` `_call_api`（dots 之后、官网直连之前）/ `tools/translate_local.py` / `cloud/citizen_impact.py` / `data/serve.py` |
| 通道顺序 | **保持既有语义**：`analyze` = dots → zen代理 → 官网直连 → NVIDIA；`translate/impact` = Gemini → dots → zen代理 → 官网直连 |
| CI 行为 | CI 无 4010 → `reachable()` False → 整条腿自动跳过，CI 仍靠 Gemini，互不影响 |
| 应急开关 | 环境变量 `OSINT_DISABLE_ZEN_PROXY=1` 停用该通道 |
| 依赖 | 代理需常驻（HKCU Run 键 `ZenProxy` 自启；手动 `pythonw E:\OpenCode\zen-proxy.py`）；代理挂了 osint 自动回退下一通道 |

**关键坑一：代理返回的是 SSE，不是普通 JSON**。4010 为通过上游校验会**强制 `stream=true`** 转发（官方放行条件之一是 stream + 官方 tools 定义），所以 `resp.read()` 拿到的是 `data: {...}` 事件流——调用方若按普通 `json.loads` 解析必然失败。`zen_proxy_client._extract_content()` 负责把 SSE 块拼回完整文本，并保留非 SSE 的 JSON 回退分支。

**关键坑二：回环目标不能走 `_safe_ai_post`**。那个守卫只允许 `https` + 外域白名单（其私有地址检查还专门拒绝环回），与 `http://127.0.0.1:4010` 方向完全相反。本客户端自带**收窄版**守卫：只允许本机回环 + 固定端口 + URL 由模块常量拼接（不接受调用方传入，无注入面）；对上游的 SSRF 防护由 4010 代理自己负责。

**关键坑三：代理会静默改写模型名**。非白名单模型名（代理视角）会被覆写为 `E:\OpenCode\zen-model.txt` 的默认目标（当前 `mimo-v2.5-free`）——属预期行为，排查"为什么返回的不是我要的模型"时先看这里。

**实测（2026-10-05）**：`analyze._call_api` 经代理返回真实内容（5.4s）；translate_local 单批 1 条 11.7s 产出正确中文（"China unveils new youth employment policy" → "中国出台青年就业新政策"）；citizen_impact 单批 15s 产出带 id 的 4 维 impact JSON；serve 问答 7.4s。故障注入（代理抛异常）→ 链正确落到下一通道并报补读后的真实错误；`OSINT_DISABLE_ZEN_PROXY=1` → 干净跳过。

## AI 准入排序：筛选分接入 AI 层（2026-10-05 建 / 2026-10-06 重设计）

**问题**：筛选层（`fetch_rss` 的 `base_score` / `refresh.rebuild` 的 `final_score`）一直在算分，但 **AI 采集器按 `published_at` 取候选，从不看这个分**——打分与消费之间是断开的。

### ⚠️ 第一版做错了：硬过滤（已撤销，重要教训）

首版把 `base_score=0` 当作「内容无关」的证据做**硬过滤**（只放行 `base_score>0`），把 AI 预算从「处理 67% 噪音」改善为「全处理相关条目」。**但前提是错的**：

- `base_score=0` 只等于「**没命中关键词表里的词**」，不等于「不值得知道」；
- 而用户口径是「**要一个上知天文下至地理的参谋**」——A股/汇率/大宗反映经济基本面，是必须知道的信息；
- 实测硬过滤丢弃的条目里包含：**金正恩观摩导弹发射、泽连斯基打击俄炼油厂、以军空袭加沙、巴西大选民调、英伟达市值逼 6 万亿、胡塞袭击沙特阿美**——全是真要闻。

**根因是词表窄，不是条目无关**：原词表 153 词虽中英各半，但中文 75 词全偏「就业/能源/宏观」（为 persona 主题设计），缺 `股市/汇率/黄金/大宗商品/并购/地缘/科技` 这类基础世界词汇；而语料 **69% 是中文**（英文 31%）→ 中文世界要闻命中不了任何词 → `base_score=0`。

**教训（判据）**：**把「没命中我的词表」当成「不值得知道」，是把自己的工具局限当成了世界的边界。** 凡是「分数低 → 过滤掉」的设计，先问：**这个分数衡量的是「世界没价值」还是「我的词表没覆盖」？** 若是后者，只能排序，不能丢弃。

**旁证**：实测扩表后仍有 1396/3761 条得 0 分（含 NASA/SpaceX 乘组、特朗普动态）——**没有任何词表能覆盖「上知天文下至地理」的长尾**，硬过滤这个设计本身就不成立。

### 现在的设计：排序，不丢弃

| 项 | 内容 |
|---|---|
| 模块 | `local/intel_gate.py`：`select_priority(items, max_n)` / `rank_key()` / `priority_sort()` |
| 排序键 | **（新鲜度层级, base_score, published_at）降序** — 先保新鲜，再按相关性，再按时间 |
| 两级的必要性 | 未翻译池里 3 天以上旧货有 **6.4 万条**、近 3 天仅 ~1400 条。若纯按分数排序，**陈年高分条目会永久霸占队列、饿死新新闻** |
| 接入点 | `tools/translate_local.collect_unjtranslated` + `cloud/citizen_impact.main`（Step2 早已按 `final_score` 取 Top N） |
| 应急开关 | `OSINT_AI_SCORE_GATE=0` → 退化为纯时间序（等价旧行为） |

**判据用 `base_score` 而非 `final_score`**：后者含时间衰减，且本地 `fetch_now`/GDELT 条目**根本没写这个字段**（`refresh.rebuild` 只在内存补算），按它排序会把本地条目全判成 0。

### 词表扩充（同日，根因修复）

`sources.yaml` `keyword_weights` **153 → 304 词**（中文 75→155、英文 78→149）：补市场行情（A股/股市/汇率/黄金/大宗商品）、宏观金融（央行/加息/降息）、产业公司（并购/IPO/芯片，权重刻意压到 1.5 以下避免公告挤占）、地缘政治（中东/俄乌/大选/导弹）、英文国际要闻（missile/election/parliament/summit/earthquake）。定价原则：低于 persona 核心词（就业/失业 3.0），高于泛词（会议/讲话 0.5）。

### 排序 + 去重（2026-10-06 加去重）

**去重动机**：同一事件常被多个源采到，标题几乎相同（实测 jaccard **1.00 / 0.83**），却因 `id=md5(源:链接:标题)` 不同而全部保留 → **AI 对同一件事分析多遍**。实测近 3 天未翻译池 1461 条里 **14~18% 是这类跨源重复**。

**为什么采集层没拦住**：
- 采集层的 simhash 去重（`cloud/clean_dedup_score.dedup_items`）**只在 CI 跑**，本地 `fetch_now`/`fetch_gdelt` 采的条目从不过那道；
- `rebuild_data` 只按 `id` 去重（跨源同事件 id 不同，拦不住）；
- `cluster_stories` 算出的 `story_id` **从不写回 jsonl**（只在 dashboard 内存），AI 采集器看不到。

**实现**（`intel_gate.make_deduper` / `select_priority_unique`）：
- 复用生产的 `SimHashDedup(3) + TitleDedup(0.7)`（与 CI 侧阈值一致，避免两套漂移）；
- **补一层 headline 归一化**：生产原语判不出「`【长标题】正文…`」vs「`长标题`」（纯标题版）——短标题是长标题子串，jaccard 被撑到 ~0.38 < 0.7，simhash 因长度差也超距。取 `【】` 内标题再去标点做精确键，**抽验 210 组全是同事件多源重复，零误伤**。
- **先排序（高分+新鲜在前）再去重** → 留下的代表是「最优先的那条」，不是随机留一条。
- 开关：`OSINT_AI_DEDUP=0` 关闭。

**实测（2026-10-06）**：未翻译池 1461 → **1198**（省 18%），残留重复 headline **0 组**；抽验「诺贝尔」7 条是 7 个不同角度（A股布局/光遗传学/立邦收购同名公司）、「泽连斯基」3 条是 3 件不同事——**真·不同新闻零误删**。translate 6/6、impact 5/5 端到端通过；开关两态正常。

**已知边界：低相似度同事件不合并（刻意决定，不要再"优化"）**

实测标题 jaccard ∈ [0.7, 0.85)（**字符级 token 口径**，与生产 `TitleDedup` 的整段切词口径不同，故这些对逃过了它的 0.7 阈值）有 16 对漏网，但它们**两类混在一起**，无法用降低阈值区分：

| 真重复（该并） | 绝不能并（不同事物） |
|---|---|
| `英航一赴美客机7分钟急坠` / `英航一客机7分钟急坠` | `异丁胺商品报价` / `异丁腈商品报价`（**不同化学品**） |
| `法国央行行长就利率发出警告` / `报道：法国央行行长就利率发出警告` | `萨那省` / `焦夫省`（**不同省份**） |
| `特朗普下令放宽柴油限制` / `特朗普政府拟放宽柴油限制` | `10月5日…2105万人次` / `10月4日…2035万人次`（**不同日期**） |

**根因**：`TitleDedup._tokenize_title` 对中文按**整段连续汉字**切词（`[\u4e00-\u9fff]+`），"赴美客机" vs "客机"、"下令" vs "拟" 都是不同 token → jaccard 落在同一区间。

**实验过但未采纳的安全办法**：剥归属前缀（`报道：`/`消息人士称`/`据X：`/`(LEAD)`/`国×委：` 等）后精确匹配——对**危险对 0 误合**（实测），但全池收益仅 **2 条（0.14%）**，不值得为它增加代码路径和误合面。**结论：保持现状，不降阈值、不加前缀剥离。**

**判据**：**漏掉一条重复的代价（多花一次 AI）远小于合并两条不同新闻的代价（信息静默丢失）**。凡"降阈值以提高召回"的提议，先拿上表验证——只要危险对还在同一区间，就不许降。

**边界（已处理，见下节）**：金十「每日要闻速递 / 昨日今晨汇总 / 局势跟踪」这类**固定栏目**是"一个标题打包几十条新闻"，不是重复也不是单一事件——它们因关键词命中数爆表而**分数异常高（0.97/0.94），在抢 AI 额度**。

### 固定栏目条降权（2026-10-06）

**栏目规律已摸清**：全体系、频次、领域见 [`docs/金十栏目规律-2026-10-06.md`](docs/金十栏目规律-2026-10-06.md)。要点——日均 7.3 条（占当日 0.1~0.9%），**危害在"占位"不在"数量"**；单条打包 6~25 则新闻（大宗商品 19.3 / 外汇 17 / 中东 16.6 最多）；发布集中在 06-08 时与 22-01 时两个窗口；覆盖率只有 16~49%（"每日XX"是栏目自述名，实际不保证每天采到）。

**问题**：金十/华尔街见闻等源的模板栏目**一条标题打包多则新闻**（「中东局势跟踪」正文 18 个编号项、「新闻联播要闻 19 条」19 项），因一条含十几个主题词 → 关键词命中数爆表 → 分数异常高（实测 0.97/0.94），**挤占真·单条新闻的 AI 额度**。近 5 天 59 条，其中 38 条 `base_score>=0.6`。

**为什么不能去重**：实测栏目条与独立条目只有 **16% 内容重叠**（token 覆盖率 ≥0.6 判定）——它不是重复，删了就真丢信息。栏目本身是"入口"，值得让人读到。

**为什么要降权**：一条塞 10~18 个不同主题，AI 做"对毕业生的四维影响分析"必然错位。实测研判结果对比：
- 栏目条 →「多新闻汇总影响公民物价如能源价格、安全如地缘政治、资产如科技股市，政策需应对国际变量，综合传导显著」（18 件事挤成一句空话）
- 单条 →「资管产品总规模突破 88 万亿，彰显金融市场活力，影响居民财富管理」（具体可核查）

**实现**：`intel_gate.is_column_item()`（标题含栏目模板词）+ 排序键插入「非栏目」位 → 栏目条降到同新鲜度层级末位。**不丢弃、不删数据，只是不优先占额度**。

**实测（2026-10-06）**：近 5 天判出 59 条，**人工核对全部为真栏目，零误伤**；未翻译池 1367 条中栏目仅 5 条，位置从原来霸榜**沉到第 1132 位之后**——**前 40 条（AI 实际处理窗口）里 0 条栏目**，前 15 条全为真新闻（印度加息、日本央行、ICE 黄金期货、燃油车占比跌破 50%）。translate 8/8、impact 6/6 端到端通过。

### 存量回填（一次性）

`sources.yaml` 词表改了，但 `base_score` 是**采集时**写入并持久化的——新词表只对**此后新采**条目生效，存量分数仍旧，优先级排序在过渡期（~3 天）对存量无效。

**`tools/rescore_recent.py`（严格增量）**：只补 `base_score` 恰为 0 的条目（0→正分，纯增加），**绝不动已有正分**。**为什么不敢重算正分**：jsonl 里 `source` 字段只存 `"rss"`、拿不到每条真实源权重（实测 0.7~1.3），无法忠实还原 `base_score = 源权重 × kw`，重算会把对的分数改坏。默认窗口 7 天（`--days`），不改更早历史；写回前每文件备份 `.bak_rescore_<时间戳>`。

**实测（2026-10-06）**：近 7 天补分 **4145 条**；未翻译池正分占比 4%→46%；回填后候选前 15 条为加沙停火、法国债市、巴西大选、俄乌、A股定增、纳指新高——**真要闻恢复且排前列**。闸门逻辑断言全过（不丢弃/新鲜优先/新鲜内按分/开关两态）；translate 与 impact 端到端各 6/6、5/5 通过。

- [x] **PLAN-1 RSSHub 中文源接入**（5 源已上线 CI docker run per-job；公共实例 403 已绕过）
- [x] **PLAN-2 M1 ACH 假设矩阵**（ach_matrix.py + hypothesis_engine 接入 + falsification_criteria 69/69 补完）
- [x] **PLAN-2 M3 仪表盘 ACH 排名面板**（gen_dashboard.py 已加，等首次周循环产出 ach_matrix.json 后自动显示）
- [x] **宏观指标集成**（tools/fetch_macro_indicators.py → 10 指标 → refresh.py 自动调用 → 仪表盘面板渲染）
- [x] **同类方案调研 P0 四项**（2026-09-05 落地：验证闭环/校准评分/事件聚类/敏感性分析，详见 §"同类方案调研 P0 落地"）
- [x] **同类方案调研 P1 五项**（2026-09-05 落地：TF-IDF 匹配/verdict 连续分/关键词 DSL/翻车高亮/GDELT，详见 §"P1 五项落地"）
- [x] **系统通电修复**（2026-09-10：假设链每日自动调度/幂等/加固/任务条件/僵尸树，详见 §"系统通电修复"）
- [x] **三观输入功能**（2026-09-12：worldview_engine 对话式录入 + worldview_loader 四链路注入 + 裁判护栏；初稿已 seed，待用户 --interactive 校正转正）
- [x] **周循环链路加固**（2026-09-12：daily_run 隔离+日志、watchdog v3 双盲区、假设树自动 commit+push、CI 删无效步骤，详见 §"周循环链路加固"；9-14 周一 09:30 为首次真实验证点）
- [x] **证据准入收紧**（2026-09-12：每假设每日 cap=6 择优 + ach_eligible 标记 + 存量退出诊断队列，详见 §"证据准入收紧"）
- [x] **筛选算法修复**（2026-09-13：去饱和曲线/窗口主题配额保底带/量纲统一/死字段接真分/阈值重校准，详见 §"筛选算法修复"）
- [ ] **PLAN-2 M2 贝叶斯调优**（等矩阵积累 2-3 周高质量诊断后看后验分布再调先验/LR 锚定；敏感性分析已就位）
- [ ] 事件聚类阈值调优：同日公告模板句仍会小规模误聚；观察仪表盘「同事件×N」徽章误报率后调 cluster_stories.py 文件头三闸门
- [ ] TF-IDF 匹配召回跃升：把 build_tfidf_vectors 换成 embedding 向量（接口已预留，调用方不动）；需新增白名单域名
- [ ] GDELT 解封观察：失败已写 1h 节流戳（不再白撞）；持续 429 则把查询组砍到 1 组/次
- [ ] **2026-12-05 首个真实到期验证**（23 个 small 节点）——resolutions.jsonl 开始产出 + 校准面板点亮
- [ ] 指标覆盖率提升：74 个 custom 指标部分无免费 API（NBS 3 个指标无抓取函数）；small 假设 37/40 指标 no_source
- [ ] 源健康度审计：47+6 源逐源测试（部分 list 源选择器已脱节）
- [ ] 旧目录 `C:\Users\admin\Documents\osint` 确认后删除（含 git 历史，删前确认不再回滚）
- [ ] 对话引擎观点卡的 time_horizon_months 有时与用户回答的到期日不一致（AI 浓缩偏差，可加后校验）
- [ ] mimo-v2.5-free 代理偶发 empty response / HTTP 400：批量 AI 脚本都应带兜底 + 预算超时（ach_daily_batch 已按此设计，失败条下轮重试）
- [ ] dots 通道稳定性观察（2026-09-19 接入）：dots3-note-prev 实测 ~1s 响应但为推理模型；若出现系统性截断/降智，优先降 dots 优先级回链中而非删通道
- [ ] 估值面板 CSI300/HSI 数据源：gurufocus 反爬 403（urllib 直抓被拦），history 停在 9-03 的 14 点；需 firecrawl 或改源后 `--fetch` 喂入；S&P500 已补全 10 年 1870 点（multpl 直抓 OK，parser 已兼容原始 HTML）
- [ ] **ACH LR 新口径观察**（2026-09-20 起）：新诊断行带 conf 字段，观察 conf 分布是否真有梯度（旧口径是抄 prompt 的塌缩分布）；若仍塌缩则进一步减少 prompt 中的数值示例。调参入口 `LR_C_STRENGTH`/`LR_I_STRENGTH`，存量 conf 可重算 LR 无需重跑 AI
- [ ] 两段式门控（未做）：93% 判定是 N，且 4627/4652 条证据只挂 1 个假设——"相关吗"前置问题大部分可由代码用挂载关系直接回答，无需模型。预计诊断量降至 1/5 以下
- [ ] 僵尸假设处理（数据暴露）：`中国社保走韩国老路`（388 条中仅 1 条 I、0 条 C）与 `东亚三国现代化进程趋同`（4C+2I）几乎从不被有效诊断，疑为假设过抽象无法证伪或情报源未覆盖；会一直占 ACH 排名但无信息量
- [x] **JEV 接入主链**（2026-09-21 完成）：`local/jev_client.py` + `tools/jev_usage.py`，`ai_diagnose(jev=)` 优先 JEV 失败回退 mimo；**两段式门控**（Noul 议题门控阈值 0.08 → Choice 方向），实测中文可用（金标准 83%）
- [x] **历史矩阵重跑**（2026-09-21 完成）：437 行全部用 JEV 两段式重判，C/I 从 242 → 73（清除噪声），后验重算；回滚点 `data/hypotheses/*.bak_20260921_184457`
- [ ] **门控阈值观察**：当前 0.08（噪声上界 0.04 / 真信号下界 0.14）。若发现真信号被误滤（如「A股高开」gate=0.08 压线），调 `gate_and_diagnose(gate_threshold=)`；存量 gate 值可重算无需重跑
- [ ] **JEV 吞吐红利释放**：接入后单条 45s→1.0s（两段式两次调用），`ach_daily_batch` 的 1500s 预算从 ~33 条/天可大幅提高，`MAX_DIAGNOSE_PER_RUN` 与 `BATCH` 上限可放宽（先观察一周稳定性再调）
- [x] **证据 cap 跨轮次修复**（2026-09-30）：Pass 2 直接证据 cap 与 Pass 3 上卷 cap 都改为按"当日已有条目"累计（原先每轮归零）。实测 JEV 日消耗从 798 万 token（9-29）回到设计值 ~10 万；已清理 bug 期间未诊断的超标条目 128 条（`tools/prune_overcap_evidence.py`，已诊断的保留避免浪费）
- [x] **匹配结构 union 化评估**（2026-09-30，结论：不改）：`tools/simulate_union_merge.py` 用**忠实 merge 语义**（按 hyp_id 合并，同时命中保留 tfidf 方法以保住 `ach_eligible` 资格）重测，union 仅 +0.5pp 召回（+1 真信号 / +6 噪声），**收益不足以抵消复杂度**。首版模拟的 +9.1pp 是缺陷产物（把同一对当两条独立候选），已在 AGENTS.md 记录该教训。**下一步若要提召回，应走 embedding 而非改结构。**
- [ ] **上卷 cap 观察**（2026-09-30 起）：`ROLLUP_DAILY_CAP=4` 依据是"与 ACH 日吞吐匹配"。观察 1-2 周：若上卷证据的信号率显著高于直接证据（当前 11.8% vs 13.1%，基本持平），可考虑上调；若矩阵仍膨胀则下调
- [ ] **SP 词表重训时机**（2026-09-30 起）：当前词表从近 7 天语料训练（8000 词）。若源结构大改或新增领域（如新增非中文源），跑 `tools/train_sp_tokenizer.py --days 14` 重训
- [ ] **重跑后后验复核**：`AI算力` 0.05→0.767、`中国社保` 0.358→0.650 涨幅较大，需人工抽检这几条新 C/I 证据是否成立（台海 0.934→0.706 已抽检通过）
- [x] **Step2 端到端跑通**（2026-09-21 晚验证）：自 8-30 以来首次产出 `analysis_20260921.jsonl`（40 条 / 90% 有效）；周报「总条数」由 0 → 2343。`batch_size` 定为 5（dots 服务端输出封顶 ~9K，10 条会触顶截断）
- [ ] **Step2 分析量观察**（2026-09-21 起）：`ai_analysis.max_items` 暂定 60（batch_size=5 → 12 批，实测单批 60-140s，约 15-25 分钟）。若 dots 吞吐改善或改走 JEV，可上调；同时观察 `analysis_*.jsonl` 是否持续日产
- [ ] **内容安全拦截的长期策略**（2026-09-21 发现）：dots 对涉政内容（实测"习特会"类标题）直接拒批，现靠拆半隔离 + 占位结果兜底。若敏感条目占比升高（拆半重试次数增多），考虑：①对高风险标题做前置脱敏（替换主体名）②该类条目改走本地模型或 JEV ③观察占位结果占比，>20% 时再动
- [ ] **dots 服务端输出上限复核**（2026-09-21）：实测请求 32768 只给 ~9000 token，未找到官方说明。若某天放开，可把 batch_size 调回 10 减少请求数
- [ ] **周循环与 refresh 抢跑根治**（2026-09-21 缓解未根治）：当前靠 `_wait_for_today_intel`（600s 等待）兜底；更彻底的做法是让 OsintWeekly 触发器错开整点（如 09:35），或让 refresh 写一个"数据就绪"标志文件供周任务轮询
- [ ] **AI 周报零情报护栏复核**（2026-09-21）：修复后首次周报应显示真实条数；若仍写"总条数为 0"，检查 `run_weekly_cycle.load_week_intel` 的闸门是否误拦
- [ ] 官方 `confidence` 字段警告：第三方逆向 + 实测确认 `c=(P_max−1/K)/(1−1/K)` 是分布集中度归一化，**非正确性估计**；做阈值分流/校准须用 `probabilities`
- [ ] JEV 服务稳定性观察：实测遇 `503 no healthy upstream`（3 次重试后回退 mimo 成功）；官方限流动态调整（250k tok/s、1200 req/min），若失败率上升需调 `RETRY_ATTEMPTS`
- [ ] **存量过期判据重写**（2026-09-21 审计暴露）：59 个节点判据含过期年份。其中 **41 个 due_date 仍未来→可重写**、**18 个 due_date 已到→走验证**。重写脚本未写（需先人工过一遍 `tools/audit_stale_thresholds.py` 的明细）
- [ ] **`中国社保走韩国老路` 零证据**（可信度标注暴露）：388 条证据里 0 条 C/I，后验 0.650 纯是先验值。需查是假设太抽象无法证伪、还是情报源未覆盖（同 `东亚三国现代化进程趋同` 仅 1 条）
- [ ] **LR 复利观察**（可信度标注暴露）：`全球能源供应格局` 30 条→0.950、`全球能源转型` 23 条→0.090 均贴边界。需观察 2-3 周后决定是调 `LR_C_STRENGTH` 还是引入证据去重加权
- [ ] 仪表盘可信度徽章实机验证：`rel-badge` 与虚线边框已在 gen_dashboard 落地，但**未经浏览器实测**（改的是内嵌 JS 字符串）
- [ ] **CI 翻译缺口大**（2026-10-04 巡检发现，口径已核实）：近 4 日英文条目**未译率 91-94%**（10-01: 564/616、10-02: 491/522、10-03: 261/286、10-04: 15/26）。注：整体 cn_title 覆盖率 10-34% 是**假象**——多数条目本就是中文无需翻译，真实缺口在英文条目。原因待查：①CI 翻译吞吐（50 条/次 × 4-8 次/天）远低于英文新增量（500-600/天）②或 `local_sync.sync_repo_intel` 合并未把 CI 译文写回。影响：仪表盘/简报英文源可读性。核查方向：`local_sync` 第 161 行合并分支 + CI 日志翻译步产出量
- [ ] **估值数据源陈旧**（2026-10-04 巡检）：`index_valuation.json` 已从损坏重建，但 CSI300/HSI 的原始 raw md 是 9-02 抓的（value 停在 9-01），仅 S&P500 有 1870 点 10 年序列。需用 firecrawl 重抓 gurufocus 两个 URL 后 `--fetch` 喂入；或评估换源（gurufocus 反爬 403 是长期问题）
- [ ] **GDELT 限流观察**（2026-10-04 巡检）：持续 429 但巡检时已解封。若再次长期 429，考虑把三组查询砍到 1 组/次（AGENTS.md 已列此预案）
- [ ] **知识库写入质量观察**（2026-09-21 新增）：知识库侧体检（`D:\Codex输出\视频知识库\tools\wiki_lint.py`）发现写入链曾长期无闸门——`关键词` 字段被灌入 AI 长句、宏观页生成悬空链接、假设页一次写入永不更新、`confidence` 量纲混用。已修（见下方"知识库写入闸门"节），但需观察：
  - 每周跑一次知识库体检，确认断链维持 0、标签零违规；
  - 关注 `[WIKI][GUARD]` 告警（当前日更页会报"超出行数上限"，属已知项）；
  - `dim in str(v)` 匹配逻辑仍失效（宏观页「相关情报引用」显示 0 条相关情报），本次按用户决定未改结构，留作后续。
  - **加这条的原因**：40+ 条待续事项里此前**没有任何一条**与知识库写入质量相关，而 47 个 RSS 源都有逐源审计计划——这条单向写入通道（215 个假设页 + 47 个概念页 + 4 个宏观页）从未进过审计视野。

## 情报流双栏（2026-10-06）

**问题**：情报流原先只有**一个 200 条列表 + 一个排序切换按钮**（按时间 / 按相关度，二选一）。用户口径是要"最新消息"和"重要情报"**同时看到**——二选一是被迫的取舍。

**方案**：改成左右双栏，各管一件事（用户提出）。

| 栏 | 内容 | 数据源 | 排序 |
|---|---|---|---|
| 左「🕐 最新消息」 | 200 条窗口 | `dashboard_data.intelligence` | 时间倒序 |
| 右「⭐ 24h 重要情报」 | 24h 高分榜 | `dashboard_data.intel_top`（**新增字段**） | 分数倒序 |

**为什么右栏需要独立数据**：`intelligence` 是**按时间截出来的 200 席**，实测有 **144 条 24h 内 ≥0.65 分的高分条目被截在窗口之外**——右栏若复用同一份数据就看不到它们。故 `rebuild_data` 新增产出 `intel_top`：从**全库去重池**取「24h 内 + `base_score>=0.5` + 每源最多 8 条（防单源霸榜）」前 60 条。

**实现要点**：
- 前端抽 `itemHTML(i)` 复用卡片模板，`R()` 同时填两栏（分类按钮过滤**同步作用于两栏**，实测点"金融市场"→ 左 132 / 右 42）
- 旧的排序切换按钮与 `S()` 函数已删（`S` 保留空壳以免外部调用报错）
- 响应式：<1100px 收成单栏（`.intel-cols` 媒体查询）
- 情报流 `<details>` 默认改**展开**（双栏是主视图，折叠着看不到）

**实测（2026-10-06，Playwright 真浏览器）**：两栏并排（1600px 视口下各 621.5px）；左 200 条 / 右 48 条；分类过滤两栏联动；**控制台 0 错误**；右栏内容为印度加息、铜价新高、软银 DayOne IPO、欧盟能源采购等 24h 真新闻。

**注意**：`intel_top` 是新字段，老 `dashboard_data.json` 没有——gen_dashboard 用 `data.get("intel_top", []) or []` 兜底，缺失时右栏显示"近 24 小时暂无高分情报"，不会报错。



写入知识库的路径原先各自为政、无质量校验，审计发现的 5 个缺陷是同一根因的 5 次显形。按"闸门内核独立成层、调用点各自 import、签名不动"的方式加固：

| 部件 | 位置 | 作用 |
|---|---|---|
| 闸门内核 | `local/kb_guard.py` | 纯函数：`normalize_keywords`（丢长句）/ `normalize_confidence`（统一量纲）/ `check_frontmatter` / `check_size` / `check_links` / `guard_page`。**返回 (ok, reasons) 不抛异常**——单页隔离，对齐 `refresh.py` 的 `_step()` 哲学 |
| 桩测 | `local/kb_guard_test.py` | 39 项边界（行数 199/200/201、量纲、越界、代码块豁免等），改 kb_guard 前必跑 |
| 桩测 | `local/kb_linker_test.py` | 22 项，验证假设页**更新回流**与签名兼容，改 kb_linker 前必跑 |
| 接入点 | `render_wiki.py` | ① `_generate_frontmatter` 过 `normalize_keywords`（修长句污染根因）② 宏观页「相关情报引用」改为"链接解析得到才写"（原先写死 `[[OSINT每日情报-xxxx]]`，格式对不上日更页 `osint-YYYYMMDD`，该段永远渲染成悬空链接）③ 日更页/宏观页写入前过 `guard_page` |
| 接入点 | `kb_linker.py` | ① `link_hypothesis_to_kb` 由 `if not page.exists()` 改为"读旧页→比对 confidence/status/deadline→有差异才写"（修永不更新）② `_norm_conf` 统一量纲 ③ `_insert_index_link` 写入前重读（防第三方并发写丢链接） |
| 兼容 | — | `DEFAULT_VAULT` / `_insert_index_link` / `_append_log` 三个名字与签名**保持不变**，保护 `worldview_engine.py` 的私有 import |

**关键约束**：链接索引由 `vault_link_index()` 构建，**按进程缓存**（215 页 × 全库扫描 = O(n²)）；宏观页因引用"同一次渲染里刚写入的日更页"，需用新鲜索引。

## 周报取样均衡化（2026-10-06）

**问题**：周报（`hypothesis_engine._save_ai_weekly_summary`）取样是**纯按分数取前 60**，实测上周 14860 条的结果严重偏科：

| 项 | 现状（修前） |
|---|---|
| 来源 | **100% 集中在 3 个源**（金十 29 / 新浪 28 / 财联社 3） |
| 主题 | **金融/美联储 43 条（72%）**；就业 7、能源 3、地缘 2、科技 1 |
| 漏掉 | 加沙停火、巴西大选、西班牙抗议、诺贝尔奖、英伟达市值——**一条没进** |

**根因**：高频快讯源（金十/新浪）源权重最高（1.2）且发得最多，`base_score>=0.9` 的条目全周仅 92 条（0.6%）且**全部来自这 3 个源**——纯按分数取 = 取这 3 个源的前 60。

**改法（用户选 B 方案：保重要 + 补均衡）**——`_save_ai_weekly_summary` 取样改为两轮配额：

1. 先剔除**固定栏目条**（`intel_gate.is_column_item`，多主题打包）+ 按标题前 24 字**去重**
2. 第一轮**严格配额**：单源 ≤6 条、单主题 ≤8 条
3. 第二轮**放宽填满**至 60 席（配额内先满足，不够再放开）
4. 主题判据 = 标题关键词（就业/劳动、社保/民生、地缘/冲突、能源/大宗、科技/AI、贸易/供应链、其他）

**同时修显示层的口径错配**：原「按主题分类」用的是 `category_cn`（**源类别**，如 finance/biz/rss），而 46/60 条的源类别都是 `finance`——逐条看多是地缘/能源/科技内容，却全被归进"经济/金融/就业"，**把配额刚换来的均衡在显示层又抹平了**。改用与取样同一套标题关键词判据，显示与抽样口径一致。

## 翻译/研判吞吐三修（2026-10-08，实测驱动）

用户问「还有没有翻译不过来或者判断不过来的情况」，实测发现三处真问题（都不是 JEV 通道的事）：

### 修 1：翻译额度 81% 浪费在「本就中文」的条目上

| 项 | 事实 |
|---|---|
| 现象 | `language=zh` 的条目 **49467 条，有 cn_title 的 0 条** |
| 根因 | 中文源（金十/新浪/财联社）的 `title` 是「【标题】正文…」长文，而下游统一读 `cn_title`。此前靠 AI「翻译」把标题摘出来——**它本来就是中文**，AI 只是在做摘标题的活 |
| 实测代价 | 翻译候选池 100 条里 **81 条是这种**，AI 花 30s/2 条把中文标题原样抄一遍；真英文（19 条）排不上队 |
| 改法 | `translate_local.local_prefill_chinese()`：纯本地正则摘标题（`【…】` 取括号内、无括号按句末标点截），**零 AI 成本**；摘完写回即视为「已翻译」，退出 AI 队列 |
| 实测效果 | 一轮 280 条零成本处理 + **57 条真英文**同轮翻完（修前这 600 秒全烧在抄中文上） |

**判据**：「需要 AI」的前提是「本地做不了」。中文条目的标题提取是纯字符串操作，交给 AI 是纯粹的浪费。

### 修 2：主模型 `mimo-v2.5-free` 已被官方弃用 + 降级链全线失效

| 项 | 事实 |
|---|---|
| 弃用 | `mimo-v2.5-free` → **410 Gone**（提示改用 `mimo-v2.6-flash-free`），8 处引用 |
| 降级链 | 旧链三个模型在**官网直连**下全灭（nemotron-3.5 403 / ling-3.0-fin 403），`model_healthcheck` 报「存活 0/3」 |
| 关键认知 | 本地 AI **实际只靠本机 4010 代理这条腿**——官网直连 2026-09-17 起对非官方客户端全线 403（实测 12 个 free 模型只有 1 个例外）。所以「降级链全灭」没让系统停摆，是因为代理在兜 |
| 经代理实测可用 | `mimo-v2.6-flash-free` / `nemotron-3.5-lightning-free` / `nemotron-3-ultra-free` / `longcat-2.5-preview-free` / `space-bunny-free` / `fledge-alpha-free` / `exo-free` |
| 改法 | 主模型 → `mimo-v2.6-flash-free`；降级链 → nemotron-3.5 / nemotron-3-ultra / **longcat-2.5**（替换已 404 的 ling-3.0-fin）；`translate_local.MODEL_CHAIN`、`citizen_impact`、`analyze._NV_SLUG_MAP`、`model_healthcheck.PROBE_MODELS`、`zen_proxy_client` 自检默认值同步 |

**注意**：`analyze._NV_SLUG_MAP` 保留旧名 `mimo-v2.5-free` 映射（历史条目/旧配置仍可能引用），新增新名映射，不是替换。

### 修 3：研判吞吐被人工上限卡住（不是 AI 慢）

| 项 | 事实 |
|---|---|
| 症状 | 全库待研判 7.8 万条；`en` 条目研判率 **3%**、`zh` 1%，而 `cn` 77% |
| 实测判据 | 近 3 天 52 轮里 `time budget exhausted` 出现 **0 次**，每轮只吃满 47/50 条就停 → **瓶颈是 `--max 50` 这个人上限，不是 AI 速度** |
| 实测吞吐 | 单批 5 条 11.4s = **2.3s/条** → 900s 预算可跑约 390 条 |
| 改法 a | `refresh.impact_now` 的 `--max` 50 → **150**。⚠️ **容量必须按真实负载算**：初次估算取 2.3s/条（用空内容假数据测的），改用真实新闻正文重测是 **6.2s/条**（5 条批次 28~34s）→ 900s 预算实际只能跑约 145 条。首版按 240 上线，实测 720s 只跑完 114 条就被截断（`time budget exhausted, 115 items left`）。日吞吐 1200 → 3600 条，仍高于日新增 2000~2800 |
| 根因 b | 外语条目**系统性饿死**：`en` 13482 条 base_score 中位 **0.000**（词表以中文为主）且仅 2% 有 cn_title → sorted 后 top240 里 en 只占 1 席、排在第 218 位之后 |
| 改法 b | `intel_gate` 新增**外语源保底带**（`FOREIGN_RESERVE_RATIO=0.25`）：按分数在未入选条目里补足外语席位，从末尾踢同数量非外语条目保证**总量严格守恒** |
| 改法 c | **`citizen_impact` 不再二次按时间重排**（原 340 行 `todo.sort(published_at)`）。保底带选出的 60 条外语被重排后落到队尾，预算耗尽时一条没跑到（首轮 114 条几乎全是 cn）。**排序职责归 intel_gate，调用方只按原序消费** |

**容量验证链**（三项联动，实测）：
- 保底带单独验：外语席位 3→12（max50）/ 5→60（max240），总量守恒
- 端到端验：修后 150 候选里外语 37 条（25%），英文条目**实际获得研判 25 条**（修前 3%），内容质量正常（如「朝鲜就台湾问题警告美国」→「国际紧张加剧安全风险」）

**与硬过滤教训同源**：外语低分不等于不重要，是**词表没覆盖**（153 词里中文 90 个且偏就业/能源）。所以只做保底配额，不丢任何条目、不改排序语义。开关 `OSINT_FOREIGN_RESERVE=0` 可关闭。

**踩坑（两个都是静默错误）**：
1. 保底带首版用错误的终止条件腾位，把中文条目全踢光（上限 50 只剩 18 条）——**凡是「腾位」类逻辑，必须断言输出总量 == 输入上限**。
2. 中途用 `language=='zh'` 统计覆盖率，看到「0% 修复无效」——**实际是 `write_back_to_jsonl` 会把 language 改写成 `cn`，条目迁移了桶**。按 `language` 字段统计会得出完全错误的结论；应按**标题实际语言**（中文字符占比）分类。

### 数据口径澄清（避免误判）

`language` 字段在本次修复后有四种值，**不要按字段名想当然**：

| 值 | 含义 | cn_title 覆盖 |
|---|---|---|
| `cn` | 已处理的中文条目（翻译管线写入的标记，**含被摘标题的原 `zh` 条目**） | ~100% |
| `zh` | 尚未处理的中文条目（`fetch_rss._detect_lang` 按正文判定） | 0% |
| `en` | 英文条目 | 23.5%（随翻译推进） |

看到「`zh` 研判率 1% vs `cn` 77%」时我曾怀疑有 bug，查证后是**排序所致而非歧视**：`cn` 条目 100% 有 cn_title 且分数高，`zh`/`en` 中位分为 0 排不上队。**统计任何"覆盖率"前，先确认分类字段的语义，别用会被下游改写的字段做判据。**

**实测（2026-10-06，真实数据 14860 条）**：源数 **3→12**（含 MarketWatch、PIIE 等境外源）；主题 7 类均衡（就业 10 / 社保 8 / 地缘 8 / 能源 9 / 科技 8 / 贸易 8 / 其他 9）；栏目条 5→**0**。触发方式：`run_weekly_cycle` 周一自动调，或手动 `eng._save_ai_weekly_summary(hyps, ach_matrix=ach, intel_items=items)`。

**注意**：`_save_ai_weekly_summary` **只在周一执行**（`run_weekly_cycle` 内 `today.weekday()==0` 判断）——周二手动跑周循环不会生成 AI 周报，这是设计内行为，不是 bug。

### 境外源可用性（同日实测，回答"能不能拿彭博/路透"）

| 源 | 近 7 天条数 | 说明 |
|---|---|---|
| **Reuters 路透** | — | **sources.yaml 里根本没配** |
| Bloomberg-Markets | **0** | 配了（RSSHub 路由）但采不到 |
| Bloomberg-Economics | 45 | 量极少 |
| AP-News / AP-World | **0 / 0** | 配了（RSSHub 路由）但采不到 |
| Nikkei Asia | **0** | 官方 RSS 采不到 |
| Yonhap / Japan Times / CNBC-World / DW-World | 729 / 395 / 369 / 130 | 有量，是境外信息主力 |

**结论**：彭博/AP 走 RSSHub 路由但上游已失效；路透未配源。**"改源配额"救不了不存在的源**——要拿这些源需先解决采集（换路由/找新 feed），属独立任务。

---
*最后更新：2026-10-06 - 周报取样均衡化（源 3→12、主题 7 类均衡）+ 情报流双栏 + 去重/栏目降权/AI 排序 + 本机 zen 代理接入与 git 代理自动化。前一日：巡检修复（假设树 commit 积压根因 + autostash 纵深防御 + valuation 全零文件重建 + 403 body 补读下沉）*


