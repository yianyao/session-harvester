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
| 后端 head | v0.33 → **v0.45**（分诊落地 / topic md / ③ 集成门 / 分诊尾部 / plan-seed 工具化 / M1 收紧 + 池清空 + 文档清账 / 死代码扫描工具化 / 沙箱补丁进版本库 / **深会话单列两类** / `regress` / 文档漂移门 / **chain 证据覆盖 + chain-audit 三门**）；**收尾提交见 `git log -1`** |
| 后端测试基线 | **685 例全绿**（venv，须带沙箱补丁，见 §2） |
| 无 PyYAML 门禁 | `Ran 644 / FAILED (errors=61)`（设计行为；**须 0 failures**）。已固化：`& $venv -X utf8 scripts\gate_no_yaml.py`（v0.43）；flake 复现：`& $venv -X utf8 scripts\flake_hunt.py --runs N`（热跑）/ `--cold --clear-pycache`（首跑类）/ `docs/reports/probe-history-flake.py`（旧版本树）（v0.45） |
| 前端仓库 | `..\harvester-view`，head `c1c2c06`，**25 例全绿**（24 + 真实载荷集成门 1），已 push |
| 主题注册表 | **14 个主题**（成员总数 938 → 1001 → **997**，v0.42 舍弃 4 条成员后）：小说 `tp-20261008-010`=**411**、素材库 `tp-20261010-005`=**199**、采集 `tp-20261010-004`=**191**、心理 `tp-20261010-003`=**69**、本机工具环境 `004-08`=**43**、SKILL `001`=**10**、`003`=**8**、`007`=**8**、`002`=**39**；零散登记 **163 条** |
| 分诊池 | **794 条** = substantive **502** + 深会话 `deep_unassigned` **292**（`--triage-deep`）；**noise_high / noise_maybe / deep_topic_hint 均已归零**（v0.42 用户裁决把 9 条舍弃进零散） |
| 库规模 | 1964 会话 / 62899 消息 / 36647 步骤 / 294 错误（`2026-10-09 10:17:07` 时点） |
| 已发布 chain | 3 条（叙事节奏 / 吾好梦中救人 / Skill 自学习进化），在 `~/.workbuddy/knowledge/topics/`。**叙事节奏链 v0.45 证据覆盖补做**：52 节点、有锚点成员 **39/55**、8 个标题级代表逐条标注；`chain-validate` errors 0、`chain-audit --quotes-ascii` 引文 75/75 + 锚点告警 0（详见 §1 与台账 H92） |
| **沙箱策略** | 本轮**中途变化**：开始时只对 `session-harvester/` 可写（实测写 `..\harvester-view` 被拒 → ③ 一度判"未执行"），后段放开为全访问才完成 ③。**每次会话都可能不同：跨仓库任务先探一次写权限**（`Set-Content` 一个探针文件即可），别凭上一轮的印象决定做不做 |

**基线自查（先做，否则会误判"项目坏了"）**：

```powershell
$venv = "C:\Users\yianyao\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
$env:PYTHONPATH = "<repo>\scripts\sandbox"    # 沙箱补丁，见 §2
& $venv -X utf8 -m unittest discover -s tests     # 预期 640 例 OK
```

**下一件事（按优先级，均已写成可执行形态）**：

