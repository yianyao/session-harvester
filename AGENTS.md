# AGENTS.md — agent 工作规则

## 0. 开工第一件事（**先读这一段，不要直接开工**）

> 背景：`WorkBuddy\<会话时间戳>\` 是**按会话隔离**的工作区，新会话看不到上一个会话的
> 目录。因此**唯一可靠的状态入口是本文件这一段**（它会被自动注入），以及仓库内
> **已提交**的交接文档。详见 `$DSH_HOME/AGENTS.md` §六。

- **当前状态快照**（每次收尾必须更新本段；最后更新 2026-10-12）：

  | 项 | 值 |
  |---|---|
  | 后端 head | v0.33 → **v0.47 批次**：v0.46 链 `786f5bb`…`fa28fbc`，**本批主提交 `3ff0252`**（+1100 −53）+ `03cf0c8`（交接口径更正）；**版本号仍 0.46.0**（升版必须同改 `docs/HANDOFF-v0.33-next.md` §0 的 `→ **v0.46**`，否则 `test_v45_hygiene` 红） |
  | 后端测试基线 | **785 例全绿**（venv，见下「基线自查」；761 → 785，+24：B8 基线门 12 / B10 keywords GC 8 / A1 判据钉子 6 等）——**数字的权威在 README 顶部「测试基线」一节**，由 `tests/test_v48_test_baseline.py` **真跑对账**钉住 |
  | 无 PyYAML 门禁 | **`GATE ran=744 failures=0 errors=61 skipped=2`**（须 0 failures；61 errors 是缺 PyYAML 的设计行为；**skipped=2 = 基线门的两条实测用例在嵌套层转 skip**，`skip ≠ 通过`）。脚本：`& $venv -X utf8 scripts\gate_no_yaml.py`；flake 复现 `scripts\flake_hunt.py`（热/冷）、旧树 `docs/reports/probe-history-flake.py` |
  | 死代码门的并发保护 | v0.45：不可信发现单列 + CLI 回 3 + 套件门可区分 skip。**本批再次踩到**：改了 `cli.py`/`sync.py` 之后仍在跑的全量套件结果作废，必须重跑 |
  | 跨测试泄漏门 | `tests/test_v45_no_monkeypatch_leak.py`（AST 查模块级打桩无还原）；权威仍是动态的 `flake_hunt.py` |
  | README↔CLI 漂移 | `tests/test_v44_doc_cmds.py` 双向比对（门 4 例绿）；**本批 `sync` 新增 `--keywords-keep-runs/--no-prune-keywords`（参数级，门只查子命令名，已在 README 写明）** |
  | 死代码扫描 | `python -m harvester deadcode-scan`；`harvester/` 与 `tests/` **0/0/0**（本批复测 exit=0） |
  | 前端 | 仓库 `..\harvester-view`，head `c1c2c06`，25 例全绿，已 push。**view 不显示库龄（API 里 `stale` 恒 null，服务端不扫盘）**；B7（闭环按钮流）**用户裁决本批不立项** |
  | 主题注册表 | **13 个主题**（成员总数 **997**）：小说 `010`=411、素材库 `005`=199、采集 `004`=191、心理 `003`=69、本机工具环境 `004-08`=43、SKILL `001`=10；零散登记 **163 条**。**已裁决：空主题 `tp-20261008-006` 已删（14 → 13）**，其悬空人账行同步清掉 |
  | 分诊池 | **807 条**（substantive 503 / `deep_unassigned` 295 / `deep_topic_hint` 9）——**本批只跑 dry-run、未落库、未归位**；v0.42 口径是 794（substantive 502 + deep 292） |
  | 已发布 chain | 3 条；叙事节奏链 52 节点 / 有锚点成员 39/55（v0.45 补做，本批未动） |
  | 库规模（2026-10-12） | **1981 会话 / 74364 消息 / 44969 步骤 / 381 错误**（65 会话有错）；`source='dsh'` **36**；**库体积 410.3 MB**（VACUUM 前 443.0） |
  | 采集耗时 | `update` **315.7 s（v0.45 全量重建）→ 44.6 s（P0-1 增量索引）→ 16.8 s（复用扫描）**；`sync` 全量 **304.7 s**（本批实测） |
  | 库龄可见性 | `index_meta` 水位 + `stale`（True/False/**None=未判定**）；报告头与 `update` 都打；**基准是"上次索引看到的语料时刻"，不是库文件 mtime**（H104 有纠偏依据） |
  | 自进化闭环 | `suggestions_meta` 的 `adopted_at`/`note`（H31 key + `pattern:` 桥 + `topic:` 命名空间）；**判据已改为两指标**（未解决率与复发率都要不升才算改善，H110）；`harvest-eval` 三值判定 + 样本门槛；**工具绝不自动改 AGENTS.md**。真机：7 条 adopted **全部 7/7 可评估**（B3 补上最后一条 `pattern:`）→ 不下结论 6 / 无变化 1 |
  | keywords 库 | `keywords_meta.db` **18.4 MB / 1 个 run / 435583 行**；**`sync` 收尾自动 GC（默认只留最近 1 次 run + VACUUM）**，见 H109 |
  | 剩余事项 SOP | **`docs/SOP-remaining-v045.md`**（§5 定期蒸馏 SOP；**§6 = v0.46 待办已全部完成或裁决**；§7 不做清单） |
  | 沙箱策略 | **每次会话都可能不同** → 跨仓库任务**先探一次写权限**再决定做不做（本批只需仓内写权限） |
  | 交接文档（正文） | **`docs/HANDOFF-v0.47-next.md`（最新，先读它）**；`docs/HANDOFF-v0.46-next.md` 为上一批留档；`docs/HANDOFF-v0.33-next.md` §0 保留为版本号来源（`test_v45_hygiene` 读它）；事实台账 H1–**H116** 在 `docs/HANDOFF-v0.22-next.md` §2 |
  | MCP 工具 | **10 个**（会话面 4 + 进化数据面 6）；漂移门 `tests/test_v45_mcp_tools.py` |
  | 未推送 | `origin/main` 落后本地 **10 个提交**（沙箱无网络，两次 `git push` 均 `Failed to connect to github.com:443`）——**有网先 push** |

- **下一件事（按序，详见 `docs/HANDOFF-v0.33-next.md` §0）**：
  1. ✅ **已完成（v0.39–v0.42）**：tests/ 死 import 清 + 死代码门扩两根；`CHAIN-AUTHOR-SPEC.md` 入库；65 条深会话复核落库（63/1/1）并修掉"机械命中跨进程不确定"（严重）；用户裁决**舍弃 9 条会话**进零散；
  2. ✅ **已完成（v0.43–v0.44）**：**V3「卡片校验与主题注册表打通」**（`cards validate --topics-meta`，只读核对；24 例新测试）、**`regress` 端到端回归语料**（7 步 / 57 条断言 / 退出码三分；**注**：`regress` 那个子代理被中断、**没交回"故意破坏→变红"证据**）、**README↔CLI 漂移门**（37/37 有提及、0 幽灵命令）；
  3. ✅ **已完成（v0.45）**：**叙事节奏 chain 证据覆盖**（长期挂账最后一项）——`chain-audit` 扩成三门（证据覆盖 / 正文↔frontmatter 双向对账 / 引文门空转显式化 + `--quotes-ascii`）；chain 30→52 节点、有锚点成员 27/55→**39/55**、8 个标题级代表逐条标注，另改正两处锚点归属（H92）；
  4. ✅ **已闭环（v0.45 第九批，H100）**：C4 那例 flake 的**成因类别已定位并修掉**——`deadcode-scan` 在**并发编辑窗口**内读遍源码时会读到半写/撕裂读状态而**假红**（探针实测 9～10/12 轮复现，每轮都命中被改写文件），这正是 v0.29 与 v0.38 两次"红一次、再跑全绿"的机制；v0.38 那棵树（`d9cac46`，574 例）冷跑 **13 轮全绿**排除代码缺陷。修法：不可信发现单列 + CLI 回 **3（请重跑）** + 套件门可区分 skip。**两条历史记录的共同缺口是"没记用例名"**（记录方式问题，已由 `flake_hunt.py` 补上）；chain 元结论回写**建议不做**（领域内容，放代理工作记忆是噪声）；
  5. ✅ **引号净化已做（v0.45 第二批）**：叙事节奏链的 72 对数据引文改成 `「」`、9 处术语改加粗（原本默认引文门对该链**空转**，现在是真实门：62 条全部逐字命中）；
  6. ✅ **已完成（v0.45 A/B）**：A 一行级（版本号 `0.45.0` + 卫生门 `test_v45_hygiene.py`、`topicexport.py:46` import 遮蔽、5 处 f-string）；B **MCP 4→10 工具 + 漂移门**（`test_v45_mcp_tools.py` 从 `apiserve.py` 源码 AST 双向核对，含元测试）；
  7. ✅ **C1/C2/C5 已完成（v0.45 第五批，H96）**：C1 `keywords-gc --keep-runs/--vacuum`（真库 40.2→**18.4 MB**；22 MB 是空闲页，只有 VACUUM 回收）；C2 `suggest-agents --coverage`（真库：建议 8/台账 8 看着相等，实际**已裁决 6、待裁决 2、陈旧 2**）；C5 `regress` 变异补证（3 处破坏全部变红，补上 H90 的欠证）；
  8. ✅ **C3 已完成（v0.45 第六批，H97）**：DSH schema 守卫（声明版本 + 消费字段形态双判据；不匹配 → `detect` STUB / `load_session` **lossy 空消息**，不半解析）；真机 **33 会话全部通过**、指纹 `d75532e18dc4`；顺带修掉 `_files()` 写死 `v4` 导致"上游改名伪装成 MISSING"的诊断缺陷；
  9. **D3 定期蒸馏 SOP 已落地（SOP §5）**：三触发 + 八步 + 每步记数字；**不含 chain 再生成**（D1 暂缓）；D2 暂缓；
  10. ✅ **C4 已闭环（v0.45 第七/八/九批，H98/H99/H100）**：三条轴都跑过——热跑（同进程，抓到并修掉一例：C3 新测试打桩 `dshmod.zstd_decompress` 未还原，已加门 `test_v45_no_monkeypatch_leak.py`）｜`--cold --clear-pycache`（首跑类）｜**旧版本树**（`git worktree add d9cac46`，574 例 × 13 轮全绿）。最终定位：那两次"红一次再跑全绿"是 **`deadcode-scan` 撞上并发编辑窗口**（半写/撕裂读 → 假红），机制已实测复现并**修掉**（不可信发现单列 + CLI 回 3 + 套件门可区分 skip）；
  11. **并发教训（v0.43）**：同一工作区并行子代理**会互相同改 `cli.py`/`README.md`**——下次派活要**按文件切分**或串行。
  12. ✅ **v0.46 本轮已完成（SOP §6 的 P0-1/P0-2/P1-3/P1-4/P2-5，详见 `docs/HANDOFF-v0.46-next.md`）**：**P0-1 索引侧增量**（`update` 315.7 s → 44.6 s → **16.8 s**：索引步只解析本次写出的 json + 顺带消掉 `export` 的重复扫描；`gone` 带两道安全闸；dsh 索引 34 = 语料 34）｜**P0-2 库龄可见性**（`index_meta` 水位 + `stale` 三值；**基准是"上次索引看到的语料时刻"而非库 mtime**——否则 `touch+update` 永久假 STALE；11 个实装源覆盖、报告头带 STALE）｜**P1-3 结构化 `isError`**（真机 8600 帧 100%，旧判据 212 → 202，消 10 误判）｜**P1-4 自进化闭环**（`adopted_at`/`note` + `harvest-eval` 三值判定 + 样本门槛 + **key↔`pattern:` 桥**；真机跑出 1 条「无变化 → 建议退休」）｜**P2-5 人账**（`topic:` 命名空间 + `ledger-sync` 落 14 主题裁决；coverage 陈旧分两类 + 人账总览；历史 117 候选簇不可回溯已写明）｜**P3-6** 记录不修。**下一批待办见 `docs/HANDOFF-v0.46-next.md` §3**（判据口径裁决 / 3 条待裁建议 / `tp-20261008-006` 成员 0 / 剩余 4 条规则补 `pattern:` / 可选 view 显示 STALE）。**§7 不做**：丢弃行前置过滤、解压缓存、D1/D2（仍暂缓）、第三方评审文档只当结构性判断/验收单用。

  13. ✅ **本批（v0.47 批次，2026-10-12，详见 `docs/HANDOFF-v0.47-next.md`）**：**B8 测试基线门**（README 顶部「测试基线」为唯一权威数字 + `tests/test_v48_test_baseline.py` **真跑对账**；自递归靠 `HARVESTER_BASELINE_NESTED` 守卫）｜**B10 `sync` 收尾自动 GC `keywords_meta`**（默认只留最近 1 次 run + VACUUM；`--keywords-keep-runs/--no-prune-keywords`）｜**A1 判据纳入复发率**（两指标都要不升才算改善；钉子 6 例 + `mutation-check-a1.py` 变红）｜**A2 三条建议落库**（2 adopted / 1 rejected）｜**A3 删空主题 `tp-20261008-006`**（14 → 13 主题 + 清悬空人账）｜**B3 最后 1 条规则补 `pattern:`**（覆盖面 6/7 → **7/7**）｜**B9 `regress` 补 G1/G2 数字断言**（13 行 steps 夹具 + 9 条数字断言；67 断言全绿 + `mutation-check-b9.py` 变红）｜**B1 `sync`**（304.7 s，`status=error` 384→381）｜**B6 VACUUM**（443.0 → **410.3 MB**）。**未做**：B7（view 闭环按钮流，用户裁决不立项）、分诊归位（只 dry-run，池 807）、卡片蒸馏。
  14. **下一件事（按序，详见 `docs/HANDOFF-v0.47-next.md` §3）**：① **有网先 `git push origin main`**（本地领先 10 个提交）；② 分诊归位（先 `--triage-json` → 人工裁决 → `--plan-seed` → dry-run → `--apply`）；③ 卡片蒸馏（SOP §5 第 5 步）；④ 可选的 view 显示库龄（需先立项）。

- **基线自查命令**（先设沙箱补丁，再跑，否则会误判"项目坏了"）：
  `$env:PYTHONPATH = "<repo>\scripts\sandbox"` 后
  `& $venv -m unittest discover -s tests` → 预期 **785 例 OK**；无 PyYAML 门禁用
  `& $venv -X utf8 scripts\gate_no_yaml.py`（须 **0 failures**，预期 `ran=744 errors=61 skipped=2`）。
  **数字的权威是 README 顶部「测试基线」一节**（`tests/test_v48_test_baseline.py`
  会真跑这两条并与 README 对账；改测试却忘改 README → 它红）。
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

- **术语与概念以官方规范为准**（2026-10-12 用户明确指示，全项目生效；详见
  `$DSH_HOME/AGENTS.md` §零）：
  - 起名/分类/写文档先查**官方规范**（标准组织、语言或库的官方文档、RFC、主流领域惯例），
    以它为准；**不采信"一家一姓"的个人定义**，也不因"本仓一直这么叫"就沿用。
  - 官方无明确说法时才自定义，且**必须显式标注「本项目自定义」并给一句话定义**。
  - **本仓已知缺口（待改正）**：错误分类 `env`/`tool_interface`/`context`/`unclassified`
    **是本项目自定分类、不是行业术语** —— UI 与文档必须标注清楚，并优先改用官方已有说法
    （可观测性领域对 error class 的惯例、OpenTelemetry Semantic Conventions 等）。
  - 自造词（「数字皆门」「闭环健康三根环」「动词卡」「库龄可见性」「人账」「零散」等）
    首次出现必须自带解释。判据：**没参与本项目的人，按公开资料能查到这个词吗？**
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
