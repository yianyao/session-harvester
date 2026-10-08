# SOP — v0.22 方案执行操作程序

> 配套：`PLAN-v0.22-unified.md`（方案）+ `HANDOFF-v0.22-execution.md`（事实台账 H1–H23、
> 环境、红线）。本 SOP 只写"怎么做"；每步引用 H 编号，**遇到不理解的事实先查 HANDOFF，
> 不要重新实测**。`$PY` = venv python（路径见 HANDOFF §2）。

---

## SOP-0 会话启动检查单（开工前 5 分钟，全过才动手）

```bash
cd C:/Users/yianyao/WorkBuddy/2026-10-05-08-38-51/session-harvester
git log --oneline -1        # 期望 1a58990（v0.20）；若更新，读新增提交再看本文档是否仍适用
$PY -m unittest discover -s tests 2>&1 | tail -2    # 期望 246 例 OK
cd ../harvester-view && git log --oneline -1         # 期望 a845b17（v2.3.1）
$PY -m unittest discover -s tests 2>&1 | tail -2    # 期望 23 例 OK
```

环境自检：
```bash
$PY -c "import yaml; print(yaml.__version__)"       # 期望 6.0.3（H9：系统 python 无，属正常）
netstat -ano | grep -E "8765|8088"                   # 确认无僵尸服务占用（有则 taskkill 旧 PID）
```
任一不符 → 先查明原因再开工，**不得带着异常基线改码**。

## SOP-1 通用任务节律（每个任务收尾必做，简称"节律"）

1. 改动 + 新测试（**测试先写、先演示会红**——红线 §5.6）。
2. 任务级验收命令（各 SOP 给出）。
3. 全量回归：两仓库 `unittest discover` 各自全绿。
4. 若动了 index.html：node vm.Script 语法校验 + LF 检查 + 手动过一遍受影响渲染分支。
5. LF 终检：改动文件 `b'\r\n' in bytes` 必须为 False。
6. 提交（格式见 SOP-X 模板）+ `git -c http.proxy=http://172.16.20.27:12603 push`。
7. 工作日志追加（会话 memory）。

---

## SOP-P0-1 cards 校验器完整性（第一优先，~50 行 + 测试）

**目标**：占位符卡不得通过 validate；evidence 核对零依赖下真实生效；未核对 ≠ 通过。

**步骤**：

1. **先写会红的测试**（新文件 tests/test_v21_cards_fix.py）：
   a. 块标量 fixture：`evidence: |` + 两行引用 → 断言 `_parse_frontmatter` 解析出多行字符串
      （当前 H8：读成 `'|'`，此断言红）；
   b. 块序列 fixture：`anchors:\n  - session_id: "x"` → 断言解析为 list（当前红）；
   c. 占位符卡 fixture：`cards new` 产物原文（H11）→ 断言 validate 出**错误级**
      （当前红：通过）；
   d. **强制降级模式用例**：`unittest.mock.patch` 使 `cards._HAS_YAML=False` 后重跑 a/b
      （H9：这是唯一能覆盖降级分支的方式，venv 有 pyyaml 永远走不到）；
   e. 过滤器断言参照 H22：checked 计数在破坏 evidence 后必须变化。
2. 改 `cards.py:115-128` 降级分支：支持块标量（`|`、`|-`、`>`，收集后续缩进行）与
   块序列（`- ` 开头的顶层列表，复用/扩展现有 `_parse_flow_seq` 语义）。
3. 占位符检测（validate_card 内，错误级）：
   - evidence 命中 `|`/`|-`/`>` 单字面量；含 `<待补`、`<粘贴`；
   - 正文含 `「<待补`；title 等于会话原标题（cards new 脚手架痕迹，H11）。
   模式集中成一个 `_PLACEHOLDER_PATTERNS` 常量，带注释"新增脚手架占位符必须同步登记"。
4. `cards.py:233` 提前 return 前移 turn:null 检查（H10）；调整 `_evidence_warnings`
   结构使所有检查可达。
5. summary 增 `evidence_unchecked` 字段（additive）；`render_cards_report`（cards.py:333
   附近）结论逻辑改为：`checked==0 且存在 evidence` → 结论"**evidence 未核对（无 PyYAML
   或原文不可得），不得视为通过**"；占位符命中 → "存在未补全的脚手架卡，禁止并入主库"。
