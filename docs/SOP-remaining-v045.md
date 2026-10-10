# SOP · 剩余事项（v0.45 收尾轮）

> 生成时点：2026-10-10，基线 **commit `cded3ae`（v0.45）**。
> 输入：① 项目自身台账（H1–**H93**）与 `docs/HANDOFF-v0.33-next.md` §0；
> ② 用户提供的第三方全量检查文档（其标称基线为 **v0.41**，故**部分结论已过期**）。
> 本文是**供 agent 消费**的执行件：每条都写"做法 + 验收（可判定/会红）+ 依赖"。
> **先读 §1 的核对结论**——评审文档里有一条已经不是待办了。

---

## §1 对第三方检查文档的逐条核对（2026-10-10 实测，基线 v0.45）

| 评审条目 | 评审说法 | 本轮实测（命令/位置） | 判定 |
|---|---|---|---|
| MCP 消费面滞后 | 4 工具停在 v0.3，缺 `topic_export`/`suggest_list`/`cards_list`/`chain_read`/`artifacts_list` | `harvester/mcpserver.py`：只有 `tool_list_sessions`/`tool_search_history`/`tool_read_session`/`tool_pack_context` 四个 + `_tools_manifest`/`_dispatch` | **成立**（当前最大缺口） |
| `regress` 仍缺（第四次点名） | "CLI 无该子命令" | **v0.43 已落地**：`harvester/regress.py` + CLI `regress`，7 步 / 57 断言 / 退出码三分（台账 **H90**） | **已过期**（评审未看到 v0.42 之后的提交） |
| 版本号失真 | `__version__` 仍 0.18.1 | `harvester/__init__.py:8` = `"0.18.1"`；`apiserve.api_meta.package_version`、`mcpserver.SERVER_INFO.version` 都取自它 | **成立**（一行 + 一条断言） |
| DSH schema 指纹缺 | DSH 0.3 落地前悬着 | `apiserve._schema_fingerprint()` 存在，但那是**本项目 `EXPECTED_SCHEMA`** 的 sha256，不是**上游 DSH transcript 形态**的版本守卫 | **成立**（需一次设计，不是一行） |
| `keywords_meta.db` 无 GC | 40MB、一周翻倍 | 40.2 MB；`keyword_runs` **4** 行、`keyword_stats` **435,583** 行；CLI 无 prune/vacuum 参数 | **成立** |
| `topicexport.py:46` 循环内 `import re` 遮蔽模块级 `re` | pyflakes 抓到 | 确认：`topicexport.py:23` 已 `import re`，第 46 行在 for 内重复 import | **成立**（删一行） |
| 5 处 f-string 无占位符 | 纯风格 | 同类命中多于 5 处（`agent_suggest.py:249`、`export_analysis.py:250`、`topicexport.py:163`、`cli.py:1146/1163/1165` 等） | **成立** |
| 建议台账只有 8 条 | 14 主题/117 候选簇的裁决应留痕 | `suggestions_meta.db.suggestion_status` = **8 行**（0.0 MB） | **成立**（口径问题，非缺陷） |
| 卡片存量少 | G3 产能瓶颈在蒸馏节奏 | 未量化（`artifacts` 463 条）；属产能不属工具 | 接受 |
| `dsh.py` 两条 unused import 未补 `noqa` | 小事 | `deadcode-scan`（`rglob` 递归，覆盖 `harvester/adapters/`）**0/0/0**，未复现 | **未复现**（关闭该条，除非换工具复现） |
| 测试/view 基线 | 582 / 25 | 后端 **640 例全绿**、view 25（v0.45） | 数字已更新 |

**结论**：评审的**诊断方向是对的**（瓶颈在"Agent 消费面"与"可对账起点"），但**待办清单里有一条已经完成**（`regress`），且它漏了本项目自己挂着的两件事（覆盖已补、1 例 flake）。按它的清单直接开工，会先做一件不需要做的事。

---

## §2 对"两条河"完成度判断的意见

