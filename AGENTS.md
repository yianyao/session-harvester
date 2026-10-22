# AGENTS.md —— session-harvester 的工作约定与项目状态

> 本文件由 `@deepseek-ai/dsh-agent-instructions` 从项目根**自动注入**（任何会话工作目录落在此仓库内都生效）。
> 通用工作准则在用户全局记忆 `$DSH_HOME/AGENTS.md`，本文件只写**本项目专属**的约定、事实、坑与状态。
> 本机专属信息（Python 路径、沙箱、网络抖动…）在 `AGENTS.local.md`（不提交）。
> 本文件是**仓库级记忆**，不是交付文档；正式交接正文在 `docs/HANDOFF-v0.49-next.md`。

---

## 一、状态从哪来（本节**不记状态**）

> 本文件只写**不会随进度变化**的东西：约定、坑、口径、文档位置。
> 状态（做到哪了、下一件事、基线数字、提交号）**一律现场取**——写在这里就会变成旧地图。

| 要什么 | 去哪拿 |
|---|---|
| 现在做到哪、下一件事、待裁决 | **本仓最新的交接文档**（见 §六 第 1 条，开工第一件事就是找它） |
| 测试基线的权威数字 | `README.md` 顶部「测试基线」（有测试真跑对账钉住；改测试忘改 README → 它红） |
| 提交/推送状态 | `git -C <仓> rev-list --count origin/main..HEAD`（== 0 才算推完；**提交号一律现取，别抄文档**） |
| 视图工程进度 | `_eng\` 四件套（铁律 #1：留原地不入库） |

## 二、这是什么 / 怎么跑

会话采集 → 主题梳理 → chain 起草。两个仓库：后端 `session-harvester`（采集与语料）＋ 前端 `harvester-view`（呈现与动作）。

```powershell
# 后端（venv；需设 PYTHONPATH=<repo>\scripts\sandbox —— 沙箱补丁，普通机器上不需要）
python -m pytest tests -q          # 或按 README 顶部「测试基线」一节给的形式跑
# 无 PyYAML 门禁（须 0 failures）：已固化为可重跑脚本
& $venv -X utf8 scripts\gate_no_yaml.py
# flake 复现
& $venv scripts\flake_hunt.py --runs N          # 热跑：抓跨测试泄漏
& $venv scripts\flake_hunt.py --cold --clear-pycache   # 冷跑：抓首跑/启动类
```

**基线必须串行跑，且跑的时候不要并发编辑任一仓**——本仓两次「假红」都源于此。
`$LASTEXITCODE` 会被最后一条命令覆盖；`Select-Object -First` 会打断上游进程并伪造 exit 1。

## 三、本仓专属坑（动手前先读）

1. **真库陷阱：`messages.text` 不是原文**——它是 FTS5 的 **CJK bigram 分词列**（形如 `'丁樾 樾瘫 瘫坐…'`）；
   **原文在 `raw`**，`text == raw` 仅 325/7011 条。按 `text` 做「同一句」归组会得到**分词前缀且不报错**。
   另：`messages` **没有 `seq` 列**（在 `steps` 上），相邻轮次用 **`rowid`**。
   （探针证据：`docs/reports/probe-messages-text-vs-raw.py`）
2. **target id 不许含 `#`**——它是 URL 片段符，`?target=ring:errors#0` 到服务端只剩 `ring:errors`；
   字符集用 `[A-Za-z0-9._:-]` 并加断言。（页面上靠 `encodeURIComponent` 侥幸能跑，手写 URL 必踩）
3. **发出去的命令必须过真解析器**（`build_parser().parse_args`）：`commandgen` **只记录可选参数、不记录位置参数**，
   曾因此发出两条跑不通的命令（`chain-validate` 缺 file、`cards validate` 缺 `--root`）。
4. **`commandgen` / `registrygen` 的漂移门是对的**：后端新增 CLI 子命令 → view 立刻红，
   修法是重生成 + 补 `COMMAND_CARD`，**别改门**。
5. **`var(--x)` 写错变量名不报错**，只是整条声明被丢弃（曾 13 处 `var(--sub)` 静默失效）。
6. **`state.loaded.X = true` 不能写在 `switchTab(X)` 之前**——`switchTab` 用 `if (!state.loaded[name])` 当加载守卫，
   先置位会让加载函数**永不执行**（症状：页面永远「加载中…」）；门只看源码字符串，抓不到，
   已补 `tests/boot_sequence.js` 真跑启动块。
