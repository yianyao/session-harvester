# HANDOFF — v0.22 执行续篇（T3 起步交接）

> **本文件是 `HANDOFF-v0.22-execution.md` 的增量续篇**：该文件的事实台账
> H1–H23、红线 §5、环境 §2 全部仍然有效，本文件不重抄，只记增量。
> 配套阅读顺序：PLAN-v0.22-unified → 旧 HANDOFF（H1–H23）→ 本文件 → SOP-T3 节。

---

## 0. 最新状态（2026-10-09，P1-2/P1-4 收官后）

| 项 | 值 |
|---|---|
| 后端 head | `a1da498`（P1-4），已推送；测试基线 **398 例全绿**（377 + P1-4 21） |
| 前端 head | `621d43e`（P1-4 view），已推送；render_smoke + view 测试全绿 |
| PLAN §3 队列 | **全部收官**（P0/P1-1/P1-2/P1-4/T0-T5/P2-1/P2-2），仅剩 P1-3 |
| 待用户复核 | ①chain-叙事节奏-draft 定稿 ②pdf-text-extractor 报告（margin 90 天裁决）③117 候选簇判定 |
| P1-2 | `439dfe7`：同根因建议合并（H15 验收 9→8 条，Edit 前置 130+13-3=140）+ triage A 节根因分组（24 pattern→2 组 9 归并）+ cards new 查重 + raw[:100] 放开 |
| P1-4 | `a1da498`/`621d43e`：export-analysis 统一导出器（5 kind × md/json 同源）+ /api/export-analysis + view 六按钮改调（端只做下载）+ 代理透传 text/markdown |

## 1. 当前状态（截至 2026-10-08 17:55，head=`9e8ce9b`）

| 项 | 值 |
|---|---|
| 后端 head | `9e8ce9b`（v0.22 T3 第 1-4 步完成），已推送 |
| 前端 head | `b085133`（T3-5 主题 tab 完成），已推送 |
| 后端测试基线 | **316 例全绿**（296 + T3 校验器 16 + T3 API 4） |
| view 测试基线 | **24 例全绿**（render_smoke.js 31 项断言 = 原 12 + 主题 tab 19） |

### 已完成任务与提交对照（PLAN §3 顺序）

| 任务 | 提交 | 一句话 |
|---|---|---|
| P0-1 cards 校验器 | `1d32ade` | 降级解析器/占位符/turn:null；双环境对账一致 |
| P0-2 API roots | `e77ca43` | _tool_rows additive roots；G1 对账一致 |
| P0-3 空态计数 | view `443f478` | G1 roots 优先渲染；G3/G4 已过滤计数 |
| P0-4 卡片池对齐 | view `f417f40` / 后端 `8ad53b2` | start.cmd 对齐主库；候选池废止 |
| P0-5 model 抽取 | `2d4f402` | 三 adapter 纯函数；真实源 dsh 95%/autoclaw 100% |
| T0 前置 | `2fa9b4c` | text-raw 契约 + 锚点格式 + 产物提取设计稿 |
| T1 注册表 MVP | `2fa9b4c` | topics_meta.db + topic CLI；叙事节奏 55 会话导入 |
| T2 五档时间线 | `cb3c952` | topics.timeline 五档 + artifacts.py 提取 MVP + topic pack |
| P1-1 采纳闭环 | `cb3c952`（状态库） | 7 adopted / 1 rejected 落库 suggestions_meta.db |
| T3 蒸馏+校验器+API | `9e8ce9b` | chain 长文 7 阶段 30 锚点 + topicchain.py + /api/topics、/api/topic/<id>/chain |
| T3-5 view 主题 tab | view `b085133` | 三栏（动态主题列表/锚点时间线/chain 文档）+ 分层复制命令，绝不执行 |

**下一步队列**：P2-1 → T4 → P2-2 → T5；P1-2 / P1-4 可并行插入。
（P1-3 在 PLAN 顺序里位于 T1 之后，SOP 无独立节——实施前先到 PLAN §4 确认其范围。）
（T3 已闭环含用户复核三点裁决，见 H39–H41。）

## 2. 增量事实台账（H24+，SOP 引用源，勿重复验证）