| # | 事项 | 为什么 | 做法 |
|---|---|---|---|
| 1 | ✅ **`tests/` 的 9 条未用 import 已清（v0.39）** | 8 条是普通死 import（`test_v19` 的 `json`/`build_suggestion_entries`、`test_v22_p1_3_cross` 与 `test_v22_t5_candidates` 的 `json`、`test_v22_t4_skilljoin` 的 `timedelta`、`test_v23_yuanbao_blockguard` 的 `tempfile`、`test_v30_noisetriage` 的 `intent_of`、`test_v31_chainaudit` 的 `audit_quotes`）；`test_v31` 的 `import yaml` 是**依赖标记**（H9） | 已做：8 删 + `import yaml` 加 `# noqa`；并把套件的 `test_harvester_and_tests_have_no_dead_code` **扩到两个根**（否则还会长回来） |
| 2 | ✅ **`CHAIN-AUTHOR-SPEC.md` 已移进 `docs/`（v0.39）** | 它原先只在 gitignore 的 `docs/reports/` 里——与 H82 的 `sitecustomize.py` 同类：**写 chain 的规范文档，新 clone 的作者拿不到** | 已做；顺带确认 `docs/reports/` 里其余都是允许保留的一次性报告 |
| 3 | ✅ **65 条 `deep_topic_hint` 已复核归位（v0.39–v0.42）** | 机械命中不等于判对（命中率约 8 成） | 已做；详见 H84–H88 |
| 4 | ✅ **「原型机」4 条同源会话已由用户裁决（v0.42）** | 4 条都不在任何主题、也没登记零散 | 用户裁决：4 条原型机 + 5 条最不确定者**舍弃进零散登记** |
| 5 | ✅ **长期未做项已清（v0.43–v0.45）** | ① `regress`（v0.43）② 叙事节奏 chain 证据覆盖（v0.45）④ V3 卡片校验↔主题打通（v0.43）。**③ chain 元结论回写全局记忆：仍建议不做**（领域内容，放进代理工作记忆是噪声） | 见 §1 与 H90/H92 |
| 6 | **无"没做成"项**（v0.45 第九批已闭环） | ① README 漂移检查 ✅ v0.44；② **那两次"红一次、再跑全绿"已定位并修掉**（H100）——`deadcode-scan` 在**并发编辑窗口**内读遍源码会撞上半写/撕裂读而**假红**，机制已实测复现（12 轮里 9～10 轮，每轮都命中被改写文件）并修（不可信发现单列 + CLI 回 **3** + 套件门可区分 skip）；v0.38 那棵树（`d9cac46`）冷跑 13 轮全绿，排除代码缺陷；③ **两条历史记录的共同缺口是"没记用例名"**（v0.29 那次被 `Select-String` 摘要过滤掉、H82 那次只记数字）→ 已由 `flake_hunt.py` 补上 | 若再现，猎人自动点名 + 留 traceback。**别再对扫描类门在并发编辑期间下结论** |
| 7 | ✅ **引号形态净化（v0.45 第二批已做）** | 叙事节奏 chain 正文原用 ASCII `"…"`（212 处），`「」` 只有 5 处界面标签 → **默认引文门对该链空转**（0 条 = 0 未命中），对下游是个静默洞 | 已做：9 处**自造术语/格式记号**改加粗或行内码（`turn x-y`），72 对**数据引文**整体换成 `「」`；`chain-validate` 52 节点 errors 0、`chain-audit`（**不带** `--quotes-ascii`）`「」` **62 条全部逐字命中** |
| 8 | ✅ **剩余事项 SOP 已全部做完（v0.45）** | A 一行级 / B Agent 消费面 / C 质量欠账 **全部完成并闭环**（C4 见第 6 行）；D3 定期蒸馏 SOP 已落地（SOP §5）；D1/D2 暂缓 | 见 `docs/SOP-remaining-v045.md` §3（A/B/C/D 状态表） |
| 9 | ⏭️ **下一批（v0.46，下周开工）** | `docs/SOP-remaining-v045.md` **§6 下一批待办**（7 项，按 P0→P3，每项带实测证据 + **可判定验收**）：**P0-1 索引侧增量**（采集侧水位已在 `sync.py:154/235-273`；索引侧 `index_exports` 仍 `DELETE` 三表全量重建，DSH 42.1 MB 每次重解压 9.1 s → 应改用 `run_update` 已有的 `new/updated/gone` 喂 `index_session`）→ **P0-2 库龄可见性**（`dbmeta` 加"语料比库新 ⇒ STALE"）→ **P1-3 结构化 `isError`**（鲁棒性）→ **P1-4 自进化评估闭环**（`adopted_at` + `harvest-eval` 三值判定）→ **P2-5 台账人账**；另有 §7 列明的**不做**项（含 D1/D2 仍暂缓、**第三方评审文档只当结构性判断/验收单用**） | 按 §6 顺序开工即可，**不必重新体检**；每项都写明了"什么时候算通过 / 什么时候会红" |
| 10 | **事实台账** | H1–**H102** 在 `docs/HANDOFF-v0.22-next.md` §2（H101 = DSH 链路体检与自进化评估；H102 = SOP 扩写 §6/§7） | 新增事实先落台账再改文档 |

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
| **M1 关键词收紧**（v0.36，用户裁决） | 先量后改：宽词的真危害是**把 4 条含整合诉求的真工作抢成素材候选**；关键词 10 → 3（用词/措辞/微表情），并把 `动作` 补进 `CRAFT_WORDS`（否则那类写作找料会掉进兜底档、从报告里消失）。详见 **H76** |
| **池子清空 + 文档清账**（v0.36） | 30 条 `noise_maybe` 用新工具归位（10→005 / 4→003 / 1→001 / 14 登记零散 / 1 skip），池 534→**505**；已删脚本的引用从全部文档与源码清掉（含已发布 chain 的对照工具引用，过 `chain-audit` 两道门后同步）。详见 **H77/H78** |
| **死代码扫描工具化 + API 探针清除**（v0.37） | `deadcode-scan` 进工具本体（认 `noqa` 豁免、只提示不默认拦人，并把"`harvester/` 必须 0 条"钉进套件）；第一次跑就抓到旧扫描漏掉的 3 条。另删掉那份**纯打印、无断言**的 API 链形状探针——其能力已由 `test_v25_topic_chains.py`（含 404 路径）与 view 真实载荷门覆盖。详见 **H79/H80** |
| 测试 | 后端 +36 例（累计 565）：`test_v33_*` 21、`test_v35_planseed` 13、`test_v30` +2；view +1 门。**后端 565 / view 25 全绿** |
| **沙箱补丁进版本库 + view 侧审计**（v0.38） | `sitecustomize.py` 移进 `scripts/sandbox/`（原在 gitignore 目录 → 新 clone 上套件的前置条件不存在，文档却教人直接跑）；`deadcode-scan` 修掉"兄弟根静默跳过却宣称覆盖"，并用它对 view 审计抓到 **1 条真死 import**（view `c1c2c06`，25 例仍全绿）。详见 **H81/H82** |
| 事实台账 | H67–H82 追加进 `docs/HANDOFF-v0.22-next.md` §2；**H93–H97** = v0.45 第二轮、A 组、B 组、C1/C2/C5、C3/C4机制/D3-SOP |
| **DSH schema 守卫**（v0.45 C3） | 声明版本（`session.v4`）+ 消费字段形态（`REQUIRED_SHAPE`）双判据；不匹配 → `detect` STUB、`load_session` **lossy 空消息**（不半解析）、`list_sessions` 带 `schema_ok`；观察指纹 `d75532e18dc4`（真机 33 会话全部通过）。`_files()` 原写死 `v4` glob 的**诊断缺陷**已修（上游改名不再伪装成 MISSING） |
| **定期蒸馏 SOP**（v0.45 D3） | 见 `docs/SOP-remaining-v045.md` §5：三触发 + 八步 + 每步记数字；**不含 chain 再生成**（D1 暂缓） |
| **chain 证据覆盖 + `chain-audit` 三门**（v0.45） | 长期挂账的最后一项落地：`chain-audit` 新增**证据覆盖**（无锚点成员按 H40 现算去重 → 分开"重复会话"与"独立代表"）、**正文锚点↔frontmatter 双向对账**（含简写归属）、**引文门空转显式化 + `--quotes-ascii`**；锚点门重叠判据含会话标题；`chain-audit` 由 16.4s → **3.7s**（批量取数）。叙事节奏链 30→**52 节点**、有锚点成员 27/55→**39/55**，8 个标题级代表逐条标注；改正两处锚点归属（"不要太 AI 化"首次出现 2025-07-15；笛卡尔定稿措辞在 c503406a t5 而非 t4）。详见 **H92** |
| 测试 | 后端 **640 例全绿**（v0.45；+10 例 `test_v31_chainaudit`）；无 PyYAML 门禁 `Ran 602 / errors=60 / 0 failures`；死代码两根 0/0/0 |

