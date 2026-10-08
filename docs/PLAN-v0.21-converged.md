# session-harvester × harvester-view 统一方案 v0.21

**三方评审收敛版** · 2026-10-08
输入：评审 A（v2.3.1 过滤链与维度审计）、评审 B（首轮架构建议）、评审 C（对 B 的事实核查）、
实施方（v0.19–v0.20.1 变更与架构判断）。
原则：**所有写进方案的主张均已按当前代码/数据实测核实**（第 1 节标注证据位置）；
互相矛盾的判断以实测为准；未核实的一律不进路线图。

---

## 1. 事实核查表（评审主张 → 实测结论）

| # | 主张 | 来源 | 实测结论 |
|---|---|---|---|
| F1 | view 缺 G3/G4 渲染页面 | B | **✗ 已过时**。renderCards/renderSkills 已在 v2.2–v2.3.1 落地（index.html:881/970），过滤链完整（rerenderReport 覆盖 5 种报告） |
| F2 | G4 关键字过滤失效（`hitQ(s.skill, s.q)` 引用错变量） | 实施方自查 | **✓ 真 bug**，已在 v2.3.1（a845b17）修复并推送 |
| F3 | CLI 已按根因聚合、API 未聚合（apiserve `_tool_rows` 直吐 `st.errors`，view 里 G1 仍是 79 行未聚合明细） | A | **✓ 成立**。`aggregate_error_roots()` 仅被 CLI render_report 调用 |
| F4 | "总失败 261 与逐工具 error 列口径不一致" | A | **✗ A 已自行撤回**（两者都等于 steps 表 status='error' 的 261） |
| F5 | cards 校验器零依赖下假报成功：降级解析器把 `evidence: \|` 读成字面量 `'\|'`（长度 1 非空→不报错）、引文核对拿到 `'\|'` 被 `len>=6` 过滤→ checked=0、结论仍写"可并入主库" | C | **✓ 成立**（cards.py:115–128 降级分支只认标量+流式序列；validate_card 无占位符检查；`cards new` 产物自带 `evidence: \|` 占位） |
| F6 | turn:null 警告不可达（cards.py:233 提前 return 在 turn 检查之前） | C | **✓ 成立** |
| F7 | 卡片池目录错位：`cards_pending/` 0 文件，真实 4 卡在 `~/.workbuddy/knowledge/cards/`，start.cmd:46 的 `--cards-root` 指向空目录 → view G3 读空柜 | C | **✓ 成立**（实测确认） |
| F8 | 模型维度实质不可用：model 覆盖 474/1946（24%）；DSH/AutoClaw/Copilot 等 adapter 未抽 `providerData.model`，仅 workbuddy_transcript 抽了；261 错误中 160 条归"(空)" | A/C | **✓ 成立**（grep 证实 adapter 均无 model 抽取） |
| F9 | "高步操作"检测 0%：40 步 Edit 死循环因每步"成功"而不可见 | A | **✓ 成立**（全库无长回合/连击/循环检测实现） |
| F10 | 跨建议/跨卡根因去重缺失：同一"Edit 前置"根因产出 2 条建议 + 1 张卡（kc-0001/kc-0004），零合并 | A/C | **✓ 成立** |
| F11 | 高频词统计缺失（无 n-gram/tfidf 任何实现） | A | **✓ 成立** |
| F12 | 蒸馏备料层已部分存在：`draft`（drafting.build_distill_packet 自包含蒸馏包）、`pack`（跨 agent 交接包）、`distill corpus/kb` | B 提及 | **✓ 证实**。缺的是产物登记、快照绑定、只读暴露端点 |
| F13 | 去重键必须用 `errstats.normalize_error` 归一模式而非原始文本 | B（原则） | **✓ 已实现于 aggregate_error_roots**，问题只在"没接到 API/导出路径"（同 F3） |

**核查小结**：评审 C 的 P0（校验器假报成功 + 目录错位）是本次最有价值的发现——
它证明 v0.19 的"引文核对升级"在零依赖配置下 100% 空转，且空壳卡拿到了通行证。
**验证机制假报成功比分析不准更危险**：它会让下游把空壳当资产。