| # | 事实 | 证据 |
|---|---|---|
| H24 | messages 表**无 seq 列**（cols=sid,role,ts,text,raw），会话内时序 = rowid；steps 才有 seq | `PRAGMA table_info(messages)` |
| H25 | **叙事节奏 tp-20261008-010 成员全为导出型源**（deepseek-export 44 / yuanbao-raw 8 / qianwen-raw 3），无 dsh 成员 → artifact 档的主题级真实闭环无数据，验收靠 test_v22_t2_timeline 合成注入 | `show_topic` 成员 Counter 实测 |
| H26 | artifacts 真实提取基准：dsh specSkill 会话（`dsh:--D-Data-git-specSkill--/session-c1b51f26-abea-4659-98a0-bc2e7b622721/session.v4.jsonl.zstd`）提取 **463 条，184 条 >400 字符完整**（防截断回潮验收源，数据已在 artifacts_meta.db） | `artifacts extract` 实测 |
| H27 | 预算口径：LEVEL_BUDGETS 管**正文 blocks**，页头/月度等固定节不占预算 → 总输出可略超名义值（coarse 实测 10215/预算 10000，属设计内） | `topics._budget_join` + 实测 |
| H28 | `fingerprint_line` 含"生成于"时间戳 → 同快照重跑产物**仅时间戳行 diff**（可归因口径）；byte 级一致断言须同秒内跑 | render_title_chain 实测 |
| H29 | sync 后 model 覆盖：dsh 95% / autoclaw 100% / deepseek-export 100% / workbuddy-transcript 100%；yuanbao/qianwen/doubao/workbuddy 源导出数据无 model 字段（范围外） | sync 后 GROUP BY 实测 |
| H30 | topics_meta.db 现有 tp-001~009（种子）+ tp-20261008-010（叙事节奏，55 成员，evidence=标题口径说明）；种子 md 解析：fm.topic 或文件名做主题名，h1 去编号前缀做关键词 | 导入脚本实测 |
| H31 | suggestion status 的 key = **建议句原文**（api_reports_agents 的 title，含句号）；8 条已落库（7 adopted / 1 rejected="browser 工具报实例失效时先重建实例再继续。"） | suggestions_meta.db 实测 |
| H32 | fine 档走 drafting.render_transcript（raw 优先）；deepseek-export 源 raw 为 bigram 原貌（"遗憾 憾并 并不"形态）——**H3 契约的如实体现，不是 bug**，蒸馏时读 text 列归一版即可 | fine 档实测 |
| H33 | dsh sid 形态 `dsh:<rel posix 路径>`，文件 = adapter.root/rel；原始 tool/call 行 tool 名**小写**（edit/write/pwsh），但 extract_dsh_records 判定只看 args 键不看 tool 名 | dsh.py + 提取实测 |
| H34 | cli.py topic 的 `--id` 参数 dest=`id_`（`args.id_`）；新增子命令时别踩 | cli.py:825 |
| H35 | `.gitignore` 含 `docs/reports/`——报告/时间线/pack 产物只留本地不入库；commit 信息里可提但别 git add | git 提交实测 |
| H36 | 叙事节奏成员口径=sessions.title 含「节奏」（55 个，H18 评审时点 41，sync 后增长可归因）；聚类粒度用户已裁决=**按技法聚** | topics_meta.db evidence 字段 |
| H37 | `_budget_join` 对首块超预算也会截断（保底 100 字符）+ 截断提示行（"预算 N 字符内展示 X/Y 条"） | topics.py 实测 |
| H38 | **H32 修正**：703 条全样本实测——raw 列恒为原文（0 条 bigram）、text 列部分为 bigram（31 条命中）。蒸馏/统计一律 raw 优先（H3 契约不变），H32 "蒸馏读 text 列"的表述作废 | 全样本统计实测 |
| H39 | **复核裁决①②**（chain 证据口径，用户 2026-10-08 采信）：标题与 turn 摘录可作证据但必须带 {sid,turn} 锚点可链接打开会话；artifact 档无数据时以 user 消息粘贴文本片段佐证演进 | chain 草稿"复核裁决"节 |
| H40 | **复核裁决③（去重口径）**：会话级 user-raw 字符 bigram Jaccard ≥0.90 判重复（实测 12 对全为同稿重贴/跨端重发），7 簇 10 个重复并入最早代表，55→45；0.80–0.90 边界带 34 对=同阶段迭代，保留；轮级同稿多轮只采首末差异。锚点不迁移（2 个锚点在重复会话上仍可回溯），计数归代表。分析底稿 docs/reports/dedupe-叙事节奏.md（gitignore 本地） | t3_dedupe.py 实测 |
| H41 | T3 全闭环：chain 草稿（55 members/7 阶段/30 锚点，fm 含 dedup 块）校验通过、正式位 `~/.workbuddy/knowledge/topics/chain-叙事节奏.md` 已同步；view「主题」tab 主题列表**数据驱动**（/api/topics 返回什么渲染什么，绝不写死主题名——用户红线）；链路验收 10 主题/30 节点/404 透传全过 | 端到端冒烟实测 |
| H42 | **P1-2 落地**（`439dfe7`）：同根因合并=pattern 集交集的连通分量（agent_suggest._merge_overlapping + root_key_assign 并查集）；计数按并集相加不重复计；title 取自身 total 最高者（并集扩充前定格）；triage _mark 加 templates 字段（additive）；clean_error_sample 共享口径（回显清理+单行+缺省不截断）；cards scaffold_card 加 dupe_warnings（normalize_error 唯一键+宽松子串比对） | 真实库 9→8 条建议；triage A 节 24→2 组+15 独立 |
| H43 | **P1-4 落地**（`a1da498`/`621d43e`）：export_analysis.build_analysis 同源双出口（json=machineWrap 头 kind/generated_at/db_fingerprint/dedup/hint + data；md=复用 render_errors/tools/skill/triage + render_sessions_md）；sessions 的 patterns_dedup=v2.3 view 口径下沉；toolstats.tool_rows 自 apiserve._tool_rows 迁入；/api/export-analysis additive；view 六按钮 exportAnalysis() 化，代理 proxy_get 放行 text/markdown（三元组透传 ctype）；agents/cards 报告保留本地 mdReport/machineWrap | 5 kind × md/json 端到端 200 |
| H44 | **运维教训（复犯）**：后台起 api-serve/view 时接 `\| head -N` 会在 N 行后破管道——Python print 失败 → handler 全部静默死亡（端口仍 LISTEN 但不响应，RemoteDisconnected）；且 Windows 允许同端口重复绑定（SO_REUSEADDR），新起实例"成功"但请求全打到僵尸。**后台服务命令绝不接 head；起服务前 netstat 查重；同端口双 LISTEN 先 taskkill** | 本次冒烟双服务同时中招 |