7. **浏览器缓存会伪造「改了没效果」**：`index.html` 改完先 Ctrl+F5（`view.py` 已加 `no-store`）。
8. **浅底弹层的前景别用 `var(--色板)`**：页面按 `prefers-color-scheme: dark` 渲染时 `--ink` 会翻成浅色字，
   变成「浅字浅底」（弹层颜色写死 + `color-scheme: light` 才对）。
9. **`start-viewv2.cmd` 必须传 `--exports-root`**：不传 → 运行中服务 `exports_root=None` →
   view 侧抑制**删得掉库内行、清不掉 exports 旧副本** → 下次 sync 把会话收回来（「我删了它又回来了」）；
   已有两道门（读启动脚本断言参数在；断言「没配时预演必须响亮警告」）。
10. **`/api/reports/errors` 的 pattern 行没有 `given_up` 字段**：照它算「未解决」得 0，真实是 31；
    要算未解决必须走 `errstats.pattern_stats.given_up`。
11. **并发教训（v0.43 与 v0.46 各复现一次）**：同一工作区并行子代理会互相同改 `harvester/cli.py` 与 `README.md`；
    派活**按文件切分**或串行；并发期间别跑全量套件（会出现假红：「红一次」先查是不是撞了并发窗口）。
12. **死代码门的并发保护**（v0.45 加）：扫描期间被改动的文件（戳变**或内容与扫描时不一致**，含撕裂读）
    其发现单列 `unstable_findings`、不计入死代码；CLI 只有不可信发现时回 **3（请重跑）**；
    套件那道门可区分 `skipTest`——**并发编辑期间不要对扫描类门下结论**。
13. **变异/改写脚本必须先关字节码写入**（P6-5 实测踩到，**静默且会伪造结论**）：
    Python 的 `.pyc` 校验只看 `(mtime, size)`。若变异串与原文**等长**（如 `规则命中`→`规则加载`）
    且写入与还原落在**同一秒**，还原后 `.pyc` 仍被判为有效 → **跑的还是变异版字节码**。
    症状：源文件明明是 A、运行时输出却是 B，而 `inspect.getsource` 读 `.py` 也显示 A（它不背锅），极难定位。
    **正确做法**：① 变异脚本 env 设 `PYTHONDONTWRITEBYTECODE=1`；② 跑前清 `__pycache__/*.pyc`；
    ③ 还原后**逐字节比对**自证（`read_text() == orig`），不等立刻中止；
    ④ 手工改完源码怀疑没生效时，先删 `__pycache__/*.pyc` 再复测。
    本仓两个变异脚本（`docs/reports/mutation-attrib.py`、`mutation-p65-view.py`）已按此加固；
    它们的"全杀"结论只有在**加固后**跑出来的才算数。

**收尾固定动作**（本仓历史证明有效）：① 死代码扫描（`harvester/` 与 `tests/` 两个根都要 0/0/0，行内 `# noqa` 视为有意保留）；
② 悬空路径检查（`git ls-files -c core.quotePath=false` 取已提交 `*.md`，逐个 `Test-Path` 其中 `docs/reports/...` 引用）；
③ 过期文本扫描（`git grep "TODO|FIXME|后续版本|待实现|暂未"`，逐条判断是「项目欠债」还是「在描述扫描本身」）。

## 四、术语与口径（承全局 §0「以官方规范为准」）

- 错误分类用的 `env` / `tool_interface` / `context` / `unclassified` **是本项目自定义分类（非行业术语）**——
  **已知缺口**：UI 与文档尚未全部标注这一点。优先对齐官方已有说法（可观测性领域对 error class 的惯例、
  OpenTelemetry Semantic Conventions）。
- `client` 非鉴权、token 非权限、destructive 的「可回滚」**只到台账快照为止**（无一键恢复）——口径诚实。
- **写 meta 只经由 B 档两段提交**（预演零副作用 → `confirm_token` → 才写）；注册表分层：事实从真解析器派生、人只写判断。
- 8765 = apiserve ｜ **8766 = actions-serve**（`harvester-view/actions/actionsserve.py`，
  **唯一** import 兄弟仓库的地方，只调用不改）；页面 `/view/*` 必须走**绝对地址** `ACT_BASE`
  （相对路径会打到 8088 并 404）。

### 四·补 · 判断纪律（P6-16，用户 2026-10-22 批准；**硬约束**）

> **任何判断必须并排显示证据；禁止对裸 sid 做判断。**

1. 做「这轮值不值得看」这类判断时，**必须同时给出证据包字段**（分诊 JSON 的
   `first_user` 引文等，`noisetriage.py:249-256` 已齐备），**不许只给一个 sid 让人自己查**。
