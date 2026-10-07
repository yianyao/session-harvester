# COMPARISON — 同类工具对比与可借鉴项（2026-10-05 联网核验版）

> 实施状态（2026-10-05 更新）：P1-1 ✅（deepseek_export.py，含 ai-hist 式源插件声明）、
> P1-2 ✅（indexing.py，FTS5）、P2-3 ✅（reader.py）、P2-4 ✅（mcpserver.py）。
> ai-hist 额外借鉴：pack 交接包 ✅（pack.py）、resume 命令不适用（本套件面向
> Chat 产品与本地日志，无原生 resume CLI）。
> P3-5 部分实装（v0.10–v0.13：report-errors / report-skill / triage，确定性
> 摩擦信号的统计主干已通，LLM 归因层按 DISTILL_PLAYBOOK 由 agent 执行）；
> P3-6 未实装（无 coding-agent JSONL 需求）。
>
> v0.4（2026-10-05 二次核验后）：官方导出通道扩展至 ChatGPT/Claude
> （official_export.py，格式多源交叉核验；weblogin 降为兜底）；
> AutoClaw 映射保真度修复（request 按用户轮次去重 / 思考与工具调用独立为
> note / answer 去重 / UTC→本地时区）+ schema 列结构守卫；
> VS Code 工作区 chatSessions 补丁日志实装（覆盖缺口补齐）。

> 本文对照外部同类工具与 session-harvester 的能力差异。
> 所有外部工具均经 web 检索核实（来源：PyPI/npm/GitHub/第三方评测），
> 与用户提供的综述有出入处以本文件核验结果为准。

## 1. 核验结论总表

| 工具 | 存在性 | 实际定位 | 与本套件关系 |
|---|---|---|---|
| RetroLens | ✅ PyPI `retrolens` (0.5.x, JoelYYoung) | CLI+Skill：把 Copilot/Claude Code 日志解析为结构化 JSON，`ls → read → read --turn → read --tool` 层级下钻；分析由 agent 的 LLM 完成 | **高度同构**：与本套件「机械步骤=CLI，语义=skill」分工完全同路 |
| AgentPulse | ✅ npm `@conalh/agentpulse` (conalh) | **实时**监控运行中的 Claude Code/Cursor/Codex 会话，6 确定性状态（converging/exploring/stuck/done/drifting/idle），无 LLM | 定位不同（监控 vs 存档提取），但其「确定性检测器」思想可借鉴 |
| ai-hist | ✅ npm `ai-hist` (RelayHistory) | 多 harness（Claude Code/Codex/Cursor/Grok 等）本地 SQLite 统一索引 + 搜索 + MCP server + 跨 agent 上下文交接（context pack）+ 云端会话插件（Claude web/Codex cloud，插件式） | **最接近本套件终态**的检索层参照 |
| DeepSeek2md | ✅ GitHub DavidShiang/DeepSeek2md | 用浏览器 userToken 调 DeepSeek web 接口，批量导出对话为按「日期+标题」分目录的 Markdown | **证明 DeepSeek 桩位可行**：token 方案已有人走通 |
| DeepSeekDataExporter | ✅ GitHub xionglongztz | 解析 DeepSeek **官方「导出所有历史对话」** 的 conversations.json → Markdown | **证明官方导出文件是更稳的数据源**（无需 token、无风控） |
| Callimachus | ✅ GitHub BetaBots-LLC（用户综述未提及） | 11 种 coding agent → 本地 SQLite 统一索引；FTS5(BM25)+本地语义向量混合检索；文件提及索引；线程→commit 关联 | 检索层最强参照（但 Node 桌面应用，非零依赖 Python） |
| agent-history | ✅ npm `@contextberg/agent-history`（用户综述未提及） | 5 种 agent 历史统一浏览 + MCP server + git commit 触发的自动提炼（"dreaming"） | MCP 暴露模式参照 |
| retro | ✅ GitHub craftwork-design/retro（用户综述未提及） | 确定性扫描 Claude Code 轨迹中的「纠正/打断/重试循环/放弃」等 8 类摩擦信号 → 产出 CLAUDE.md 规则提案 | 确定性信号检测的最完整参照 |
| Keddy / dsh-prism / LLM Chat Exporter / LangSmith 等 | ⚠️ 未逐一核验 | 云端可观测性平台或未证实工具 | 与本套件定位距离远，暂不采信 |

**核验后对用户综述的修正**：
1. AgentPulse 的"分类"是**实时会话状态**判定，不是对历史轨迹的内容分类——借鉴价值在方法论而非功能。
2. ai-hist 比综述描述的更强：不止检索，还有云源插件（Claude web）、上下文交接包。
3. 综述遗漏了三个更相关的工具：Callimachus（检索层天花板）、agent-history（MCP 模式）、retro（确定性信号）。
4. 综述结论"没有单一工具满足读取+分类组合"**成立**——但也正因如此，本套件 + skill 的组合在「个人跨设备、零依赖、Windows 桌面 AI 应用」这个细分定位上没有直接竞品；竞品全部集中在 coding-agent JSONL 日志（Claude Code/Cursor/Codex）生态。
   > **[2026-10-05 外部审计修订]** 上一行结论已过时：dsh-chat-import（明确支持 WorkBuddy/DSH，Interchange v1 协议 + export-md CLI）、OpenViking ingest、dsh-session-flow 均已出现。修订结论：采集层「当桥不当地基」（dsh-chat-import 有 DSH 版本天花板 0.3 与附件单向泄漏等硬伤），OpenViking 官方确认不保留工具调用输入输出（做不了 2.2/2.3）；本套件护城河在分析层。详见 AUDIT-2026-10-05.md。

