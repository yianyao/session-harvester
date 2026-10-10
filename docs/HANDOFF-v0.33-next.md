# HANDOFF — v0.33 交接（**新会话从这里开工**）

> 交接时点：2026-10-10。v0.33 系列最后一提交见 `git log -1`（本文件随收尾提交一起更新，
> 故不写死自身哈希）。
> **为什么先读本文件**：`WorkBuddy\<会话时间戳>\` 是按会话隔离的工作区，新会话
> **看不到**上一个会话的目录；本文件在**仓库内且已提交**，是可靠的状态入口之一
> （另一个是项目 `AGENTS.md` §0 状态快照与 `$DSH_HOME/AGENTS.md` §六）。
> 前置阅读：本文件 §0 → 项目 `AGENTS.md` §0 → `docs/HANDOFF-v0.22-next.md`
> §2（事实台账 **H1–H73**）→ `docs/HANDOFF-v0.24-next.md` §7.4（待办细目）。

---

## §0 状态快照（**只读这一段也能开工**）

| 项 | 值（2026-10-10 实测） |
|---|---|
| 后端 head | v0.33 系列 + v0.34/v0.35：`a76f19c` 分诊落地 / `8c0d013` topic md / `2718731` ③ 集成门搬家 / `74af173` 分诊尾部 / `0902f19` **plan-seed 工具化**；**收尾提交见 `git log -1`** |
| 后端测试基线 | **564 例全绿**（venv，须带沙箱补丁，见 §2） |
| 无 PyYAML 门禁 | `Ran 555 / FAILED (errors=59)`（设计行为；**须 0 failures**——出现 failures 说明有人把"环境缺依赖"写成了断言） |
| 前端仓库 | `..\harvester-view`，head `533a18d`，**25 例全绿**（24 + 真实载荷集成门 1），已 push |
| 主题注册表 | **14 个主题**：小说 `tp-20261008-010`=**378**、素材库 `tp-20261010-005`=**185**、采集 `tp-20261010-004`=188、心理 `tp-20261010-003`=53；零散登记 **139 条** |
| 分诊池 | 尾部复核后再降：**534 条**（substantive 503 / noise_maybe 30 / noise_high 1；**topic_hint 与 craft_material 均已归零**） |
| 库规模 | 1964 会话 / 62899 消息 / 36647 步骤 / 294 错误（`2026-10-09 10:17:07` 时点） |
| 已发布 chain | 3 条（叙事节奏 / 吾好梦中救人 / Skill 自学习进化），在 `~/.workbuddy/knowledge/topics/` |
| **沙箱策略** | 本轮**中途变化**：开始时只对 `session-harvester/` 可写（实测写 `..\harvester-view` 被拒 → ③ 一度判"未执行"），后段放开为全访问才完成 ③。**每次会话都可能不同：跨仓库任务先探一次写权限**（`Set-Content` 一个探针文件即可），别凭上一轮的印象决定做不做 |

**基线自查（先做，否则会误判"项目坏了"）**：

```powershell
$venv = "C:\Users\yianyao\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
$env:PYTHONPATH = "<repo>\docs\reports"      # 沙箱补丁，见 §2
& $venv -X utf8 -m unittest discover -s tests     # 预期 551 例 OK
```

**下一件事（按优先级，均已写成可执行形态）**：

| # | 事项 | 为什么 | 做法 |
|---|---|---|---|
| 1 | `noise_maybe` **30 条**仍未处理 | 用户已裁决**不自动登记**（精度约 2/3），但可以像 v0.33 那 41 条与 v0.34 那 28 条那样**逐条复核**后二分 | 用 `--triage-json` + `--triage-brief noise_maybe` 看全量 → 写 `judgment.yaml`（overrides/noise/skip）→ `--plan-seed --require-covered noise_maybe` → `--apply`（缺省 dry-run）。**判断不写脚本**（v0.35 已把机械部分工具化） |
| 2 | 后端侧剩余的一次性脚本 | `docs/reports/check-api-chain.py`（API 链端点形状检查）每改一次 API 都该跑，但它属**后端**的测试而非 view 的 | 移进 `harvester/tests/`（或并入既有 API 测试），完成后删除原件 |
| 3 | M1 关键词是否收紧（待你裁决） | M1 的宽关键词（描写/动作/语气…）会**改变整个语料的分诊结果**（H71）：v0.33 后新命中 28 条，其中 23 条命中是对的、5 条是小说正文（已改判） | 保留则下轮继续人工复核；收紧则改 `topic keywords`，但会失去"素材检索"的自动提示 |
| 4 | 其余仍躺在 `docs/reports/` 的一次性脚本 | 按用户 2026-10-10 第 1 点要求（第二轮强调）逐个审：`resolve-members.py` / `move-039-to-T9.py` / `register-topics.py` / `topic-overlap.py` / `commit-msg-*.txt` 等 | 判定口径：这个动作**下次采集/起草还会不会重跑**？会 → 进 `harvester/`（配 CLI + 测试 + README）；纯回放历史 → 保持一次性并在本节备案 |

---

## §1 本轮（v0.33 / v0.33.1）完成项

| 项 | 结果 |
|---|---|
| **语义分诊落地**（v0.32 交接 ①） | assign **272** 条 / noise **19** 条；新建 `tp-20261010-005`「创作素材与背景检索」162 成员。详见 **H67** |
| 三个真缺陷修复 | ① `register_topic` 同日缺口撞 id（**H68**）；② `triage()` FTS5 全表扫 → **529 秒变 2 秒**（**H69**）；③ 候选 sid 集合重复构建 |
| `topic-consolidate --triage-json` | 分诊的**全量**机器出口 + `first_user`；人读报告仍 60 条（**H70**） |
| `topic md`（v0.32 交接 ②） | 人读一页：是什么/跨多久/关键转折/结论与未决；两条纪律（不列成员、不臆造结论）见 **H72** |
| **前端集成门搬家**（v0.32 交接 ③） | 移进 `harvester-view/tests/`（`check_real_payload.py` + `render_real.js` + 套件接线），view head `533a18d`，**25 例全绿**；断言改成**从载荷推导**，原件已删。详见 **H73** |
| **分诊尾部 28 条归位**（v0.34） | H71 的新命中：**23 条确实该进素材库 M1**（问近义词/动作怎么描写/用词判断），**5 条对象是具体文本**（点评/润色/改写/讨论小说正文）→ 改判小说主题。判定口径"对象是具体文本 → 010；问词/动作本身 → M1"。详见 **H74** |
| **分诊 → plan 工具化**（v0.35） | 用户第 1 点要求的落实：v0.33/v0.34 靠 `docs/reports/make-plan-*.py` 一次性脚本干的活进了工具本体（`harvester/planseed.py` + `--plan-seed` / `--judgment` / `--require-covered`；顺带收回 `--triage-brief`）。**真库逐条复现**了那两份已落库 plan 后才删的旧脚本。详见 **H75** |
| 测试 | 后端 +35 例（v0.33：22；v0.35：13）：`test_v33_triagejson` 8、`test_v33_triagebatch` 4、`test_v33_topicmd` 9、`test_v22_topics` +1、`test_v35_planseed` 13；view +1 门。**后端 564 / view 25 全绿** |
| 事实台账 | H67–H75 追加进 `docs/HANDOFF-v0.22-next.md` §2 |

## §2 环境速查（沿用；本轮无变化）

- **沙箱补丁（H53，必须先设）**：本会话沙箱下 `os.mkdir(p, 0o700)` 建出的目录连本
  进程都写不进去 → `tempfile` 必失败 → 直接跑套件会"整体崩"。
  `$env:PYTHONPATH = "<repo>\docs\reports"`（加载 `sitecustomize.py`）即可；
  工作区外项目（如 view）用同一招。
- venv 解释器：`C:\Users\yianyao\.workbuddy\binaries\python\envs\default\Scripts\python.exe`
- 无 PyYAML 解释器：`…\versions\3.13.12\python.exe`
- 提交信息用 `-F 文件`（别用 PowerShell here-string —— 已踩两次；v0.33 又差点踩）
- 推送：`git -c http.proxy=http://172.16.20.27:12603 push`
- meta 库：`topics_meta.db`（含 `sessions_noise`）/`suggestions_meta.db`/
  `artifacts_meta.db`/`keywords_meta.db`；备份 `topics_meta.db.bak-*`
  （本轮回滚点：`topics_meta.db.bak-20261010-121653-pre-apply`）

