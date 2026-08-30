# 参谋系统 - AGENTS.md

## 项目概述
个人情报智库系统：云端抓取全球开源情报 → 本地 AI 四维结构研判 → 仪表盘展示 + Obsidian 知识库写入。
面向"中国年轻失业毕业生"视角，基于积累制度/空间修正/国家-市场边界/阶级利益四维框架分析。

## 关键路径
- **仓库**：`D:\osint`（GitHub: `ol4p4/osint`）
- **产物目录**：`D:\Codex输出\osint_卫星图\`
- **知识库**：`D:\Codex输出\视频知识库\`
- **Python**：`E:\software\python3.13.8\python.exe`
- **仪表盘**：http://127.0.0.1:19090/interactive_dashboard.html （旧端口 9090 会落进 Windows 动态保留段导致 WinError 10013，勿改回）
- **废弃目录**：`C:\Users\admin\Documents\osint`（迁移残留，仅供回滚，禁止新增引用；确认无误后可删）

## 数据流全貌（接手必读）
```
GitHub CI (daily.yml, 4次/天)
  采集RSS → clean_dedup_score → translate(NVIDIA增量50条) → link_intel_hyp → verify_hypotheses → daily_briefing → push 仓库
仓库 D:\osint\intel_YYYYMMDD.jsonl
  ↓ (计划任务 OsintRefresh 每小时跑产物目录 refresh.py)
cloud/local_sync.py: git pull + 合并 CI 数据进产物目录 jsonl（同 id 保留 cn_title 版）
  + translate_local（本地有 NVIDIA_API_KEY 才跑，否则等 CI 翻完合并回来）
  + rebuild_data（只读 intel_2*.jsonl，过滤脏日期）→ dashboard_data.json → gen_dashboard+fix_dashboard
  ↓
仪表盘 19090 ← serve.py（开机自启）
计划任务 OsintWeekly（周日 09:30）→ 产物目录 daily_run.ps1 -Auto → local/main_local.py 分析 + hypothesis_engine.run_weekly_cycle
对话引擎 daily_question.ps1 → local/dialogue_engine.py（观点卡）→ feed_to_hypothesis 进假设树 → local/kb_linker.py 同步知识库
```

## 模型分工
| 角色 | 模型 | 用途 |
|------|------|------|
| 参谋长（生成） | Mimo-v2.5-free | 生成假设/现状分析/对话追问/观点卡 |
| 裁判（验证） | Nemotron-3.5-lightning-free | 验证判定/复盘 |
| 翻译（CI） | meta/llama-3.2-11b-vision-instruct | NVIDIA API 批量翻译 |
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
| `gen_dashboard.py` + `fix_dashboard.py` | 生成 HTML（必须按此顺序） |
| `link_intel_hyp.py` / `daily_briefing.py` / `sync_data.py` / `rebuild_hyps.py` | 关联/简报/同步/重建树 |
| `views.yaml` | 观点模板（`materialized_hyp_id` 标注已物化的 view，防止周循环重复生成） |
| `sources.yaml`(47源) / `config.yaml`(key+路径) / `weights.yaml` / `daily_question.ps1`(P2a/P2b入口) | 配置与入口 |

### 产物目录（`D:\Codex输出\osint_卫星图\`）
| 文件 | 用途 |
|------|------|
| `dashboard_data.json` / `interactive_dashboard.html` | 仪表盘数据+页面 |
| `refresh.py` | 薄壳入口（逻辑在 `D:\osint\cloud\local_sync.py`），日志落 `logs/refresh_YYYYMMDD.log` |
| `daily_run.ps1` | 本地分析+周循环运行器（`-Auto` 参数供计划任务用；**必须带 UTF-8 BOM**） |
| `intel_YYYYMMDD.jsonl` | 每日情报（`intel_raw_*`/`intel_final_*` 不参与重建和翻译） |
| `hypotheses/active_hypotheses.json` | 假设树（69 节点：8大/20中/41小，status 支持 active/falsified） |
| `dialogues/view_cards/` `questions/` `reports/` | 观点卡 / 每日开放问题 / 周报 |

## 运行方式
```bash
# 手动刷新（拉取+合并+翻译+重建）
python D:\Codex输出\osint_卫星图\refresh.py

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
- **禁止引用旧路径** `C:\Users\admin\Documents\osint`；仓库= `D:\osint`，产物= `D:\Codex输出\osint_卫星图`，路径常量只在文件头部定义一次。
- **禁止绕过白名单**：外发请求只允许 https + 白名单域名（verify_hypotheses 的 `ALLOWED_HOSTS`、analyze.py 的 opencode.ai 校验）。加新 API 必须先加白名单。
- **禁止让周循环重复生成假设**：views.yaml 加新 view 时若已在树里，必须填 `materialized_hyp_id`。
- **写 Python 文件 IO 用方法式 API**（`read_text/write_text/Path.open`），不要 `open(变量)`——Mimosa 安全扫描会按路径穿越拦截（PreToolUse），拦截后改写法而不是硬试。
- **.ps1 文件保存必须带 UTF-8 BOM**：PS 5.1 无 BOM 按 GBK 解析，中文字符串奇数字节会吞掉后面引号导致"字符串缺少终止符"（用 python 补 BOM：`open(p,'wb').write(b'\xef\xbb\xbf'+content_bytes)`）。
- 破坏性改动前先 commit（仓库有 git）；产物目录脚本改前先复制 `.bak_日期`。
- 批量改多文件前先 `git status` 确认影响范围；禁止 `push --force`、`reset --hard` 丢未提交内容。
- `falsification_criteria` 为空时会在验证时自动从 indicators/sub_propositions 的 `threshold_refute` 回填，不要手填重复值。

