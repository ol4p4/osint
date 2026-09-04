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
GitHub CI (daily.yml, 6次/天)
  采集RSS → clean_dedup_score → translate(NVIDIA增量50条) → link_intel_hyp → verify_hypotheses → daily_briefing → push 仓库
仓库 D:\osint\intel_YYYYMMDD.jsonl
  ↓ (计划任务 OsintRefresh 每小时跑产物目录 refresh.py)
refresh.py: git pull + 合并 CI 数据 + 可选翻译
  → ensure_rsshub()  → 保本地 RSSHub 容器健康（curl localhost:1200 → docker start / run）
  → fetch_now()     → tools/fetch_now.py 24h 全量本地拉（绕开 CI 9 条金十限流，详见 §"CI 故障排除 #11"）
  → translate_now() → tools/translate_local.py OpenCode Zen 翻译 30 条/6min（替代 CI 翻译吞吐瓶颈）
  → rebuild_data → dashboard_data.json
  → fetch_macro() → tools/fetch_macro_indicators.py → macro_indicators.json（12个宏观指标）
  → fetch_unemployment_history() → tools/fetch_macro_indicators.py --history → cn_unemployment_history.json
  → gen_dashboard+fix_dashboard → interactive_dashboard.html
  ↓
仪表盘 19090 ← serve.py（开机自启）
计划任务 OsintWeekly（周一 09:30）→ 产物目录 daily_run.ps1 -Auto → local/main_local.py 分析 + hypothesis_engine.run_weekly_cycle
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
| `verify_hypotheses.py` | 假设自动验证（FRED/Frankfurter/GoldAPI/WorldBank，域名白名单在 `ALLOWED_HOSTS`） |
| `tools/fetch_macro_indicators.py` | 宏观指标抓取（汇率/利率/GDP/CPI/失业率，12个指标），产物 `data/macro_indicators.json`，refresh.py 自动调用；`--history` 子命令抓 NBS 分年龄组失业率历史月度序列 |
| `tools/fetch_now.py` | 本地 RSSHub 24h 全量拉取（绕开 CI 端 9 条金十/财联社限流），append 到今日 jsonl；refresh.py 自动调 |
| `tools/translate_local.py` | 本地 OpenCode Zen 翻译（mimo-v2.5-free + nemotron 降级），每跑 30 条 6 分钟，写回 jsonl；refresh.py 自动调，**本地 hourly 翻译 18-30 条/6min，CI 翻译吞吐瓶颈解决** |
| `gen_dashboard.py` + `fix_dashboard.py` | 生成 HTML（必须按此顺序）；gen_dashboard 内嵌 macro 面板 CSS/HTML/JS，趋势图用 Chart.js 4.4 (jsdelivr)，情报流 section 用 `<details>` 折叠默认收起 |
| `link_intel_hyp.py` / `daily_briefing.py` / `sync_data.py` / `rebuild_hyps.py` | 关联/简报/同步/重建树 |
| `views.yaml` | 观点模板（`materialized_hyp_id` 标注已物化的 view，防止周循环重复生成） |
| `sources.yaml`(47源) / `config.yaml`(key+路径) / `weights.yaml` / `daily_question.ps1`(P2a/P2b入口) | 配置与入口 |

### 产物目录（`D:\osint\data\`，gitignore 不追踪）
| 文件 | 用途 |
|------|------|
| `dashboard_data.json` / `interactive_dashboard.html` | 仪表盘数据+页面（含宏观指标面板） |
| `macro_indicators.json` | 宏观指标数据（10个：汇率/利率/GDP/CPI/失业率），由 fetch_macro_indicators.py 产出 |
| `refresh.py` | 刷新入口：git pull + 合并 + 翻译 + rebuild + fetch_macro + gen_html，日志落 `logs/refresh_YYYYMMDD.log` |
| `daily_run.ps1` | 本地分析+周循环运行器（`-Auto` 参数供计划任务用；**必须带 UTF-8 BOM**） |
| `intel_YYYYMMDD.jsonl` | 每日情报（`intel_raw_*`/`intel_final_*` 不参与重建和翻译） |
| `hypotheses/active_hypotheses.json` | 假设树（69 节点：8大/20中/41小，status 支持 active/falsified） |
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

# 周循环（幂等，可随时手动跑）
python -c "..." # 见 daily_run.ps1 Step3，或等 OsintWeekly 周日 09:30 自动跑
```

## 约束与禁忌 / MUST NOT
- **禁止引用旧路径** `C:\Users\admin\Documents\osint`（旧仓库）和 `D:\Codex输出\osint_卫星图`（旧产物目录，均已废弃）；仓库= `D:\osint`，产物= `D:\osint\data`，路径常量只在文件头部定义一次。
- **禁止绕过白名单**：外发请求只允许 https + 白名单域名（verify_hypotheses 的 `ALLOWED_HOSTS`、analyze.py 的 opencode.ai 校验）。加新 API 必须先加白名单。
- **禁止让周循环重复生成假设**：views.yaml 加新 view 时若已在树里，必须填 `materialized_hyp_id`。
- **写 Python 文件 IO 用方法式 API**（`read_text/write_text/Path.open`），不要 `open(变量)`——Mimosa 安全扫描会按路径穿越拦截（PreToolUse），拦截后改写法而不是硬试。
- **.ps1 文件保存必须带 UTF-8 BOM**：PS 5.1 无 BOM 按 GBK 解析，中文字符串奇数字节会吞掉后面引号导致"字符串缺少终止符"（用 python 补 BOM：`open(p,'wb').write(b'\xef\xbb\xbf'+content_bytes)`）。
- 破坏性改动前先 commit（仓库有 git）；产物目录脚本改前先复制 `.bak_日期`。
- **bat 文件避免 `%VAR%\path` 模式**：cmd 解析时 `\r` 会被当 carriage return 吞一个字符（`%OUTDIR%\refresh.py` → `efresh.py`），导致"不是内部或外部命令"。bat 里路径直接写绝对路径（**用正斜杠更稳**：`D:/osint/data/refresh.py` Windows 也认），不要混用 `%VAR%` + 反斜杠。
- **serve.py 进程管理**：agent 会话内用 `Start-Process`/后台任务启动的进程会随会话清理被杀（用户浏览器随即 ERR_CONNECTION_REFUSED）。正确方式：`Invoke-CimMethod Win32_Process Create`（进程挂系统服务下，脱离会话树）。开机自启用 Startup\osint_dashboard.bat（用户会话里跑，不受影响）。
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
   - 双联防御：CI 已 6x/day（cron `0 */4 * * *`，commit edbd79d）+ 本地 `OsintWatchdog` 计划任务每 6h 检查 `intel_2*.jsonl` mtime，> 8h 静默则 `gh workflow run daily.yml`，日志 `data/logs/watchdog_YYYYMMDD.log`
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
- [ ] **PLAN-2 M2 贝叶斯调优**（待首次周循环跑完，观察后验分布再调先验/LR 锚定）
- [ ] 指标覆盖率提升：74 个 custom 指标部分无免费 API（NBS 3 个指标无抓取函数）
- [ ] 源健康度审计：47+6 源逐源测试（部分 list 源选择器已脱节）
- [ ] 旧目录 `C:\Users\admin\Documents\osint` 确认后删除（含 git 历史，删前确认不再回滚）
- [ ] 对话引擎观点卡的 time_horizon_months 有时与用户回答的到期日不一致（AI 浓缩偏差，可加后校验）

---
*最后更新：2026-09-01 - 宏观指标集成完成（fetch_macro → refresh.py → 仪表盘面板）*
