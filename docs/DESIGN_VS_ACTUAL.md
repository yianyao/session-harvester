# 设计方案 vs 实际交付 —— 比对分析报告

> 比对对象：《Agent 会话轨迹分析工具——设计方案 v1.0》（2026-10-05，桌面文档）
> 对照实际：session-harvester（2026-10-06 v0.10 收口状态，含 DB 实证；v0.10 补齐 G2/G3 确定性一半）
> 方法：逐节映射，全部结论有代码/DB 证据；区分"未做"、"变形实现（更优/不同）"、"有意偏离"。

---

## 0. 结论速览

| 设计目标 | 状态 | 一句话 |
|---|---|---|
| G1 (2.2) 工具/技能使用诊断 | ✅ 完成 | `report-tools` 含失败率/重试/放弃率；v0.10 补 `--since` 时间窗（绕行检测仍缺） |
| G2 (2.3) Harness 踩坑发现 | ✅ 完成 | `report-errors` 三分类+位置分桶（真实库 243 错误 100% 归类）+ `suggest-agents` 建议池（9 条，人工并入） |
| G3 (2.1) 知识卡片 | 🔶 半程 | `cards validate`（frontmatter 规范+锚点查库）已实装并产出首张已验证卡片；extract 仍走人工（LLM 层按需）；归一决策：卡片=候选池、kb=主库 |
| G4 (2.4) 行为画像视图 | 🔶 主干完成 | `report-skill`（v0.11）：按 skill 聚合调用/触发任务/调用后行为链/锚点，--skill 深挖=可蒸馏清单；motif 频率矩阵与群体视图未做 |

**总体判断（v0.10 更新）**：设计核心决策"厚语料、薄分析"被执行且**语料层远超设计**（双源 → 9 源 1949 会话）；诊断层 G1/G2 确定性部分全部落地（report-tools/report-errors/suggest-agents/cards validate），"诊断→修改闭环"最后一公里已通（首份建议池 9 条 + 首张已验证卡片）；提炼层 LLM 组件按设计"可抛弃"姿态仍未建。

---

## 1. 逐节比对

### §2 总体架构

| 设计 | 实际 | 判定 |
|---|---|---|
| 采集层"双源双轨"（WorkBuddy + DSH + dsh-chat-import 桥） | 9 个数据源实装（含设计外的 VS Code/AutoClaw/DeepSeek 官方导出/ChatGPT/Claude 导出/元宝/千问/豆包） | ✅ 超额 |
| 存储层 SQLite + FTS5 bigram | `harvester.db`：sessions/messages/steps + FTS5 | ✅（schema 形态有偏离，见 §3） |
| 诊断层确定性无 LLM | report-tools / report-traces / search / read，全确定性 | ✅ 部分 |
| 提炼层 LLM 可抛弃（错误归因/卡片生成/交叉验证） | 未建独立组件；由 skill `session-knowledge-distill` 人工执行替代 | 🔶 变形 |

### §3 统一内部 schema v1

| 设计表 | 实际 | 判定 |
|---|---|---|
| sources（含 schema_fingerprint） | 无独立表；来源记在 sessions.source | ❌ 未做（指纹由两处替代：AutoClaw adapter 列结构守卫、verify/schema_canary.py 直采指纹） |
| sessions（user_hash / started_at / ended_at / turn_count） | sessions(sid, source, session_id, title, category, created_at, updated_at, file) | 🔶 变形：无 user_hash（单用户场景暂无此需求，设计隐私约束第 1 条未触发） |
| steps（role/kind 五分类/ts/content） | steps(sid, seq, ts, tool, phase, status, error, detail)——工具 I/O 专用表，对话正文在 messages | 🔶 变形：kind 五分类未按设计落列；tool_call/tool_result 以 phase/status 表达 |
| tool_calls / tool_results 独立表 | 并入 steps（tool + status + detail） | 🔶 合并实现，够用 |
| cards 表 | 无 | ❌ 未做 |
| steps_fts | messages（FTS5 bigram，raw 列存原文） | ✅（表名不同） |
| 锚点 = session_id+turn+message_id+step_id | sid + seq（steps）；报告行/语料带 `<!-- SRC: 来源|id|标题|日期 -->` 溯源锚 | 🔶 弱化：可溯源到会话，**无 turn/message_id 级锚点** |
| 标准 SQL 保迁移退路 | 未验证可移植性（依赖 SQLite FTS5） | 🔶 未执行该纪律 |

### §4 采集层

