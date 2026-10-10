# Skill 自学习进化路线图（v1.0，2026-10-10）

> **本文档是什么**：把散落在 14 个会话（主题 `tp-20261010-002`「Skill 自学习进化
> 与跨平台设计」）里的设计意图，与 `D:\Data\AI\Skills\` 下**实际落地物**逐条对照，
> 收敛成一份"现在在哪、缺什么、下一步做什么、怎么判定做成了"的路线图。
> **消费者**：用户本人（决策）+ 后续 Agent（照单执行）。
> **配套**：复盘正文见 chain 长文 `chain-Skill自学习进化.md`（同期产出）；
> 文件级盘点证据直接写在本文 §3 各条的「证据」列（路径 + 行号），可逐条复核。
>
> **证据等级（本文全篇遵守）**：
> - `{sid, turn N}` = 会话锚点，`turn` 为 `reader.split_turns` 权威口径（user 回合 1-based），可回溯原文；
> - `路径:行号` = 本轮**实读**过的文件位置（未实读的不写行号）；
> - 「待核实」= 本轮无法确认，**不推测填充**。

---

## §0 一页结论

| 维度 | 结论 |
|---|---|
| 设计侧 | 2026-09-19 ~ 09-22 四天内定形：从"业务 skill 分发后脱钩自进化"→ 宪法契约 → 结构收敛 → 自学习本体 → author-skill/意图澄清 |
| 落地侧 | 工单自述 **Phase 0–7d 全部收官**（`R-20260921-10`），当前无待办工单；宪法 v1.0 已冻结、11 条不变量、协议 13 节、工厂侧 10 模块、生成物侧 6 件套均在位 |
| 但 | **"收官"≠"可用"**：判定分流用硬编码关键词、A 类回归只做存在性断言、B 类零用例、**回传闭环零运行期产物**、两处不变量承诺的 Critical 升级未执行 |
| 下一步（本文 §4） | R1 漂移清算（零风险）→ R2 用**意图澄清 skill** 跑通第一份真实自学习产物 → R3/R4/R5 把首版简化项做实 → R6 跨平台实机验证 |
| 最大风险 | 不是技术，而是**没有一次真实的端到端闭环**：体系目前是"骨架 + 自测"，从未被真实业务使用过 |

**一句话**：设计蓝图已经指到"业务进化"那一档（蓝图 Phase 3），仓库侧 Phase 0–7d 也已
宣告收官——但那七段是**首版简化的建成**。先把声明与实现之间的 5 处漂移抹平（R1），
再用一个真实业务包把回传闭环跑出第一份产物（R2）—— 这两件事做完，
"自学习"这个词才算有证据。

---

## §1 口径与两条提醒

1. **两套 Phase 编号并存，不要混用。**
   - 会话里出现过的是 2026-09-20 的**设计蓝图编号**（`Phase 0 契约签订 / Phase 1 结构收敛 /
     Phase 1.5 / Phase 2 自学习本体 / Phase 3 业务进化`，见 `{deepseek-export:28de2b3a…, turn 4}`）；
   - 仓库工单实际按 **Phase 0–7d** 推进（`.ticket_state.json` 的 9 个归档轮次：`03a/03b/04a/04b/06/07/08/09/10`）。
   - 两套编号的**映射关系未经用户确认**（待核实）。本文一律用**仓库实际阶段名**指代现状。
2. **"收官"的读法**：`R-20260921-10` 原话是「Phase 7 全段收官……整个自学习进化体系
   （Phase 0–7d）落地」，但同一轮的收口项全是**脚本自测与不变量检查**通过，
   没有任何一项是"真实业务包用它学到了一课"。所以本文把"是否产出真实学习产物"
   单列为 R2 的验收条件。

---

## §2 四天里定下来的六件事（设计侧，带锚点）

### 2.1 起点：业务 skill 要与工厂脱钩，自己长大（09-19）

用户先问的是"业界 skill 自进化的手段有哪些"`{deepseek-export:06d2b5b3…, turn 1}`，
把目标锁死在**业务类**——"生成产品概念图、视觉效果的图/视频"，要它"日积月累发现我的
偏好、设计的规律"`{…, turn 2}`，并追加一个当时没人能答的问题：能不能联网搜同风格
作品来对比吸收，但"如何确保网上作品足够优秀、对比维度在哪"`{…, turn 3}`。

同一天在另一条线上，立场成型：**"SKILL 工厂生成的业务 SKILL 分发之后，直接跟 base
工厂脱钩，所以才希望它能自我学习独立进化"**，且明确"自学习包括了业务的学习"
`{deepseek-export:f1e588a9…, turn 4}`；自学习该做在"分形态之前还是之后"也被提出来
`{…, turn 3}`。

> **为什么重要**：这一条决定了后面所有架构取舍——脱钩 ⇒ 生成物运行期**零 pull**、
> 回传只能**显式**、路径不能写死。宪法三条底线里的两条（不依赖宿主、业务优先受益）
> 都能追到这句话。

### 2.2 从"打补丁"升级为"立契约"（09-20 上午）

09-20 是设计爆发日：同一份诉求（读两个元包 → 判定问题 → 扩展自学习，**不依赖模型和
Agent**）被同时投给了 deepseek-export 与 qianwen-raw
`{deepseek-export:28de2b3a…, turn 1}` / `{qianwen-raw:aab4f5a4…, turn 1}`。
在吸收两份外部评审后，方案被整合成"7 阶段 v2.0 可执行路径"，其中 **Phase 0 不再是
"对话"，而是产出一份《自学习宪法》**`{deepseek-export:28de2b3a…, turn 4}`。

同一天另一条会话里，这份整合稿被逐句判读：结论是 **5 条真进步**（契约交付物化 /
台账先于本体的约束 / 状态机化 / YAML 单向流 / 原子门禁）、**2 条会反噬**——其中
"台账先于本体"若靠时序约束不可校验，**必须改成终态一致性断言**
`{workbuddy-transcript:…-09-20-10-48-49/235f128e…, turn 1}`。

> **为什么重要**：这是全链唯一一次把"看起来更好的方案"顶回去的记录，也是后来
> `invariants.yaml`（机器可读硬约束）而非"文档里的规矩"的来源。

### 2.3 学习成果的归属：业务 SKILL 先受益，然后才回本地（09-20 中午）

Q1–Q10 冻结表被逐条拍板。最关键的问答是：学习成果"累积到本地，再由用户显式执行"，
但**"学习成果是业务 SKILL 在使用过程中自学习并应用到本 SKILL 的。这是重点。业务
SKILL 要首先受益，然后才是累积到本地"**`{deepseek-export:28de2b3a…, turn 7}`；
学习信号源被定为"用户运行反馈 / 失败案例日志 / 自评估"
`{…, turn 5}`；并要求给生成物**预留一条把学习成果结构化回传工厂的通道**
`{…, turn 6}`。当天下午方案 `冻结`，并要求落盘 `CONSTITUTION.md` 全文
`{…, turn 8}` / `{…, turn 9}`。

> **为什么重要**：这条直接定义了后来的 `backflow_spec.yaml` 与"回传是事后、显式、
> 可选"的宪法底线。

### 2.4 跨平台不是口号，是被硬盘和路径逼出来的（09-20 全天）

三处硬约束在同一天暴露：

1. **路径**：脚本里出现 `dst: ~/.workbuddy/pitfalls/…` 时，用户直接问
   **"以后我要把工厂搬到其他机子上咋办？"**`{deepseek-export:28de2b3a…, turn 15}`，
   随后上传 `porting_guide.md` 要求一并检查`{…, turn 16}`；
2. **宿主能力**：在千问侧，用户问"你能否读我硬盘上的文件"，然后贴出
   `learning_log` 读写脚本让它解释用途`{qianwen-raw:dee8c18e…, turn 2}` / `{…, turn 3}`
   ——同一份设计在不同宿主上的"能不能读盘"并不一致；
3. **方案侧**：另一条会话把 Phase 1 拆成 `1 + 1.5`，明确"**落盘但不激活**"
   `{workbuddy-transcript:…-09-20-17-13-08/edf86e4c…, turn 2}`；并追加要求
   "通读 `D:\skill\过程文档` 全部文档……去芜存精"`{…, turn 5}`。

跨平台的**经验证据**（本轮探针实测）：B 主题 14 个会话里，有 2 组首条诉求**逐字相同**
却分别出现在不同平台——"提交文件为 skill-authoring 和 base"出现在 deepseek-export
与 qianwen-raw；"我现在希望扩展功能，将自学习作用于 base + skill-authoring"出现在
qianwen-raw 与 workbuddy-transcript，**两者相隔 1 分钟**（17:14 vs 17:13）。平台之间
不互通，用户只能把同一诉求重讲一遍。

> **为什么重要**：跨平台设计不是审美问题，是"同一诉求要在 4 个宿主上重讲"这个日常
> 事实的工程化回应。

### 2.5 自我批判：值得称道的只有自学习机制（09-21）

一份"假定功能已实现"的事实过程文档被拿来挑缺陷。用户逐条反驳，其中最锋利的三句：

- 关于回传：**"仅凭几个 skill 本身很难完成更多的 L0/L1 级学习成果的沉淀，更看重的
  还是生成的业务 SKILL 本身业务能力的进化"**`{deepseek-export:20e23adf…, turn 2}`；
- 关于工厂模式：**"当前的工厂生成模式，说实话，值得称道的只是自学习机制，其他方面
  和一句话生成的差距不大"**`{…, turn 4}`；
- 关于新方向：**"标准、规范、安全之外，意图不够清晰，应该是做 skill 的另一大痛点"**，
  因此提出把工厂生成方式**改成动态的**——先追问（目的/场景/行业/业务）再确认，再检索
  相关行业业务`{…, turn 3}`；并明确"澄清阶段放在工厂侧，生成的业务 skill 无此需求"
  `{…, turn 4}`。

> **为什么重要**：这是全链的价值排序声明——**业务能力进化 > 机制沉淀**，
> 而**意图澄清 > 规范完备**。R2 之所以选"意图澄清 skill"做第一个真实业务包，
> 依据就在这里。

### 2.6 author-skill：先划工程边界，再自我怀疑（09-22）

author-skill 被定义为"能**自动、动态**生成可自学习进化 skill 的 skill"
`{qianwen-raw:8c7b2030…, turn 1}`，并立刻被三条工程前提框住：
**①落地动作发生在 Skill 包自身（SKILL.md/frontmatter/捆绑脚本/评测集/指令模式）
②单个 Skill 作者可独立完成，不依赖组织流程或 CI 基建 ③主体依赖宿主运行时能力
（记忆设施、度量看板）的不收**`{deepseek-export:e1a7ddea…, turn 1}`
（原文另见 `{qianwen-raw:8c7b2030…, turn 3}`）。

随后两条线并行：一是**要先把 SOP 分解出来**，让 author-skill 能自动填的自动填、
不能的再人工`{deepseek-export:e1a7ddea…, turn 3}`；二是意图澄清流程的反复收敛——
用户否掉了 AI 建议的"输入校验 / 槽位状态追踪"（判为多余）`{qianwen-raw:8c7b2030…, turn 12}`，
要求参照既有流程"去芜存精"，最终收敛到**意图置信度四档（PROCEED / ASSUME / ASK /
CONFIRM）+ 分支澄清**`{…, turn 14}`。

同一天用户自己抛出了动摇性问题：**"作为个人开发者，设计 author-skill 这种形态的
skill 有无必要"**`{deepseek-export:e1a7ddea…, turn 4}` / `{qianwen-raw:8c7b2030…, turn 5}`。
**本轮未在任何素材中找到这个问题的结论**——列入 §5 未决项 U1。

---

## §3 当前落地盘点（实读，2026-10-10）

### 3.1 已在位（全部实读确认）

| 层 | 落地物 | 位置（实读） |
|---|---|---|
| 契约 | 宪法 v1.0，2026-09-20 冻结，含三条底线（不依赖宿主 / 业务优先受益 / 不变量锁死）与 Q1–Q10 冻结表 | `skill-authoring/references/core/constitution.md` |
| 约束 | `invariants.yaml` 11 条（8 Critical + 3 Warning），含元规则 INV-009「本清单不可自改」 | `…/references/core/invariants.yaml` |
| 单一真相源 | `rule_registry.yaml` 18 条规则 → `rule_registry.md` 由 `render_md.py` 渲染，`--check` 拦截手改 | `…/references/core/rule_registry.{yaml,md}` |
| 协议 | 自学习协议 13 节（状态机/权限门/门禁/CLI/学习日志/回滚/评测/回传/宿主无关/版本化/实体指认表） | `…/references/core/self_learning_protocol.md` |
| 工厂侧本体 | `scripts/self_learning/` 10 模块（7b 建 7：types/signals/judge/regression/learning_log/orchestrator + `__init__`；7d 加 3：backflow_router/backflow_feedback/backflow_channel） | `…/scripts/self_learning/` |
| 生成物侧 | 6 件套最小套件（协议摘要 / 最小 CLI 3 命令 / 最小门禁 4 Checker / 最小 orchestrator / 最小 learning_log / config 模板） | `…/scripts/templates/skill_learning_min/` |
| 回传 | `backflow_spec.yaml`：export protocol 1.0，三 scope 分流，三档通道 **manual 默认（不强制）** | `…/references/core/backflow_spec.yaml` |
| 门禁 | `gate.sh` 默认 exec 到 `gate_runner.py`（full 15 Checker），`USE_RUNNER=0` 降级串行 | `…/scripts/gate.sh`、`gate_runner.py` |
| 迁移 | `porting_guide.md`：目录随行 / 路径重解析 / 无技能发现宿主显式读取 / frontmatter 兼容 / 换行符五步 | `…/references/core/porting_guide.md` |
| 评测 | self-test 14 份 yaml（70 例，退出码 + stdout 断言）；A 类行为评测 4 条；生成物套件 4 例 | `…/scripts/tests/*.yaml`、`…/references/meta/behavior_evals.yaml` |
| 工单 | 已归档 9 轮，最近一轮 `R-20260921-10` = Phase 7d 回传闭环轮；当前工单为空 | `…/references/meta/.ticket_state.json`、`registry.md` |

### 3.2 缺口清单（每条都实读过原文）

| # | 缺口 | 证据（实读） | 性质 |
|---|---|---|---|
| G1 | INV-010 / INV-011 的 `transition_note` 承诺"Phase 4 / Phase 7 后升级为 Critical"，两个 Phase 都已收官，**升级动作不存在** | `invariants.yaml:126,128,130` 与 `:136,138,140` | 声明 vs 实现，最典型的"写着要做没做" |
| G2 | INV-011 注释仍写「当前 learning_log 未建」，而 7b 已实装写入侧 | `invariants.yaml:136` vs `scripts/self_learning/learning_log.py` | 过期注释 |
| G3 | 载体内存命名不一致：权限矩阵写 `learning_log.yaml`，真实载体是 `learning_log.jsonl` | `permission_matrix.yaml:35` vs `learning_log_format.yaml:10` | 术语漂移 |
| G4 | 判定分流是**首版简化**：`judge_change` 用内置关键词分级，`permission_matrix.judge_rules` 参数预留、暂不消费 | `scripts/self_learning/judge.py:3-5` | 首版简化 |
| G5 | A 类回归只做"字段存在性断言"，未真实执行用例 | `scripts/self_learning/regression.py:4-5` | 首版简化 |
| G6 | B 类（业务质量）评测**零用例**：`cases: []`，注释说"Phase 7c 由业务包填充"，7c 已收官仍未填 | `references/meta/behavior_evals.yaml:70-71` | 空实现 |
| G7 | 反哺仍有 kind 未实装 | `scripts/self_learning/backflow_feedback.py:105`（`kind=%s 暂未实装反哺`） | 未实装 |
| G8 | 导出件 `source_version` 写死 `unknown`，注释挂 `TODO(Phase 7)`，而 Phase 7 已收官 | `scripts/skill_cli_export.py:57` | 过期 TODO |
| G9 | **回传闭环零运行期产物**：仓库内无 `.learning_state.json`、`learning_log.jsonl`、`learning_export_*`、`learning_inbox/`、`business_archives/`、`backflow_acks.yaml` | 递归查找零命中；设计上"只定义、不建空目录" | **最大缺口：无一次真实闭环** |
| G10 | base 工单实例仍留"等待 7d"过期文案（7d 已收官） | `base/references/meta/change_tickets.md:11-12` | 过期文案 |

> 说明：会话里提到的过程文档目录 `D:\skill\过程文档` 在本机**不存在**（待核实是否已
> 迁移/改名），故本文未能把当时的原始方案文本纳入对照。

---

## §4 路线图

> 排序原则：**先抹平声明与实现的漂移（零语义风险）→ 再用真实业务逼出一次完整闭环 →
> 最后才提能力上限**。每一步都给"怎么判定做成了"，不给的话就是没做完。

### R1 漂移清算（G1/G2/G3/G8/G10）— 建议立即做

| 项 | 动作 | 可判定验收 |
|---|---|---|
| R1-1 | INV-010/INV-011：要么执行升级（`severity: Critical` + `violation_action: block` + 删 `transition_note`），要么显式改成"决定不升级 + 理由"并去掉过渡注记 | `check_invariants.py` 仍 PASS；`grep transition_note invariants.yaml` 无残留承诺 |
| R1-2 | 修正 INV-011 过期注释（learning_log 已建） | `grep "未建" invariants.yaml` 零命中 |
| R1-3 | 统一载体命名（`learning_log.yaml` → `learning_log.jsonl`） | `grep -r "learning_log.yaml" .` 零命中 |
| R1-4 | `source_version` 从 `learning_state.json` 读出生版本 | 新增 self-test 用例：导出件的 `source_version` ≠ `unknown`；删掉该 TODO |
| R1-5 | base 工单"等待 7d"文案改为已关单 | `grep "等待 7d" base/` 零命中 |

**验收总门**：`bash scripts/gate.sh --pkg-root skill-authoring` 与 base 双包 full 全绿；
上面五条 grep 断言同时成立。**风险**：低（不改语义，只改声明与命名）。

### R2 用「意图澄清 skill」跑通第一份真实自学习产物（G9）— 最高价值

**依据**：用户自己把意图澄清定为第一个要做的业务 skill
`{qianwen-raw:8c7b2030…, turn 6}`，且明确它是"另一大痛点"`{deepseek-export:20e23adf…, turn 3}`；
同时 `D:\Data\git\Agent Skill\foundation\clarification\` 下已有
`V0.1-最小可用版 → V1.0-数据驱动版` 共 5 个版本的现成设计。

| 步骤 | 动作 | 可判定验收 |
|---|---|---|
| R2-1 | 用 `foundation/clarification/V1.0-数据驱动版` 作为输入，经生成协议六步产出一个**真实业务包**（而非临时测试包） | 包内 6 件套齐全（生成协议自检⑥ 6/6）；`gate_min.py` 4 Checker PASS |
| R2-2 | 真实使用一次并触发一轮自学习（低风险档，如坑表条目增补） | 产出**第一份真实 `learning_log.jsonl`**（append-only，字段齐 9 项）与 `.learning_state.json`（状态机合法转移） |
| R2-3 | `export-learning` → 用户显式送入工厂 `learning_inbox/` → `import-learning` | 三分流落痕：`business_archives/` 或 `lessons.md` 待固化区；协议版本不兼容时 exit 1（负例可复现） |
| R2-4 | ack 回执通知源包 | `backflow_acks.yaml` 出现该源包条目 |

**验收总门**：仓库内**首次出现** G9 列举的六类实体中的至少 4 类（`learning_log.jsonl`、
`.learning_state.json`、`business_archives/` 或 lessons 待固化条目、`backflow_acks.yaml`）；
且这一轮的证据链（日志行 → 状态文件 → 导出件 → 分流落点 → ack）可逐条对应。
**风险**：中（首次真实跑，会暴露 judge/regression 的简化处 —— 那正是 R3/R4 的输入）。

### R3 判定分流接回权限矩阵（G4）

现状：`judge.py` 用硬编码关键词分级，`permission_matrix.yaml` 的 `judge_rules` 形同虚设
——这正是宪法 P-1「真相源唯一」被自己违反的地方。

- 动作：`judge_change` 改为消费 `permission_matrix.judge_rules`；硬编码关键词仅作 fallback，
  且 fallback 命中要记日志（可观测）。
- 验收：新增 self-test 用例——**同一变更在"规则表命中"与"规则表未命中"两条路径下
  分级可区分**；且故意改坏 `judge_rules` 中一条时测试必须红（可证伪）。

### R4 回归与评测从"存在性"升到"真执行"（G5/G6）

- R4-1：A 类回归真实执行 `behavior_evals.yaml` 的断言（当前 4 条：INV-001/002/004/008）。
  验收：故意注入一个 CRLF 文件，A 类回归必须报 FAIL 且指向 INV-001。
- R4-2：B 类评测补**首例**（业务包侧，建议就是 R2 的意图澄清包：例如"模糊需求 → 澄清
  后槽位是否齐全"）。验收：该用例在"澄清流程被跳过"时判定为 FAIL。
- **纪律**：B 类无 LLM 时按协议第九节的降级路径（B2→B1 + 用户确认），**不得静默跳过**。

### R5 回传通道补齐（G7 + 通道升级评估）

- R5-1：`backflow_feedback` 未实装的 kind 落地或显式标注"不支持并给出替代路径"。
  验收：`grep "暂未实装" scripts/` 零命中。
- R5-2：`source_version`（R1-4）在真实导回时被工厂侧消费（版本拦截已有，补正向用例）。
- R5-3（可选）：`semi_auto` 通道实机验证（需 `SKILL_FACTORY_ROOT`）。**是否升档见 U5。**

### R6 跨平台实机验证（对话里被追问、仓库里只有指南）

- 动作：按 `porting_guide.md` 五步，在**非 Windows 宿主**（Git Bash / macOS 均可）实测：
  脚本改 `PYTHON` 环境变量后 `gate.sh` 可跑；无官方校验器时按适配表降级为 frontmatter 自检清单。
- 验收：目标宿主上 `gate` full 全绿 + 官方校验器缺失时的降级路径留下实验记录；
  `grep -r "C:" scripts/` 零命中（当前唯一命中是 `scripts/README.md` 的
  `QUICK_VALIDATE_PATH` 示例，属已登记降级项）。

---

## §5 未决项（需用户裁决，工具不代决）

| # | 未决 | 来源 | 影响 |
|---|---|---|---|
| U1 | **author-skill 这种形态对个人开发者是否必要**——用户 09-22 自己提出，本轮未找到结论 | `{deepseek-export:e1a7ddea…, turn 4}`、`{qianwen-raw:8c7b2030…, turn 5}` | 决定 R2 之后要不要继续投 author-skill，还是只做业务包 |
| U2 | 意图澄清是**独立业务 skill** 还是 **author-skill 内嵌模块**——两条会话里的说法不一致 | `{deepseek-export:20e23adf…, turn 4}`（澄清放工厂侧）vs `{qianwen-raw:8c7b2030…, turn 6}`（第一个想做意图澄清 skill） | 直接决定 R2 的包形态 |
| U3 | 会话蓝图编号（Phase 0/1/1.5/2/3）与仓库工单编号（Phase 0–7d）的映射 | 本文 §1 | 影响后续所有"到哪个阶段了"的表述 |
| U4 | INV-010/INV-011 是**升级**还是**撤销承诺**（R1-1 的两种走法） | `invariants.yaml:126-140` | 决定门禁强度 |
| U5 | 回传是否从 `manual` 升到 `semi_auto`——用户对回传价值评价一向不高（"仅凭几个 skill 很难沉淀"） | `{deepseek-export:20e23adf…, turn 2}` | 决定 R5-3 做不做 |

---

## §6 与 session-harvester（本项目）的关系

这条路线图的**证据面**正是本项目要自动化的事，两者可以互为客户：

1. **主题化复盘已成流程**：`topic register/add/merge` → `topic pack --level coarse` →
   Agent 蒸馏 → `chain-validate` 校验。本轮用同一套机制产出了 B 主题的 chain
   （`tp-20261010-002`，14 成员），做法可直接复用到 skill 自学习线之后每一轮。
2. **"改了不回归"是本项目欠的账**：`regress` 端到端回归语料（待办 #11）在本项目自身
   也尚未落地；而 skill-authoring 已有 70 例 self-test + 15 Checker 门禁——
   这条线值得**双向借鉴**：本项目的 `regress` 可以照 `gate_runner` 的"单次遍历 + 并发 +
   JSON 报告"形态做。
3. **缺口扫描可确定性化**：本文 G1–G10 是用"声明关键词扫描 + 逐条实读"找出来的，
   与 AGENTS.md 的过期文本扫描同源，可做成 `grep + 断言` 的常驻探针。
   本轮另新增两道对"语义交付物"的门（现已是 `chain-audit` 的两道门，形态可直接
   移植到 skill-authoring 的评测层）：`chain-audit` 的引文逐字门（引文逐字可回溯，
   `未命中>0` 即红）与 `chain-audit --no-quotes` 的锚点语义门（结论与证据并排，
   供人工抽查）——**对应到本体系的对应物就是"B 类行为评测"**
   （当前 `cases: []`，见 G6）。
4. **主题 B 的成员没有 skill 遥测**：14 个成员全是导出型源（deepseek-export / qianwen-raw /
   yuanbao-raw / workbuddy-transcript），`steps` 里 **0 次 Skill 类调用**——
   意味着"哪个 skill 在哪个阶段有效"这类 join 分析**在本主题上无数据**
   （与既有 chain 的 H25 同因）。要用真实遥测做 T4 多例，需另选成员含
   autoclaw / workbuddy-transcript skill 调用的主题。

---

## §7 本文没能核实的（限制声明）

1. `D:\Data\AI\Skills\skill-authoring` 与 `D:\Data\git\Agent Skill\self-learing\skill-authoring`
   **文件名集合一致（92 个）**，但**未逐字节比对**——两处谁是权威副本待确认。
2. 会话里引用的 `D:\skill\过程文档` 在本机不存在，当时的原始方案文本未纳入对照。
3. `references/core/invariants.md`、`authoring_protocol.md`、`pitfalls_*.yaml` 仅做了定向读与
   关键词扫描，**未逐文件深读**——可能仍有本轮未发现的漂移。
4. 工单归档摘要（`.ticket_state.json` 的 9 轮 summary）是**自述**，本轮只交叉验证了其中
   "当前无待办工单""Phase 7 收官""收口 9 项全 PASS"等表述与 `registry.md` 的一致性，
   未重跑当时的 gate 复现其结论。
5. 路线图 §4 的 R2–R6 是**方案建议**，不含工时估算——按本项目惯例，工时由用户裁决后
   再由执行 Agent 拆解。
