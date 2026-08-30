# PLAN-2：ACH 假设矩阵升级（贝叶斯校准 + 证伪优先）

> 状态：待执行 | 优先级：P2 | 预估工作量：分三期（M1 一天 / M2 一天 / M3 半天）
> 背景：CIA 分析师 Richards Heuer 的 ACH（竞争性假设分析）方法论，开源参考：
> - Burton/Analysis-of-Competing-Hypotheses（109★，Heuer 本人授权开源版）
> - lightcaptainguy/bayesian-ach-reasoning-prototype（ACH+贝叶斯更新）
> - ApartsinProjects/EMR-ACH（LLM 地缘政治事件预测，与我们最接近）
>
> 现状缺陷：verify_hypothesis 让 AI「拍脑袋」输出 new_confidence——
> ① 每个假设孤立验证，不看证据对**其他假设**的影响；
> ② 只堆支持证据，没有系统化证伪（falsification_criteria 大多为空）；
> ③ 置信度无概率语义，两轮之间不可比。

---

## 一、方法论：ACH 的三个核心转变

| 维度 | 现状（假设树） | ACH 升级后 |
|------|--------------|-----------|
| 验证单位 | 单假设 ± 证据列表 | **证据 × 全部假设** 的诊断矩阵 |
| 评分逻辑 | 支持证据堆叠 → 置信度上升 | **反驳证据加权** → 找最先被杀死的假设 |
| 证据价值 | 所有证据同等 | **诊断性证据**优先（能区分假设的证据才值钱） |
| 置信度 | AI 直接拍 0-1 数 | 贝叶斯后验：`P(H|E) = P(E|H)·P(H) / P(E)`，可追溯可复盘 |

核心洞察（Heuer 原话意译）：**一条同时支持所有假设的证据没有价值；
分析的目的不是证明你对，而是找出哪条证据最能区分相互竞争的假设。**

## 二、数据模型（新增 data/hypotheses/ach_matrix.json）

```jsonc
{
  "version": 1,
  "updated": "2026-08-30",
  "hypotheses": [                       // 行：8 个 major 假设（可扩展到 medium）
    {"id": "hyp_e2b46e32", "title": "东亚三国现代化进程趋同", "prior": 0.68}
  ],
  "evidence": [                          // 列：每条证据的诊断记录
    {
      "id": "AEN20260827004751315",
      "date": "2026-08-27",
      "summary": "韩国央行上调2026增长前景，芯片出口强劲",
      "diagnosis": {                     // 每证据 × 每假设的判定
        "hyp_e2b46e32": {"code": "C", "lr": 1.2, "note": "出口韧性支持趋同叙事"},
        "hyp_d26158d3": {"code": "N", "lr": 1.0, "note": "与社保路径无关"},
        "hyp_HM003":     {"code": "I", "lr": 0.7, "note": "芯片景气与AI算力受限假设相斥"}
      }
    }
  ],
  "scoring": {                           // 派生（每次重算）
    "hyp_e2b46e32": {"posterior": 0.66, "refute_hits": 2, "support_hits": 5, "rank": 1}
  }
}
```

判定码语义：
- **C**（Consistent，一致）：证据与假设预期相符 → `lr>1`（默认 1.2~1.5，按证据强度）
- **I**（Inconsistent，不一致）：证据与假设预期相斥 → `lr<1`（0.5~0.8）——**ACH 的灵魂**
- **N**（Neutral，中性/非诊断）：证据与该假设无关 → `lr=1.0`（不影响）
- **二次证据**（与假设核心强相关且权威源）lr 加倍

## 三、贝叶斯更新公式（替代 AI 拍脑袋）

```
先验 odds(H) = prior / (1 - prior)
每条证据:  odds(H|E) = odds(H) × LR(E)        （LR = P(E|H) / P(E|¬H)，由 AI 估计 + 阈值锚定）
多证据独立假设下: odds 累乘
后验 confidence = odds / (1 + odds)
```

LR 的锚定规则（防 AI 乱给）：
1. 证据命中某 indicator 的 `threshold_refute` → 该证据对该假设强制 `lr ≤ 0.5`
2. 证据命中 `threshold_support` → `lr ≥ 1.5`
3. 其余由 AI 在 [0.6, 1.4] 区间估计（诊断性强的证据允许超出）
4. `falsification_criteria` 为空的假设：**先由 AI 补写**（M1 的一部分），再参与更新

## 四、代码设计（新增 1 个模块 + 2 处接入）

### 新增 `local/ach_matrix.py`（预计 ~260 行）