6. 真实验收：用 venv 和**系统 python 各跑一次**（H9 复现路径）：
   ```bash
   $PY -m harvester cards validate --root "%USERPROFILE%/.workbuddy/knowledge/cards" --db harvester.db
   python -m harvester cards validate --root "%USERPROFILE%/.workbuddy/knowledge/cards" --db harvester.db
   ```
   验收：两环境结果一致；4 张真实卡出具新结论；`cards new` 产物被拒。
7. 节律收尾。提交信息模板：`fix v0.21: cards 校验器完整性（块标量/占位符/未核对显式化）`。

## SOP-P0-2 API 根因聚合接线（~15 行）

1. 测试先行：test_v20 补用例——`api_reports_tools` 返回的 Edit 行含 `roots` 数组且
   `len(roots) <= 12`、每项含 `{pattern, class, count, sample}`；并断言 roots 总错误数
   == 该工具 error 计数（防聚合丢数）。
2. `apiserve.py:399 _tool_rows`：对每个工具调 `toolstats.aggregate_error_roots(st.errors)`
   （H16），additive 加 `"roots": [...]`；**保留原 `errors` 字段**（additive 红线 §5.2）。
3. view：renderTools 优先渲染 `t.roots`（有则用聚合表，无则降级现状 errors 表）；
   **断言过滤生效**（H22：输入关键字后行数必须变化）。
4. 验收：起冒烟服务（端口查净 + ProxyHandler 绕代理），`/api/reports/tools` Edit roots
   ≈11 行且与 `docs/reports/G1_tools_report.md` 的根因行数一致。

## SOP-P0-3 view 空态文案带计数（~6 行）

1. renderSkills（:881）/renderCards（:970）：过滤态空结果显示
   `已过滤：0/N <单位>（过滤前 M，当前库无匹配异常）`——N=过滤后、M=过滤前，
   单位随报告类型（个 skill / 张卡 / 条）。
2. 断言：`rep-erronly` 勾选后 meta 行文案含"已过滤"（H22 教训）。
3. 更新 view README 的 G3/G4 小节一行说明。

## SOP-P0-4 卡片池对齐（~5 行 + 用户知情）

1. 改 `harvester-view/start.cmd:46`：`--cards-root` 直接指
   `%USERPROFILE%\.workbuddy\knowledge\cards`（主库即校验对象；候选池概念废止或改为
   主库子目录，向用户确认一句即可，不必展开讨论）。
2. 验收：起服务 → view G3 显示 4 张真实卡（非空柜、非"未配置"）。
3. docs/CARD_WORKFLOW.md 如有 cards_pending 表述，同步改。

## SOP-P0-5 adapter 补 model 抽取（3 个文件，~30 行）

1. 读 `workbuddy_transcript.py` 的 providerData.model 取法作为模式。
2. dsh.py / autoclaw.py / vscode_copilot.py 各自在会话级解析处提取 model 填入
   SessionRecord（字段已存在，H13——只缺 adapter 赋值）。
3. 测试：每 adapter 一个最小 fixture（真实日志片段脱敏）断言 model 非空。
4. 真实验收（需重建索引或增量导入后）：
   ```bash
   $PY -c "import sqlite3;con=sqlite3.connect('file:harvester.db?mode=ro',uri=True);print(con.execute('SELECT source, COUNT(*), SUM(model IS NOT NULL AND model!=\"\") FROM sessions GROUP BY source').fetchall())"
   ```
   验收：dsh/autoclaw/vscode-copilot 覆盖 ≥95%；报表"(空)"行失去解释力（H13 从 24% 提升）。
   注意：model 列回填需要重跑 index——先问用户可否重建/增量导入，**不要擅自重建全库**。

## SOP-P1-1 采纳闭环（用户参与，执行方只备料）

1. 为用户准备审阅材料：G2 建议池当前条目清单（标题+owner+unresolved_count+证据锚点）。
2. 用户逐条裁决后，执行：
   ```bash
   $PY -m harvester suggest-status --meta suggestions_meta.db --key "<条目标题>" --status adopted|rejected
   ```
