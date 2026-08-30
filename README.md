# OSINT 个人智库系统

> **云端情报官** + **本地参谋长** 双模式架构
> 
> 为「处于结构性转折期的年轻劳动者」构建的每日开源情报内参系统

## 🏗️ 架构概览

```
☁️ 云端（GitHub Actions - 情报官）           💻 本地（参谋长）
┌─────────────────────────────────┐        ┌─────────────────────────────────┐
│ 定时触发 (每日 07:00 CST)        │        │ 双击 run.ps1 / 手动触发          │
│ 抓取 4 类源 → 原始 JSONL         │ ─────▶ │ 读取 JSONL + 知识库 + 人设        │
│ 清洗/去重/基础评分               │ Artifact│ AI 四维深度分析 (可插拔 API)      │
│ 上传 Artifact / 推送私有 Gist    │ /Gist  │ 生成 简报 + 仪表盘 + 入库页        │
└─────────────────────────────────┘        └─────────────────────────────────┘
```

### 职责分工

| 阶段 | 云端（情报官） | 本地（参谋长） |
|------|----------------|----------------|
| **输入** | `sources.yaml`（源定义） | `intel_YYYYMMDD.jsonl` + `知识库/` + `persona.md` + `weights.yaml` |
| **处理** | 抓取 → 清洗 → 去重 → 基础打分 | 语义分析 → 影响研判 → 行动建议 → 实体链接 → 知识融合 |
| **输出** | `intel_YYYYMMDD.jsonl` (Artifact/Gist) | `brief_YYYYMMDD.md` + `dashboard.html` + `wiki/` 新页面 |
| **密钥** | 仅需 `GIST_TOKEN` | `OPENAI_API_KEY`、`BASE_URL`、`MODEL`（本地配置） |

---

## 📁 目录结构

```
osint/
├── config.yaml               # 本地配置（API、路径、模型）
├── sources.yaml              # 共享：数据源定义
├── weights.yaml              # 共享：权重参数
├── persona.md                # 用户画像（喂给 AI）
├── run.ps1                   # 双击运行入口
├── install_deps.ps1          # 一键安装依赖
├── cloud/                    # ☁️ 云端采集（GitHub Actions 跑这里）
│   ├── fetch_rss.py          # RSS源采集
│   ├── fetch_list.py         # 列表页采集（政府/统计/智库）
│   ├── clean_dedup_score.py  # 清洗/去重/基础评分
│   └── main_cloud.py         # 云端入口
├── local/                    # 💻 本地研判（双击 run.ps1 跑这里）
│   ├── load_intel.py         # 情报加载（Artifact/Gist/本地）
│   ├── load_knowledge.py     # 知识库加载（RAG索引）
│   ├── analyze.py            # AI 四维深度分析（核心）
│   ├── render_brief.py       # 生成每日简报
│   ├── render_dashboard.py   # 生成仪表盘 HTML
│   ├── render_wiki.py        # 生成 Obsidian 页面
│   └── main_local.py         # 本地入口
├── .github/workflows/daily.yml  # GitHub Actions 定时任务
└── README.md
```

---

## 🚀 快速开始

### 1. 安装依赖
```powershell
# 以管理员权限运行（推荐）
.\install_deps.ps1
```

### 2. 配置 API Key
编辑 `config.yaml`，填入你的兼容 OpenAI 格式的 API Key：
```yaml
api:
  base_url: "https://api.deepseek.com/v1"   # 或 https://api.z.ai/v1 / 硅基流动 / 自建
  api_key: "sk-xxxxxxxxxxxxx"               # 你的 API Key
  model: "deepseek-chat"
```

### 3. 运行本地分析
双击 `run.ps1` 或在 PowerShell 中运行：
```powershell
.\run.ps1
```

### 4. 查看产物
产物输出到 `D:\osint\data\`：
- `brief_YYYYMMDD.md` - 每日简报（双击用 Obsidian/浏览器阅读）
- `dashboard_YYYYMMDD.html` - 交互式仪表盘（双击在浏览器打开，可筛选/钻取）
- `wiki/osint-YYYYMMDD.md` - 每日情报概念页（含双向链接）
- `wiki/宏观-*.md` - 宏观分析框架概念页
- `analysis_YYYYMMDD.jsonl` - 完整分析明细

---

## ⚙️ 核心配置文件

| 文件 | 说明 | 修改频率 |
|------|------|----------|
| `config.yaml` | 本地运行配置（API、路径、输出格式） | 低 |
| `sources.yaml` | 数据源定义（RSS、列表页、API、关键词权重） | 中 |
| `weights.yaml` | 评分权重（时效衰减、来源权重、画像加成、四维权重） | 中 |
| `persona.md` | 用户画像（系统提示词核心上下文） | 低 |

### 数据源分类（sources.yaml）
- **rss_sources**: 新华社、人民日报、外交部、统计局、海关、发改委、央行、证监会
- **gov_portals**: 中国政府网、人社部、工信部、教育部（列表页+详情页）
- **stats_sources**: 统计局月度数据 API、央行风险提示
- **think_tanks**: 社科院、发改委宏观院、中金宏观、华泰固收

### 四维权重（weights.yaml）
```yaml
persona_boost:
  dimension_weights:
    accumulation: 0.30     # 积累制度视角
    spatial: 0.25          # 空间修正视角
    state_market: 0.25     # 国家-市场边界视角
    class_interest: 0.20   # 阶级/利益集团视角