---

## 2. 架构定论（四方收敛）

```
harvester（确定性引擎）          Agent（语义蒸馏执行者）         view（决策驾驶舱）
  捞取 / 归一 / 去重 / 打包   →   消费 draft/pack 任务包     →   呈现 / 触发 / 导出
  classify / 校验 / 指纹          产出卡片草稿（可抛弃层）        （只读 + 复制命令）
      ↓                                                                ↑
      └──── cards §8 + suggestions_meta + distilled meta（产物存档，带库快照指纹）────┘
```

三条不变量（违反即方案失败）：

1. **view 只读且不执行**：仅转发 GET；触发=复制命令，执行由人或 Agent 完成。
   （对我此前"view 勾选→直接喂 Agent"设想的一处修正，采纳评审 C。）
2. **去重口径单点收敛在 harvester**：`normalize_error` 为唯一聚类键，view 与导出只透传。
   （采纳评审 A/C：两条渲染路径分叉、两处去重逻辑迟早漂移。）
3. **产物可复现**：每份蒸馏/建议产物记录生成时的 `db_fingerprint`，模型升级后重跑
   才能区分"结论变了"还是"数据变了"。（采纳评审 A，补入我此前方案的缺口。）

---

## 3. 路线图

### P0 止血——数据信任 > 可读性 > 归因覆盖

| 项 | 内容 | 位置 | 规模 |
|---|---|---|---|
| **P0-1** | **cards 校验器完整性**：① 降级解析器支持块标量 `\|`/`\|-`/`>` 与块序列；② turn:null 警告挪出提前返回；③ 占位符卡（`<待补…>`/evidence=='\|'）判**错误级**，`cards new` 产物不得通过 validate；④ `evidence_checked==0` 在结论中显式输出"**未核对**"而非"通过"；⑤ **反向测试**：故意破坏 evidence 必须使断言变红 | cards.py + tests | ~50 行 |
| **P0-2** | **API 根因聚合接线**：`_tool_rows` 调 `aggregate_error_roots`，additive 加 `roots` 字段；view G1 优先渲染 roots（有则用、无则降级） | apiserve.py + index.html | ~15 行 |
| **P0-3** | **空态文案带计数**：G3/G4 过滤态显示"已过滤：0/N 张问题卡（全库 N 张均通过）"，区分"过滤掉了"与"功能坏了" | index.html | ~6 行 |
| **P0-4** | **卡片池对齐**：start.cmd 的 `--cards-root` 改指真实池（或在 start.cmd 内同步 cards_pending→真实池）；4 张既有卡归位；定稿"候选池 vs 主库"唯一路径 | start.cmd + 文档 | ~5 行 |
| **P0-5** | **adapter 补 model 抽取**：dsh/autoclaw/vscode_copilot 照 workbuddy_transcript 取法抽 `providerData.model`，目标覆盖 ≥95%，模型表"(空)"行失去意义 | adapters ×3 | ~30 行 |

### P1 管线与维度——让体系产生真实反馈

| 项 | 内容 | 说明 |
|---|---|---|
| **P1-1** | **采纳闭环（项目存亡级验收）**：建议池人工过一遍 → `suggest-status adopted/rejected` 落库 → 至少 1 条真实并入 AGENTS.md → 30 天后重跑 report-tools 前后对比 | 方案止损线的正解：体系的最终检验不是"分析多准"，而是有没有一条建议真的改变了 agent 行为 |
| **P1-2** | **跨建议/跨卡根因去重**：suggest-agents 与 triage 产出前先按 normalize_error 聚类键合并，同根因只出一条主条目+引用 | 确定性，零依赖 |
| **P1-3** | **交叉表**：错误类别 × harness × model 一条 SQL + 渲染，回答"哪个 harness 的哪类坑最多" | 依赖 P0-5（否则 model 列一半是空） |
| **P1-4** | **export-analysis 统一导出器**：sessions/tools/errors/skills/triage 五类，(class, pattern) 去重，人读 md + 机器读 JSON 同源同口径；view 导出按钮改调它 | 收敛我此前方案 P1 与评审 A 的导出诉求 |