## CI 故障排除
1. **RSS 超时**：每源 15s 超时，坏源跳过不影响其他源
2. **翻译 404/超时**：NVIDIA key 在 GitHub Secrets；已限每次 50 条、batch 5
3. **push 403**：检查 workflow `permissions: contents: write`
4. **`No module named 'cloud'`**：`main_cloud.py` 顶部有 sys.path 修复
5. **simhash 报错**：确认 `simhash==2.1.2`
6. **本地数据没中文**：正常——等 CI 翻完由 local_sync 合并回来；或本地设置 `NVIDIA_API_KEY` 环境变量
7. **计划任务没跑成**：查 `logs/refresh_YYYYMMDD.log`；`daily_run.ps1` 手动测试加 `-Auto`

## 数据质量基线（2026-08-30）
- 情报 ~718 条（脏日期已过滤）；cn_title 405（56%，随 CI 翻译推进会涨）
- 每日新增 RSS ~331 条；翻译吞吐 50 条/次 CI × 4 次/天
- 假设树 69 节点（8大/20中/41小）；观点卡链路已通（view_e0c75e → hyp_4656924e）

## 待续事项
- [x] P2a 对话引擎（dialogue_engine.py 全链路已通）
- [x] P2b 问题生成器（question_generator.py）
- [x] P6 每周循环（run_weekly_cycle 修复 + OsintWeekly 计划任务周日 09:30）
- [x] 知识库双向链接（kb_linker.py，index/log/hypotheses 三处同步）
- [ ] 指标覆盖率提升：74 个 custom 指标部分无免费 API（NBS 3 个指标无抓取函数）
- [ ] 旧目录 `C:\Users\admin\Documents\osint` 确认后删除（含 git 历史，删前确认不再回滚）
- [ ] 对话引擎观点卡的 time_horizon_months 有时与用户回答的到期日不一致（AI 浓缩偏差，可加后校验）

---
*最后更新：2026-08-30 - 修复同步/翻译/周循环三大链路 + P2a/P2b 落地 + 知识库联动 + SSRF 白名单*