- **河 1（采集/处理 → Agent 可读数据）**：机械半边（同步/归一/检索/诊断/主题/导出）确实已闭环，评审给的 ~85% 合理。**缺的那 15% 不是"数据没加工"，而是"分发通道只有一个 HTTP"**——MCP 停在 4 个通用工具，主题/链/建议/卡片/产物这五类"进化数据"Agent 拿不到。
- **河 2（view：人可读 + Agent 可用）**：view 薄、引擎厚是**正确架构**，不建议为"Agent 可用"往 view 里塞逻辑。评审把河 2 的缺口归到河 1 的断点上，这个判断我同意。
- **一处要改口径**：评审把"`regress` + MCP + 版本号"并列为"最后一段路"。实际**只剩 MCP + 版本号**；`regress` 已是既成事实，它的**残余风险**是另一件事——`regress` 的子代理当年**没交回"故意破坏→变红"证据**（H90 已如实标注），这条该补，但属"给门补证明"，不是"建门"。
- **一条我不同意的隐含结论**：不能把 MCP 扩面当成"换皮"。扩了 MCP 就必须同时上**工具清单 ↔ 后端能力**的漂移门（v0.44 刚给 README 上过同一道门），否则重演"文档/接口说的和做的不一致"。这一步不做，MCP 扩面=引入新的静默漂移面。

---

## §3 SOP（按执行顺序；每条都可判定）

### A 组 · 一行级修复（先做，半小时量级）

| # | 事项 | 做法 | 验收（会红的断言） |
|---|---|---|---|
| A1 | 版本号与提交对账 | `harvester/__init__.py:8` → 与"最新发布线"一致（当前应随 v0.45）；`mcpserver.SERVER_INFO.version`、`apiserve.api_meta.package_version` 都取自它，改一处即全通。**本仓库无 `CHANGELOG`**（已 glob 确认），故单一来源取 `docs/HANDOFF-v0.33-next.md` §0 快照里那行版本号 | 新增测试：从 HANDOFF §0 正则抽 `v\d+\.\d+`，与 `__version__` 的前两位比对；再断言 `__version__ != "0.18.1"` 这类**明显过期值**（两处其一忘改即红） |
| A2 | 删 `topicexport.py:46` 的循环内 `import re` | 直接删该行（`topicexport.py:23` 已有模块级 `import re`） | 套件绿 + `deadcode-scan` 两根 0/0/0 |
| A3 | 清 f-string 无占位符 | 逐处去掉 `f` 前缀（`cli.py`、`agent_suggest.py`、`export_analysis.py`、`topicexport.py`） | 新增一条**静态检查**测试：AST 扫 `JoinedStr` 无 `FormattedValue` → 报错（否则还会长回来） |
| A4 | 关闭或复现 `dsh.py` unused import 条 | 已用 `deadcode-scan` 实测 0 条 → **关闭**；若换 pyflakes 复现再补 `# noqa` | 无需断言（记录在案） |

### B 组 · 主线：Agent 消费面（河 1 的最后一段）

| # | 事项 | 做法 | 验收 |
|---|---|---|---|
| B1 | MCP 扩五个只读工具 | `mcpserver.py` 增 `topic_list`/`topic_export`/`chain_read`/`suggest_list`/`cards_list`/`artifacts_list`。**复用现状（已读源码）**：`topic_export`→`topicexport.topic_bundle`＋`render_topic_json`；`chain_read`→`topicchain.load_chain`＋`apiserve.api_topic_chain`；`suggest_list`→`suggestmeta.load_statuses`。**需要新写的小件（各约 10 行）**：`cards_list`（`cards.py` 现只有 `validate_cards`/`render_report` 与 `cli.cmd_cards`，没有"列全部卡"的纯函数）与 `artifacts_list`（`artifacts.show_artifacts` 是**按 sid** 取）。全部只读、`mode=ro`、返回结构带 `db_fingerprint` | 每个工具一条契约测试：入参 → 返回结构含 `db_fingerprint`；**真实库跑一次**（14 主题）断言主题数与 `/api/topics` 一致 |
| B2 | MCP ↔ 后端能力漂移门 | **注意：`apiserve` 当前没有路由表常量**（已读源码：HTTP 分派是 `if path == "/api/…"` 的硬编码分支，如 `apiserve.py:736/753`）。建议先加一个 `ROUTES` 常量表并把分派改成查表（这一步本身就是防漂移的前提），再仿 `tests/test_v44_doc_cmds.py` 做双向比对 | 元测试：**故意**从 `_tools_manifest()` 删一个工具 → 必须红；再故意加一个后端没有的工具 → 必须红 |
| B3 | MCP 输出与 HTTP 同源对账 | 同一主题分别走 MCP 与 HTTP，比对关键字段（主题数、chains_count、成员数） | 差异 >0 即红（**additive 红线**：MCP 不得自造字段） |
| B4 | MCP 文档与 `draft/pack` 关系说明 | README 增"MCP 工具清单 + 与 HTTP 端点对照表" | B2 的漂移门同时覆盖 README（防文档腐烂） |