## 3. T3 开工要点（下一任务）

按 SOP-T3 五步，补充本会话掌握的实施上下文：

1. **蒸馏输入**：`topic pack --id tp-20261008-010 --db harvester.db --level coarse
   --out docs/reports/topic-pack-节奏技法-coarse.md`（10274 字符）已生成；
   创作时建议在**新会话**直接 Read 该 pack + 按 fine 档按需拉选中之会话全文
   （`topic chain --level fine --sid <sid>`，mid 档 30K 可做补充）。
2. **长文形态**：frontmatter 必填 topic/members/anchors(stages)/
   generated_from(db_fingerprint)/prompt_version；正文=阶段分组+
   "怎么想的→怎么变的→为什么"，每节点挂 `{sid, turn}` 锚点
   （turn=split_turns 权威口径，messages 按 rowid 推导，H24）。
3. **独立校验器**（不复用 §8 卡片校验）：新模块（建议 `harvester/topicchain.py`）
   ——锚点逐个查库可回溯、stages 有成员证据、frontmatter 必填项检查；
   长文存 `%USERPROFILE%\.workbuddy\knowledge\topics\chain-<topic>.md`。
4. **API additive**：`/api/topics`（注册表+簇统计）、`/api/topic/<id>/chain`
   （chain 文档结构化），均挂 db_fingerprint；api_version=1 不动（红线 2）。
5. **view 主题 tab**：左=主题列表 / 中=时间线（分层切换+锚点跳转）/
   右=chain 文档；触发=复制命令，绝不执行（红线 3）；render 改动必须配
   render_smoke.js 断言（H22）。
6. **蒸馏由谁做**：长文创作是 Agent 语义工作（SOP-T3 明确"由你自己在会话中
   消费 pack 执行"）——新会话开工时 T3 第 1-2 步即创作，第 3-5 步是确定性代码。

## 4. P1-2 / P1-4 要点（可并行插入）

- **P1-2 跨建议/跨卡根因去重**（H15）：normalize_error 唯一键；注意
  agent_suggest.py 的 raw[:100] 截断（H7）一并处理。
- **P1-4 export-analysis 统一导出器**：(class, pattern) 去重，人读/机器读同源。
- 两者均为确定性代码任务，测试先行节律不变。

## 5. 环境速查（无变化，摘最常用）

- venv Python：`C:\Users\yianyao\.workbuddy\binaries\python\envs\default\Scripts\python.exe`
- push：`git -c http.proxy=http://172.16.20.27:12603 push`
- HTTP 冒烟绕代理：`build_opener(ProxyHandler({}))`；起服务前查端口
- meta 库 trio：topics_meta.db / suggestions_meta.db / artifacts_meta.db
  （均已 gitignore；harvester.db.bak-20261008 为 sync 前备份）

## 6. 开工流程

1. 读 PLAN §4（T3 行）→ 旧 HANDOFF（H1–H23 + 红线）→ 本文件 → SOP-T3。
2. SOP-0 检查单：版本对账（预期 head≥`9e8ce9b`）+ `python -m unittest discover
   -s tests`（预期 **316 例 OK**）+ 端口自查。
3. T3 按 §3 顺序：创作 → 校验器（测试先行）→ API → view → 节律收尾。
4. 冲突回到 PLAN §0 裁决；§7 用户交互协议继续生效（P1-1/T1 已闭环，T4 的
   skill 选定届时仍需用户指定）。