## §3 已完成 / 未完成 / 明确不做

- **已完成**：主题语义梳理（126→14）、`topic merge/rename/delete/keywords/
  export/md/dedupe/turns`、`topic-consolidate`（出包/执行/零散登记/分诊+JSON）、
  `chain-audit`、`noisetriage`、`topicexport`、多链 API、view 多链渲染与人读形态。
- **未完成**：§0 表里的四件。另：`docs/reports/` 下仍有 `consolidate-topics.py` /
  `finalize-keywords.py` / `make-noise-plan.py` 属**一次性数据操作**（通用形态已进
  `topic-consolidate`，不必再工具化）。`check-api-chain.py` 待移（见 §0 表 4）。
- **③ 前端集成门搬家（本轮已完成，`harvester-view` 提交 `533a18d`）**：
  1. `docs/reports/check-view-real.py` → `harvester-view/tests/check_real_payload.py`
     （取数逻辑改为**选链最多的主题**而非写死 `tp-20261008-010`；新增退出码 **3 =
     未执行**：后端仓库/库不在时不让调用方误当成通过）；
  2. `docs/reports/view-real-render-check.js` → `harvester-view/tests/render_real.js`；
  3. **断言全部改成从载荷推导**（主题数、chains_count、首阶段互斥、锚点节点数 =
     载荷里 anchors 的 nodes 总数、首个关键词可见）——原脚本写死"324 成员 / 50 节点 /
     作品本体主线"，一次性用没问题，**变成每轮都跑的门就会假红**；"载荷里根本没有
     这种主题"的项输出 `n/a` 并单列计数，不混进 ok（AGENTS.md §五 16）；
  4. 接入 view 套件：`tests/test_v33_real_payload.py`（node 缺失 → skip；脚本报 3 →
     skip 且理由写明"未执行：…"；断言失败 → 失败）；
  5. 验证：`ok 12 / n/a 0 / FAIL 0`、view **25 例 OK**；后端两份原件已删（避免两份）；
  6. 后端仓库文档里的历史引用（台账 H60、HANDOFF-v0.24 §7.4）保留原文，**迁移事实由
     H73 记录**——不追改历史条目。