### C 组 · 质量欠账（评审 P2/P3 剩余 + 本项目自留）

| # | 事项 | 做法 | 验收 |
|---|---|---|---|
| C1 | `keywords_meta.db` GC | `report-keywords` 增 `--keep-runs N`（缺省保留最近 N 次运行）+ `--vacuum`；删旧 `keyword_runs` 与其 `keyword_stats` | 测试：造 3 次 run → `--keep-runs 1` 后只剩 1 次，且**按 run 删干净**（不许留孤儿 stats 行） |
| C2 | 建议台账口径 | 明确"什么必须进 `suggestion_status`"：14 个主题的归并/裁决、117 候选簇的取舍都要有 status 记录（现仅 8 条） | 新增导出/核对命令输出"已裁决但无台账"清单；**清单非空即红**（或人工裁决项数 = 台账条数） |
| C3 | DSH schema 指纹 | 设计：`adapters/dsh.py` 记 `schema_fingerprint`（对 DSH transcript 形态的稳定字段集合做 hash），落 `sessions` 或 meta 表；`detect` 不匹配时**显式降级**而非静默解析 | 测试：喂一份"字段被改名"的假 transcript → `detect` 报降级/未识别，**不许**产出半成品记录 |
| C4 | 1 例 flake 定位 | 复现时留 `-v`；已排除"并发期间假红" | 复现记录写进台账；未复现前如实记"未定位" |
| C5 | `regress` 的"故意破坏→变红"补证 | 对 `regress` 的 57 条断言做变异自检（照 v0.45 的 `mutation-check` 做法） | 至少 3 处口径改坏 → `regress` 必须红；把结果写进台账（补上 H90 的欠证） |

### D 组 · 待用户裁决（产品口径，非技术欠账）

| # | 事项 | 需要你定什么 | 影响 |
|---|---|---|---|
| D1 | 已发布 chain 是否随主题重生成 | 主题 `tp-20261008-010` 已 **411** 成员，而 chain 仍是 **55** 成员的历史快照——是"历史文档不动"还是"定期重生成"？ | 决定 chain 是"档案"还是"活文档"；活文档就需要一条 `chain-regenerate` 的 SOP |
| D2 | chain 元结论回写全局记忆 | 我此前建议**不做**（领域结论进代理工作记忆=噪声）；若你认为该做，改口径即可 | 影响 `suggest-status` 台账条数（C2 相关） |
| D3 | 卡片产能节奏 | G3 卡片的瓶颈在"多久蒸馏一次"，不设节奏就没有存量 | 决定是否把"定期蒸馏"写成 SOP 而非待办 |

---

## §4 本轮已顺手做掉的两件（用户决定 1、2）

1. **不留**：删除两个来源不明的未跟踪文件（`docs/session-harvester-功能大白话与进化价值.md/.html`）。
2. **净化引号**（判据：**对下游有用**）——有用，因为净化前**默认引文门对这条链是空转的**（正文 0 条 `「」`=0 未命中，只有记得加 `--quotes-ascii` 才真核，等于给下一个消费者留了个静默洞）。做法：先把 9 处以引号承载的**自造术语/格式记号**改成加粗或行内码，再把 72 对**数据引文**整体换成 `「」`（成对整体扫描，因为有一处引用跨行）。验证：
   - `chain-validate`：52 节点 / errors 0 / warnings 0
   - `chain-audit`（**不带** `--quotes-ascii`）：`「」` 引文 **62 条全部逐字命中**、未命中 0、锚点告警 0、覆盖 39/55（真缺口 8）
   - 备份：`docs/reports/chain-叙事节奏.md.bak-pre-quote-normalize`（一次性产物，gitignore 目录）
