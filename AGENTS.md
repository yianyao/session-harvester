# AGENTS.md — agent 工作规则

## 0. 开工第一件事（**先读这一段，不要直接开工**）

> 背景：`WorkBuddy\<会话时间戳>\` 是**按会话隔离**的工作区，新会话看不到上一个会话的
> 目录。因此**唯一可靠的状态入口是本文件这一段**（它会被自动注入），以及仓库内
> **已提交**的交接文档。详见 `$DSH_HOME/AGENTS.md` §六。

- **当前状态快照**（每次收尾必须更新本段；最后更新 2026-10-10）：

  | 项 | 值 |
  |---|---|
  | 后端 head | v0.33 → **v0.45**，**收尾提交见 `git log -1`** |
  | 后端测试基线 | **680 例全绿**（venv，见下「基线自查」；须带沙箱补丁，见交接 H53） |
  | 无 PyYAML 门禁 | **`GATE ran=639 failures=0 errors=61`**（须 0 failures）。已固化成可重跑脚本：`& $venv -X utf8 scripts\gate_no_yaml.py`；flake 复现用 `& $venv -X utf8 scripts\flake_hunt.py --runs N` |
  | 跨测试泄漏门 | **`tests/test_v45_no_monkeypatch_leak.py`**：AST 查"给**模块级**对象打桩且文件里无还原手段"（`addCleanup`/`patch`/`try:finally`/二次赋值）；**权威仍是动态的 `flake_hunt.py`**（文件级还原证据区分不出是哪一处被还原，弱点写在测试 docstring 里） |
  | README↔CLI 漂移 | 已**钉进套件**（`tests/test_v44_doc_cmds.py`，双向：漏文档化 / 幽灵命令；含抽取 sanity 与"比对本身会红"的元测试）。v0.44 实测 37 个子命令全有提及、0 条幽灵命令 |
  | 死代码扫描 | `python -m harvester deadcode-scan`；**`harvester/` 与 `tests/` 都须 0/0/0**（套件里 `test_harvester_and_tests_have_no_dead_code` 已把两个根都钉住；**行内写 `# noqa` 即视为有意保留**）。根名支持**兄弟目录**（`harvester-view` 会被真找到），**找不到的根在报告里显式列出**——不许静默跳过还宣称覆盖 |
  | 前端 | 仓库 `..\harvester-view`，head `c1c2c06`（集成门在 tests/；末尾一次 cleanup 删了它的死 import），**25 例全绿**，已 push |
  | 主题注册表 | **14 个主题**（成员总数 **997**）：小说 `010`=**411**、素材库 `005`=**199**、采集 `004`=**191**、心理 `003`=**69**、本机工具环境 `004-08`=43、SKILL `001`=**10**；零散登记 **163 条**（v0.42 用户裁决舍弃 9 条后） |
  | 分诊池 | **794 条** = substantive **502** + `deep_unassigned` **292**（`--triage-deep`）；**noise_high / noise_maybe / deep_topic_hint 均已归零**（v0.42 舍弃 9 条进零散；深会话两类永不参与零散判定的口径不变） |
  | 已发布 chain | 3 条；**叙事节奏链 v0.45 补做 + 引号已净化**：52 节点、有锚点成员 **39/55**、8 个标题级代表逐条标注；引号按规范统一（术语加粗、数据引文 `「」`）后**默认引文门真实生效**（`chain-audit` 不带 `--quotes-ascii`：`「」` 62 条全部逐字命中、锚点告警 0） |
  | 剩余事项 SOP | **`docs/SOP-remaining-v045.md`**（A 一行级 / B Agent 消费面 / C 质量欠账 / D 待裁决；含对第三方检查文档的逐条实测核对——其中 `regress` 那条**已过期**） |
  | 沙箱策略 | **每次会话都可能不同** → 跨仓库任务**先探一次写权限**再决定做不做 |
  | 交接文档（正文） | **`docs/HANDOFF-v0.33-next.md`（最新，先读它）**；备档 `~/.workbuddy/knowledge/handoffs/session-harvester-v0.33.md`；事实台账 H1–**H98** 在 `docs/HANDOFF-v0.22-next.md` §2；待办细目在 `docs/HANDOFF-v0.24-next.md` §7.4 |
  | MCP 工具 | **10 个**（会话面 4 + 进化数据面 6：`topic_list`/`topic_export`/`chain_read`/`suggest_list`/`cards_list`/`artifacts_list`）；漂移门 `tests/test_v45_mcp_tools.py` 从 `apiserve.py` 源码 AST 抽 `/api/*` 双向核对 |

