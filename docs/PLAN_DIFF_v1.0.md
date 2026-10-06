# 当前成果（session-harvester v0.5）vs 《轨迹分析工具设计方案 v1.0》差异对照

> 日期：2026-10-06 | 对照基准：Desktop《轨迹分析工具设计方案.md》（2026-10-05）
> 结论先行：两者**定位不同而非同一工具的两个版本**。方案是「公司内 20 人、群体画像、
> SQLite 数仓为永久资产」的诊断系统；harvester 是「个人、跨设备、零依赖、采集广度优先」
> 的提取蒸馏套件。方案 §3–§8 中 harvester 已覆盖约三分之一（集中在 §5 全部、§4 一半、
> §6 一小半）；方案独有的是**诊断深度**（steps 规范化 + 重试/放弃/三分类）与
> **多用户纪律**；harvester 独有的是**采集广度与蒸馏管线**。

## 1. 逐层对照

### 采集层（方案 §4 ↔ harvester adapters/）

| 方案要求 | harvester 现状 | 判定 |
|---|---|---|
| WorkBuddy transcript adapter（259.6MB jsonl，主路径） | 未做。现有 workbuddy.py 只读 memory-log（自述总结），v0.5 已在 README 诚实标注 🔶 | **最大缺口** |
| memory-log 降级为独立 category + 标注"非对话轨迹" | 只改了 README 文案，adapter 未拆 category | 半覆盖 |
| DSH adapter（zstd 可选依赖、schema 指纹） | 未做；本机 `~/.dsh/sessions` 已确认存在，是真实缺口 | 缺口 |
| dsh-chat-import 仅作交叉验证器/格式桥 | 观点一致（COMPARISON.md 修订已采纳"当桥不当地基"） | 理念已同步 |
| 增量采集（字节偏移游标） | 明确未做（每次索引全量重建，规模内够用） | 缺口 |
| 系统提示注入保留（system_inject） | 未做（transcript 未接入） | 随 transcript 缺口 |
| （方案未要求）多源探测与接入 | probe 固化探测、AutoClaw（含保真度修复+schema 守卫）、VS Code 三路存储、ChatGPT/Claude/DeepSeek 官方导出导入器、weblogin 兜底 | **harvester 独有** |

### 存储层（方案 §3 §5 ↔ harvester indexing.py models.py）

| 方案要求 | harvester 现状 | 判定 |
|---|---|---|
| 规范化 steps 表（role/kind/step_id） | 无。消息只有 role(user/assistant/note)+raw payload | 缺口（结构性） |
| tool_calls/tool_results 独立结构化表 | 无。v0.5 统计从 note 文本正则解析（toolstats.py 注释已声明简化口径，不做 toolCallId 配对） | 缺口 |
| 锚点 = session+turn+message_id+step_id | SRC 溯源锚点只到「来源+会话+标题+日期」；reader 有 turn 概念但未持久化 | 半覆盖（粒度不够） |
| FTS5 bigram 预分词（双侧改写+原文列） | **v0.5 已实装，与方案逐条吻合**；实测 10/10 召回 | 全覆盖 |
| 增量游标 / schema 指纹入库 | AutoClaw 有 PRAGMA 守卫+迁移 seq，但不入 sources 表；其余源无指纹 | 半覆盖 |
| 标准 SQL 可移植纪律 | harvester 未承诺（个人工具可接受） | 不适用 |

### 诊断层（方案 §6 ↔ harvester cli.py toolstats.py）

| 方案命令 | harvester 对应 | 判定 |
|---|---|---|
| report-tools（失败率/重试率/平均重试/放弃率/绕行检测/--since） | `report-tools` 已有调用数/失败率/错误码 TOP；**无**重试率、放弃率、绕行、时间窗 | 1/3（第一版硬数据已能出） |
| report-errors（三分类 env/tool_interface/context + 位置分桶） | 无（只有原始错误码） | 缺口 |
| query --fts（返回锚点+原文） | `search` 已有（bigram+原文 snippet），锚点只到会话级 | 基本覆盖 |
| behavior --skill（群体行为序列） | 未做 | 缺口（依赖 steps 表） |
| regress（回归语料跑批 diff） | 未做 | 缺口 |
| ingest（增量+指纹） | 无（index 为 FTS 索引非数仓 ingest） | 缺口 |

