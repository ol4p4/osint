# 参谋系统 - AGENTS.md

## 项目概述
个人情报智库系统：云端抓取全球开源情报 → 本地 AI 四维结构研判 → 仪表盘展示 + Obsidian 知识库写入。
面向"中国年轻失业毕业生"视角，基于积累制度/空间修正/国家-市场边界/阶级利益四维框架分析。

## 关键路径
- **仓库**：`D:\osint`（GitHub: `ol4p4/osint`）
- **产物目录**：`D:\Codex输出\osint_卫星图\`
- **知识库**：`D:\Codex输出\视频知识库\`
- **Python**：`E:\software\python3.13.8\python.exe`
- **仪表盘**：http://127.0.0.1:9090/interactive_dashboard.html

## CI 流水线（GitHub Actions `daily.yml`）
- **频率**：每天 4 次（UTC 02/08/14/20 = 北京时间 10/16/22/04）
- **超时**：25 分钟
- **步骤**：采集 RSS → NVIDIA 翻译（增量，每次最多 50 条新条目）→ 假设关联 → 假设验证 → 生成简报 → 推送回仓库
- **权限**：`contents: write`（必须，否则无法推送翻译结果）
- **翻译模型**：`meta/llama-3.2-11b-vision-instruct`（通过 `https://integrate.api.nvidia.com/v1`）
- **翻译限制**：每次最多翻译 50 条未翻译条目（有 `cn_title` 的跳过），batch 大小 10

## 模型分工
| 角色 | 模型 | 用途 |
|------|------|------|
| 参谋长（生成） | Mimo-v2.5-free | 生成假设/现状分析 |
| 裁判（验证） | Nemotron-3.5-lightning-free | 验证判定/复盘 |
| 翻译（CI） | meta/llama-3.2-11b-vision-instruct | NVIDIA API 批量翻译 |
| DeepSeek | 已弃用 | 输出过于官方，无批判性 |

AI 调用通过 OpenCode Zen 免费代理，伪造请求头：
```python
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}",
    "User-Agent": "opencode/latest/1.3.15/cli",
    "x-opencode-client": "cli",
    "x-opencode-session": uuid.uuid4().hex,
}
```

## 文件结构

### 仓库根目录
| 文件 | 用途 |
|------|------|
| `cloud/main_cloud.py` | CI 主入口：采集→去重→评分→推送 |
| `cloud/fetch_rss.py` | RSS 抓取（每源 15s 超时） |
| `cloud/fetch_list.py` | 列表页抓取（需 cssselect） |
| `cloud/clean_dedup_score.py` | 去重+评分（修复了 content 字段映射和 simhash 位溢出） |
| `cloud/translate.py` | NVIDIA API 增量翻译 |
| `local/analyze.py` | 本地 AI 四维分析（参谋长） |
| `local/hypothesis_engine.py` | 假设生成/更新/验证 |
| `gen_dashboard.py` | 从 dashboard_data.json 生成 HTML |
| `fix_dashboard.py` | 修复 esc() 函数位置（必须在 gen_dashboard.py 之后运行） |
| `link_intel_hyp.py` | 情报→假设关联 |
| `verify_hypotheses.py` | 假设自动验证（FRED/WorldBank 等） |
| `daily_briefing.py` | 每日简报生成 |
| `sync_data.py` | 同步假设+修复短摘要 |
| `rebuild_hyps.py` | 重建假设树（修改 TREE 字典后运行） |
| `sources.yaml` | 47 个情报源配置 |
| `config.yaml` | API 密钥和模型配置 |
| `weights.yaml` | 关键词权重 |
| `requirements.txt` | Python 依赖（含 cssselect、simhash 2.1.2） |

### 产物目录（`D:\Codex输出\osint_卫星图\`）
| 文件 | 用途 |
|------|------|
| `dashboard_data.json` | 仪表盘数据（intel + hyps + stats） |
| `interactive_dashboard.html` | 自包含仪表盘 HTML |
| `serve.py` | HTTP 服务器（端口 9090） |
| `refresh.py` | 拉取+重建+生成（定时任务调用） |
| `intel_YYYYMMDD.jsonl` | 每日翻译后的情报 |
| `hypotheses/active_hypotheses.json` | 假设树（68 节点：8大/20中/40小） |
| `indicator_history.json` | 指标历史数据 |

## 运行方式

### 手动生成仪表盘
```bash
python gen_dashboard.py
python fix_dashboard.py
# 然后 python -m http.server 9090 或启动 serve.py
```

### 手动刷新（拉取+重建）
```bash
python D:\Codex输出\osint_卫星图\refresh.py
```

### 启动 HTTP 服务
```bash
# 方式1：直接运行
python D:\Codex输出\osint_卫星图\serve.py

# 方式2：Startup 文件夹已有快捷方式，开机自动启动
```

### 重建假设树
修改 `rebuild_hyps.py` 中的 `TREE` 字典，然后运行：
```bash
python rebuild_hyps.py
```

## CI 故障排除

### 常见失败原因
1. **RSS 超时**：每个源 15s 超时，坏源会跳过，不影响其他源
2. **翻译 404**：检查 NVIDIA API key 是否有效，模型是否可用
3. **翻译超时**：已限制每次 50 条，如果还超时减小 `translate.py` 中的限制
4. **push 403**：检查 workflow 的 `permissions: contents: write`
5. **`No module named 'cloud'`**：`main_cloud.py` 顶部有 `sys.path` 修复
6. **simhash 报错**：确认 `simhash==2.1.2`（不是 4.1.2）

### 数据质量指标
- 总情报数：~719 条（跨 7 天）
- 有中文标题比例：~56%（405/719）
- 每日新增 RSS：~331 条
- 翻译吞吐：~50 条/次 CI

## 待续事项
- [ ] P2a 对话引擎：用户说想法 → 5 轮追问 → 观点卡 → 假设检验
- [ ] P2b 问题生成器：AI 分析后自动生成开放性问题
- [ ] P6 每周循环：每周日汇总数据生成/更新假设
- [ ] 知识库双向链接：wiki/hypotheses/ 与 index.md/log.md 同步
- [ ] 指标覆盖率提升：74 个 custom 指标部分无免费 API

---
*最后更新：2026-08-29 - CI 全链路修复 + 增量翻译 + 仪表盘持久化服务*
