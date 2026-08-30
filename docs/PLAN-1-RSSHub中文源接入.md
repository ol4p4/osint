# PLAN-1：RSSHub 中文源接入方案（替换失效的列表页采集）

> 状态：待执行 | 优先级：P1 | 预估工作量：半天（部署 1h + 配置 1h + 验证 2h）
> 背景：fetch_list.py 的 CSS 选择器与改版后的政府网站脱节，CI 实测全部 0 条；
> 且 gov.cn 首页为 JS 渲染页（894 字节），静态抓取天然拿不到。
> 核心思路：**不修选择器，换武器**——RSSHub 把"任意网站→标准 RSS"做成了社区维护的路由库，
> 我们只消费它的 RSS 输出，fetch_rss.py **零代码改动**即可接入。

---

## 一、为什么是 RSSHub（三个硬理由）

1. **零解析维护**：网站改版由 RSSHub 社区修路由，我们不再维护 `list_selector`。
2. **与现有管线完全兼容**：RSSHub 输出标准 RSS/XML，`fetch_rss.py` 的 feedparser 直接吃——接入 = 加 URL，不改一行抓取代码。
3. **中文源覆盖极广**：国务院/人社部/统计局/财新/华尔街见闻/知乎热榜/微博热搜/36氪 全有现成路由（这些正是 persona 信号监测清单想要的）。

## 二、部署架构（选 Vercel 免费方案）

```
                    ┌─────────────────────────────┐
                    │  RSSHub（Vercel 免费实例）     │
                    │  https://osint-rsshub.vercel.app │
                    └──────────┬──────────────────┘
                               │ 标准 RSS/XML
   GitHub Actions（美国）◄──────┤ 网络畅通（美国→美国）
   每 6h: fetch_rss 抓全部源 ────┘
        → translate(50/次) → citizen_impact(50/次) → push 仓库
                               │
   本地 OsintRefresh(每小时) ◄──┘ git pull（本地永远不需要直连 RSSHub）
        → data/refresh.py 合并 → dashboard_data.json → HTML
```

**关键决策：本地不需要访问 RSSHub**。数据流经 GitHub 中转，绕开国内网络对 vercel.app 的不稳定。
本地备用：可选 `docker run -d -p 1200:1200 diygod/rsshub` 供本地直抓调试。

## 三、部署步骤（一次性）

### 步骤 1：Vercel 部署 RSSHub（约 20 分钟）
1. 打开 https://github.com/DIYgod/RSSHub → Fork
2. Vercel → Add New Project → Import 刚 fork 的 RSSHub
3. Framework Preset 会自动识别；环境变量先不加（基础路由无需配置）
4. Deploy → 得到形如 `https://rsshub-xxx.vercel.app` 的域名
5. 验证：浏览器打开 `https://<域名>/gov/zhengce/zuixin`，应返回 XML（`<rss` 开头）

### 步骤 2：核对路由清单（30 分钟）
下表为候选路由，部署后逐个 `curl -s -o /dev/null -w "%{http_code}" https://<域名>/<路由>`
（200=可用；404=该路由不存在或需参数，去 https://docs.rsshub.app/zh/routes 搜源站名找替代）：

| 用途 | 路由 | 置信度 | 对应 persona 信号 |
|------|------|--------|------------------|
| 国务院最新政策 | `/gov/zhengce/zuixin` | 高（官方文档路由） | 政策线 |
| 知乎热榜 | `/zhihu/hotlist` | 高（最著名路由之一） | 社会情绪 |
| 华尔街见闻-全球财经 | `/wallstreetcn/news/global` | 高 | 宏观/资产线 |
| 36氪快讯 | `/36kr/newsflashes` | 高 | 科技/就业结构 |
| 微博热搜 | `/weibo/search/hotlist` | 中（可能需 cookie） | 社会情绪 |
| 国家统计局-最新发布 | `/gov/stats/*`（具体子路由部署后核对） | 中 | 数据线 |
| 人社部-新闻 | 搜索文档"mohrss"核对 | 中低（若无→备选） | 就业政策线 |
| 财新网 | `/caixin/...`（多子路由） | 中 | 综合 |

### 步骤 3：写入 sources.yaml（追加到 rss_sources，格式与现有源完全一致）
```yaml
  - name: 国务院-政策文件
    url: "https://osint-rsshub-xxx.vercel.app/gov/zhengce/zuixin"
    category: gov            # 归一化后为 gov
    weight: 1.3              # 政府源高权重
  - name: 知乎热榜
    url: "https://osint-rsshub-xxx.vercel.app/zhihu/hotlist"
    category: social
    weight: 0.8
  - name: 华尔街见闻-全球
    url: "https://osint-rsshub-xxx.vercel.app/wallstreetcn/news/global"
    category: finance
    weight: 1.0
  - name: 36氪-快讯
    url: "https://osint-rsshub-xxx.vercel.app/36kr/newsflashes"
    category: tech
    weight: 0.9
  # ...核对后继续追加
```

### 步骤 4：fetch_list.py 的处置
- **不删**：`fetch_list.py` 保留（接口已修好），作为 RSSHub 无路由时的"自写选择器"后备
- CI 的 `main_cloud.py` 里 List 步骤保留（0 条也无害，`|| true` 容错已有）
- sources.yaml 里旧的 `gov_portals/stats_sources/think_tanks` 三段**保留但注释掉条目**（选择器失效状态下抓它们只浪费 CI 时间：每源 15s 超时 × 10 源 ≈ 2.5 分钟/次）

## 四、抓取量与配额影响（要心里有数）

| 项 | 现状 | 接入后 |
|----|------|--------|
| RSS 源数 | 47（英文为主） | 47 + 5~8 中文 |
| 每日 raw | ~317 条 | 预计 +80~150 条（热榜类条目多但重复率高） |
| dedup 后 final | ~176 条 | 预计 200~260 条 |
| 翻译配额 | 50 条/次 × 4 次 = 200/天 | 需求约 200~260/天 → **每天约追平，周末清零** |
| citizen_impact | 50/次 × 4 次 | 同上（时间预算 8 分/次） |
| CI job 时长 | ~12 分钟 | +1~2 分钟（新源抓取） |

**注意**：热榜类（知乎/微博）条目生命周期极短（半天内被时间衰减压到 0.1 底分），最终能上仪表盘 Top200 的主要是 weight 高的政策/财经源——符合"信号监测"定位。

## 五、验证清单

1. Vercel 实例逐路由返回 XML ✓
2. 触发 CI（`gh workflow run daily.yml`）→ 日志确认新源 `[RSS] 国务院-政策文件: N items` 且 N>0
3. 一轮 CI 后本地 `git pull` + `refresh.py` → dashboard_data.json 出现中文源条目（`source_name` 含"国务院"等）
4. 仪表盘按时间排序 → 中文政策/热榜条目出现在 Top 区
5. 一周观察：翻译积压是否清零（`cn_title` 比例应回到 ≥80%）

## 六、回滚

- 所有新源在 sources.yaml 独立成段，出问题直接注释掉（fetch_rss 对坏源 15s 超时自动跳过，不会拖垮管线）
- Vercel 实例删除即完全下线，无任何本地残留

## 七、后续增强（可选，第二期）

1. RSSHub 配置 `ACCESS_KEY`（防止实例被外人滥用）
2. 加 Redis 缓存（Vercel 免费档有请求限额，缓存减少重复抓取）
3. 为 persona 信号监测清单逐项配专属路由（如统计局三大指标、青年失业率口径变化）