```

---

## 🧠 四维分析框架（参谋长核心）

每条情报经过 AI 分析，输出结构化研判：

```json
{
  "macro_diagnosis": {
    "accumulation_node": "生产/实现/分配/再生产",
    "spatial_layer": "中心/外围/特区/都市圈",
    "state_market_shift": "国家进场/市场退场/边界模糊/试点先行",
    "class_interest": "受益集团/成本承担集团"
  },
  "structural_implication": "如何改变劳动力市场结构、技能溢价、资产价格、行业利润池",
  "personal_action_space": {
    "window_months": 18,
    "concrete_moves": [{"action": "...", "rationale": "...", "risk": "..."}],
    "avoid_traps": ["..."],
    "signals_to_watch": ["..."]
  },
  "knowledge_links": ["[[宏观-积累制度与劳动力市场]]", "[[实体-国家电网]]"],
  "confidence": 7,
  "contradictions": "政策文本vs历史执行偏差 / 地方利益vs中央意图"
}
```

---

## ☁️ 云端部署（GitHub Actions）

### 1. 创建私有仓库
在 GitHub 创建私有仓库，推送本项目代码。

### 2. 配置 Secrets
在仓库 Settings → Secrets → Actions 添加：
| Secret | 说明 |
|--------|------|
| `GIST_TOKEN` | GitHub Personal Access Token（gist 权限） |
| `GIST_ID` | 可选：已有私有 Gist ID，留空自动创建 |

### 3. 启用 Actions
Actions 会每日 07:00 CST 自动运行，产物上传为 Artifact 并推送到私有 Gist。

### 4. 本地获取产物
`config.yaml` 中配置：
```yaml
mode:
  cloud_fetch:
    method: "gist"        # gist / artifact / local
    gist_id: "your_gist_id"
    github_repo: "owner/repo"
```

---

## 📊 产物说明

### 每日简报 (`brief_YYYYMMDD.md`)
- 执行摘要 + 核心结构性判断
- 高/中置信度情报深度研判（四维诊断+结构含义+行动空间）
- 行动清单汇总（推荐行动/避坑指南/观测信号）

### 交互式仪表盘 (`dashboard_YYYYMMDD.html`)
- 单文件 HTML，双击即用
- Alpine.js + Chart.js 驱动
- 筛选器：来源/优先级/积累环节/空间层级/国家-市场/关键词
- 6 个图表：优先级/来源/积累环节/空间层级/国家-市场/利益集团
- 卡片展开查看：四维诊断、结构含义、推荐行动、避坑、观测信号、知识库链接、AI推理链

### Obsidian 知识库页 (`wiki/`)
- `osint-YYYYMMDD.md` - 每日情报概念页（符合 SCHEMA.md 规范）
- `宏观-积累制度与劳动力市场.md` 等 - 宏观框架概念页
- 自动双向链接 `[[...]]`
- 自动更新 `wiki/index.md` 和 `wiki/log.md`

---

## 🔧 自定义与扩展

### 添加新数据源
编辑 `sources.yaml`，在对应分类下添加：
```yaml
- name: "新源名称"
  url: "https://example.com/rss.xml"
  type: "rss"
  category: "gov"
  weight: 1.2
  keywords: ["关键词1", "关键词2"]
```

### 调整权重
编辑 `weights.yaml`：
- `time_decay.half_life_hours`: 时效半衰期（默认 48h）
- `source_weights`: 来源基础权重
- `keyword_weights`: 关键词权重字典
- `persona_boost.tags`: 画像标签加成
- `persona_boost.dimension_weights`: 四维权重

### 更换 AI 模型
编辑 `config.yaml`：
```yaml
api:
  base_url: "https://api.z.ai/v1"
  api_key: "your_key"
  model: "glm-4-flash"
  fallback_models:
    - base_url: "https://api.siliconflow.cn/v1"
      model: "Qwen/Qwen2.5-72B-Instruct"
      api_key: "your_key"
```

---

## 📝 知识库规范（Obsidian）

本系统生成的页面遵循 `D:\Codex输出\视频知识库\wiki\SCHEMA.md`：
- YAML frontmatter 必填字段
- 至少 2 个 `[[双向链接]]`
- 文件名小写连字符
- 自动更新 `index.md` 和 `log.md`

---

## 🐛 故障排查

| 问题 | 解决方案 |
|------|----------|
| `run.ps1` 报错找不到 python | 运行 `install_deps.ps1` 或手动装 Python 3.10+ |
| API 请求失败 | 检查 `config.yaml` 的 `base_url`、`api_key`、`model`；尝试切换 fallback_models |
| 云端 Actions 失败 | 检查 Secrets 配置；查看 Actions 日志错误详情 |
| 仪表盘打开空白 | 确保浏览器允许本地文件运行 JS；或用 HTTP 服务器 `python -m http.server` |
| 知识库链接不生效 | 确认 Obsidian 打开的是 `D:\Codex输出\视频知识库`；重新加载插件/重启 |

---

## 📄 许可证

个人使用，自由修改。

---

## 🙏 致谢

- 数据来源：新华社、人民日报、政府门户、统计局、海关、发改委、央行、证监会、社科院、券商研报等公开公共信息
- 技术栈：Python、Feedparser、Requests、LXML、SimHash、OpenAI SDK、Alpine.js、Chart.js
- 灵感来源：政治经济学、积累制度分析、空间修正理论、国家-市场边界研究