### 提炼层（方案 §7 §8 ↔ harvester distill.py + DISTILL_PLAYBOOK + skill）

| 方案要求 | harvester 现状 | 判定 |
|---|---|---|
| LLM 错误归因（置信度+抽样复核） | 刻意无 LLM：语义步骤交给宿主无关 playbook + 人工/agent | **理念分歧**（见 §3） |
| 知识卡片（frontmatter 冻结 + anchors + evidence + validate） | kb-init/kb-stats + 9 话题文件；形态是 topics+台账，**无** frontmatter 规范与锚点校验 | 雏形（规范差一截） |
| 可抛弃设计（LLM 组件限时 2 人天） | 对应物是 playbook 与 skill 薄绑定分离 | 等价理念 |

### harvester 独有、方案未覆盖的

- **采集广度**：官方导出通道（ChatGPT/Claude/DeepSeek 零风控）、weblogin 三级兜底、
  probe 文件系统签名探测、VS Code workspace chatSessions 补丁日志重构。
- **蒸馏管线**：outline→选择→分类导出→aggregate 语料→kb 知识库（2.1 的机械半边）。
- **MCP server**（4 工具 stdio JSON-RPC）与 **pack 上下文交接包**（跨 agent 接续）。
- **read --turn 分层下钻**、零第三方依赖纪律、79 个单测。

## 2. 覆盖率估算（按方案 §3–§8 条目加权）

| 方案章节 | 覆盖度 | 说明 |
|---|---|---|
| §5 中文检索 | 100% | 且修正一处：方案称"snippet() 正常可用"，实测 bigram 列上 FTS snippet 输出二元组汤，harvester 改 Python 侧原文截窗——方案此处应更正 |
| §4 采集层 | ~40% | 桥理念同步；transcript/DSH/增量三缺口 |
| §6 诊断层 | ~30% | report-tools 第一版 + query |
| §3 存储层 | ~35% | bigram 全覆盖；steps/结构化表/锚点粒度缺 |
| §7 §8 提炼层 | ~25% | kb 雏形；卡片规范与 LLM 归因未做 |
| §10 隐私 | 0% | 个人工具暂不需要；扩到 20 人前必须补（user_hash/知情同意） |

## 3. 三个需要决策的分歧点

1. **LLM 的位置**：方案把归因/卡片做成"可抛弃 LLM 组件"；harvester 把语义步骤留在
   playbook+skill（宿主无关）。不冲突——方案 M3 的 cards validate/置信度规则可以约束
   playbook 执行者，建议合并采纳。
2. **数仓 vs 轻索引**：方案的 steps 规范化表是重试率/放弃率/behavior 的**前置条件**，
   note 文本正则到不了（toolstats 已声明简化口径）。若坚持方案，M1 的第一批工作不是
   写新命令而是**把 tool_result 从 note 升级为结构化表**。
3. **范围**：方案不管网页 Chat 与官方导出（harvester 强项），harvester 不管群体画像
   （方案目标）。合并路线：harvester 做采集广度层，方案的 schema+诊断层在其上做深度，
   WorkBuddy transcript adapter 是双方路线的**共同第一步**。

## 4. 合并建议（若按方案推进）

1. M0 对账项照做（dsh-chat-import 单会话实测），同时把 harvester 的 AutoClaw
   保真度判据（request 去重/answer 去重/中英思考合并）写进字段映射表——这些是
   方案评审报告里没有的实测结论。
2. transcript adapter 直接复用 harvester 的 SessionRecord/adapter 契约与 schema
   守卫模式，产出落到方案 steps 表。
3. toolstats.py 保留为"无数仓时的退化路径"，升级版从 tool_results 表算重试/放弃。
4. 卡片规范按方案 §8 冻结 frontmatter，kb 话题文件迁移时补 anchors。