| 设计 | 实际 | 判定 |
|---|---|---|
| 4.1 WorkBuddy adapter（transcript 主路径，system_inject 不可丢） | workbuddy_transcript.py（v0.6）：user_query 提取、harness 注入上下文独立 note | ✅ |
| 4.1 memory-log 降级独立 category | workbuddy.py 标注"非对话轨迹" | ✅ |
| 4.1 增量采集（字节偏移游标持久化） | indexing.py 明确"整库重建，毫秒级，简单优于增量；变大再考虑 mtime 增量" | ⚪ **有意偏离**（有记录的决策，非遗漏） |
| 4.2 DSH adapter（不依赖 dsh-tools、zstd 分层降级） | dsh.py：compression.zstd→zstandard→zstd.exe→node 分层，全不可用则 STUB；approval/sandbox note | ✅ |
| 4.2 schema 指纹 + 漂移单 parser 隔离 | 无 sources.schema_fingerprint；替代物 = AutoClaw 守卫 + canary（覆盖直采源，**不覆盖 DSH**） | 🔶 部分 |
| 4.2 OTel span 旁路统计 | report-traces（p50/p95/失败率/取消率，与 steps 互校） | ✅ |
| 4.3 dsh-chat-import 交叉验证器 | 无对账记录（走了"真机核验自有 parser"路线） | ❌ 未做（该桥的价值已随 9 源格局稀释） |

### §5 中文检索

| 设计 | 实际 | 判定 |
|---|---|---|
| FTS5 bigram 双侧改写、2-5 字词 100%、原文列、snippet | 全部实现（v0.5，tests/test_v05 覆盖） | ✅ |
| 排除 trigram/detail=none | 已按结论落地 | ✅ |

### §6 诊断层 CLI

| 设计命令 | 实际 | 判定 |
|---|---|---|
| ingest --source（增量+指纹） | index（整库重建，无增量无指纹） | 🔶 有意偏离（见 §3/§4） |
| report-tools --since 30d | report-tools：失败率/重试率/放弃率 + `--since`（v0.10）✅；绕行检测（连续相同工具调用）未做；放弃口径与设计不同（设计：连续失败≥3 次后转向=放弃；实际：error 后同会话同工具未再调用=放弃） | 🔶 主干完成，2 项缺口 |
| report-errors（三分类+轨迹位置分桶） | `errstats.py`：env/tool_interface/context 正则映射（规则序=优先级）+ 开场/中途/收尾分桶 + 归一模式聚类（路径/引号/数字→占位符）带锚点；真实库 243 错误 100% 归类（118 用法/102 环境/23 目标态） | ✅ |
| query --fts | search（bigram、高亮摘要、raw 展示） | ✅（更名） |
| behavior --skill（G4 motif 频率） | `behstats.py` report-skill：每 skill 的调用数/成功率/触发任务(args)/调用后工具链 top5/锚点清单；深挖模式输出全部调用点供 Agent 蒸馏 | 🔶 主干完成（motif 矩阵未做） |
| cards extract / cards validate | `cards.py` validate：frontmatter 必填字段/枚举/confidence 校验 + 锚点查索引库（PyYAML 可选依赖，无则降级逐行提取）；extract 仍人工 | 🔶 validate ✅、extract 留白（LLM 层按需） |
| regress 回归语料跑批 | 不存在（等价物=132 个单测，但非全流程回归语料） | 🔶 替代物不等价 |

### §7 提炼层

| 设计 | 实际 | 判定 |
|---|---|---|
| 错误归因组件（三分类+置信度+强制原文引用） | 未做 | ❌ |
| 知识卡片生成组件 | 未做（人工蒸馏经 playbook） | ❌ |
| 交叉验证（统计为准+告警、每批抽 10 条复核、<80% 收紧） | 未做机制化；skill 流程中有人工环节但无一致率门槛 | ❌ |
| "LLM 组件限时 2 人天、prompt 视为耗材" | 纪律本身被遵守的另一面：**干脆没建**，等待真实需求触发 | ⚪ 符合止损精神 |

### §8 知识卡片规范

frontmatter 字段（id/title/type/tags/anchors/evidence/confidence/created）——v0.10 起 `cards validate` 消费该规范（必填字段/枚举/confidence 区间/锚点查索引库）。**归一决策（2026-10-06）**：卡片目录（~/.workbuddy/knowledge/cards）= 机器可校验的**候选池**；kb-init/playbook 知识库 = 人工维护**主库**；卡片 validate 通过后人工并入主库——一池一库，双轨消除。首张已验证卡片：kc-20261006-0001-edit-before-read.md。

### §9 里程碑

| 阶段 | 设计验收 | 实际 | 判定 |
|---|---|---|---|
| M0 | 双源映射表签字、指纹机制就绪 | schema 实际演进为 sessions/messages/steps（v0.6 冻结）；指纹机制未建 | 🔶 变形完成 |
| M1 | 双 adapter+建库+report-tools；Top3 失败工具可回答；三项数字留档 | **超额**：9 源 1949 会话/53778 消息；report-tools 可回答；建库时长/体积有 memory 留档 | ✅ |
| M2 | 错误三分类+AGENTS.md 条目建议+回归语料 | 三分类 ✅、条目建议 ✅（suggest-agents 建议池 9 条，首条 Edit/Write 前先 Read 实测 102 次）；回归语料未做 | 🔶 主干完成（2026-10-06 v0.10） |
| M3 | 卡片库+LLM 提炼+交叉验证 ≥80% | 未做机制；playbook+skill 人工蒸馏已在产出 | ❌（变形替代在跑） |
| 止损线 | M1 后一个月无实际修改则停止 | **已触发实际修改**（AGENTS.md 由人工依据蒸馏结论持续更新）——但注意：修改来源是人工蒸馏而非设计的诊断→建议管线 | ✅ 项目存活理由成立 |