## §2 环境速查（沿用；本轮无变化）

- **沙箱补丁（H53，必须先设）**：本会话沙箱下 `os.mkdir(p, 0o700)` 建出的目录连本
  进程都写不进去 → `tempfile` 必失败 → 直接跑套件会"整体崩"。
  `$env:PYTHONPATH = "<repo>\scripts\sandbox"`（加载 `sitecustomize.py`）即可；
  工作区外项目（如 view）用同一招。**v0.38 已把它从 `docs/reports/`（gitignore）移进
  `scripts/sandbox/` 并写进 README**——全量套件的前置条件不该躺在一个不入库的目录里。
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
- **未完成**：见 §0 表的下一件事（其余一次性脚本再审 / 池里 2 条原型机线索裁决 /
  view 侧再审 / 508 条 substantive 是否要挖新主题）。
- **`docs/reports/` 一次性脚本清账**（按用户 2026-10-10 第 1 点要求逐个体检；判定
  口径：**这个动作下次采集/起草还会不会重跑**）——本轮**已把被工具取代的那批删除**
  （本轮收尾提交一并处置）。**映射关系（结论，不列旧文件名）**：引文逐字门与锚点语义门
  → `chain-audit`；会话级去重 → `topic dedupe`；回合索引 / raw 全文导出 → `topic turns`；
  分诊出 plan 与主题梳理 → `topic-consolidate` 的梳理包与 plan（含 `--triage-*`、
  `--plan-seed`）；关键词定稿 → `topic keywords`；主题注册与归位 → `topic register` +
  `topic-consolidate` 的 plan。**留下的都有理由**：

  | 留下的 | 为什么 |
  |---|---|
  | `sitecustomize.py` | 已移进 **`scripts/sandbox/`**（v0.38，进版本库）并写进 README：它是**环境适配件**不是一次性脚本，而全量套件的前置条件不该躺在 gitignore 目录里 |
  | `run_tests.py` | 文档里的备用跑法（HANDOFF-v0.24 环境节） |
  | `probe-*.py` / `resolve-members.py` / `topic-overlap.py` | 一次性**探查**（回答当时的具体问题），属报告；**别再当工具用** |
  | `verify-planseed-*.py` / `judgment-v0*.yaml` | 迁移验证 + **逐轮判断文件**（允许保留的一次性形态） |