3. adopted 条目由用户并入 AGENTS.md（编号顺延、清理被取代旧条目——AGENTS.md 既有纪律）。
4. 记录裁决日期；30 天后重跑 `report-tools` 出前后对比（可建 once automation）。
5. **T1 主题选定在此环节一并问用户**（真实小说主题 + 3–5 会话 sid）。

## SOP-P1-2 跨建议/跨卡根因去重

1. 去重键 = `errstats.normalize_error`（唯一权威，HANDOFF §5.4；禁自造第二把键）。
2. `build_suggestion_entries` 产出前：同键模板条目合并（计数相加、samples 合并去重、
   title 取 calls 最高者）；triage A 节同理。
3. 卡片查重提示：`cards new` 时按 normalize_error 比对既有卡 evidence/正文，命中则
   警告"疑似已有卡 kc-xxxx"（复用 triage 的"疑似已有卡"逻辑，若有）。
4. 验收（H15 复现用例）：合并后"Edit 前置"根因全局只出 1 条主建议。
5. agent_suggest.py:161/175 的 `raw[:100]` 改为不截断（多行引用折叠 `<details>` 交给
   view 渲染层处理；md 导出保持单行 + 全文见锚点）。

## SOP-P1-3 交叉表 class × harness × model

1. 一条 SQL：`SELECT s.source, st.error 按 classify_error 分桶, s.model, COUNT(*)`，
   以 apiserve additive 端点 `/api/reports/cross`（或并入 reports/errors）+ CLI 渲染。
2. 依赖 P0-5（否则 model 列无意义）；分类复用 `classify_error`，禁第二套分类。
3. 验收：能回答"哪个 harness 的哪类坑最多"（工具/环境/用法三列非全零）。

## SOP-P1-4 export-analysis 统一导出器

1. 新 CLI：`python -m harvester export-analysis --kind sessions|tools|errors|skills|triage
   --dedup root --format md|json --out ...`。
2. 去重：kind=tools/errors 走 (class, normalize_error(pattern))；sessions 走既有
   patterns_dedup 口径（v0.20 已有，勿重写）。
3. json 出口统一 `machineWrap` 头（kind/generated_at/db_fingerprint/hint 去重口径说明）。
4. view 四个导出按钮改调该端点产物（view 端只做下载，不再本地拼装去重逻辑）。

## SOP-T0 T 轨前置（锚点/提取器设计/text-raw 契约）

1. **text-raw 契约落地**：ADAPTER_CONTRACT.md 增条目"messages.text 为 bigram 索引文本，
   统计/提取必须 raw 优先"；写一个防回归测试：构造 bigram text 行 + raw 原文行，
   断言任何走 text 的统计函数结果与走 raw 的不同（故意让 text 是干扰文本）。
2. **锚点细化**：确认 messages 表已有 turn 粒度（读取路径 apiserve.py:297 一带）；
   T 轨所有新锚点格式定为 `{sid, turn, seq?}`；draft/chain 产出统一此格式。
3. **产物提取 pass 设计文档**（先写设计再动码，写入 docs/）：输入=原始 jsonl（源文件
   路径来自 sources.json），解析 function_call 的 Write/Edit 完整 args（绕开 H1 的
   400 字截断），输出=diff 序列表（独立 meta 库 `artifacts_meta.db`：sid/turn/ts/tool/
   old_text/new_text/锚点），**采集库零改动**（红线 §5.1）。
4. 验收：对任一 Write/Edit 密集会话产出 ≥1 条完整 diff（old/new 均非截断）。

## SOP-T1 主题注册表 MVP + 标题时间序（~150 行）

1. `topics_meta.db`（独立 meta 库）：topics 表（id/name/keywords/members JSON/created）。
   CLI：`topic register <name>` / `topic add <id> <sid...>` / `topic list` / `topic show <id>`。
2. 种子导入：9 个 topics md 文件 → 9 个 topic 记录（keywords 从文件标题/正文抽取，
   members 留空待补）。
3. `topic chain <id> --level coarse-title`：成员会话标题按时间排序去重（每标题带首现
   时间）+ 月度分布。落盘挂 db_fingerprint（dbmeta.py 已有，复用）。
4. **用户参与**：请用户指定真实小说主题与 3–5 个成员 sid；「叙事节奏」41 会话（H18）
   导入后把 18 行演进链产物给用户看，请其裁决聚类粒度（按作品/按技法）——记录裁决再进 T2。