- **下一件事（按序，详见 `docs/HANDOFF-v0.33-next.md` §0）**：
  1. ✅ **已完成（v0.39–v0.42）**：tests/ 死 import 清 + 死代码门扩两根；`CHAIN-AUTHOR-SPEC.md` 入库；65 条深会话复核落库（63/1/1）并修掉"机械命中跨进程不确定"（严重）；用户裁决**舍弃 9 条会话**进零散；
  2. ✅ **已完成（v0.43–v0.44）**：**V3「卡片校验与主题注册表打通」**（`cards validate --topics-meta`，只读核对；24 例新测试）、**`regress` 端到端回归语料**（7 步 / 57 条断言 / 退出码三分；**注**：`regress` 那个子代理被中断、**没交回"故意破坏→变红"证据**）、**README↔CLI 漂移门**（37/37 有提及、0 幽灵命令）；
  3. ✅ **已完成（v0.45）**：**叙事节奏 chain 证据覆盖**（长期挂账最后一项）——`chain-audit` 扩成三门（证据覆盖 / 正文↔frontmatter 双向对账 / 引文门空转显式化 + `--quotes-ascii`）；chain 30→52 节点、有锚点成员 27/55→**39/55**、8 个标题级代表逐条标注，另改正两处锚点归属（H92）；
  4. **仅剩 1 项"没做成"**：沙箱首跑那 **1 例 flake 仍未定位**（并发期间的假红 v0.43 已归因）；复现时留 `-v` 定位，别当"已知 flaky"糊过去。chain 元结论回写**建议不做**（领域内容，放代理工作记忆是噪声）；
  5. ✅ **引号净化已做（v0.45 第二批）**：叙事节奏链的 72 对数据引文改成 `「」`、9 处术语改加粗（原本默认引文门对该链**空转**，现在是真实门：62 条全部逐字命中）；
  6. ✅ **已完成（v0.45 A/B）**：A 一行级（版本号 `0.45.0` + 卫生门 `test_v45_hygiene.py`、`topicexport.py:46` import 遮蔽、5 处 f-string）；B **MCP 4→10 工具 + 漂移门**（`test_v45_mcp_tools.py` 从 `apiserve.py` 源码 AST 双向核对，含元测试）；
  7. ✅ **C1/C2/C5 已完成（v0.45 第五批，H96）**：C1 `keywords-gc --keep-runs/--vacuum`（真库 40.2→**18.4 MB**；22 MB 是空闲页，只有 VACUUM 回收）；C2 `suggest-agents --coverage`（真库：建议 8/台账 8 看着相等，实际**已裁决 6、待裁决 2、陈旧 2**）；C5 `regress` 变异补证（3 处破坏全部变红，补上 H90 的欠证）；
  8. ✅ **C3 已完成（v0.45 第六批，H97）**：DSH schema 守卫（声明版本 + 消费字段形态双判据；不匹配 → `detect` STUB / `load_session` **lossy 空消息**，不半解析）；真机 **33 会话全部通过**、指纹 `d75532e18dc4`；顺带修掉 `_files()` 写死 `v4` 导致"上游改名伪装成 MISSING"的诊断缺陷；
  9. **D3 定期蒸馏 SOP 已落地（SOP §5）**：三触发 + 八步 + 每步记数字；**不含 chain 再生成**（D1 暂缓）；D2 暂缓；
  10. ✅ **C4 半闭环（v0.45 第七批，H98）**：`scripts/flake_hunt.py` 首跑**抓到并修掉一例真 flake**（`test_v06` 4/5 轮红）——根因是**本轮 C3 新测试打桩 `dshmod.zstd_decompress` 未还原**（单跑看不见、in-process 重复才暴露）；已修 + 复跑 5 轮全绿；并把这一类做成门 `tests/test_v45_no_monkeypatch_leak.py`。**历史那例（沙箱首跑）仍未复现**，两者不是同一件事；
  11. **并发教训（v0.43）**：同一工作区并行子代理**会互相同改 `cli.py`/`README.md`**——下次派活要**按文件切分**或串行。

