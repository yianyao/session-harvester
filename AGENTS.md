# AGENTS.md — agent 工作规则

## 0. 开工第一件事（**先读这一段，不要直接开工**）

> 背景：`WorkBuddy\<会话时间戳>\` 是**按会话隔离**的工作区，新会话看不到上一个会话的
> 目录。因此**唯一可靠的状态入口是本文件这一段**（它会被自动注入），以及仓库内
> **已提交**的交接文档。详见 `$DSH_HOME/AGENTS.md` §六。

- **当前状态快照**（每次收尾必须更新本段；最后更新 2026-10-13 会话 6）：

  | 项 | 值 |
  |---|---|
  | 后端 head | v0.33 → **v0.49 批次**：v0.46 链 `786f5bb`…`fa28fbc`；v0.47 主提交 `3ff0252` + `03cf0c8`；v0.48 交接链 `4df962b`/`38c7712`/`52af86d`；**会话 6 新增**：会话抑制（`harvester/suppress.py` + 四挂点 + CLI + 32 例测试）。**版本号仍 0.46.0**（升版必须同改 `docs/HANDOFF-v0.33-next.md` §0 的 `→ **v0.46**`，否则 `test_v45_hygiene` 红） |
  | 后端测试基线 | **817 例全绿**（venv，见下「基线自查」；785 → 817，+32 = 会话抑制测试）——**数字的权威在 README 顶部「测试基线」一节**，由 `tests/test_v48_test_baseline.py` **真跑对账**钉住 |
  | 无 PyYAML 门禁 | **`GATE ran=776 failures=0 errors=61 skipped=2`**（须 0 failures；61 errors 是缺 PyYAML 的设计行为；**skipped=2 = 基线门的两条实测用例在嵌套层转 skip**，`skip ≠ 通过`）。脚本：`& $venv -X utf8 scripts\gate_no_yaml.py`；flake 复现 `scripts\flake_hunt.py`（热/冷）、旧树 `docs/reports/probe-history-flake.py` |
  | 死代码门的并发保护 | v0.45：不可信发现单列 + CLI 回 3 + 套件门可区分 skip。**会话 6 又踩一次**：并发编辑 sibling 仓时门禁报 `errors=62`、deadcode 报「1 条不可信（`harvester/x.py`，其实是测试夹具）」——**串行重跑分别是 61 与 0/0/0**（连带教训：`Select-Object -First` 会打断上游进程并伪造 exit 1） |
  | 跨仓漂移门（**新，务必知道**） | **后端新增 CLI 子命令 → view 的 `test_v35_actions.test_no_drift_against_real_parser` 会红，这是正确行为**（会话 6 加 `suppress *` 时实测红了）。修法固定：`python -m actions.commandgen` 重生成 + 归并 `commandgen.COMMAND_CARD`/`TIER_OVERRIDE` + `registrygen` 重生成。**别为了绿去改门** |
  | 跨测试泄漏门 | `tests/test_v45_no_monkeypatch_leak.py`（AST 查模块级打桩无还原）；权威仍是动态的 `flake_hunt.py` |
  | README↔CLI 漂移 | `tests/test_v44_doc_cmds.py` 双向比对（门 4 例绿）；**会话 6 `suppress` 子命令已写入 README** |
  | 死代码扫描 | `python -m harvester deadcode-scan`；`harvester/` 与 `tests/` **0/0/0**（会话 6 串行复测 exit=0） |
  | 前端 | 仓库 `..\harvester-view`，会话 6 开工时 head `c36e72e`，**164 例全绿 + 5 个 node 门全绿**（render_smoke / render_cmd / render_home / boot_sequence / **render_work**）。**v0.49 起**：工作台上**每个数字（含闭环健康每一段）都点得进「处理单」**（`/api/workitems?target=`，数字与明细**同源**、`reconcile_ok=false` 会响亮报警）；**组件底色一律走主题变量**、**不许用未定义的 CSS 变量**（两道由 `tests/test_v40_theme_contrast.py` 真算 WCAG 对比度钉住）；**选中 chip 按用户裁决「不改深色，改其他的」给了专用底** `--chip-on-bg`（白字浅色 5.17:1 / 深色 6.70:1，**`--accent` 未动**）；**会话页已接抑制**（详情「删除（抑制）」+「已抑制」全量清单 + 每条「恢复」） |
  | 会话抑制（v0.49 后端线） | `harvester/suppress.py` + 四挂点（`exporter.export(skip_sids=)` / **`exporter.prune_suppressed`** / `indexing.index_exports(skip_sids=)` / `sync.run_sync`+`run_update` 接线并把抑制 sid 并入 `drop_sids`）；CLI `harvester suppress list\|add\|remove\|prune-exports`（**默认 dry-run，`--apply` 才写**）。抑制表建在 `topics_meta.db`，与 `sessions_noise` **分开建表**。**口径**：删的是**仓内副本**，**不删采集源**；`remove` 不恢复数据，能不能恢复**只取决于语料是否还在**，**不设时限**（用户 ⑧ 裁决；用户另裁决**不加**"语料是否还在"的探测）。**view 侧已完成**（B 档 11：可逆 8 / 破坏性 3）。**未做**：真库首次 `--apply`（须先备份 410 MB 库 + `quick_check`） |
  | 主题注册表 | **13 个主题**（成员总数 **997**）：小说 `010`=411、素材库 `005`=199、采集 `004`=191、心理 `003`=69、本机工具环境 `004-08`=43、SKILL `001`=10；噪声登记 **163 条**（旧称「零散」，本项目自定义待淘汰；官方术语 noise——聚类标准词，库表 `sessions_noise`，见 `docs/TERMINOLOGY.md` §3-§4）。**已裁决：空主题 `tp-20261008-006` 已删（14 → 13）**，其悬空对账行（旧称「人账」；官方术语 reconciliation/对账）同步清掉 |
  | 分诊池 | **807 条**（substantive 503 / `deep_unassigned` 295 / `deep_topic_hint` 9）——**本批只跑 dry-run、未落库、未归位**；v0.42 口径是 794（substantive 502 + deep 292） |
  | 已发布 chain | 3 条；叙事节奏链 52 节点 / 有锚点成员 39/55（v0.45 补做，本批未动） |
  | 库规模（2026-10-12） | **1981 会话 / 74364 消息 / 44969 步骤 / 381 错误**（65 会话有错）；`source='dsh'` **36**；**库体积 410.3 MB**（VACUUM 前 443.0） |
  | 采集耗时 | `update` **315.7 s（v0.45 全量重建）→ 44.6 s（P0-1 增量索引）→ 16.8 s（复用扫描）**；`sync` 全量 **304.7 s**（本批实测） |
  | 库龄可见性 | （官方术语 = 数据新鲜度 data freshness/staleness；旧称「库龄可见性」本项目自定义待淘汰）`index_meta` 水位 + `stale`（True/False/**None=未判定**）；报告头与 `update` 都打；**基准是"上次索引看到的语料时刻"，不是库文件 mtime**（H104 有纠偏依据） |
  | 自进化闭环 | `suggestions_meta` 的 `adopted_at`/`note`（H31 key + `pattern:` 桥 + `topic:` 命名空间）；**判据已改为两指标**（未解决率与复发率都要不升才算改善，H110）；`harvest-eval` 三值判定 + 样本门槛；**工具绝不自动改 AGENTS.md**。真机：7 条 adopted **全部 7/7 可评估**（B3 补上最后一条 `pattern:`）→ 不下结论 6 / 无变化 1 |
  | keywords 库 | `keywords_meta.db` **18.4 MB / 1 个 run / 435583 行**；**`sync` 收尾自动 GC（默认只留最近 1 次 run + VACUUM）**，见 H109 |
  | 剩余事项 SOP | **`docs/SOP-remaining-v045.md`**（§5 定期蒸馏 SOP；**§6 = v0.46 待办已全部完成或裁决**；§7 不做清单） |
  | 沙箱策略 | **每次会话都可能不同** → 跨仓库任务**先探一次写权限**再决定做不做（本批只需仓内写权限） |
  | 交接文档（正文） | **`docs/HANDOFF-v0.49-next.md`（最新冷启动交接，先读它）**；`docs/HANDOFF-v0.48-next.md` / `v0.47` / `v0.46` 为前几批留档；`docs/HANDOFF-v0.33-next.md` §0 保留为版本号来源（`test_v45_hygiene` 读它）；事实台账 H1–**H116** 在 `docs/HANDOFF-v0.22-next.md` §2 |
  | MCP 工具 | **10 个**（会话面 4 + 进化数据面 6）；漂移门 `tests/test_v45_mcp_tools.py` |
  | 未推送 | 现场取：`git -C <仓> rev-list --count origin/main..HEAD`（会话 6 期间网络两次失败 → **有网先 push 两仓**） |

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
  14. **下一件事（按序，详见 `docs/HANDOFF-v0.47-next.md` §3）**：① **有网先 `git push origin main`**；② 分诊归位（先 `--triage-json` → 人工裁决 → `--plan-seed` → dry-run → `--apply`）；③ 卡片蒸馏（SOP §5 第 5 步）；④ 可选的 view 显示库龄（需先立项）。

  15. ✅ **会话 6（2026-10-13，详见 `docs/HANDOFF-v0.49-next.md`）**：**视图线**——用户实测反馈驱动：**处理单 v0.49**（`actions/workitems.py` 唯一真源：一份明细派生若干具名指标，`mode=items`→`len(items)`、`mode=sum`→`sum(item.metrics[sum_field])`；`/api/overview` 与 `GET /api/workitems?target=` 同源；实测真库 **17 target / 25 指标全部对账通过**）+ **队列明细逐行指向 target**（两个聚合数显式标 `aggregate`、不派活）+ **配色修复**（根因：dark 媒体查询下 `--ink` 翻浅色字而 11 处组件底色写死浅色 → 对比度 **1.1:1**；另有 **13 处 `var(--sub)` 而该变量从未定义**）+ **闭环健康 11 段也可点、有建议**；view 测试 105 → **164**，新增 4 道门 + **变异复核 6/6 全部被抓**。**后端线**——**会话删除（抑制）**按用户裁决落地：`harvester/suppress.py` + **四挂点齐做**（导出拦 / **清旧 export 副本** / 索引兜底 / sync+update 接线并入 `drop_sids`）+ CLI `suppress list|add|remove|prune-exports`（默认 dry-run）+ **32 例测试**（含核心验收「抑制 → sync → 不在」与「文件真被删掉」）；测试 785 → **817**、gate 744 → **776**。**续批（同日晚）**——用户三条裁决：**① 不改深色、改其他**（chip 给专用底 `--chip-on-bg`，白字 5.17/6.70:1，`--accent` 未动）；**② 不给抑制加语料探测**；**③ 继续** → **抑制的 view 侧完成**（`session_suppress` destructive + `session_unsuppress` reversible，**B 档 9 → 11**，`GET /api/suppressed` 只读不建表，会话页「删除（抑制）」+「已抑制」全量清单 + 每条「恢复」，口径四句逐字钉住；view 145 → **164 例**）。**未做**：**真库首次 `suppress --apply`**（须先备份 + `quick_check`，**下一步第一件事**）、P4 进化层。

- **基线自查命令**（先设沙箱补丁，再跑，否则会误判"项目坏了"）：
  `$env:PYTHONPATH = "<repo>\scripts\sandbox"` 后
  `& $venv -m unittest discover -s tests` → 预期 **817 例 OK**；无 PyYAML 门禁用
  `& $venv -X utf8 scripts\gate_no_yaml.py`（须 **0 failures**，预期 `ran=776 errors=61 skipped=2`）。
  **数字的权威是 README 顶部「测试基线」一节**（`tests/test_v48_test_baseline.py`
  会真跑这两条并与 README 对账；改测试却忘改 README → 它红）。
  ⚠ **基线必须串行跑，且跑的时候不要并发编辑任一仓的文件**——会话 6 的两次"假红"
  （`errors=62`、deadcode 报「1 条不可信」）都是这么来的，串行重跑即恢复（61 与 0/0/0）。
  ⚠ **`$LASTEXITCODE` 会被最后一条命令覆盖**，`Select-Object -First N` 还会打断
  上游进程并伪造 exit 1 —— **看输出，别只看退出码**（会话 6 误读多次）。
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
  - **本仓已知缺口（2026-10-13 已查证收账）**：错误分类 `env`/`tool_interface`/
    `context`/`unclassified` **是本项目自定分类、不是行业术语**——公开可查的官方
    惯例是 OpenTelemetry SemConv 的 `error.type`（低基数、well-known values +
    custom value 均允许，**custom 值必须文档化**）。结论：保留自定分类（全库数据
    已按此落库，改名成本大于收益），其文档化载体 = `docs/TERMINOLOGY.md` §2 +
    `static/glossary.json`（UI 侧已逐条标「本项目自定义」）。
  - **权威术语册 = `docs/TERMINOLOGY.md`**（2026-10-13 起，v1.1 立场：**官方术语
    优先，尽量不自造词**）：能找到官方/领域通用术语的一律用官方名（如 数字皆门→
    下钻 drill-down、闭环健康三根环→闭环健康面板 closed-loop feedback、动词卡→
    命令分组、库龄可见性→数据新鲜度 staleness、人账→对账 reconciliation、
    零散→噪声登记 noise）；实在无对照才自造，且必须标「本项目自定义」并登记
    术语册 §3（视为待淘汰别名，替换计划见 §4）。自造词首次出现必须自带解释。
    判据：**没参与本项目的人，按公开资料能查到这个词吗？**
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