### §10 隐私硬约束

| 条款 | 状态 |
|---|---|
| user_id 只存哈希 | ⚪ 未触发（单用户，库中无身份字段） |
| 画像仅群体层面 | ⚪ 部分解除（report-skill 已覆盖 skill 维度行为画像） |
| 20 人语料知情同意 | ⚪ 未触发（尚未纳入多user语料） |
| 禁止能力/态度推断 | ✅ 蒸馏 skill 遵守 |

### §11 风险清单执行情况

| 风险 | 对策落地 |
|---|---|
| 1 DSH 0.3 漂移 | 🔶 指纹未建；隔离原则已体现（单 parser），canary 思路已延伸到直采源 |
| 2 SQLite 规模 | ✅ 53778 消息远在安全区；整库重建秒级 |
| 3 system_inject 丢失 | ✅ 自研 adapter 保留（风险源 dsh-chat-import 已弃用） |
| 4 LLM 归因反向错误 | ⚪ 未触发（组件未建） |
| 5 回归语料覆盖 | ❌ 未建 |
| 6 dsh-chat-import 实测不符 | ✅ 风险源已移除 |
| 7 过度投入被模型淘汰 | ✅ 执行良好（诊断层薄、提炼层未建） |

---

## 2. 超出设计的交付（设计中不存在）

- **网页 Chat 三平台直采管线**（元宝 1223 / 千问 117 / 豆包 3 会话）+ app-driven capture 方法学 + `schema_canary` 指纹探针 + RECON 侦察报告与备用通道
- **三家官方导出 adapter**（DeepSeek 真机核验 316 会话 mapping DAG / ChatGPT / Claude）
- **MCP server**（4 工具，任意 agent 运行时直查）与 **pack 上下文交接**
- **weblogin 登录态三级流程**、**report-traces**、**132 个单测**

## 3. 未完成清单（按设计原文，建议优先级）

v0.11 又完成原 #2：`report-skill`（30 skill/94 调用，行为链+锚点；157 测试全绿）。v0.10 已完成原 #1/2/3(validate+归一)/5(--since)：`report-errors`、`suggest-agents`、`cards validate`、`report-tools --since`（148 测试全绿；真实库 243 错误 100% 归类；首份建议池 9 条；首张已验证卡片）。

| # | 缺口 | 设计出处 | 建议优先级 | 理由 |
|---|---|---|---|---|
| 1 | report-tools 绕行检测（连续相同工具调用）、放弃口径对齐设计定义 | §6 | 中 | 小改动；放弃口径需先决策（现口径更简单但偏保守） |
| 3 | regress 全流程回归语料（分层抽样） | §6/§11-5 | 中低 | 148 单测是零件级回归，非端到端 |
| 4 | cards extract（LLM 卡片生成）+ 交叉验证 | §7/§9-M3 | 低（按需） | validate 已就位，extract 等候选池人工维护成本真的大了再建 |
| 5 | LLM 错误归因组件 | §7 | 低（按需） | 确定性三分类已 100% 覆盖真实库，LLM 归因暂无增量价值 |
| 6 | schema 指纹入 sources 表 | §3/§4.2/§11-1 | 低 | canary+守卫已覆盖直采与 AutoClaw；DSH 漂移目前靠测试兜底 |
| 7 | 锚点细化到 turn/message_id 级 | §3 | 低 | 卡片锚点目前用 session_id+turn 承载，够用 |

## 4. 一句话总结

设计方案 M0–M1 **超额**完成并大幅扩源（9 源直采/导出全部打通，含设计中不存在的三平台自建管线）；v0.10 补齐 G2 全链路与 G3 确定性一半，**"诊断→修改闭环"已通**：report-errors（243 错误 100% 归类）→ suggest-agents（建议池 9 条）→ 人工并入 AGENTS.md / 扩写卡片 → cards validate（锚点查库）→ 并入主库。v0.11 补齐 G4 确定性主干（report-skill）与卡片脚手架（cards new，三通道全链路验证：DSH 坑卡/元宝知识卡/WorkBuddy 工作流卡均 validate 通过）。剩余缺口（绕行检测/motif 矩阵/regress/LLM 组件）均为"等需要再建"。另记录已知小缺口：模型级归属未入库（transcript 原始文件含 providerData.model，index 未存，需要时改管线重索引）。