- **明确不做**（用户裁决）：不改 `D:\Data\AI\Skills\`（路线图 R1–R6 由用户那边开工）；
  工具不调用任何 Agent/模型（确定性、离线是设计特性）；不自动登记任何"疑似零散"
  （精度约 2/3 —— 本轮 41 条逐条复核再次证实：约 1/3 是创作素材）。

## §4 已知坑（本轮新增，勿重踩）

1. **`docs/reports/` 整个目录被 gitignore**（`.gitignore:23`）——本轮的分诊 JSON、
   plan、生成脚本都进不了版本库。**结论与判断必须落到已提交的台账/交接里**，
   否则下一个会话（尤其是换工作区时）看不到（全局记忆 §六 17b 的同一坑）。
2. **`COUNT(*)+1` 生成序号**：任何"删过再新建"的场景都会撞唯一约束（H68）。
   夹具不覆盖的状态转移，真库第一次遇到就炸。
3. **FTS5 虚拟表的 `UNINDEXED` 列没有索引**：按它做相关子查询 = 每会话整表扫
   （H69）。`messages.sid`/`role` 都是 UNINDEXED，批量取数要**先筛 sid 再一次扫描**。
4. **给人看的摘要不能当机器输入**：`render_triage` 每类 60 条，照它填 plan 会静默
   漏行（H70）。同一数据要分人读/机读两个出口，并测试钉住差异。
5. **给主题加宽关键词会改变整个语料的分诊结果**（H71：craft_material 142→0、
   topic_hint 109→28），因为 `classify` 是 `topic_hint > craft > 查询型` 的优先级。
   加宽前先想清楚它会不会把别类吃掉。
6. **PowerShell 承载含引号的代码 = 必坏**（本轮修 f-string 时又中招一次）：
   写文件一律用 `write`/`edit` 工具；中文串里不要嵌 ASCII 双引号。
7. **性能修复未必能配上"会红"的断言**：H69 的 529→2 秒没有回归测试
   （计时断言 flaky；`set_trace_callback` 看不到子查询执行次数）。这类改动要在
   交接里**明说没有测试**，别让下一个会话以为它有覆盖。
8. **一次性脚本变成"每轮都跑的门"时，写死的真实数字必须改成从数据推导**（H73）：
   原"真实载荷渲染检查"写死 324 成员 / 50 节点 / 2 条链，一次性跑没问题；搬进
   view 套件后数据一变就**假红**（主题数 13→14、小说主题成员 324→373）。
   判据：**这个断言在数据变化时该不该跟着变？** 不该变 → 从载荷推导；
   该变 → 它就不是一道门，别放进套件。

## §5 实测数字（口径显式，便于对账）

| 指标 | 值 | 口径 |
|---|---|---|
| 分诊扫描 | 853 → **562** → **534** | 非任何主题成员、非零散登记、user 回合 ≤ 3；853 归置前 / 562 归置 272+19 后 / 534 再归置尾部 28 条后 |
| 尾部 28 条归位 | M1 **23** 条 / 010 **5** 条 | 判定口径：对象是具体文本 → 010；问词/动作本身 → M1 |
| 主题成员（终值） | 010: **378**、M1: **185**、004: 188、003: 53、002: 38 | `topic list`（apply 输出对账：373+5、162+23） |
| assign | **272** 条 / 12 个目标 | plan `assign` 段；含新建 M1 的 162 条 |
| noise 登记 | **19** 条（120 → **139**） | 41 条 noise_high 逐条复核后的"纯取信息"部分 |
| noise_high 复核四分 | 19 / 20 / 1 / 1 | 登记零散 / 归 M1 / 归 003 / 保持现状 |
| 成员对账 | 010: 324→**373**、003: 27→**53**、002: 36→**38**、004: 29→**40**、M1: 0→**162** | 注册表成员数（apply 输出） |
| `triage()` 耗时 | **529 秒 → 2 秒** | 同机同库，1964 会话 / 62899 消息；输出逐项一致 |
| 测试 | **564 例 OK**（venv）/ 555 errors=59（无 PyYAML，0 failures） | `unittest discover -s tests` |
| plan-seed 等价性 | v0.33：assign 272 / keep 2 / noise 19 / skip 1 **全一致**；v0.34：assign 28 / keep 12 / noise 0 **全一致** | 与两份已落库 plan 做语义比对（`docs/reports/verify-planseed-*.py`） |
| view 测试 | **25 例 OK**（24 + 真实载荷集成门） | `harvester-view`：`unittest discover -s tests` |
| 真实载荷门 | `ok 12 / n/a 0 / FAIL 0` | 真库 14 主题；多链主题 `tp-20261008-010` 2 条链 |
| `topic md` 体量 | 2037 字符（无链主题）/ 约 6K（有链主题） | 一页；不列成员 |