- **③ 前端集成门搬家（本轮已完成，`harvester-view` 提交 `533a18d`）**：把两份真实载荷探针
  移进 `harvester-view/tests/`：
  1. `check_real_payload.py`（取数逻辑改为**选链最多的主题**而非写死 `tp-20261008-010`；新增退出码 **3 =
     未执行**：后端仓库/库不在时不让调用方误当成通过）；
  2. `render_real.js`；
  3. **断言全部改成从载荷推导**（主题数、chains_count、首阶段互斥、锚点节点数 =
     载荷里 anchors 的 nodes 总数、首个关键词可见）——原脚本写死"324 成员 / 50 节点 /
     作品本体主线"，一次性用没问题，**变成每轮都跑的门就会假红**；"载荷里根本没有
     这种主题"的项输出 `n/a` 并单列计数，不混进 ok（AGENTS.md §五 16）；
  4. 接入 view 套件：`tests/test_v33_real_payload.py`（node 缺失 → skip；脚本报 3 →
     skip 且理由写明"未执行：…"；断言失败 → 失败）；
  5. 验证：`ok 12 / n/a 0 / FAIL 0`、view **25 例 OK**；后端两份原件已删（避免两份）；
  6. 后端仓库文档里的历史引用（台账 H60、HANDOFF-v0.24 §7.4）已去掉已删脚本的文件名，
     **迁移事实由 H73 记录**——不改动其时间/结论/数字。
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
| 主题成员（v0.36 终值） | 010: **378**、005: **195**、004: 188、003: **57**、002: 38、001: **9** | `topic list`（v0.36 对账：185+10、53+4、8+1） |
| assign | **272** 条 / 12 个目标 | plan `assign` 段；含新建 M1 的 162 条 |
| noise 登记 | **19** 条（120 → **139**） | 41 条 noise_high 逐条复核后的"纯取信息"部分 |
| noise_high 复核四分 | 19 / 20 / 1 / 1 | 登记零散 / 归 M1 / 归 003 / 保持现状 |
| 成员对账 | 010: 324→**373**、003: 27→**53**、002: 36→**38**、004: 29→**40**、M1: 0→**162** | 注册表成员数（apply 输出） |
| `triage()` 耗时 | **529 秒 → 2 秒** | 同机同库，1964 会话 / 62899 消息；输出逐项一致 |
| 深会话口径（v0.39） | `--triage-deep` → 863 条 = 浅 505 + **deep_topic_hint 65** + deep_unassigned 293；`--plan-seed --require-covered deep_topic_hint` 产出 assign **65** 条、dry-run 接受 | `--triage-deep --triage-json` |
| 测试 | **640 例 OK**（venv）/ 602 errors=60（无 PyYAML，0 failures） | `unittest discover -s tests` |
| 死代码扫描 | `harvester/` **0/0/0**（工具化后第一次跑抓到旧扫描漏掉的 3 条：2 条真死已删 + 1 条可用性探测改为 `noqa` 豁免） | `python -m harvester deadcode-scan`；套件 `test_harvester_package_has_no_dead_code` |
| M1 关键词收紧 | 10 → **3**（用词/措辞/微表情）；28 条旧命中里仍命中 7，跌破 21（4 条含整合诉求的真工作 / 14 条兜底档 / 3 条纯查询） | `docs/reports/probe-m1-keywords.py` + 重跑分诊 |
| 零散登记 | 139 → **153**（v0.36 复核 30 条 `noise_maybe`，登记 14 条） | `--noise-list` |
| **深会话口径边界** | ≤3 回合池 **505** → ≤12 回合池 **821**（+316：topic_hint **53** / craft 36 / noise_maybe 24 / substantive 707） | `--triage-max-turns`（v0.38 实测） |
| plan-seed 等价性 | v0.33：assign 272 / keep 2 / noise 19 / skip 1 **全一致**；v0.34：assign 28 / keep 12 / noise 0 **全一致** | 与两份已落库 plan 做语义比对（`docs/reports/verify-planseed-*.py`） |
| view 测试 | **25 例 OK**（24 + 真实载荷集成门） | `harvester-view`：`unittest discover -s tests` |
| 真实载荷门 | `ok 12 / n/a 0 / FAIL 0` | 真库 14 主题；多链主题 `tp-20261008-010` 2 条链 |
| `topic md` 体量 | 2037 字符（无链主题）/ 约 6K（有链主题） | 一页；不列成员 |
