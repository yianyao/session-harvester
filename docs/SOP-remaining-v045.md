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

### A 组 · 一行级修复（先做，半小时量级）**——✅ 已完成（v0.45 第三批）**

| # | 事项 | 做法 | 验收（会红的断言） | 状态 |
|---|---|---|---|---|
| A1 | 版本号与提交对账 | `harvester/__init__.py:8` → **`0.45.0`**；`mcpserver.SERVER_INFO.version`、`apiserve.api_meta.package_version` 都取自它，改一处即全通。**本仓库无 `CHANGELOG`**（已 glob 确认），故单一来源取 `docs/HANDOFF-v0.33-next.md` §0 快照里 `→ **vX.Y**` 那行 | `tests/test_v45_hygiene.py`：从 HANDOFF §0 正则抽 `→ **vX.Y**` 与 `__version__` 前两位比对；另断言三段式且 `!= "0.18.1"` | ✅ |
| A2 | 删 `topicexport.py:46` 的循环内 `import re` | 已删（`:23` 模块级已有） | 套件绿 + `deadcode-scan` 两根 0/0/0 | ✅ |
| A3 | 清 f-string 无占位符 | **真违规 5 处**（`agent_suggest.py:249`、`cli.py:1163/1165`、`export_analysis.py:250`、`topicexport.py:162`）已去 `f` 前缀；新增 AST 静态门 | `test_v45_hygiene.py`：AST 扫 `harvester/` 全树无违规 + **两条元测试**（合成源码必须报出、`{x:04d}` 的格式说明符不算违规） | ✅ |
| A4 | 关闭或复现 `dsh.py` unused import 条 | 已用 `deadcode-scan` 实测 0 条 → **关闭**；若换 pyflakes 复现再补 `# noqa` | 无需断言（记录在案） | ✅ |

**A 组实测（2026-10-10）**：`tests/test_v45_hygiene.py` **5 例绿**；后端套件 **645 例全绿**；死代码两根 0/0/0。
**过程教训（记一笔）**：AST 检查器首版把 `f"{x:04d}"` 的**格式说明符**也算成"无占位符 f-string"（Python 3.12+ PEP 701 把 format_spec 也建成 `JoinedStr`）→ 30 条"违规"里 **26 条是假阳性**，真违规只有 5 条。**先怀疑检查器**：加 `test_checker_ignores_format_spec` 钉住。

### B 组 · 主线：Agent 消费面（河 1 的最后一段）**——✅ 已完成（v0.45 第四批）**

| # | 事项 | 做法 | 验收 | 状态 |
|---|---|---|---|---|
| B1 | MCP 扩六个只读工具 | `mcpserver.py` 增 `topic_list`/`topic_export`/`chain_read`/`suggest_list`/`cards_list`/`artifacts_list`（**10 工具**）。复用现状：`topic_list`→`apiserve.api_topics`；`chain_read`→`apiserve.api_topic_chain`；`topic_export`→`topicexport.topic_bundle`+`render_topic_json`；`suggest_list`→`suggestmeta.load_statuses`。**新写的小件**：`artifacts.list_artifacts`（元数据＋体量，**不含正文**）、`cards.list_cards`（frontmatter 摘要，不校验）。CLI `mcp-serve` 增 `--topics-meta/--chain-root/--cards-root/--artifacts-meta/--suggestions-meta` | `tests/test_v45_mcp_tools.py`（12 例）+ `tests/test_v45_mcp_chain_tools.py`（4 例，依赖 PyYAML 故单列，见 H9）；**真库冒烟**：14 主题 / 200 产物 / 台账 `{adopted:7, rejected:1}` / chain 52 节点 | ✅ |
| B2 | MCP ↔ 后端能力漂移门 | **改用 AST 从 `apiserve.py` 源码抽 `/api/*` 字面量**，而非引入 `ROUTES` 表（不重排 30+ 端点的 `if/elif`，additive 优先）；`TOOL_SOURCES` 声明每个工具的来源（`local` 或端点） | 双向一致 + 端点真实存在 + 工具数 ≥10；**元测试覆盖三类漂移**（工具数不足／两向不一致／幽灵端点）并含"全合规必须为空"的反向对照 | ✅ |
| B3 | MCP 输出与 HTTP 同源对账 | `topic_list`/`cards_list` 的载荷与 `apiserve` 同名函数**逐字段相等**（`chain_read` 同理） | 三条 `assertEqual`；注明"保证的是**没有二次加工**，不是两份实现一致" | ✅ |
| B4 | MCP 文档与对照表 | README「检索 · 索引 · MCP」节：10 工具表（含"与 HTTP 的关系"列）+ 漂移门说明 + 接入命令带新参数；另两处"4 工具"表述已同步 | README↔CLI 漂移门 4 例仍绿 | ✅ |