## 2. 能力差距分析（session-harvester 视角）

> 下表"本套件现状"列更新于 2026-10-07（v0.16 后）——初版差距表中"检索无/
> 缺 turn 读取/缺 MCP"三条已在 v0.3 消除，此处为防误读重写。

| 能力 | 本套件现状 | 外部标杆 | 差距 |
|---|---|---|---|
| 数据源覆盖 | 9 源实装：4 本地轨迹（WorkBuddy 日志+transcript / VS Code Copilot / AutoClaw / DSH）+ 3 官方导出（DeepSeek/ChatGPT/Claude）+ 3 平台登录态直采（元宝/千问/豆包），另有 6 桩位 | Callimachus 覆盖 11 种 coding agent | 仍缺 Claude Code/Cursor/Codex 等 JSONL 生态（P3-6，等需要再接） |
| 检索 | ✅ FTS5 全文 + CJK bigram 中文 100% 命中（v0.3 起） | ai-hist / Callimachus：BM25+语义向量、文件提及索引 | 无语义向量与文件提及索引（个人库暂不需要） |
| 分层读取 | ✅ read → --turn 逐回合下钻（v0.3 起） | RetroLens：overview→turn→tool call | 已消除；--tool 单列工具条目未做（note 里有） |
| Agent 接入 | ✅ mcp-serve（4 工具，任意宿主）+ pack 跨 agent 交接包（v0.3 起） | ai-hist/agent-history：MCP server | 已消除 |
| 云端源 | 三平台直采实装（服务端原始 JSON 落 corpus/）+ 3 家官方导出；weblogin 三级流程兜底 | ai-hist 插件式云源；DeepSeek2md token 方案 | token 方案刻意不做（脆弱+风控） |
| 确定性信号 | 部分实装：report-errors 三分类+重试/放弃率（v0.10）、report-skill 行为链（v0.11）、triage 蒸馏队列（v0.13） | AgentPulse 6 状态 / retro 8 类摩擦信号 | 实时监控不采纳（定位是历史归档）；motif 矩阵/绕行检测等 LLM 层留白 |
| 零依赖/可移植 | ✅ 纯标准库单目录 | 竞品多为 npm/Node 桌面应用 | **本套件优势，须保持** |

## 3. 可借鉴项（按优先级）

### P1-1 DeepSeek 官方导出文件 Adapter（参考 DeepSeekDataExporter）
DeepSeek 网页版自带「系统设置 → 数据管理 → 导出所有历史对话」，产出
conversations.json。新写一个文件型 Adapter：用户手动导出一次，套件解析落库，
即可把最大的桩位转为实装——**无需账号密码、无需 token、无风控风险**，
与「不硬猜」铁律兼容（数据源是官方导出物，格式确定）。
- 命令形态：`harvester import-deepseek conversations.json`（或文件型 Adapter 通用化：`--from-export <file>`）
- 同类机会：通义/元宝/豆包若有官方导出功能，走同一路径（接入前逐一实测格式，不硬猜）。

### P1-2 全文检索层（参考 ai-hist / Callimachus）
把导出产物或聚合语料索引进 SQLite FTS5：
- `harvester index --from exports/` → 建 `index.db`（FTS5 虚表：title/text/source/date/category）
- `harvester search "关键词" [--source ...] [--after 2026-08]` → 命中会话+片段
- 仍是标准库（sqlite3 内置 FTS5），保持零依赖。
这是「第 4 步聚合」之后的自然延伸，也是蒸馏 skill 找增量素材的工具。

### P2-3 分层读取接口（参考 RetroLens 的下钻模型）
`harvester read <纲要序号>` → 输出会话概览（turn 列表）；`--turn N` → 单轮全文；
`--turn N --tool` → 含工具调用条目。让 agent/人可以逐步下钻而非整包导出。
实现成本低（export 的 json 已具备全部数据）。

### P2-4 MCP server 暴露（参考 ai-hist-mcp / agent-history）
把 `scan`/`search`/`read` 包成 MCP server，任意宿主（VS Code、AutoClaw、Claude 等）
即可直接查会话历史——这正是用户「工具不能只能自个用」诉求的终极形态。
工作量中等；建议在 P1-2 检索层落地后再做（MCP 工具直接查 index.db）。

### P3-5 确定性摩擦信号（参考 retro / AgentPulse）
对语料做确定性检测：重试循环（同错误连续 N 次）、用户纠正语句、会话中断等，
产出统计附在反馈台账。只做「高召回候选」，判断留给 LLM/skill。进阶项，暂缓。

### P3-6 coding-agent JSONL 生态接入（参考 RetroLens/agent-history 的 reader 规范）
若日后使用 Claude Code/Codex 等，按其公开日志格式加 Adapter；
reader 契约已在业界收敛（IReader / custom reader 模式），本套件 BaseAdapter 契约与之同构，接入成本低。

## 4. 不采纳项

- **云端可观测性平台**（LangSmith/Langfuse/Phoenix）：面向生产 traces，需上传数据或自建服务，与「本地、私密、零依赖」定位冲突。
- **实时会话监控**（AgentPulse 的 live 模式）：解决的是"盯着跑"问题，不是"历史归档"问题。
- **token 抓取方案**（DeepSeek2md 路线）：能用但脆弱且带风控；优先官方导出文件路线，token 方案留作官方导出不可用时的备选。
