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
   P6-6 实测：新增只读命令后**会连红两次**（两道不同的门）——
   ① `COMMAND_CARD` 漏了 → "命令 X 没有归并到任何动词卡"；
   ② 归了卡、但 `READ_ACTIONS` 没加 → "**A 档命令没有可执行动作**"（`test_v54_cli_actions`）。
   第 ② 条的口径是「**只读命令 = 页面能执行**」，只读却只能复制命令，等于把最安全
   的一类动作挡在页面外。所以新增 A 档命令的完整动作是：`commandgen` 归卡 →
   `registrygen.READ_ACTIONS` 加动作 → `actionsserve` 写 `_action_*` 处理器 →
   `python -m actions.commandgen` + `python -m actions.registrygen` 重生成 → 两道 `--check`。
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
14. **复用别人的取数函数前，先用探针看清它的返回形态**（P6-6 实测：5 处依赖我推错 4 处）。
    本仓的聚合函数**不是**统一的"list[dict]"风格：`pattern_stats[pat]["samples"]` 是
    **list[tuple] `(sid, seq, error)`**；`chainstats.collect()` 四组也全是元组
    （`long=(sid,步数)`、`streaks=(sid,工具,次数,最长)`）；`collect_skill_invocations(db)` 收的是
    **库路径**（不是连接）、且**不收 `since_days`**；`collect_attributions()["by_pattern"][pat]`
    是**整行** dict，候选在 **`"rule"`** 键下而不是行本身。
    **最危险的一种**：取错键不会报错，只会得到 `rule_id=None`——"看起来有归因、实际空值"。
    所以：① 新接一个数据面先写 `docs/reports/probe-*.py` 打印真实字段与首元素；
    ② 凡"取值可能为空"的地方都要有**对账断言**（如 P6-6 的包 ↔ `collect_attributions` 逐条比对）。
15. **"渲染断言"要防载荷回声**（P6-8 实测，**变异逃逸**）：view 页面末尾有「原始载荷 JSON」
    折叠块，整段载荷（含 `line`/`hint`/`data-*` 属性）都在 `innerHTML` 里。所以
    `out.innerHTML.includes("仅 dsh 源")` 会在**页面根本没渲染那一行**时照样为真。
    **正确做法**：断言先切出**目标元素那一片**再查（如
    `/<p class='sub2 cov'>[\s\S]*?<\/p>/`），或用 `querySelector` 取节点；
    凡"整段 innerHTML 里找字符串"的断言，先问：**这个串还有别的路径进 DOM 吗？**
    实测代价：M12 变异（页面把覆盖率改写成 100%）第一版**存活**，改成分片断言后 12/12 全杀。
16. **覆盖度/分母必须现算并随数据一起给**（P6-8）：任何"部分源/部分库"的数据面，
    数字旁必须带 `已登记 N / 总会话 M（pct）` 与一句"**本面不代表全库**"，
    且 `pct` 由 `COUNT` 现算（写死 100 或 0 都会让门变假——mutation-p68 M1/M2 钉住）。
    产物面实测是 **8 / 1776 = 0.5%**，验收文字里"≈2%"是乐观估计，**以实测为准**。
17. **变异锚点必须能唯一确定"是哪一处"**（P6-9，**变异曾因此假存活**）：
    同一句源码可能出现在两个渲染分支里（下轨 `esc(cv.line_text||cv.line)` vs 归因台
    `esc(cov.line_text)`），只按"看起来像"的串选锚点，会改到**另一个函数**上，
    于是"门没红"被误读成"断言太弱"。做法：① 锚点带上**所在函数的局部特征**
    （类名/变量名/相邻行）；② 变异条目写明"哪条门会抓"，并且**只跑那条门**；
    ③ 两处都要门就各留一条（mutation-p69 的 M13/M14）。
18. **"页面要纯文本"不许在前端去标记**（P6-9）：把后端 Markdown（`**x**`）直接塞进 HTML
    会显示成星号；前端 `replace(/\*\*/g,"")` 是**第二套口径**。正确做法是后端**同源现算**
    一个纯文本字段（`line_text`），门钉"两形态说同一组数字 + `line_text` 无标记"。
19. **加完测试文件先跑死代码门**（2026-10-23 实测）：给新测试留了个用不到的 `import re`
    → `test_v37_deadcode` 红 → `test_v48_test_baseline` 两条实测对账跟着红
    （无 YAML 门的 `failures=0` 被破坏）→ **四道门同时红，而单独跑新测试是绿的**。
    死代码门 70 秒、全量套件 9 分钟：**先跑便宜的那道**。
20. **"除时间戳外可复现"要先数清有几个时间戳**（2026-10-23 修掉一条真偶发）：
    `test_v29` 的载荷里 `generated_at` 有**两处**（顶层 + `db_fingerprint` 内，`dbmeta.py:165`
    也按秒取时刻），只 `pop` 顶层 → 两次 render 跨秒即红（400 轮红 4 轮，差异键恒为
    `db_fingerprint`）。要么递归清掉所有时间戳，要么让被测函数接受注入时钟；
    否则这条门以 ~1% 概率随机红，最后没人再信它。
21. **归因规则扩容的判据是"真实串"，不是"覆盖率好看"**（2026-10-23 实测，P6-1 增量）：
    正确姿势是 `probe-unattributed.py` 打出未归因模式的**原始错误串** → 一个根因一条规则
    → 重跑并**逐模式核对**（新归因说不说得通、老归因有没有被改写）→ 再配映射门与变异。
    实测 7→24 条后：模式 19/67 → 65/67、错误 401/532 → 529/532，**既有归因零改写**
    （新规则一律追加在末尾＝最低优先级）。**故意留白**的两条（`tool_executor_failed`/`ABORTED`）
    是"报错没给根因"——为了 100% 覆盖率给它们编 why 就是**假归因**。
    门：`tests/test_v71_attrib_rules_expanded.py`（真实串对答案 + 无死规则 + 劫持陷阱）；
    **死规则**（一条也没命中的规则）与**劫持**（新规则抢走老规则的串）是扩容的两个典型坏味。
22. **接口 handler 自己拼的载荷行会漏字段**（2026-10-23 实测，v0.80）：
    `pattern_stats` 加了 `first_seen`，但 `/api/reports/errors` 的行是 `apiserve`
    **手工重组**的（`{pattern,class,count,tools,samples,attrib}`）→ 新字段没透出，
    页面那句「其中 N 条是本窗口首见」永远算成 0 条，**不报错**。
    规矩：① 在下游那个文件里加 `first_seen` 时，去 handler/序列化层**逐个核字段**；
    ② 门要打在**接口载荷**上（不是内部函数），且与库对账（`min(first_seen)` ==
    `SELECT MIN(ts) … AND ts>=since`）；③ 窗口态的语义要写清（"本窗口内最早"≠"全库最早"）。
23. **静态接线断言（源码文本）要找得到"真调用点"**（2026-10-23 实测，M8 假存活）：
    断言 `html.includes("encodeURIComponent(q)")` 被**别处**同名调用掩护，
    把目标处改成拼裸串仍绿。做法：断言串要带上**调用上下文**
    （`/api/sessions?q="+encodeURIComponent(q)`），否则它测的是"仓库里有人这么写过"。
    同类：JS 字符串里嵌 ASCII 双引号会把**页面**语法弄坏（症状是
    `node tests/xxx.js` 报 `SyntaxError: Unexpected identifier '中文'`，
    看着像测试坏了，其实是 `static/index.html` 坏了）——中文串一律用「」。

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