**B 组实测（2026-10-10）**：新增 16 例测试；后端套件 **661 例全绿**；无 PyYAML 门禁 **`Ran 620 / errors=61 / 0 failures`**（+18 例、+1 error——新 chain 工具测试按 H9 在缺 PyYAML 时**显式 error**，不是静默跳过）；死代码两根 0/0/0（**扫描当场抓到** `mcpserver.py` 我多写的 `import contextmanager`，已删）。
**顺带修掉一个老缺陷**：`cards.py` 的跳过判据写成 `p.name.upper() in ("INDEX.md", "README.md")`——大写比混合大小写**永不相等**，于是 `README.md`/`INDEX.md` 一直被当成卡片校验；`list_cards` 与 `validate_cards` 现共用 `_SKIP_NAMES = ("INDEX.MD", "README.MD")`，并由 MCP 测试钉住（"README 不算卡片"）。

### C 组 · 质量欠账（评审 P2/P3 剩余 + 本项目自留）

| # | 事项 | 结果 | 状态 |
|---|---|---|---|
| C1 | `keywords_meta.db` GC | 已做：`kwstats.prune_runs` + 独立子命令 **`keywords-gc --keep-runs N [--vacuum]`**（另在 `keywords` 上加 `--keep-runs/--vacuum` 顺手回收）。**真库实测 40.2 MB → 18.4 MB**，但**删 run 只删了 3 条记录、0 条统计行**——22 MB 是**空闲页**（旧 schema 迁移 `DROP TABLE` + `INSERT OR REPLACE` 留下的碎片），**只有 VACUUM 能回收**。这条口径改了我们对"库在膨胀"的归因：不全是新数据 | ✅ |
| C2 | 建议台账口径 | 已做：`agent_suggest.coverage_report/render_coverage` + `suggest-agents --coverage [--coverage-out f]`。**真库实测很有价值**：建议 8 条、台账 8 条**看着对得上**，实际 **已裁决 6 / 待裁决 2 / 台账陈旧 2**——"数字相等"纯属巧合。裁决本身仍由人做（工具只对账、不代改） | ✅ |
| C5 | `regress` 的"故意破坏→变红"补证 | 已做：`docs/reports/mutation-check-regress.py`（一次性）——基线 **57 断言 / 0 失败**；三处变异分别落在三层都**变红**：① 分诊判定计数 +1 → 1 条断言红；② assign 砍一条 → 14 条红；③ apply **真写少一个成员** → 5 条红。**教训**：③ 首版改的是返回值里一个**没有任何断言读**的计数字段，结果"仍绿"——那不是断言无效，是**变异瞄错了目标**（先怀疑夹具） | ✅ |
| C3 | DSH schema 指纹 | **未做（下一轮）**：需先定"对 DSH transcript 的哪些稳定字段做指纹"，再让 `detect` 在不匹配时**显式降级**（不许静默半解析）。`apiserve._schema_fingerprint()` 是本项目 `EXPECTED_SCHEMA` 的 sha256，**不是**这件东西 | ⏳ |
| C4 | 1 例 flake 定位 | **未做**：需自然复现（沙箱首跑那次）；已排除"并发期间假红"（v0.43 归因）。复现时留 `-v` | ⏳ |

**C 组实测（2026-10-10）**：新增 10 例测试（`test_v45_keywords_gc.py` 6 + `test_v45_suggest_coverage.py` 4）；后端 **671 例全绿**；无 PyYAML 门禁 **`Ran 630 / errors=61 / 0 failures`**；死代码两根 0/0/0。
**过程中被自己的门抓住一次**：新子命令 `keywords-gc` **忘了写进 README** → `test_v44_doc_cmds` 当场变红（这正是 v0.44 那道门存在的意义：文档与 CLI 双向比对，人工通读漏得掉，机器漏不掉）。

### D 组 · 待用户裁决（产品口径，非技术欠账）

| # | 事项 | 用户裁决（2026-10-10） | 影响 |
|---|---|---|---|
| D1 | 已发布 chain 是否随主题重生成 | **暂缓** | 现状：主题 411 成员 / chain 55 成员快照按"历史档案"看待，不做自动重生成 |
| D2 | chain 元结论回写全局记忆 | **暂缓** | 我此前的建议（不做）继续有效，不写入 `~/.dsh/AGENTS.md` |
| D3 | 定期蒸馏节奏 | **做**：采纳"定期蒸馏" | 需在下一轮落成可执行 SOP（频率、触发条件、验收）；它属**产能节奏**而非工具能力，不进 `harvester/`，写在 SOP 里即可 |

---

## §4 本轮已顺手做掉的两件（用户决定 1、2）

1. **不留**：删除两个来源不明的未跟踪文件（`docs/session-harvester-功能大白话与进化价值.md/.html`）。
2. **净化引号**（判据：**对下游有用**）——有用，因为净化前**默认引文门对这条链是空转的**（正文 0 条 `「」`=0 未命中，只有记得加 `--quotes-ascii` 才真核，等于给下一个消费者留了个静默洞）。做法：先把 9 处以引号承载的**自造术语/格式记号**改成加粗或行内码，再把 72 对**数据引文**整体换成 `「」`（成对整体扫描，因为有一处引用跨行）。验证：
   - `chain-validate`：52 节点 / errors 0 / warnings 0
   - `chain-audit`（**不带** `--quotes-ascii`）：`「」` 引文 **62 条全部逐字命中**、未命中 0、锚点告警 0、覆盖 39/55（真缺口 8）
   - 备份：`docs/reports/chain-叙事节奏.md.bak-pre-quote-normalize`（一次性产物，gitignore 目录）
