# HANDOFF — v0.22 执行续篇（T3 起步交接）

> **本文件是 `HANDOFF-v0.22-execution.md` 的增量续篇**：该文件的事实台账
> H1–H23、红线 §5、环境 §2 全部仍然有效，本文件不重抄，只记增量。
> 配套阅读顺序：PLAN-v0.22-unified → 旧 HANDOFF（H1–H23）→ 本文件 → SOP-T3 节。

---

## 0. 最新状态（2026-10-09，update 增量同步落地后）

| 项 | 值 |
|---|---|
| 后端 head | `4103512` + 待办收尾两项（H50），**测试基线 485 例全绿**（2026-10-10 同步：v0.24 新增 6 例；须带 `docs/reports/sitecustomize.py` 的 PYTHONPATH 补丁，见 H53） |
| 前端 head | `6d7876f`（G2 交叉表渲染）；**view 测试基线 24 例全绿**（H48 修 encoding 前实为 23/24） |
| PLAN §3 队列 | **全部收官**（P0/P1-1..4/T0-T5/P2-1/P2-2） |
| 用户裁决 2026-10-09 | ①chain 定稿（校验器通过）②margin 90 天认可 ③117 簇已注册（主题 10→127）④AutoClaw 排查**不开** |
| 数据更新双轨 | `update` = 日常增量（库即水位，只导新 sid/updated_at 变化）；`sync` = 全量对账基线（语义不变） |
| AI CHAT 接入 | 维持收件箱协议：官方导出包丢 inbox/ 自动收割；重复包内容级判重 |
| P1-2 | `439dfe7`：同根因建议合并（H15 验收 9→8 条，Edit 前置 130+13-3=140）+ triage A 节根因分组（24 pattern→2 组 9 归并）+ cards new 查重 + raw[:100] 放开 |
| P1-4 | `a1da498`/`621d43e`：export-analysis 统一导出器（5 kind × md/json 同源）+ /api/export-analysis + view 六按钮改调（端只做下载）+ 代理透传 text/markdown |
| P1-3 | 见 H45：交叉表 class × harness × model（collect_errors additive model 键 + cross_stats + /api/reports/errors `cross` 字段 + render_report 交叉表节） |

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
| H45 | **P1-3 落地**：交叉表 class × harness(source) × model——collect_errors_from_db SELECT 补 `s.model`（errors 项 additive `model` 键，NULL 归"（未知）"与 facets 口径一致）+ `errstats.cross_stats(errors)` 纯聚合（行含 source/model/四类计数/total，total 降序，首行=最多坑组合）+ /api/reports/errors additive `cross` 字段（SOP-P1-3"或并入 reports/errors"选项）+ render_report 新增"数据源 × model × 错误类别交叉表"节（CLI report-errors 自动生效）。分类仍单点 classify_error，禁第二套。真实库验收：10 行，sum(total)==error_count==by_class 合计=290，首行 dsh×deepseek-flash 126 条以 tool_interface 为主（三列非全零） | tests/test_v22_p1_3_cross.py 8 例；真库对账实测 |
| H46 | **fixture 旧 schema 教训**：test_v10/test_v13 手写 sessions DDL 缺 model 列（v0.15 前旧态）→ P1-3 给 collect_errors_from_db 加 SELECT s.model 后全量回归 8 例 no such column。修复=补齐 fixture DDL 对齐冻结 schema（test_v13 位置插入同步补 NULL 列）；**不做运行时 schema 嗅探降级**（fail loud 纪律）。新增消费 sessions 列的代码时，先 grep tests 里 `CREATE TABLE sessions` 手写 schema 是否同步 | 全量回归 406 例复跑 |
| H47 | **update 增量同步 + adapter sid 契约修复**：①新增 `run_update`/CLI `update`——收件箱收割 → 库水位对比（`sessions(sid, updated_at)`，**库即状态**无独立水位文件）→ 仅导出新 sid/updated_at 变化（export 新增 `select_sids` 参数，sid 为库口径 `{source}:{session_id}`）→ 整库重建索引；sync 保持全量作对账基线。②H47 教训：vscode-copilot `load_session` 用裸 sid 作 `rec.session_id` 而 `list_sessions` 给 `db::/empty::/ws::` 前缀形态——**list 与 load 的 session_id 不一致**导致对比 sid≠入库 sid，增量对该源永远判新（全量模式靠文件名幂等掩盖）。修复=rec.session_id 用完整入参（base 契约隐含要求，实为既有 bug）；迁移=删 exports 旧 vscode-copilot 文件 24 个 + 重导（topics_meta 无该源引用，安全）。真机验收：二跑新增 0/跳过 1963 收敛；活跃会话 updated_at 变化重导=续聊语义正确。用户裁决：AI CHAT 维持收件箱协议（官方导出包丢 inbox），不自动抓网页平台 | tests/test_v22_update.py 5 例 |
| H48 | **第三方全量复查（2026-10-09）三处修复 + 一处自我更正**：①`test_v24_render.py` 的 `subprocess.run` 缺 `encoding="utf-8"` → Windows locale=cp936 解码 node 的 UTF-8 中文输出抛 UnicodeDecodeError、stdout 变 None、assertIn 抛 TypeError；**view 测试基线因此实际是 23/24 而非"全绿"**。修复后 24 例 OK。②`topics.py:217/401` 写 `f"- 库快照：{fingerprint_line(fp)}"` 而 `fingerprint_line` 已自带前缀 → 产物出现"库快照：库快照："；新增 `dbmeta.FINGERPRINT_PREFIX` 单一来源，两处改 `f"- {fingerprint_line(fp)}"`。③**P2-2 排序口径修正**：`freq` 是"单条文本内出现次数×条数"累加，故长会话里反复出现的人名压过真主题词（真库实测「丁樾」freq=50577 而全库仅 6723 条消息）。新增 `doc_freq` 列（= 含该 gram 的消息条数），排序改 doc_freq 优先；真库复测「丁樾」doc_freq=1421 居首暴露停用词需求。旧 meta 库（缺列）：读时显式提示重跑、写时就地重建。④**自我更正**：我上一轮报告称 `candidates.task_signature` 是死函数——**错**，我的扫描只覆盖 `harvester/` 未覆盖 `tests/`，实为测试用 API（`test_v22_t5_candidates` 3 处引用）。已恢复该函数并补 docstring（单会话口径，批量走 `_load_features` 避 N+1）。真正死代码只有 2 处 import，已清 | test_v23_docfreq.py 6 例；后端 417 例 / view 24 例全绿 |
| H49 | **P0 级待办前两项落地（2026-10-09 续，用户裁决后）**：①**依赖清单**（#5）：新增 `pyproject.toml`——`dependencies=[]`（核心零依赖承诺不变）+ extras `yaml`/`dsh`/`web`/`verify`/`all`；**并修一处入口缺陷**：`[project.scripts]` 若挂 `harvester.cli:main` 会让所有非零退出码静默变 0（console script 包装器只调函数、不 sys.exit），故新增 `cli.run()` 显式 `sys.exit(main())` 并挂它。守卫测试 `test_v23_deps_manifest`：AST 扫描 harvester/ 内**凡在 try 块中的第三方导入**必须出现在某个 extra 里——增删任一侧不同步即红（stdlib 与 `compression` 探针显式豁免）。②**T4 after_stage**（#15）：实测确认 `--margin-days` **只向后放宽**且原实现**三条归组路径都没有日期检查**——真库跑出"阶段七 span 至 2026-07-10，而 95/95 次命中全部落在阶段结束之后"（即该节此前把"阶段后 80 天"的调用呈现为阶段内贡献）。修法：`day > span_end` 判据**三条路径一律适用**（含 member 节点精确匹配——节点只保证"属本主题该 turn"，不保证日期在 span 内），kind 加 `_after` 后缀 + `after_stage` 布尔，报告头与逐 stage 标题均标注计数。③**交叉表补 skill 维**（#14）：新增 `errstats.cross_stats_by_skill`（活跃 skill × harness × 类别，与 `cross_stats` 同源 sum(total) 恒等）+ API additive `cross_skill` + 报告新节。归属口径=该错误 seq 之前**最近一次** Skill 类调用载入的技能。**过程中自身引入并修掉一个真 bug**：`_skill_name_from_detail` 的键回退链含 `command`，若不按 `SKILL_TOOLS` 白名单过滤，pwsh 的 `{"command":"icacls ..."}` 会整条变成 skill 名（真库实测污染出 "icacls tests\... | dsh" 这类行）；已修并留回归测试，同时确认**既有 report-skill 路径干净**（该 bug 仅存于新增索引构建） | test_v23_deps_manifest 5 例 / test_v23_after_stage 6 例 / test_v23_skill_cross 9 例；后端 **437 例全绿** |
| H50 | **待办收尾两项（2026-10-09，用户裁决后）**：①**元宝块类型防御**（#2，用户裁决"扩充白名单"）：**探针第一版是假通过**——查 `messages.raw` 得"0 未知类型"，实因 raw 为**纯文本**（非 JSON）、0 条可解析；修正为直读 adapter 真正解析的 `corpus/yuanbao_raw/detail_*.json` 后实测 **1223 文件 / 11 种块类型 / 白名单仅 9 种**：`drawWithSearchGuid ×13`（AI 出图提示词，属创作内容）、`doc_percent ×1`（系统通知）。两者纳入白名单；draw → note（`[draw]` 保内容），doc_percent → `[notice]` 不入正文；新增"白名单内但无解析分支"独立告警（与"源端新增类型"成因区分）。真实源验证 **8558 → 8572 条消息（+14 = 原先被丢的 14 块）**、零未知类型；断言已证明可证伪。**教训**：探针必须对齐被测 parser 的真实数据落点，否则"0 异常"可能是"0 解析"。②**停用词表**（#1，用户裁决"接受"）：新增 `harvester/data/stopwords_zh.txt`（313 条，只含通用虚词；红线=不含任何作品人名/主题词）并接为 `keywords` 默认启用；新增 `--stopwords`（可多次叠加私有表）与 `--no-stopwords`；`pyproject` 补 `package-data`（否则装包后默认表丢失、过滤静默失效）。真库验收：一个/自己/怎么/没有/出来/不是/什么/还是/就是/起来 **十项残留 doc_freq 全为 0**。红线由测试守护（随包表不得含 >=3 字 CJK 词条，放行名单 <=5 且需登记；并断言真库高频人名不在表内）。③**附带修一处被本次改动暴露的真 bug**：`extract_grams` 对纯 ASCII 段也做 n-gram 滑窗 → 产出 il/ll/es/or/er/en/se/sk/nt/in/on 等无意义片段，doc_freq 口径上线后占据真库前 30 名；改为 **ASCII 整词、CJK 仍滑窗**（既有断言 `extract_grams("abc def",2)==["ab","bc","de","ef"]` 正是该 bug 的固化，已改写并注明） | test_v23_yuanbao_blockguard 8 例 / test_v23_stopwords 11 例；后端 **457 例全绿（+20）** |
| H51 | **v0.24 主题合并（#10 的确定性一半）**：新增 `topics.merge_topics` + CLI `topic merge --id <目标> --from "<源,源>" [--keep-sources]`——**单事务**；成员按 sid 去重（目标已有者**保留原证据**，新并入者证据追加「（合并自 <源>）」可追溯）；关键词按顺序并集（**不去噪**）；缺省删除源主题行。真实库落地：主题 127→**126**（+2 新建 −3 合并），`tp-20261009-054/084/088` 并入新建 `tp-20261010-002`（6 成员带来源尾注）+ 补 8 个同簇成员 = 14。**合并会把自动聚类候选的标题 bigram 碎片关键词带进来**（实测带入"两个/ut/及工/回传/成物/于本/给的/牲规/态一"），已用一次性脚本 `docs/reports/fix-keywords.py` 定稿为 7 个——**语义去噪不由工具代做**（红线：工具不判断主题） | 真库 merged 输出；`topic list` 计数 126；CLI 烟测（新建→合并→重跑报 KeyError）；`tests/test_v24_topic_merge.py` 4 例 |
| H52 | **取文口径修正（真 bug）**：`drafting.render_transcript` 原为 `text or raw`（`drafting.py:91`），与 H3/H38「raw 恒为原文、text 为检索 bigram」契约**相反**。真库实测 `messages` **62899 条中 33832 条 text ≠ raw**（样本：text=`丁樾 樾瘫 瘫坐 坐在…` vs raw=`丁樾瘫坐在椅子上回气。…`）→ **fine 档 / 蒸馏包此前有 53.8% 的消息渲染的是 bigram 原貌而非原文**。修为 **raw 优先**（raw 空时回落 text）；真机验证 `qianwen-raw:2ca550d8…` 现在渲染原文、bigram 串不再出现；回归 2 例（raw 优先 / raw 空回落）。**H32 曾写"fine 档走 render_transcript（raw 优先）"——当时是错的，本轮才真对齐** | `docs/reports/probe-textraw.py` 实测；`tests/test_v24_topic_merge.py::TestTranscriptRawFirst` |
| H53 | **本会话环境事实：DSH 沙箱下 `tempfile` 不可用（非项目缺陷）**：`os.mkdir(p, 0o700)` 建出的目录**连本进程都无法写入**（Errno 13），连 `icacls` 读该目录 ACL 都报 Access is denied；`0o755/0o777/默认` 正常。`tempfile.mkdtemp()`(0o700) / `mkstemp()`(0o600) 因此必失败 → **既有 457 例基线在本会话直接跑会整体崩**（`tests.test_v14` 6 例 `ERROR: unable to open database file`，与被测代码无关）。绕过：`docs/reports/run_tests.py`（进程内把传给 `os.mkdir`/`os.open` 的 mode 补组/其他位，**不改项目代码与测试语义**；`docs/reports/` 已 gitignore）。**基线实测（2026-10-10）**：venv 解释器 **485 例全绿**；无 PyYAML 的 base 解释器 `Ran 482 / FAILED (errors=35)`（预期门禁；3 个模块 collection error 故 463→460） | 现场探针（mkdir 0o700 vs 0o755）；`run_tests.py` 前后对比；两次全量运行 |
| H54 | **主题 A/B 注册与成员口径**：A=`tp-20261010-001`「吾好梦中救人」**41 成员**，口径=`messages.raw` 含书名（**标题口径仅 5 个太薄**；`messages.text` 口径得 0 ——"0 命中"实为"0 可解析"的假通过，同 H50 教训）；B=`tp-20261010-002`「Skill 自学习进化与跨平台设计」**14 成员**（合并 3 主题得 6 + 同簇补 8）。A 会话级去重（user-raw 字符 bigram Jaccard≥0.90，H40 口径）：**41→33 代表、8 个并入、24 对边界带（0.80–0.90 保留）**；**2 个成员 user 回合 0**（`deepseek-export:ca52e61b…` 等）**不可挂锚点**（chain-validate 判越界）。B 无重复（reps=14）。跨平台证据：B 内 2 组首条 user 诉求**逐字相同**却分投不同平台（一组相隔 1 分钟：qianwen 17:14 / workbuddy 17:13） | `docs/reports/{probe-members2,resolve-members,dedupe-topic,probe-crossplatform}.py` 实测；`topic show` |
| H55 | **主题语义梳理（126 → 13）**：按用户 2026-10-10 三项裁决执行（粒度 12 类 / 舍弃 12 条 / `069《情绪囚笼》` 并入小说组）。**① 诊断**：117 个自动候选是**词面聚类**（标题 bigram）产出的"会话对"，实测**成员重叠仅 10 对、114 个孤例** → **不能靠共享会话自动合并**；9 个种子主题（`tp-20261008-001..009`）**成员为 0**——用户自己的类目从未挂过成员，故正解是"碎片归位到种子 + 补 2 个新类目（小说、梦境心理）"。**② 执行**：`docs/reports/consolidate-topics.py`（带**完整性校验**：库内每个主题必须归入"目标/源/舍弃/保留"之一，否则拒绝执行——首次 dry-run 即抓出漏项 `tp-20261009-110`）+ `topic merge/rename/delete`。**③ 结果 13 个主题**（12 类目 + 待定 1 条 `tp-20261009-072`「评《无星之夜》」）；关键成员数：小说 `tp-20261008-010`=**324**（55 既有 + 269 并入，含 `tp-20261010-001`）、`tp-20261010-004` 会话采集=**185**、`tp-20261010-002` Skill=**36**、`tp-20261010-003` 梦境心理=**27**。**④ 回滚点**：`topics_meta.db.bak-20261010-085029-pre-consolidation`；被删 12 条主题快照在 `docs/reports/deleted-topics/*.json`。**⑤ 执行事故**：首次 `--apply` 在 T1 合并落库后崩于 `created[key]` KeyError（脚本键名不一致），多步破坏性操作未做事务包裹 → 从备份**整体还原后修脚本重跑**（教训：多步破坏性脚本要么单事务、要么可重入；本轮靠"先备份"兜住）。**⑥ 偏差备案**：`tp-20261009-039` 方案 §1 说归 T9、§2 旧列表说归 T3；先按 T3 执行，后用 `move-039-to-T9.py` 挪回 §1（T9 3 成员、T3 36 成员） | `consolidate-log.txt`；`/api/topics` 实测 13 条 |
| H56 | **v0.25 新增 `topic rename` / `topic delete`**：`rename_topic` **保 id**（故已发布 chain 的 `topic_id` 仍对得上，重名/缺主题/空名分别 KeyError/ValueError，**fail loud 不静默合并**）；`delete_topic` 返回含 name/keywords/members/created 的**快照**，CLI `--out` 落盘留痕（回滚依据）。测试 4 例：保 id 与成员、重名冲突与失败路径零改动、同名 no-op、删除快照 + 再删报错 | `tests/test_v24_topic_merge.py::TestTopicRenameDelete` |
| H57 | **一个主题多条 chain（additive，v0.25）**：合并后 `tp-20261008-010` 同时拥有「叙事节奏」与「吾好梦中救人」两条 chain，而旧 `/api/topic/<id>/chain` **只返回文件名序第一条**（第二条在 view 里根本不可见）。改法：**旧字段语义不变**（仍是第一条），新增 `chains[]`（每项含 `name`/`stages`/`nodes`/`prompt_version`/`body`/`chain_path`/`fm`）与 `chain_count`；`/api/topics` 新增 `chains_count`/`chain_names`（需 `chain_root`；旧 3 参调用降级为 0，不破旧上游，红线 §5.2）。**chain 显示名优先取正文 H1**——否则同主题两条 chain 都显示成同一个主题名、人分不清。测试 5 例；真实库验证 `/api/topics` 13 主题、`tp-20261008-010` `chain_count=2` 且两条 H1 名可区分 | `harvester/apiserve.py`；`tests/test_v25_topic_chains.py`；`docs/reports/check-api-chain.py` |
| H58 | **v0.26 主题梳理流水线（把"整理"变成可重复一环）**：新增 `harvester/consolidate.py` + CLI `topic-consolidate`，三档用法——① `--plan-out`：产**梳理包**（主题信号表：成员数/关键词/已有链数/前 3 成员标题 + **零散会话候选** + 可直接填的 YAML 模板）；② `--apply <plan.yaml>`（**缺省 dry-run**，加 `--yes` 才写）；③ `--noise-list` 看零散登记。**文件级原子**：全程操作 meta 库的临时副本（`.tmp-apply`），全部成功才 `os.replace` 换入——中途异常则真库**逐字节未变**（回归 v0.25 那次"T1 落库后脚本崩"事故；测试用 `mock.patch` 故障注入 + `read_bytes()` 相等断言）。**完整性校验**：每个主题必须出现在 target/from/discard/keep/renames 之一，否则拒绝执行（**renames 也算归位**——这条是测试抓出来的校验器漏洞）。**零散会话只登记进 meta**（`sessions_noise`，sid+reason+created，幂等），**采集库只读、不删会话**。真实库出包实测：13 主题 + **448 个零散会话候选**（规则：user 回合=1 且首条正文<40 字符 且 不属于任何主题） | `tests/test_v26_consolidate.py` 10 例；`docs/reports/consolidate-packet.md` |
| H59 | **零散登记与消费方的接线状态**：`sessions_noise` 是**判定登记**而非删除（`harvester.db` 只读，红线）。后续消费方（`topic-candidates`/`keywords`/卡片生成）若要按它过滤，需各自显式读 meta 表——**本轮未接线**（待办 V4）；不读它的调用方行为完全不变（additive） | `consolidate.NOISE_RULES` / `list_noise()` |
| H60 | **v0.27 view 主题页：多链 + 人读形态**（前端仓库 `harvester-view` 提交 `7066e5e`）：① `renderTopics` 新增「链」列（`chains_count` + 链名，无链显示占位符）、**0 成员类目灰显**；② `renderChain(d,out,idx)` 支持 `d.chains[]`——渲染第 idx 条 + 顶部切换按钮（**只切换渲染，无执行语义**，红线 3 不破），单链旧上游完全兼容；③ 新增 `bindChainSwitch` 把按钮接到重渲染；④ 新增 `kwText`：`keywords` 在 API 里是 JSON 字符串（给机器用），人眼前拆成「、」列表，**解析失败原样显示不吞内容**。验证：view 测试 24 例 OK（`render_smoke.js` 新增 12 条断言，含"单链不渲染切换条"与"点击真的换链"的接线断言）；**另用真实 API 载荷做渲染检查**（`docs/reports/check-view-real.py` + `view-real-render-check.js`，14 项全过：13 主题列表 / 小说主题 2 条链可切换 / 第 2 条 50 节点）。**注意**：`renderChain` 签名变了（多了 `idx`），`render_smoke.js` 的 `extract()` 已同步支持自定义签名；改 view 渲染函数时要一并提取它调用的 `kwText`/`bindTopicNodes`，否则测试抛 ReferenceError（本轮踩过） | view 仓库 `7066e5e`；`harvester-view/tests/render_smoke.js` |
| H61 | **V4 零散会话接线（v0.28）**：登记了不用等于没登记。① `consolidate.noise_sids(meta)` 供消费方取零散集合（**只读**：缺表返回空集、不顺势建表——消费方持只读连接，本轮实现的第一版会建表，已修并加断言）；② `topic-candidates` 排除项从"已注册成员"扩为 `已注册 ∪ 零散`；③ `keywords` 新增 `exclude_sids`（CLI 从 `--topics-meta` 读 `sessions_noise` 自动传入），统计口径新增 `params.noise_excluded`/`noise_msgs_excluded`。**踩坑**：`noise_msgs_excluded` 首版按全 role 计数 → 测试当场抓出 `2≠1`（该 sid 的 assistant 消息也被算进去），已改为与统计同 role 口径。缺省不过滤 ⇒ 未登记时行为与接线前完全一致（additive） | `tests/test_v26_consolidate.py::TestNoiseWiring` 3 例；485 例全绿 |## 3. T3 开工要点（下一任务）

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