```python
# -*- coding: utf-8 -*-
"""ACH 矩阵：证据×假设诊断 + 贝叶斯置信度更新"""

class ACHMatrix:
    def __init__(self, matrix_file): ...          # 加载/初始化 ach_matrix.json
    def sync_hypotheses(self, hyps): ...          # 树有变化时同步行（新增假设 prior 继承）
    def find_undiagnosed(self, hyps, intel): ...  # 找 evidence_log 里未入矩阵的证据

    def ai_diagnose(self, evidence, hyps):
        """一次 AI 调用：单条证据 × 全部活跃假设 → [{hyp_id, code, lr, note}]
        prompt 要点：
        - 输出每个假设的 C/I/N + 一句诊断理由
        - 特别提示：'找出这条证据最伤害哪个假设'（证伪优先）
        - 若证据命中某假设 indicators 的 threshold_refute/support，标注出来（LR 锚定用）
        """

    def anchor_lr(self, code, lr, evidence, hyp):
        """LR 锚定：threshold_refute 命中→cap 0.5；threshold_support 命中→floor 1.5"""

    def bayesian_update(self, hyps):
        """按上面的公式重算每个假设的 posterior → 写回 confidence 字段"""

    def sensitivity(self):
        """敏感性分析：逐条虚拟删除证据，看排名变化幅度 → 输出 top-5 诊断性证据"""

    def export_markdown(self):
        """Obsidian 表格：假设行 × 证据列(最近30条) + C/I/N 着色 + 排名变化
        输出 data/reports/ach_matrix_YYYYMMDD.md"""

    def save(self): ...
```

### 接入点 1：每周循环（`hypothesis_engine.run_weekly_cycle` 内，验证步骤前）

```python
# 替换现有 verify_hypothesis 的 AI 拍脑袋流程：
ach = ACHMatrix(matrix_file)
ach.sync_hypotheses(hyps)
undiagnosed = ach.find_undiagnosed(hyps, intel_items)
for ev in undiagnosed:
    diag = ach.ai_diagnose(ev, active_majors)     # 复用 analyzer 的 API 调用
    ach.record(ev, diag)
ach.bayesian_update(hyps)                          # 统一重算 posterior → confidence
ach.export_markdown()                              # 周报自动嵌入矩阵
ach.save()
```

### 接入点 2：CI 增量诊断（可选，第二期）

`link_intel_hyp.py` 之后加一步：只诊断**当天新增证据**（每条 1 次 AI 调用），
矩阵在仓库里持久化（ach_matrix.json 随 auto commit 推送），本地 pull 同步。

### 接入点 3：仪表盘（M3）

假设卡片（hypModal 弹层）新增「ACH 排名」徽章 + 「诊断性证据 top3」+ 置信度变化小箭头（↑↓）。
gen_dashboard 读 ach_matrix.json 的 scoring 段即可。

## 五、falsification_criteria 批量补全（M1 前置任务）

68 个树节点里该字段几乎全空——ACH 的 LR 锚定依赖它。一次性脚本：

```python
# tools/fill_falsification.py（跑一次即弃）
# 对每个 level=major/medium 的假设：取 title+rationale+indicators，
# 1 次 AI 调用生成 2~3 条可证伪判据（可观察、有数字阈值、有时间线），
# 写回 falsification_criteria + 补齐 indicators[].threshold_refute
```

- 输入：68 节点 ÷ batch 4 ≈ 17 次 AI 调用，约 5 分钟
- 人工抽检 10% 后接受

## 六、验证标准

| 里程碑 | 验证方式 |
|--------|---------|
| M1（矩阵+补全） | 68 节点 falsification_criteria 100% 非空；手动抽 5 条证据进矩阵，判定与人工常识一致率 ≥80% |
| M2（贝叶斯） | 构造 3 条假证据序列（连续反驳某假设）→ 该假设 confidence 单调下降且 refuted 触发正确；连续支持 → 上升但**永远 <0.95**（防过度自信，odds 上限） |
| M3（仪表盘） | 周报含矩阵表格；假设弹层显示排名与箭头 |

## 七、风险与对策

1. **AI 诊断质量不稳** → prompt 里强制「先复述证据要点再判定」；抽样人工复核
2. **证据独立性假设不成立**（同一事件多条报道）→ link_intel_hyp 关联时按事件聚类，同事件只入矩阵一次
3. **矩阵膨胀** → 只保留最近 60 天证据在活动矩阵，更老的归档进 weekly report
4. **8 个假设太多导致矩阵稀疏** → 只对 major 8 个 + 当期争议最大的 4 个 medium 建全矩阵，其余 mini 版

## 八、与现有代码的兼容性

- `verify_hypothesis()` 保留但降级为「无证据可诊断时的兜底」，不再主用
- `falsification_criteria` 语义不变（仍由 verify/回填逻辑写），ACH 只读
- `active_hypotheses.json` 结构不变，新增字段（posterior/rank）向下兼容旧 dashboard
- 全部新逻辑在 `local/ach_matrix.py` 单文件内，出问题删掉两个接入点即回滚