2. **引文硬门：≥6 字**（承 `chainaudit.py:31` 先例）；引文必须可用 `search` 抽验命中。
3. **事实 / 归因 / 裁决三层分离**（P6 冻结决策 A1）：机器只出事实与**候选**
   （会错，须显式标 `candidate=True`），**裁决只进台账**（`attribations` / `card_review`）。
4. **归因规则放数据文件**：`harvester/data/attrib-rules.yaml`（**随包分发**），
   **不放 `.py`**——放代码就重演 `errstats._RULES` 的困境（新 Harness 接入时无人重审）。
   四要素 `match`/`class`/`why`/`fix` **缺一即加载失败**（fail loud）。
   **为什么不放 `docs/`**：`docs/` 被 `.gitignore:30` 整目录忽略 → 放那儿换机即丢。
5. **人裁过的归因不再进候选**（同 `card_review` 语义）；被移出的落「已裁决」段，
   **不许静默消失**（"处理过"不能看起来像"数据变少了"）。

## 五、工具化铁律（用户明确要求）

做出来的必须是两个仓库的**工具本体**，不是一次性脚本。判定口径：**这个动作下次采集/起草还会不会重跑？**
会 → 进 `harvester/` 或 `harvester-view/`（配 CLI + 测试 + README）。逐轮只允许留**判断文件**
（如 `judgment-v0xx.yaml`）与一次性报告。
已据此回收：`topic dedupe/turns`、`chain-audit`、`topic md`、`--triage-json`/`--plan-seed`/`--triage-brief`。

## 六、文档地图（**入口是「最新」，不是某个版本号**）

1. **最新交接文档 = 开工第一站**（本仓的交接放在被 gitignore 的 `docs\` 里，只在本机）：

   ```powershell
   Get-ChildItem docs\HANDOFF-*.md | Sort-Object LastWriteTime -Descending |
     Select-Object -First 3 Name, LastWriteTime
   ```

   取最新的那份，**先读它的 §0/§1**；`HANDOFF-v*.md` 是历史留档，要考古再往前翻。
2. **版本号来源**：`docs\HANDOFF-v0.33-next.md` 的 §0（`test_v45_hygiene` 读它——**升版本号必须同改它**）。
3. **测试数字的唯一权威**：`README.md` 顶部「测试基线」（`tests/test_v48_test_baseline.py` 真跑对账钉住）。
4. **剩余事项 SOP**：`docs\SOP-remaining-v045.md`（§8 = 用户新提六项 N1–N6 登记表；§5 = 定期蒸馏 SOP）。
5. **体检与自进化评估**：`docs\SELF-EVOLUTION-AND-DSH-PIPELINE.md`；事实台账在
   `docs\HANDOFF-v0.22-next.md` §2。
6. **会话抑制提案**：`docs\PROPOSAL-session-suppress.md`（结论以最新交接为准）。
7. **view v2 工程四件套**（权威位置 = `_eng\`，用户明示留原地；先读第一份）：
   `PROJECT-IRON-RULES-v1.md` → `SOP-harvester-view-v2.md`（含冻结决策与头部进度指针）→
   `TODO-harvester-view-v2.md`（台账）→ `HANDOFF-view-v2-kickoff.md`（逐会话记录，**最新一节在最上面**）。
8. **用户手动携带的冷启动副本**（内容最全，放在本仓外）：
   `D:\Data\AI\交接文档\` 下与本项目同名的那一份——用户换会话时会直接把它交给新会话。
9. **P6 归因与可评审数据面工程**（进行中）：纪律在 `_eng\SOP-attrib-review.md`
   （冻结决策 A1–A11 + 判据 ①–⑤），台账在 `_eng\TODO-attrib-review-P6.md`；
   **依据文档已收编**到 `docs\inputs-2026-10-22\`（含逐份勘误，**冲突以勘误为准**）。
   归因层已实装：`harvester/attrib.py` + `harvester/data/attrib-rules.yaml` +
   CLI `attrib` / `attrib-review` + 测试 `tests/test_attrib.py`。

## 七、不受本仓控制的既有事实（别再重复踩）

- `docs/reports/` **整个目录被 gitignore** → 一次性产物可以放那儿，但**结论/判断必须写进已提交的交接与台账**。
- 已被工具取代的一次性脚本**已删除**，文档里不留旧文件名（用户 2026-10-10 第 2 点）。
- 第三方评审文档**只当结构性判断与已完成项验收单**，不当待办清单执行。
- 交接文档要能自包含：不引用会话隔离目录、不写盘符路径（正式交付文档处）。