### P2 真缺口——确定性检测 + 蒸馏落位

| 项 | 内容 | 说明 |
|---|---|---|
| **P2-1** | **report-chains**：长回合（步数>p95）/ 同工具连击（连续 N 步同工具）/ 序列循环（相邻去重后 A→B 周期）/ 空转率（错误后重试>3 仍失败）/ 高步会话 Top N；全部从现有 steps(sid,seq,tool,phase,status) 确定性可算 | 补 S1"高步操作"0% 缺口；Agent 陷 40 步死循环必须可见 |
| **P2-2** | **report-keywords**：n-gram 词频（可配停用词），落独立表 + 只读端点，供 view"高频词捞取" | 补 S2 缺口 |
| **P2-3** | **蒸馏产物落位**：`draft/pack` 产物登记进独立 meta 库（带 db_fingerprint 快照绑定 + prompt 版本），新增 `/api/distilled` 只读端点；view 只加"蒸馏产物"页签 + 复制命令按钮，**零分析逻辑** | 备料层已存在（F12），缺的只是登记/暴露/呈现 |
| **P2-4** | **主题级会话去重第一版**：任务签名 = 首条用户消息归一 + 工具序列 top-k 哈希；高频签名 = "反复发生的同类任务"，确定性聚类 | 会话维度 S2 的"高频+去重" |

### P3 明确不做

- view 内不放去重/蒸馏/归因逻辑（保持无状态、只读、可抛弃、单文件可测）；
- view 不执行任何命令（只复制）；
- LLM 不进 harvester 核心（确定性层保持可测试、可复现）；
- 不先做任何 view 新页面（展厅已盖齐，先补管线与卡池产能）。

---

## 4. 验收标准（全部可测）

- **P0-1**：占位符卡 validate 输出错误级且结论 ≠"可并入主库"；`evidence: |-` 块标量 fixture 下 evidence_checked>0；故意破坏 evidence 的反向测试变红；4 张既有卡在补丁后重新校验并出具新结论。
- **P0-2**：`/api/reports/tools` 的 Edit 带 roots 且 ≤ 约 11 行、每行含 class；view G1 与 CLI 报告同形。
- **P0-4**：start.cmd 启动后 view G3 显示 4 张真实卡（非空柜）。
- **P0-5**：dsh/autoclaw/copilot 会话 model 覆盖 ≥95%；模型表"(空)"行错误占比 <20%。
- **P1-1**：≥1 条建议 status=adopted 且出现在 AGENTS.md；30 天后 report-tools 对比有数字结论。
- **P1-2**：同一根因在 suggest-agents + triage 合并后只出 1 条主条目。
- **P2-1**：对真实库注入一条 40 步同工具序列的合成会话，report-chains 能报出（防"恒真测试"重演）。
- **P2-3**：任一蒸馏产物可通过 /api/distilled 查到，且能回答"基于哪个库快照生成"。

## 5. 执行顺序与理由

```
P0-1（信任地基）→ P0-2 ∥ P0-3（并行小改）→ P0-4 → P0-5
→ P1-1（人工参与，立即启动不等技术）→ P1-2 → P1-3 → P1-4
→ P2-1 → P2-3 → P2-2 → P2-4
```

理由：P0-1 排第一——噪声进噪声出，校验器假报成功会让后面所有分析失去意义；
P1-1 不依赖任何代码，是止损线的唯一解，应与技术项并行推进；
P1-3 排在 P0-5 之后（model 列否则为空）；P2-1 优先于 P2-2（死循环比高频词更贵）。

---

*本方案取代此前对话中的所有零散 roadmap（含实施方 v0.20 回复中的 P1/P2/P3 表述）；
与 HANDOFF-product-usability.md 的已完成项不冲突，仅约束后续工作。*