5. 验收：同库快照重跑，产物 byte 级一致（可复现红线）。

## SOP-T2 分层时间线 + topic pack

1. `topic chain <id> --level coarse|mid|fine|artifact`（预算：粗 ~10K=每会话标题+首
   user+末 assistant；中 ~30K=user 消息序；细/产物=选定会话按需）。**全部走 raw**（H3）。
2. `topic pack <id>` 复用 drafting.build_distill_packet，但预算分层化（H5 的 24K 单会话
   预算不可直接复用——按 level 传 max_chars）。
3. 验收：五档各自不超预算档位；artifact 档展示同文本版本序（时间序即版本序，H19 不走
   文件快照）。

## SOP-T3 Agent 蒸馏 + topic-chain 长文 + 暴露

1. 蒸馏由 Agent（你自己在会话中）消费 topic pack 执行；产物 = `topic-chain` 长文
   （frontmatter: topic/members/anchors(stages)/generated_from(db_fingerprint)/prompt_version；
   正文 = 阶段分组 + "怎么想的→怎么变的→为什么"，每节点挂 `{sid, turn}` 锚点）。
2. **独立校验器**（不复用 §8 卡片校验，红线：链是长文不是卡片——PLAN §0.2-A 裁决）：
   锚点必须可回溯（逐个查库）、stages 必须有成员证据、frontmatter 必填项检查。
   长文存 `knowledge/topics/chain-<topic>.md`。
3. API additive：`/api/topics`（注册表+簇统计）、`/api/topic/<id>/chain`（chain 文档
   结构化），均挂 db_fingerprint。
4. view「主题」tab：左=主题列表（/api/topics）、中=时间线（分层切换、diff 芯片可展开、
   锚点跳转）、右=chain 文档/产物对照。触发=复制命令（`topic pack ...`），**绝不执行**。
5. 验收：任一"为什么改"节点一键跳回原文；同快照重跑产物 diff 可归因。

## SOP-T4 skill 进化 join

1. 时间线节点锚点 × skill 调用锚点（behstats 已有 anchors）交叉表。
2. 首份报告：对用户指定 1 个真实 skill，出具"在 N 次创作推进中各贡献了什么/哪次拖了后腿"
   ——此判断为 Agent 语义输出，交用户复核后可落 chain 文档附录。
3. 验收：报告含锚点回链，人工抽验 1 条属实。

## SOP-T5 自动聚类候选推荐器（后期）

标题 n-gram + 任务签名（首条 user 消息归一 + 工具序列 top-k）产**候选**（推荐器，
不改注册表权威）；候选准确率由用户判定并记录。原 v0.21 P2-4 并入此项。

## SOP-P2-1 report-chains（失败翼，确定性）

五种检测（全部从 steps(sid,seq,tool,phase,status) 算，H14）：长回合（步数>p95）、
同工具连击（连续 N≥5 步同工具）、序列循环（相邻去重后 A→B 周期 ≥3）、空转率
（错误后同工具重试 >3 仍失败）、高步会话 Top N。**验收含合成注入**：构造 40 步同工具
合成会话，报告必须报出（防恒真测试重演，红线 §5.6）。

## SOP-P2-2 report-keywords

n-gram（2/3-gram）词频 + 可选停用词；**只对 messages.raw 统计**（H3 契约的第一个
既有适用点）；落独立表 + 只读端点；view 后续接"高频词捞取"（不急）。

---

## SOP-X 提交与产物规范

提交信息模板：
```
feat v0.21: <任务号 标题>

- <改动点 1（含验收数字）>
- <改动点 2>
- tests：<新增用例数>（全套 N 例全绿）
```

任务完成定义（DoD）：代码 + 会红的测试先行 + 任务验收命令通过 + 两仓库全量回归 +
LF/JS 检查（若涉 view）+ 提交推送 + 工作日志。四者缺一不算完成。

顺序（v0.22 §3）：P0-1 → P0-2∥P0-3 → P0-4 → P0-5 → P1-1（用户）→ T0∥P1-2/4 →
T1 → P1-3 → T2 → T3 → P2-1 → T4 → P2-2 → T5。