- **基线自查命令**（先设沙箱补丁，再跑，否则会误判"项目坏了"）：
  `$env:PYTHONPATH = "<repo>\scripts\sandbox"` 后
  `& $venv -m unittest discover -s tests` → 预期 **680 例 OK**；无 PyYAML 门禁用
  `& $venv -X utf8 scripts\gate_no_yaml.py`（须 **0 failures**）。
  （补丁 = `scripts/sandbox/sitecustomize.py`：沙箱下 `os.mkdir(0o700)` 建出的目录
  连本进程都写不进 → `tempfile` 全崩。**普通机器上不需要**，见 README「沙箱环境适配」。）

- **收尾纪律**：交接正文写进仓库并**提交**（必要时 push）+ 更新本段快照 +
  同轮 `read` 读回验证后再声称"已生成"（`$DSH_HOME/AGENTS.md` §六 17/18 条）。

## 开工前上下文自检（强制）

1. **触发条件**：任务预计修改 ≥4 个文件，或需要多轮"修改→测试→验证"循环（如跨端点/跨仓库的功能改造）。
2. **自检动作**：实施前评估当前会话剩余上下文空间（含已消耗轮次、摘要压缩次数、剩余预算）。
3. **判定充足** → 直接开工，按常规流程实施、测试、提交。
4. **判定不足** → 立即停止实施，产出交接文档后结束本轮。交接文档最低要求：
   - 已核实的事实：文件路径、行号、关键口径、实测数字（必须标注测量时点与口径定义）；
   - 方案设计与验收标准（可执行、可判定通过/失败）；
   - 已完成项 / 未完成项 / 明确不做项清单；
   - 已知坑与规避方法（含历史踩坑记录的引用）。
   - 存放：`docs/HANDOFF-<主题>.md`（跨项目时放工作区根目录）。
5. 交接文档是**供 agent 消费的参考件**：结构稳定可解析、术语精确、口径显式写数值，不加口语化解释。

## 本项目固定铁律（执行时同样适用）

- `harvester.db` 只读：所有分析/产物走只读连接（api-serve 已有 authorizer 模式可复用）。
- API 变更只增不删不改语义（additive）：既有字段一个不少，测试对账 + monkeypatch 守卫。
- 统计口径变更必须同步更新测试对账数字与 README 端点说明。
- **能力一律做进工具本体，不许只做在 `docs/reports/`**（2026-10-10 用户明确要求）：
  凡"以后还会再用一次"的东西（校验门、审计、去重、派生素材、导出），必须落在
  `harvester/`（或 view 仓库）里，配 CLI 入口 + 测试 + README 说明；
  `docs/reports/` 只放**一次性产物**（本次执行的报告、被 gitignore 的中间文件）。
  判定口径：这个动作**下一次采集 / 下一次起草还会不会重跑**？会 → 进工具。
  反例与修正：引文逐字门、锚点语义门曾是一次性脚本，已提升为 `chain-audit`
  （`harvester/chainaudit.py` + CLI + 7 例测试）。
