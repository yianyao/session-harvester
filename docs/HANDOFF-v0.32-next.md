# HANDOFF — v0.32 交接（**新会话从这里开工**）

> 交接时点：2026-10-10。上一轮 head：`5dde45f`（本文件随本轮提交一起更新）。
> **为什么先读本文件**：`WorkBuddy\<会话时间戳>\` 是按会话隔离的工作区，新会话
> **看不到**上一个会话的目录；本文件在**仓库内且已提交**，是可靠的状态入口之一
> （另一个是项目 `AGENTS.md` §0 状态快照与 `$DSH_HOME/AGENTS.md` §六）。
> 前置阅读：本文件 §0 → 项目 `AGENTS.md` §0 → `docs/HANDOFF-v0.24-next.md`
> §7.4（待办）→ `docs/HANDOFF-v0.22-next.md` §2（事实台账 H1–H66）。

---

## §0 状态快照（**只读这一段也能开工**）

| 项 | 值（2026-10-10 实测） |
|---|---|
| 后端 head | 本轮提交（v0.32：`topic dedupe` / `topic turns` 工具化；见 `git log -1`） |
| 后端测试基线 | **529 例全绿**（venv 解释器，**须带沙箱补丁**，见 §2） |
| 无 PyYAML 门禁 | `Ran 520 / FAILED (errors=45)`（**设计行为**，不是坏了） |
| 前端仓库 | `..\harvester-view`，head `7066e5e`，**24 例全绿** |
| 主题注册表 | **13 个主题**（语义梳理后）+ 零散登记 **120 条** |
| 已发布 chain | 3 条（叙事节奏 / 吾好梦中救人 / Skill 自学习进化），在 `~/.workbuddy/knowledge/topics/` |
| 库规模 | 1964 会话 / 62899 消息 / 36647 步骤 / 294 错误（`2026-10-09 10:17:07` 时点） |

**基线自查（先做，否则会误判"项目坏了"）**：

```powershell
$venv = "C:\Users\yianyao\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
$env:PYTHONPATH = "<repo>\docs\reports"      # 沙箱补丁，见 §2
& $venv -X utf8 -m unittest discover -s tests     # 预期 529 例 OK
```

**下一件事（按优先级，均已写成可执行形态）**：

| # | 事项 | 为什么 | 做法 |
|---|---|---|---|
| 1 | **语义分诊**：109 条 `topic_hint` + 142 条 `craft_material` + 41 条 `noise_high` | 这是"以后采集的数据怎么归位"的实测样本；`assign` 已就绪但没有真实使用过 | 读 `docs/reports/triage-report.md` 与 `topic-consolidate --triage` 输出 → 写 plan（`assign:` 归主题 / `noise:` 登记零散）→ `--apply`（缺省 dry-run） |
| 2 | V3 剩余产物：**给人读的 `topic.md`** | 给机器的 `topic.json` 已完成（`topic export`）；人读那半还没做 | 复用 `topicexport.topic_bundle`，渲染成一页：是什么/跨多久/关键转折/结论/未决 |
| 3 | 前端集成门搬家 | 真实载荷渲染的两份探针仍在 `docs/reports/`（一次性脚本），但它每改一次 view 都该跑 | 移进 `harvester-view/tests/` 并接入 view 的 24 例 |

---

## §1 本轮（v0.32）完成项

| 项 | 结果 |
|---|---|
| `topic dedupe` 工具化 | 新增 `harvester/topicprep.py::dedupe_topic/render_dedupe` + CLI；H40 口径（≥0.90 判重复、0.80–0.90 边界带保留）。真库实测：小说主题 **324 成员 → 238 代表 / 86 重复 / 233 边界对 / 2 个无正文** |
| `topic turns` 工具化 | 同模块 `turns_of/render_turns` + CLI `--full`；回合号 = user 消息 1-based（H24），文本取 `messages.raw`（H57 口径） |
| 测试 | `tests/test_v32_topicprep.py` 9 例（重复并代表、边界带只报不并、无关会话不并、缺主题报错、回合号、`--full` 给原文、成员不在库不静默丢） |
| `AGENTS.md`（项目） | 新增 §0「开工第一件事」：状态快照 + 下一件事 + 基线自查命令 + 收尾纪律；新增铁律「能力一律进工具本体，`docs/reports/` 只放一次性产物」 |
| `$DSH_HOME/AGENTS.md`（全局） | 新增 §六「交接与跨会话续作」3 条：①交接写在会话工作区=没写（附实测根因）②"我写过了"不是证据，要同轮读回③交接第一屏必须是状态快照 |

## §2 环境速查（沿用；本轮无变化）

- **沙箱补丁（H53，必须先设）**：本会话沙箱下 `os.mkdir(p, 0o700)` 建出的目录连本
  进程都写不进去 → `tempfile` 必失败 → 直接跑套件会"整体崩"。
  `$env:PYTHONPATH = "<repo>\docs\reports"`（加载 `sitecustomize.py`）即可；
  工作区外项目（如 view）用同一招。
- venv 解释器：`C:\Users\yianyao\.workbuddy\binaries\python\envs\default\Scripts\python.exe`
- 无 PyYAML 解释器：`…\versions\3.13.12\python.exe`
- 提交信息用 `-F 文件`（别用 PowerShell here-string —— 本轮又踩了一次）
- 推送：`git -c http.proxy=http://172.16.20.27:12603 push`
- meta 库：`topics_meta.db`（含 `sessions_noise`）/`suggestions_meta.db`/
  `artifacts_meta.db`/`keywords_meta.db`；备份 `topics_meta.db.bak-*`

## §3 已完成 / 未完成 / 明确不做

- **已完成**：主题语义梳理（126→13）、`topic merge/rename/delete/keywords/export/
  dedupe/turns`、`topic-consolidate`（出包/执行/零散登记/分诊）、`chain-audit`
  （引文逐字门 + 锚点语义门）、`noisetriage`、`topicexport`、多链 API、
  view 多链渲染与人读形态。
- **未完成**：§0 表里的三件；`docs/reports/` 下的主题梳理 / 关键词定稿 / 分诊出 plan 三类脚本属
  **一次性数据操作**（通用形态已进 `topic-consolidate`，不必再工具化）。
- **明确不做**（用户裁决）：不改 `D:\Data\AI\Skills\`（路线图 R1–R6 由用户那边开工）；
  工具不调用任何 Agent/模型（确定性、离线是设计特性，语义判断收敛到"Agent 读包填
  plan"这一个点）；不自动登记任何"疑似零散"（分诊精度约 2/3）。

## §4 已知坑（本轮新增，勿重踩）

1. **交接写进会话工作区 = 没写**（全局记忆 §六 有完整根因与三条纪律）。
2. **PowerShell 承载含引号的代码 = 必坏**（本轮修 format 字符串时又中招）：
   改用 `write`/`edit` 工具写文件，别用 `-replace` 拼 Python 代码。
3. **锚点语义门的启发式边界**：note 写"跨会话关系"（同日重发/跨端复问）时不与
   本回合有交集 → 已排除；该门默认**不影响退出码**（要判失败加 `--strict`）。
4. **字符 bigram 连贯度在中文短句上区分力弱**（相关的"方差/标准差"追问实测 0.067）
   → 只能作报告信号，不能做判据。
