# 「参谋系统」项目文档

## 系统架构

### 四层角色分离
- **情报官（云端）**：GitHub Actions 抓取 RSS/政府/国际源
- **参谋长（本地）**：生成假设/现状分析 → Mimo-v2.5-free
- **裁判（本地）**：验证判定/复盘 → Nemotron-3.5-lightning-free
- **用户（终审）**：复核判定

### 模型红线测试结果（2026-08-20 已完成）
- **DeepSeek**：从不拒绝但输出极其官方（"中国政府高度重视""关乎亿万人民福祉"），完全无法用于批判性分析。**弃用。**
- **Mimo**：最优秀——2800+字符详细分析，有数据表格，结构清晰，直接分析风险和博弈。**参谋长（生成假设）。**
- **Nemotron**：学术风格好——用"代际契约的结构性崩塌""阶层权益重新分配"等专业术语，从不拒绝。**裁判（验证判定）。**

### 输出目录
- 产物：`D:\Codex输出\osint_卫星图\`
- 知识库：`D:\Codex输出\视频知识库\`
- API：OpenCode Zen 免费代理（伪造请求头调用）

## 实施进度

### P0 修复 AI 调用 ✅ 已完成
- [x] 移除 OpenAI SDK，改用 urllib 伪造请求头
- [x] 移植早报的伪造头：User-Agent: opencode/latest/1.3.15/cli
- [x] 中文响应乱码修复（markdown清理+max_tokens=8192）
- [x] 完整分析测试通过：3条情报全部正确分析，置信度7-9

### P1 模型红线测试 ✅ 已完成
- [x] 三模型 × 5 问题测试完成
- [x] 结论：Mimo参谋长 + Nemotron裁判
- [x] config.yaml 已更新

### 待续
- [ ] P2：假设引擎开发
- [ ] P3：现状基线报告
- [ ] P4：云端采集加东亚源
- [ ] P5：知识库接入
- [ ] P6：每周循环 + 仪表盘
- [ ] P7：自动化与早报打通

## 关键设计决策

### 假设生命周期
```
灵感闪记(idea) → 拆子命题(2-6个) → 假设卡(幅度+时间窗+可信度)
→ 每周证据更新 → 到期验证(AI判定+用户复核) → 偏差复盘 → 回灌权重
```

### 知识库扩展
- `wiki/views/`：观点卡（原始观点）
- `wiki/hypotheses/`：假设卡（带幅度/时间窗/指标）
- `wiki/verifications/`：验证复盘记录
- `wiki/baselines/`：现状基线报告

### AI调用约束（必须）
```python
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}",
    "User-Agent": "opencode/latest/1.3.15/cli",
    "x-opencode-client": "cli",
    "x-opencode-session": uuid.uuid4().hex,
}
```

### 已知问题
- 模型可能返回 ```json 代码块包裹的JSON（已加清理）
- max_tokens=8192（已调整）
- 三模型的中文输出通过文件写入验证正常（控制台显示乱码是Windows编码问题）

---
*最后更新：2026-08-20 - P0/P1完成*