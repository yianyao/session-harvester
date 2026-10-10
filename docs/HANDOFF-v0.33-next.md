# HANDOFF — v0.33 交接（**新会话从这里开工**）

> 交接时点：2026-10-10。上一轮 head：`8c0d013`（v0.33.1；本文件随本轮收尾提交更新）。
> **为什么先读本文件**：`WorkBuddy\<会话时间戳>\` 是按会话隔离的工作区，新会话
> **看不到**上一个会话的目录；本文件在**仓库内且已提交**，是可靠的状态入口之一
> （另一个是项目 `AGENTS.md` §0 状态快照与 `$DSH_HOME/AGENTS.md` §六）。
> 前置阅读：本文件 §0 → 项目 `AGENTS.md` §0 → `docs/HANDOFF-v0.22-next.md`
> §2（事实台账 **H1–H72**）→ `docs/HANDOFF-v0.24-next.md` §7.4（待办细目）。

---

## §0 状态快照（**只读这一段也能开工**）

| 项 | 值（2026-10-10 实测） |
|---|---|
| 后端 head | `8c0d013`（v0.33.1：语义分诊落地 + `topic md`） |
| 后端测试基线 | **551 例全绿**（venv，**须带沙箱补丁**，见 §2） |
| 无 PyYAML 门禁 | `Ran 542 / FAILED (errors=54)`（**设计行为**，不是坏了：新增 9 个 chain 类测试各显式报错） |
| 前端仓库 | `..\harvester-view`，head `7066e5e`，**24 例全绿**（本地**领先 origin/main 1 个提交，未 push**） |
| 主题注册表 | **14 个主题**（本轮新建 `tp-20261010-005`）+ 零散登记 **139 条** |
| 分诊池 | 归置后重跑：**562 条**（substantive 503 / noise_maybe 30 / topic_hint 28 / noise_high 1） |
| 库规模 | 1964 会话 / 62899 消息 / 36647 步骤 / 294 错误（`2026-10-09 10:17:07` 时点） |
| 已发布 chain | 3 条（叙事节奏 / 吾好梦中救人 / Skill 自学习进化），在 `~/.workbuddy/knowledge/topics/` |
| **本会话沙箱** | **只对 `session-harvester/` 可写**；`..\harvester-view` 与 `~/.dsh`、`~/.workbuddy` 等**工作区外路径一律拒绝**（实测 Set-Content 被拒）。跨仓库任务需提权（见 §3 ③） |

**基线自查（先做，否则会误判"项目坏了"）**：

```powershell
$venv = "C:\Users\yianyao\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
$env:PYTHONPATH = "<repo>\docs\reports"      # 沙箱补丁，见 §2
& $venv -X utf8 -m unittest discover -s tests     # 预期 551 例 OK
```

**下一件事（按优先级，均已写成可执行形态）**：

| # | 事项 | 为什么 | 做法 |
|---|---|---|---|
| 1 | **前端集成门搬家**（交接 v0.32 §0 的 ③，**本轮未执行**） | `check-view-real.py` + `view-real-render-check.js` 仍在 `docs/reports/`（一次性脚本），但每改一次 view 都该跑 | 见 §3 ③ 的**完整配方**；需先解决沙箱不可写 `..\harvester-view` |
| 2 | **复核分诊池剩下的 28 条 `topic_hint`** | 它们是 M1 关键词进索引后**新命中**的（H71）；其中可能有"小说正文被 M1 的 描写/动作/语气 命中"的错配（正是本轮修过的那类） | 跑 `--triage-json` → 看这 28 条的 `reason`/`first_user` → 错配的改判 `tp-20261008-010`，对的 `assign` 进 M1 |
| 3 | `noise_maybe` **30 条**仍未处理 | 用户已裁决**不自动登记**（精度约 2/3），但可以像本轮 41 条那样**逐条复核**后二分 | 先出 `--triage-json`，逐条判"纯取信息 / 有写作指向"，再写 plan（**别一刀切登记**） |
| 4 | `make-plan` 的机械部分工具化 | 本轮 `docs/reports/make-plan-v033.py` 干的活（分诊 JSON → plan 草稿 + 硬校验）**每轮都要重做**，按项目铁律应进工具本体 | 建议形态：`topic-consolidate --plan-seed <triage.json> --out plan.yaml [--judgment <yaml>]`：机械映射 + 显式判断文件，脚本里的三张清单就是 `--judgment` 的样例 |

---

## §1 本轮（v0.33 / v0.33.1）完成项

| 项 | 结果 |
|---|---|
| **语义分诊落地**（v0.32 交接 ①） | assign **272** 条 / noise **19** 条；新建 `tp-20261010-005`「创作素材与背景检索」162 成员。详见 **H67** |
| 三个真缺陷修复 | ① `register_topic` 同日缺口撞 id（**H68**）；② `triage()` FTS5 全表扫 → **529 秒变 2 秒**（**H69**）；③ 候选 sid 集合重复构建 |
| `topic-consolidate --triage-json` | 分诊的**全量**机器出口 + `first_user`；人读报告仍 60 条（**H70**） |
| `topic md`（v0.32 交接 ②） | 人读一页：是什么/跨多久/关键转折/结论与未决；两条纪律（不列成员、不臆造结论）见 **H72** |
| 测试 | +22 例：`test_v33_triagejson` 8、`test_v33_triagebatch` 4、`test_v33_topicmd` 9、`test_v22_topics` +1。**551 例全绿** |
| 事实台账 | H67–H72 追加进 `docs/HANDOFF-v0.22-next.md` §2 |

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
  `topic-consolidate`，不必再工具化）。
- **③ 前端集成门搬家的完整配方**（本轮因沙箱**未执行**，不是忘了）：
  1. 需能写 `..\harvester-view`（本会话属工作区外，实测拒绝写入）→ 需提权或换个
     能写该仓库的会话；
  2. `git mv` 等价操作：把 `docs/reports/check-view-real.py` →
     `harvester-view/tests/check_real_payload.py`（**路径要改**：`parents[2]` 指向
     session-harvester 仓库根、`VIEW` 改为 `static/index.html`、`OUT` 改到
     `tests/_tmp/`），`view-real-render-check.js` → `harvester-view/tests/render_real.js`；
  3. 接入 view 的 24 例：`tests/test_v24_render.py` 里加一条"真载荷渲染检查"
     （用 `subprocess.run([sys.executable, ...])` 调 Python 侧，或直接在 node 断言里
     读 payload），**断言要能红**（先故意改一个字段名确认失败）；
  4. 在 view 仓库跑全量（预期 24+1 例）、提交（view 当前**领先 origin 1 个提交未 push**，
     一并处理）；
  5. 完成后把 `docs/reports/` 下这两个脚本删掉，避免出现两份。
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

## §5 实测数字（口径显式，便于对账）

| 指标 | 值 | 口径 |
|---|---|---|
| 分诊扫描 | 853 → **562** | 非任何主题成员、非零散登记、user 回合 ≤ 3；归置前后同一库 |
| assign | **272** 条 / 12 个目标 | plan `assign` 段；含新建 M1 的 162 条 |
| noise 登记 | **19** 条（120 → **139**） | 41 条 noise_high 逐条复核后的"纯取信息"部分 |
| noise_high 复核四分 | 19 / 20 / 1 / 1 | 登记零散 / 归 M1 / 归 003 / 保持现状 |
| 成员对账 | 010: 324→**373**、003: 27→**53**、002: 36→**38**、004: 29→**40**、M1: 0→**162** | 注册表成员数（apply 输出） |
| `triage()` 耗时 | **529 秒 → 2 秒** | 同机同库，1964 会话 / 62899 消息；输出逐项一致 |
| 测试 | **551 例 OK**（venv）/ 542 errors=54（无 PyYAML） | `unittest discover -s tests` |
| `topic md` 体量 | 2037 字符（无链主题）/ 约 6K（有链主题） | 一页；不列成员 |
