# session-harvester — AI 会话采集·导出·检索·蒸馏一体化套件

从本机各 AI 工具（Agent harness / IDE 助手 / 网页 Chat）提取全部历史会话，
建成**可全文检索的本地库**，并支撑后续蒸馏（思路过程 / 工具改进 / Harness
踩坑 / 用户画像）。

当前资产：**9 个数据源已实装入库**，`harvester.db` 共 1964 会话 /
62899 消息 / 36647 工具步 / 294 错误（截至 2026-10-09 库快照，随 sync
持续增长；FTS5 中文可检索，sessions 含模型归属列）；另有 6 个桩位
留接口。

依赖边界（诚实声明）：

- **`harvester/` 包本体零第三方依赖**——纯 Python 3.10+ 标准库，单测同
  （unittest）。`python -m harvester ...` / `python -m unittest discover -s tests`
  中的 `python` 指代"你的解释器"。
- **`topic chain` / `chain-validate` 需要 PyYAML**（T3，唯一硬依赖）。设计上
  **不提供降级解析**：块结构静默误读比直接报错危险。缺它时这两条命令与
  45 个相关测试会明确报错——**这是预期行为，不是安装坏了**。
  （`cards validate` 不同：它有降级解析器，无 PyYAML 也可用。）
- ⚠️ **跑测试前先确认解释器有 PyYAML**。本机验证过的解释器：
  `C:\Users\yianyao\.workbuddy\binaries\python\envs\default\Scripts\python.exe`
  （3.13 + PyYAML 6.0.3，**529 例全绿**）。用无 PyYAML 的解释器会得到
  `Ran 469 tests / FAILED (errors=34)`——那 34 例全是 PyYAML 缺失所致。
  自检一行：`python -c "import yaml; print(yaml.__version__)"`。
  **注意**：无 PyYAML 时 chain 相关 HTTP 端点会**静默降级为 404**（`api_topic_chain`
  对读不出的 chain 文档一律跳过）——"环境缺库"与"这个主题真没有 chain"在响应上
  不可区分，排查时先验解释器。
- **`verify/` 采集工具需要 `requests` + `websocket-client`**（登录态直采管
  线，见下文），用独立 venv 运行，不污染包本体。
- 敏感文件（`weblogin_profile/`、`webchat.accounts.json`）与运行产物已列入
  `.gitignore`。

## 快速开始

### A. 已有索引库（最常见）

```bash
python -m harvester search "cookie 登录态" --db harvester.db   # 全文检索（<<>> 高亮）
python -m harvester read 42 --turn 1     # 分层读取：先纲要后下钻（1..N / last / all）
python -m harvester mcp-serve --db harvester.db               # MCP server，任意 agent 直查
```

### B. 从零全流程

```bash
# 0. 固化探测：扫描本机文件系统签名，发现所有 AI 会话数据源 → sources.json
python -m harvester probe

# 1. 查看各数据源探测状态（STUB 类附带登录态/获取指引）
python -m harvester adapters

# 2. 扫描并生成会话纲要（outline.md / outline.json：序号|来源|日期|标题|条数|概要）
python -m harvester scan

# 3. 按纲要序号导出（大类=来源分类，小类=月份；支持区间与逗号组合）
python -m harvester export --select "3,5-9" --out exports
python -m harvester export --all --out exports

# 4. 建全文索引后进入 A（检索是 search/MCP 的前置）
python -m harvester index --all --db harvester.db
```

### B+. 日常增量（推荐）：一键同步

```bash
python -m harvester sync      # 收件箱收割 -> 全量导出 -> 整库重建索引，末尾报"本次新增 N 个会话"
```

- **本地文件源**（workbuddy-transcript / dsh / autoclaw / vscode-copilot）：
  零人工，`sync` 直接扫文件系统增量入库；可挂系统定时任务每天跑。
- **官方导出源**（deepseek-export / chatgpt-export / claude-export）：
  唯一人工动作 = 把导出包（zip 或 json）丢进 `inbox/` 目录。`sync` 让各
  导出适配器竞标认领（结构校验，绝不臆测解析），归档到 `inbox/done/<来源>/`
  并入库；认领失败的文件留在原地报告，绝不静默丢弃。
- 幂等可重跑：导出文件名确定性覆盖，索引整库重建，新增判定=sid 集合差。
- 默认包含 note 消息（思考/工具步骤）——工具步骤藏在 note 的 raw 里，
  排除会让重建后的 steps 表为空、G1/G2/G4 分析失效；确要纯对话加
  `--no-notes`。
- 协议细节与采集/分析的正式接口边界见 **`docs/ADAPTER_CONTRACT.md`**。

导出产物结构：`<大类>/<年-月>/<时间戳>__<来源>__<标题>.{md,json}` +
`export_manifest.json`（清单+桩位状态）。思考过程/工具调用等非对话条目
**默认包含**（note 角色），蒸馏与 2.2/2.3 分析依赖这些证据，勿关闭；
确需纯对话加 `--no-with-notes`。

### B++. 日常增量（推荐）：update

```bash
python -m harvester update    # 收件箱收割 -> 只导出新增/变更会话 -> 整库重建索引
```

- **增量口径**（2026-10-09 用户裁决）：不全量重取历史轨迹，只获取未获取的
  ——新 sid 导出；`updated_at` 变化（续聊）重导该会话；其余跳过。
- **库即水位**：高水位 = 索引库 `sessions(sid, updated_at)`，无独立状态
  文件；首跑无库 = 天然全量。
- `sync` 保持全量语义，作为对账/rebuild 基线（怀疑漏数据时跑一次 sync 对账）。

### 3. 整理（每次采集之后跑一遍）

采集只是入库；**入库之后要"整理"**：把新会话语义聚合成主题、把零散不成系统的
挑出去。这一步是固定流程，不是一次性动作：

```bash
# ① 出「梳理包」：主题信号表 + 零散会话候选 + 待填的执行模板
python -m harvester topic-consolidate --meta topics_meta.db --db harvester.db \
    --chain-root ~/.workbuddy/knowledge/topics --plan-out docs/reports/consolidate-packet.md
# ② 由人/Agent 填 plan.yaml（哪些并成一个主题、哪些舍弃、哪些登记为零散）
# ③ 先 dry-run 校验，再加 --yes 执行
python -m harvester topic-consolidate --meta topics_meta.db --apply plan.yaml
python -m harvester topic-consolidate --meta topics_meta.db --apply plan.yaml --yes \
    --snap-dir docs/reports/deleted-topics
python -m harvester topic-consolidate --meta topics_meta.db --noise-list
python -m harvester topic-consolidate --meta topics_meta.db --db harvester.db \
    --triage-out docs/reports/triage-report.md   # 零散分诊：取信息 vs 整合信息
python -m harvester topic-consolidate --meta topics_meta.db --db harvester.db \
    --triage-json docs/reports/triage-full.json  # 分诊**全量** JSON（填 plan 用这个）
python -m harvester topic-consolidate --meta topics_meta.db --db harvester.db \
    --triage-brief topic_hint --triage-brief-out docs/reports/brief.txt  # 一行一条，不截断
# ④ 分诊 JSON + 逐轮判断 → plan 草稿（机械映射与校验在工具里，见下）
python -m harvester topic-consolidate --meta topics_meta.db \
    --plan-seed docs/reports/triage-full.json --judgment docs/reports/judgment.yaml \
    --craft-topic new:M1 --require-covered noise_high \
    --seed-out plan.yaml
```

### 3.1 分诊 → plan：机械部分在工具里，判断在文件里（v0.35）

**为什么有这一步**：v0.33/v0.34 两轮都把「读分诊 → 写 plan」写成了
`docs/reports/make-plan-*.py` 一次性脚本，而它**每轮采集/起草都要重跑**。按项目
铁律，机械部分进了 `harvester/planseed.py`；`docs/reports/` 只留**逐轮的判断文件**。

- `--triage-brief [判定类]`：一行一条（`verdict / sid / 日期 / 命中主题 / 首条原文`），
  **不截断条数**（人读报告每类封顶 60 条）；加 `--triage-brief-out` 落盘。
- `--plan-seed <triage.json>`：把 `topic_hint` 的机械命中映射成主题 **id**、
  把 `craft_material` 整类归位（`--craft-topic tp-x` 或 `new:KEY`）、把未 assign 的
  主题列进 `keep`（完整性），并按判定类打出**覆盖统计**。
  `--require-covered noise_high` 表示"这一类必须逐条归置完，否则退出码 2"。
- `--judgment <yaml>`：**逐轮判断**，就是原来一次性脚本里那些清单的形态：

  ```yaml
  version: 1
  new_topics:                        # 要新建的主题（可选）
    - {key: M1, name: 创作素材与背景检索, keywords: [描写, 用词]}
  craft_topic: new:M1                # craft_material 整类归这里
  overrides:                         # 逐条改判：sid → tp-xxx / new:KEY
    qianwen-raw:abc: tp-20261008-010
  noise: [yuanbao-raw:xyz]           # 登记零散（**必须显式列出**，不自动登记）
  skip: [{sid: yuanbao-raw:def, why: 语义两可}]   # 本轮不动（与"忘了"区分开）
  ```

- **fail loud 而非静默**：judgment 里出现分诊结果里没有的 sid、指向不存在的主题、
  或同一 sid 既改判又登记零散 → 报错退出 2；`topic_hint` 命中的主题名已被改名/
  删除 → 报错（不丢行）。
- **覆盖统计里 `unhandled` 是要看见的**：缺省不动的 `substantive` / `noise_maybe`
  会显式计数（未处理 ≠ 通过）。
- 产出的 plan **保证能过 `validate_plan`**（完整性 + 重复检查），可直接
  `--apply`（缺省 dry-run）。
- 迁移等价性已在真库上验证：新工具**逐条复现**了 v0.33（assign 272 / noise 19 /
  skip 1）与 v0.34（assign 28 / keep 12）两份已落库 plan，随后删除了那三个一次性
  脚本；两轮的判断固化为 `docs/reports/judgment-v033.yaml`、`judgment-v034.yaml`。

- **零散分诊（取信息 vs 整合信息）**：按「只要求查询（是什么/含义/翻译/出处/
  推荐…）、没提分析提炼整理归纳」判"这轮对话只为取信息"。**实测精度不足以自动
  落库**：真库 853 条里高置信 41 条，其中仍有约 1/3 是**创作素材检索**
  （"轿车外壳面板名称""交通锥与围栏材质区别"——为小说找料），故另立
  `craft_material` 一类；多轮"前后不连贯"因字符 bigram 在中文短句上区分力弱
  （相关追问也可能零重叠），只作**报告信号**，不进高置信。分诊**只产判定与
  理由，登记与否由 plan 决定**。
- **分诊有两个人读/机读出口，别混用**（v0.33）：`--triage-out` 是人读报告，
  **每类最多 60 条**（`…另有 N 条`）；`--triage-json` 是**全量**（含 `first_user`
  首条原文 200 字符，供复核机械关键词提示）。**填 plan 必须用 JSON** —— 照人读
  报告填，尾部条目会静默漏掉且不报错。
- **`assign`：把新会话并入已有主题**（采集后的主路径）。plan 里写
  `assign: [{target: tp-x, sids: [...], evidence: "口径"}]`；校验会挡住
  「已是别的主题成员」「既登记零散又并入主题」两种自相矛盾。
- **零散候选的机械规则**需**三条同时成立**：user 回合 = 1 且首条正文 < 阈值
  字符（`--noise-max-chars`）、不属于任何主题、**steps 0 步**。第三条是必须的
  ——只上"短提问"会把「Skill编写规范提炼」（11 字符却是真工作）误判成噪声。
- **关键词要人工定稿**：合并会把各源的自动聚类关键词并进来（实测小说主题
  并成 237 个，大量是标题 bigram 碎片"织的/与现/随嗞"）。用
  `topic keywords --id <tp> --keywords "k1,k2"` 覆盖式定稿（去噪是语义判断，
  工具不代做）。全部 13 个主题定稿后合计 467 → 65 个关键词。
- **文件级原子**：执行全程在 meta 库的临时副本上，全部成功才换入 ——
  中途任何异常，真库逐字节不变（v0.25 那次"合并落库后脚本崩"的教训）。
- **完整性校验**：库内每个主题必须出现在 plan 的 target/from/discard/keep/
  renames 之一，否则拒绝执行（防"漏掉一个悄悄留着"）。
- **零散会话只登记、不删除**：写 meta 库的 `sessions_noise` 表
  （`--noise-list` 可查）；`harvester.db` 始终只读。
- **登记了就真的会被用上**：`topic-candidates` 产候选时排除
  「已注册主题成员 ∪ 已登记零散会话」；`keywords` 在给了 `--topics-meta`
  时同样排除零散会话（报告头 `noise_excluded` / `noise_msgs_excluded`
  给出计数）。**没登记过则行为与接线前完全一致**（缺省不过滤）。

### 4. 主题注册（T 轨）：发现簇 → 注册 → 页面出现

主题**不是固定清单**：由聚合数据中发现簇后注册进 meta 库，注册即出现在
view「主题」tab；代码零写死主题名。命令：

```bash
python -m harvester topic-candidates --db harvester.db --out docs/reports/   # 聚类推荐候选簇（只产候选，不改注册表）
python -m harvester topic register --meta topics_meta.db --name "主题名" --keywords "k1,k2"   # 认可后注册
python -m harvester topic add --meta topics_meta.db --id <tp-id> --sids "<sid1>,<sid2>" --evidence "出处"   # 挂成员
python -m harvester topic merge --meta topics_meta.db --id <目标> --from "<源1>,<源2>"   # 并成一个主题（成员/关键词并入后删源）
python -m harvester topic rename --meta topics_meta.db --id <tp-id> --name "新名"        # 改名（保 id，已发布 chain 不受影响）
python -m harvester topic delete --meta topics_meta.db --id <tp-id> --out <快照.json>    # 删除（先落快照，可回滚）
python -m harvester topic keywords --meta topics_meta.db --id <tp-id> --keywords "k1,k2" # 关键词定稿（覆盖式；去噪是语义判断）
python -m harvester topic dedupe --meta topics_meta.db --db harvester.db --id <tp-id> [--out f]  # 会话级去重（H40 口径；chain 必填口径来源）
python -m harvester topic turns --meta topics_meta.db --db harvester.db --id <tp-id> [--full] [--cap N]  # 回合索引 / raw 全文（锚点与逐字引文来源）
python -m harvester topic export --meta topics_meta.db --db harvester.db --id <tp-id> \
    --chain-root ~/.workbuddy/knowledge/topics --out docs/reports/topic-<tp-id>.json      # 给 Agent 的结构化导出
python -m harvester topic md --meta topics_meta.db --db harvester.db --id <tp-id> \
    --chain-root ~/.workbuddy/knowledge/topics --out docs/reports/topic-<tp-id>.md        # 给人读的一页速览
python -m harvester chain-validate "C:/.../chain-长文.md"   # 主题 chain 长文独立校验（members/stages/nodes）
python -m harvester chain-audit "C:/.../chain-长文.md" --db harvester.db   # chain 内容审计（引文逐字 + 锚点语义 + 证据覆盖）
python -m harvester chain-audit "C:/.../chain-长文.md" --db harvester.db --quotes-ascii --list-limit 0   # 连 ASCII 引号一起核、明细不限条数
```

### 4.1 chain 内容审计（写完 chain 必跑）

`chain-validate` 只管**结构**（sid 在成员内、turn 是否越界），**管不了引文真伪，
也管不了"这个 turn 是否真说了这句 note"，更不管"有多少成员根本没被引到"**。
`chain-audit` 补三道内容门：

- **引文逐字门**（硬门）：正文所有 `「」` 必须能在成员会话 `raw` 或标题里逐字找到
  （自动归一空白与 Markdown 加粗标记；`……` 多段省略引用逐段比对）。**未命中即
  退出码 1**。实测价值：起草 Agent 曾把检索列 bigram 当原文、引文经"还原"后并非
  逐字；也有把原话压缩改写的（漏掉半句）。
  **空转会显式报出**：若正文 `「」` 为 0 条而 ASCII `"…"` 引用不为 0，报告写
  `本门空转`——"0 未命中"不等于"已核过"。加 `--quotes-ascii` 把 ASCII 引号
  一起逐字核（同样计入硬门）；引文按**成对**扫描，短引用不会让后续引用错位。
  （实测：真库首条 chain 写于规范 §5 之前，212 个 ASCII 引号从未被核过，
  补核后清掉 39 处以引号承载的自造术语 + 4 处不逐字的引文。）
- **锚点语义门**（启发式）：逐节点把 `note` 与该回合 `raw`（**含会话标题**）
  并排列出，并对 **note 的 CJK 2-gram 与原文零重叠**的节点告警。实测价值：
  抓到过"整条挂错 sid/turn"（note 写"王德荣与沈望的剧组旧交"，而该 turn 在讲
  "锚点的心理学依据"）。**已知误报类别**：note 写的是**跨会话关系**（"同日第三处
  重发""同一疑问跨端复问"）时本就不与本回合有交集——这类已排除；note 写**标题级**
  依据（如"肯定性标题：节奏把控佳"）也不算错配。锚点门默认**不影响退出码**
  （要它判失败加 `--strict`）。
- **证据覆盖**（v0.45，informational）：量出**有多少成员根本没有 turn 级锚点**
  （只有标题级），并把"无锚点的**重复会话**"与"无锚点的**独立代表**"分开——
  后者才是真缺口（H40：锚点不迁移，重复会话无须各自挂）。同时做
  **正文锚点 ↔ frontmatter 双向对账**：正文引了却未登记的 `{sid, turn}`
  （view 里不可点、覆盖统计也漏）与登记了却在正文用不到的节点都会列出；
  简写 `（同会话 turn N）` 按**同一行最近的前置完整锚点**归属。
  实测价值：真库首条 chain 55 成员里只有 27 个有锚点，且 10 个正文锚点没进
  frontmatter（30→52 节点、覆盖 27/55 → 39/55，剩下 8 个独立代表逐条标注为标题级）。
  `--list-limit 0` 看全量明细（缺省 20 条，超出会显式印"另有 N 条"）。

### 5. 主题结构化导出（给 Agent 用）

`topic export` 产出 `harvester.topic/1` schema 的 JSON：主题是什么（id/名称/
关键词/成员数）、时间跨度与月度分布、**成员逐条**（sid/来源/标题/时间/证据尾注，
已入索引库的在前）、该主题的**链清单与锚点合并视图**（一个主题可有多条 chain）、
来源分布、`health` 自检（不在索引库的成员、被登记为零散的成员）、以及
`howto`（可复制的 pack/fine/chain-validate 命令，**只给文本不执行**）。

同库快照重跑，除 `generated_at` 外逐字节一致（可复现红线）。

### 5.1 主题人读那一半（`topic md`，一页速览）

`topic md` 复用同一个 bundle，渲染成**一页**回答四件事：**是什么**（关键词/成员数/
来源/健康）、**跨多久**（首末时间 + 月度条形）、**关键转折**（已发布 chain 的
frontmatter 锚点：阶段/跨度/锚点数）、**结论与未决**（chain 正文的「元结论」
「待补与限制」小节，单节超 1200 字符截断并指向全文）。

两条纪律（都有会红的测试）：

- **不列成员**。成员逐条在 `topic export` 的 JSON 里；人读那页列出来，大主题就是
  几百行，"一页"没了。
- **不臆造结论**。关键转折/结论/未决只从已发布 chain 来，取不到就空着，并在页面上
  **分开**写「未执行」（没传 `--chain-root`，没去读）与「尚未生成」（读了，确实
  还没有）——混同会让人以为主题没内容。**不用成员标题凑内容**。

- 注册库默认读 `topics_meta.db`（与 harvester.db 同目录）；**view 的
  「主题」tab 需要 api-serve 启动时带 `--topics-meta <topics_meta.db>`**
  （`start.cmd` 已内置），未配置时该 tab 空表并提示 hint，不炸。
- `topic merge` 只做确定性的并集（成员按 sid 去重、证据带「合并自 <源>」
  尾注、关键词并集），**不做语义判断也不给关键词去噪**——自动聚类候选的
  关键词常含标题 bigram 碎片，合并后需人工定稿关键词。
- `topic rename` **保 id**：主题改名后，已发布 chain 的 `frontmatter.topic_id`
  仍然对得上，不需要重发产物。`topic delete` 先落快照再删，快照含
  名称/关键词/成员，可直接作为回滚依据。
- **一个主题可以有多条 chain**（`/api/topic/<id>/chain` 新增 `chains[]` 与
  `chain_count`，旧字段仍是第一条；`/api/topics` 带 `chains_count`）。
  多链时 chain 的显示名取正文首个 `#` 标题，否则同主题各链会显示成同一个名字。
  view「主题」tab 据此显示**每条主题有几条链**（链列），多链时可切换查看；
  0 成员类目灰显（那是尚未挂成员的种子类目）。`keywords` 在 API 里是 JSON
  字符串（给机器用），页面渲染成人读的「、」列表。
- 批量注册场景（117 簇级别）参考 `scripts/register_candidates_20261009.py`
  ——解析候选报告后逐簇调 `topics.register_topic` + `add_members`。
  语义梳理（把碎片并回类目）**不要照抄一次性脚本**：走
  `topic-consolidate` 的梳理包 → plan（`groups`/`discard`/`keep`）→ `--apply`，
  完整性校验（每个主题必须归位）已内建；逐条归位散会话用
  `--triage-json` + `--plan-seed`（v0.35，见 §3.1）。
  （历史的一次性梳理脚本已按"能力进工具本体"的要求删除：其做法——库内每个主题
  必须归位、否则拒绝执行——已内建为上述 plan 的完整性校验，口径见
  `docs/HANDOFF-v0.22-next.md` 台账 H55/H58。）

### 5.2 卡片校验与主题注册表打通（V3，v0.43）

> 缺口原文（HANDOFF-v0.24 §7.1 V3）：「当前只有 chain（叙事复盘）一种产物；
> **卡片校验与主题未打通**」。打通前：卡片锚点指着会话、会话在主题注册表里
> 有没有归属，卡片侧一无所知——`cards validate` 永远说不出"这张卡属于哪个主题"。

判定口径（`--topics-meta` 给了才执行；**缺省不带 → 结果、报告、退出码与旧版
逐字一致，结果里不会多出任何字段**）：

| 情形 | 判定 |
|---|---|
| 未声明主题，锚点会话已在某主题 | **警告**：报出主题 `id（名称）`，提示可在 frontmatter 补 `topic_id` |
| 未声明主题，锚点未登记任何主题/零散 | **警告**：卡片与注册表暂无关联可核对 |
| 未声明主题，锚点为**已登记零散**会话 | **警告**（另一句）：零散是"已裁决不进主题"，与"漏归主题"是两回事 |
| 声明 `topic_id`（或 `topic`），锚点确为该主题成员 | 通过（结果带 `topics`） |
| 声明主题，但锚点会话不是该主题成员 | **错误**：卡片挂的会话与卡片声称的主题不一致 |
| 声明主题，但该主题不在注册表 | **错误**：卡片指了一个不存在的主题 |
| 声明主题，但卡片没有 anchors | **错误**：无法核对（不静默通过） |

一致性只**报告**不自动修正（改卡还是改注册表是语义裁决）；`topic_id` **不是**
必填字段——§8 冻结字段清单未含它，强制必填会让既有卡全红。

两条实测踩到的坑（都有会红的测试钉住）：

- **sid 双形态**：注册表成员一律用 `sessions.sid`（带源前缀，如
  `workbuddy-transcript:xxx`），而卡片锚点可能是裸 `sessions.session_id`
  （`cards new` 脚手架取的就是它）。只做精确比对会**静默漏判**——真实卡
  `kc-20261006-0003/0004` 的锚点其实都在主题成员里却被判"未登记"。
  给了 `--db` 时会把每个成员的两种写法都登记为同一主题的键（只读，
  不写任何库），报告里以「另有 N 个 session_id 别名键」显式计数。
- **fail loud**：`--topics-meta` 指了读不出来的库（不存在／不是 SQLite／
  缺 `topics` 表／`members` 不是合法 JSON）→ 抛错、CLI 退出码 2；
  **绝不静默返回空索引**（空注册表与不可读注册表必须能分开，否则一次
  没执行的检查会被伪装成通过）。

给机器的那一半：`validate_cards(..., topics_meta=...)` 的每张卡片结果
additive 多出 `declared_topic_id` 与 `topics`（`[{id, name}]`），summary 多出
`topic_reviewed` / `topics` / `topic_member_rows` / `topic_alias_keys` /
`topic_anchors_checked` / `topic_anchor_hits` / `topic_anchor_noise` /
`topic_undeclared_cards`（对账：命中 + 零散 + 未关联 = 核对过的锚点数）。
给人那一半：报告里多一行「主题核对（`--topics-meta`）…」；没给时写
**「主题核对：未执行」**——不许让人以为已经核对过主题。

**未做**（明确不做，本轮不替人定口径）：`/api/cards` 的 additive 端点字段
（要给每张卡带主题 id/名，得先定 api-serve 的 `--topics-meta` 拼接口径），
以及 `cards new --topics-meta`（起卡时提示该会话属哪个主题——"要不要让机器
替人猜主题"是语义裁决，先不猜）。

## 全命令速查表

> `python -m harvester <命令> --help` 看完整参数。默认都读当前目录
> `harvester.db`；分析口径统一用 `messages.raw`。

### 采集与同步

| 命令 | 用途 |
|---|---|
| `probe` | 固化探测：扫描本机数据源签名并生成 sources.json |
| `adapters` | 显示各数据源探测状态（OK/STUB/MISSING） |
| `scan` | 扫描数据源并生成可导出会话纲要 |
| `export` | 按纲要序号导出会话（`--select`/`--all`） |
| `sync` | 一键同步（全量）：收件箱收割 → 导出 → 重建索引，幂等；对账/rebuild 基线 |
| `update` | 一键同步（增量）：只导出新 sid/updated_at 变化的会话；日常更新用这条 |
| `weblogin check` | 探测各网页 Chat 产品浏览器登录态 |
| `weblogin init-config` | 生成账号密码配置模板（预留接口） |
| `weblogin prepare <产品>` | 打开自动化浏览器完成人工登录 |

### 索引与检索

| 命令 | 用途 |
|---|---|
| `index` | 构建 FTS5 全文索引 |
| `search` | 全文检索历史会话 |
| `read` | 按纲要序号读会话（`--turn` 下钻） |
| `pack` | 产出跨 agent 上下文交接包 |
| `mcp-serve` | MCP stdio server：Agent 直查历史 + 主题/链/建议/卡片/产物（10 工具） |

### 分析报告（消费索引库）

| 命令 | 用途 |
|---|---|
| `report-tools` | 工具调用/失败率统计 |
| `report-errors` | 错误三分类报告（含 数据源×model×类别 交叉表；v0.23 追加 **活跃 skill×数据源×类别** 交叉表，API 字段 `cross_skill`） |
| `report-chains` | 工具链失败翼报告（长回合/连击/循环/空转） |
| `report-traces` | OTel trace 工具统计（耗时/失败率/取消） |
| `report-skill` | Skill 行为画像（G4）：按 skill 聚合调用/行为链 |
| `report-skill-join` | skill 进化 join：chain 锚点 × skill 调用锚点交叉表（T4）。`--margin-days` **只向后放宽**；落在 stage 结束后的调用标 `_after`（`after_stage=True`），与阶段内调用分开 |
| `keywords` | n-gram 关键词统计（只统计 messages.raw；落 keywords_meta.db）。**排序口径 doc_freq 优先**（= 含该词的消息条数），freq 为出现总次数。默认启用随包通用停用词表（`harvester/data/stopwords_zh.txt`，只含通用虚词）；人名/专名请用 `--stopwords PATH` 叠加私有表（可多次），`--no-stopwords` 关闭过滤。**ASCII 段按整词统计**（英文切片片段已消除）；CJK 段为 2/3 字滑窗，跨词边界的片段（如「上的」）属无分词器的固有代价。`--keep-runs N` + `--vacuum` 在统计后顺手回收旧 run |
| `keywords-gc` | **回收 keywords_meta.db**（v0.45）：`keyword_stats` 是**累积表**（每跑一次 keywords 追加一整份 n-gram 表），不回收就随运行次数线性膨胀。`--keep-runs N`（缺省 1）只留最近 N 次 run 并**按 run 删净**统计行（不留孤儿），`--vacuum` 确有删除时回收磁盘。真库实测：4 次 run / 435,583 行 → 40.2 MB，`--keep-runs 1 --vacuum` 后 **18.4 MB**（其中 22 MB 是**空闲页**，只有 VACUUM 能回收） |
| `suggest-agents` | 从错误模式生成 AGENTS.md 候选条目（建议池，不直改）。`--coverage` 追加**建议池 ↔ 台账**覆盖核对（v0.45）：列出`待裁决`与`台账陈旧`（建议句已不在池里）两类。真库实测：建议 8 条 / 台账 8 条看着对得上，实际**已裁决 6、待裁决 2、陈旧 2**——"数字相等"是巧合 |
| `suggest-status` | 建议池状态落库（pending/adopted/rejected） |
| `export-analysis` | 统一分析导出器：sessions/tools/errors/skills/triage × md/JSON 同源（view 导出按钮走这条） |

### 蒸馏与知识库

| 命令 | 用途 |
|---|---|
| `aggregate` | 聚合会话为语料（蒸馏喂料） |
| `triage` | 蒸馏队列：新错误 pattern/旧坑重现/Skill 行为链/高信号会话 |
| `draft` | 蒸馏包：会话原文+卡片规范+指令 → 自包含 md 喂 Agent |
| `artifacts` | 产物提取：Write/Edit args 回源 → artifacts_meta.db |
| `cards validate` | 校验卡片目录（§8 规范 + 锚点真实命中索引库）；可选 `--topics-meta topics_meta.db` 追加**卡 ↔ 主题一致性核对**（见「卡片校验与主题注册表打通」） |
| `cards new` | 从索引库会话生成卡片脚手架（evidence 留白） |
| `kb-init` | 建知识库骨架（幂等） |
| `kb-stats` | 知识库盘点 |

### 主题注册（T 轨）

| 命令 | 用途 |
|---|---|
| `topic-candidates` | 自动聚类候选推荐：只产候选簇报告，不改注册表 |
| `topic-consolidate` | 主题梳理流水线：出梳理包（信号表/零散候选/模板）→ 执行人填的 plan（完整性校验 + 文件级原子 + 快照）→ 查零散登记；分诊三出口（人读报告 `--triage-out` / 全量 JSON `--triage-json` / 一行一条简报 `--triage-brief`）；**`--plan-seed` 把分诊 JSON + `--judgment` 判断文件变成可执行的 plan 草稿**（机械映射与校验在工具里，判断不落代码） |
| `topic register/add/remove/merge/rename/delete/list/show/…` | 主题注册表：认可候选后注册进 topics_meta.db（注册即出现在 view 主题 tab）；`merge` 把多个主题并成一个（成员去重 + 证据带来源尾注 + 删源）；`rename` 保 id；`delete` 带快照；`export` 给机器（topic.json）、`md` 给人（一页速览，四问：是什么/跨多久/关键转折/结论与未决） |
| `chain-validate` | topic-chain 长文独立校验（frontmatter + 锚点可回溯） |

### 服务

| 命令 | 用途 |
|---|---|
| `api-serve` | 只读 HTTP JSON API（默认 127.0.0.1:8765；非回环 host 必须 --token；`--topics-meta/--cards-root/...` 启用对应端点） |
| `deadcode-scan` | 死代码扫描（AST）：未用 import / 未被引用函数 / 未被引用常量。名字引用统计覆盖 `harvester`+`tests`+`harvester-view`（H48：只扫 `harvester/` 会把测试用到的 API 误判成死函数），默认只对 `harvester/` 报发现；缺省**只提示**，加 `--fail-on-found` 才判失败 |
| `regress` | 端到端回归语料：**从空库**跑 分诊（含 `--triage-deep`）→ `--plan-seed` → `apply`（dry-run + 真写）→ `topic export`/`md` → H87 跨进程确定性，逐项核对关键数字。全程只用临时库（造自 `indexing.SCHEMA` + `index_session`），**真库一字节不动**；`--out` 人读 / `--json` 机读；退出码 0 通过 / 1 失败 / 3 未执行 |

## 死代码扫描（每轮收尾的固定动作）

```bash
python -m harvester deadcode-scan                  # 提示（退出码恒 0）
python -m harvester deadcode-scan --fail-on-found   # 当门用（有发现即退出码 1）
python -m harvester deadcode-scan --root harvester --out docs/reports/deadcode.md
```

口径与豁免：

- **未用 import**：模块内出现过的名字（属性根名 `json.dumps` 的 `json`、字符串字面量
  里的词）都没有 → 报了基本就是真没用；
- **未被引用函数 / 常量**：名字在**全仓文本**里只出现 1 次（定义处）→ 可能是死，
  也可能是给别人用的公开 API，故只提示。**不用调用图**的理由：CLI 的 `cmd_*` 查表、
  `argparse` 的 `func=`、框架回调会让调用图大面积误报，文本口径宁可漏也不误删；
- **类方法不算**（可能是父类接口覆盖）；
- **行内带 `noqa` 即视为有意保留**：真实案例是"import 只为探测可用性"
  （`from compression import zstd as _z  # noqa: F401`）——那种 import 名字本就不用，
  是 flake8 的既有约定。**要保留死代码，就在该行写 `# noqa` 并说明理由**。

套件里有一条 `test_harvester_package_has_no_dead_code`（`harvester/` 必须 0 条），
所以这道例行检查不靠人记得跑。

## 端到端回归语料（regress，改链路前后都该跑）

```bash
python -m harvester regress                                    # 跑一遍，打印人读报告
python -m harvester regress --out docs/reports/regress.md      # 人读报告落盘
python -m harvester regress --json docs/reports/regress.json   # 机读结果（下游是程序）
python -m harvester regress --keep-temp --temp-base .          # 留临时库供人工翻查
python -m harvester regress --corpus drift                     # 故意漂移语料：实测「会红」
```

**为什么有它**：`scan`→`triage`→`--plan-seed`→`topic-consolidate --apply`→
`topic export` 每一步都有自己的单测，但**没有一条"从空库跑完整条链路、把中间数字
与产物都核对一遍"的端到端回归**。项目里有大量"改了 A 结果 B 悄悄变了"的历史
（H83 双出口漏条、H87 跨进程不确定），这类问题出在**步骤之间**，各自全绿的单测
挡不住。

跑哪几步、每步断言什么：

| 步 | 断言 |
|---|---|
| `corpus` | 临时库建成：3 个固定 id 主题、12 条会话、各会话 user 回合数与语料自述一致、预置成员就位 |
| `triage` | 浅分诊各判定类计数逐类对账；深分诊只多出 `deep_*` 两类且浅层逐 sid 判定不变（v0.39/v0.42 口径）；并列关键词会话的判定与归属被钉住 |
| `plan_seed` | 覆盖统计 `_total`、assign 逐主题成员、noise、显式 skip 不进 unhandled、keep 覆盖剩余主题 |
| `apply` | plan 过 `validate_plan`；dry-run 通过且**不写库**；真写库后成员数/主题数/零散登记逐项一致 |
| `export` | `topic export` 的 `members_count` 与 meta 一致、`health` 为 0、JSON 可解析；`topic md` 非空且含主题名；产物落盘非空 |
| `determinism` | **H87**：不同 `PYTHONHASHSEED` 的独立进程跑同一 meta，关键词次序与分诊结果必须逐 sid 相同 |

两条口径：

- **绝不碰真库**：全程在 `tempfile` 里用语料造临时索引库（`indexing.SCHEMA` +
  `index_session`，与 `tests/test_v30_noisetriage.py` 同一套夹具构造方式）与临时
  meta 库，跑完即删；真 `harvester.db` / `topics_meta.db` **一字节不动**。所以它
  **没有** `--db` / `--meta` 参数——这是故意留白的，不给"顺手指向真库"的后门
  （套件里有一条测试钉住这两个参数不许出现）。
- **人读/机读分出口**：`--out` 给人（表格化、逐条列期望值 vs 实测值），`--json`
  给程序（结构稳定、`sort_keys`）。**下游是程序就别给人读那份**。

退出码：`0` 全通过 / `1` 有断言失败 / `3` **未执行**（前置缺失，例如缺 PyYAML
——**未执行 ≠ 通过**，调用方据此 skip 而不是当绿灯）/ `2` 用法错误。
与 `harvester-view/tests/check_real_payload.py` 的既有约定一致。

明确没有覆盖：夹具里关键词都是 2 字，**"长关键词优先"那条规则没被测到**；
链路只到 `topic export`/`topic md`（`chain-audit` 仍只有自己的单测）；
不测性能；真库的"删过主题/跳号"那类状态不在范围内（详见
`harvester/regress.py` 模块 docstring）。

## 沙箱环境适配（DSH 沙箱专用，**可选**）

在 DSH 沙箱里跑套件前需要它，普通机器上不需要：

```powershell
$env:PYTHONPATH = "<repo>\scripts\sandbox"   # 加载 sitecustomize.py
& $venv -X utf8 -m unittest discover -s tests
```

- **它解决什么**：本会话沙箱把 `os.mkdir(path, 0o700)` 建出的目录 provision 成
  **连本进程都写不进去**的形态（`PermissionError: Errno 13`），而 `tempfile.mkdtemp()`
  用 0o700、`mkstemp()` 用 0o600 → **任何基于 tempfile 的测试都会失败**（与项目代码
  无关，见台账 H53）。`scripts/sandbox/sitecustomize.py` 只是把传给 `os.mkdir`/`os.open`
  的 mode 补上组/其他位，解释器启动时自动导入。
- **为什么它在 `scripts/sandbox/` 而不是 `docs/reports/`**：它一度放在
  `docs/reports/`（gitignore 目录），结果**全量套件的前置条件在新 clone 上不存在**，
  文档却按"它就是有"来教人跑测试。环境适配件也要进版本库（v0.38）。
- 对**工作区外的项目**（如兄弟仓库 `harvester-view`）同样有效——它不要求写入目标目录。

## 套件结构

```text
                     ┌─ 采集管线（verify/，见「网页 Chat 直采」节）
                     │   元宝/千问/豆包 登录态直采 → corpus/*_raw/
                     ▼
采集（probe）──► 纲要（scan）──► 选择导出（export）──► 检索/读取/交接（index/search/read/pack）
   discovery.py      outline.py        exporter.py         indexing.py / reader.py / pack.py
        │
        └──► 聚合语料（aggregate）──► 知识库（kb-*）──► MCP（mcp-serve，agent 运行时直查）
                distill.py              distill.py        mcpserver.py

语义归类/增补按 docs/DISTILL_PLAYBOOK.md 执行（宿主无关；WorkBuddy 的 skill
`session-knowledge-distill` 仅为该 playbook 的薄绑定）。
```

| 模块 | 文件 | 职责 |
|---|---|---|
| 发现 | `discovery.py` | 文件系统签名扫描，产出 sources.json |
| 采集 | `adapters/` | 各数据源 Adapter（实装 11 个 + 桩位 6 个） |
| 直采管线 | `verify/` | 元宝/千问/豆包登录态采集器 + schema 探针（venv 依赖） |
| 导出 | `exporter.py` | 按来源大类/月份小类落盘（md+json） |
| 检索 | `indexing.py` | FTS5 全文索引与查询（零依赖） |
| 读取 | `reader.py` | 分层读取：read → --turn 逐回合下钻 |
| 交接 | `pack.py` | token 预算内的跨 agent 上下文交接包 |
| MCP | `mcpserver.py` | stdio MCP server（10 工具）：任意 agent 运行时直查历史与进化数据 |
| 诊断 | `toolstats.py` / `errstats.py` | 工具失败率/重试放弃；错误三分类+位置分桶（均含按 Agent/数据源分组） |
| 行为画像 | `behstats.py` | report-skill：按 skill 聚合调用/触发任务/调用后行为链（G4 确定性主干） |
| 建议闭环 | `agent_suggest.py` | AGENTS.md 候选条目生成（建议池，人工并入） |
| 卡片库 | `cards.py` | §8 frontmatter 校验（validate）+ 会话起卡脚手架（new）+ **卡↔主题一致性核对**（`--topics-meta`，只读，缺省不启用）；工作流见 docs/CARD_WORKFLOW.md |
| 蒸馏队列 | `triage.py` | triage：新会话确定性初筛排队（新错误pattern/旧坑重现/Skill行为链/高信号会话，T1） |
| 蒸馏包 | `drafting.py` | draft：会话原文+卡片规范+指令 → 自包含 md 喂任意 Agent 起草（T2 确定性一半；草稿落 cards_pending/，validate 照跑，并入人工） |
| 蒸馏 | `distill.py` | 语料聚合、知识库骨架、盘点（机械部分） |
| 语义提炼 | skill `session-knowledge-distill` | 通读语料后的归类、话题增补、反馈台账（agent 步骤） |

## 当前数据源支持状态

| 状态 | 数据源 | 说明 |
|---|---|---|
| ✅ OK | WorkBuddy 会话轨迹（v0.6） | `~/.workbuddy/projects/*/*.jsonl`（约 268MB 真实逐回合轨迹，含子代理）；`<user_query>` 提取真实发言、harness 注入上下文与思考/工具调用均独立为 note |
| ✅ OK | DSH / DeepSeek Harness（v0.6） | `~/.dsh/sessions/*/*/session.v4.jsonl.zstd`（多 frame zstd；解压按 compression.zstd→zstandard→zstd.exe→node 分层，全部不可用则 STUB）；approval/sandbox 策略产出 note（2.3 信号） |
| ✅ OK | 腾讯元宝（登录态直采，v0.8）/ 通义千问（CDP 直采，v0.9）/ 豆包（app-driven capture，v0.9） | 无官方导出、正文在服务端 → 登录态调官方 API 或截获官方响应，schema 即服务端原始 JSON；详见「网页 Chat 直采管线」节 |
| ✅ OK | VS Code Copilot Chat | `session-store.db`（SQLite）+ 空窗口 jsonl + 工作区 `chatSessions/*.jsonl`（补丁日志重构，v0.4） |
| ✅ OK | AutoClaw 桌面版 | `runtime.sqlite`；request 按用户轮次去重、思考/工具调用独立为 note、answer 去重、UTC→本地时区（v0.4）；schema 列结构守卫，改版自动降级 STUB |
| ✅ OK | DeepSeek / ChatGPT / Claude 官方导出文件 | 解析各家「数据导出」产出的 conversations.json（ZIP/目录/单文件均可）；**无需账号密码、零风控**。DeepSeek 真实导出（mapping/fragments DAG，2026-10-06 真机核验，316 会话全量通过）支持分支主链选择与 THINK/FILE/SEARCH/TOOL_* 证据提取。获取方式见 `harvester adapters` 输出 |
| 🔶 部分 | WorkBuddy 工作区日志 | 59 个工作区的每日日志。**注意：这是 agent 自述的工作总结，不是对话轨迹**——真实逐回合轨迹在 `~/.workbuddy/projects/`（上一行 v0.6 已实装） |
| 🔶 STUB | DeepSeek 桌面版 / 元宝 / 豆包 / 千问 / Trae / AutoClaw 用户目录 | 桩位留接口；元宝/豆包/千问的正文本体在服务端，已由直采管线覆盖（见下） |
| ⚪ MISSING | AutoClaw 自部署实例 | 需用户提供数据位置后接入 |

## 网页 Chat 直采管线（元宝 / 千问 / 豆包）

三平台均无官方导出、会话正文本体在服务端。统一原则：**不碰账号密码、
不做风控对抗，在登录态下让页面/浏览器自己调官方端点，采服务端原始 JSON**，
产物落 `corpus/{yuanbao,qianwen,doubao}_raw/`。adapter 消费本地归档，
与服务端解耦。

### 标准流程（采集 → 校验 → 入库）

```bash
# 1) 启动带调试端口的 chrome（持久 profile 保持登录态），后台任务托住进程
#    chrome.exe --remote-debugging-port=<9333|9334> --remote-allow-origins=* \
#        --user-data-dir=<持久目录> about:blank
#    （元宝管线早期走 agent-browser 页内 fetch，无需调试端口）

# 2) 跑对应采集器（小样本先行：先 list 阶段或首个会话）
#    元宝:  verify/yuanbao_receiver.py（本地接收器）+ verify/yuanbao_detail_harvest.py
#    千问:  verify/cdp_driver.py + verify/qianwen_detail_harvest.py
#    豆包:  verify/doubao_harvest.py

# 3) schema 指纹校验（PASS 才放行全量；DRIFT 停下 diff，退出码 1）
python verify/schema_canary.py <yuanbao|qianwen|doubao> <新鲜样本.json>

# 4) 全量采集完成后入库。注意：--all 不包含官方导出文件，两源并存必须显式带上
python -m harvester index --all \
    --deepseek-file "D:/Download/deepseek_data-2026-10-06.zip" --db harvester.db
```

### 三平台分述（端点与坑，均真机核验）

**腾讯元宝（v0.8，1223 会话）**：agent-browser 页内 fetch
`POST /api/user/agent/conversation/list|v1/detail`。分页参数在**顶层**
`{limit, offset}`——嵌套 `pagination:{offset}` 会被服务端静默忽略（恒回
第一页）。本地接收器按 `POST /list/<n>`、`/detail/<cid>` 落盘（no-cors
会丢弃自定义请求头，批次号必须走 URL 路径）。deepSearch 块的
`contents[].msg` 即思考过程 → `[think]` note。

**通义千问（v0.9，117 会话 / 469 轮）**：CDP 页内 fetch
`POST /api/v2/session/page/list`（body 顶层 `{next_token}` 游标）+
`GET /api/v1/session/msg/list`（`have_next_page` 时以 `pos=<末条 pos>`
续拉）。响应侧 `multi_load/iframe` 为正文，`plan_cot/post` 与
`bar/workflow` 的 `bar_thinking` 步骤为思考 → `[think]`；`signal/bar/
paa/survey` 4 类元数据跳过。`error_code` 成功值是 int 0（falsy）。

**豆包（v0.9，3 会话）**：请求经 **Web Worker** 发出且带 msToken/a_bogus
签名（重放报 712012002），页面级 fetch/XHR 钩子捕获为 0——唯一可行路径是
**app-driven capture**：让页面自己发请求，CDP Network 域截获响应体
（`verify/doubao_harvest.py`，单读线程设计）。端点：`/im/chain/recent_conv`
（会话列表）+ `/im/chain/single`（逐会话消息，滚动触发更早，按 message_id
去重）。`user_type` 1=用户 / 2=bot；content_block 映射：10000 正文、
10040 思考标题（`[think]`，**思考正文走流式通道不落盘**，adapter 仅记
标题）、10082 澄清 `[ask]`、10019 文件 `[file-op]`、10030 产物
`[artifact]`、10025 网搜 `[search]`、2074 生成图 `[image]`。

### 平台改版应对：采集器失效的真实边界与预案

- **存量不坏**：`corpus/*_raw/` 是采集时刻冻结的原始 JSON 快照，adapter
  解析本地归档、与服务端解耦——改版只影响"增量采集"，已入库数据永续可用。
- **前置探针**：见流程第 3 步 `verify/schema_canary.py`。指纹定义与
  adapter 消费的字段同步维护（adapter 新消费某字段时同步加进 REQUIRED）。
- **修复成本实证**：改版通常是端点参数/字段名小改（diff 修复），非重写——
  千问从零侦察到 469 轮全量入库 1 天、豆包含签名绕行半天。adapter 对未知
  块类型警告跳过，平台"新增"块大概率无感通过。
- **兜底通道**：豆包有账号级官方导出申请（约 14 天）、千问有官方数据管理
  导出、元宝有 toolkit/MHTML——自建管线挂掉时按
  `docs/RECON_CHAT_EXPORTS_v1.0.md` §2.1/§3.1/§4.1 切换。

### 兜底：weblogin 登录态三级流程（直采管线下游备用）

```bash
python -m harvester weblogin check         # 第1级：探测浏览器登录态（cookie 统计）
python -m harvester weblogin init-config   # 第2级：生成账号配置模板（明文仅存本机）
python -m harvester weblogin prepare deepseek-desktop   # 第3级：用户在可见窗口手动登录
```

安全约定：账号密码文件只存本机不上传；不做网络凭据传输；登录由用户本人
在可见窗口完成。工具不代填表单（各产品登录页结构无公开承诺，不硬猜）。
**有官方导出的（DeepSeek/ChatGPT/Claude）一律优先走导出文件通道。**

## 检索 · 索引 · MCP

```bash
# 索引四种来源（可组合）
python -m harvester index --all --db harvester.db          # 实时扫描全部数据源
python -m harvester index --from exports --db harvester.db # 从导出产物建索引
python -m harvester index --deepseek-file conversations.json --db harvester.db
python -m harvester index --export-file chatgpt-export=C:/x/conversations.json \
    --export-file claude-export=C:/y/conversations.json --db harvester.db  # 可多次

# 全文检索（多词 OR；命中带 <<>> 高亮摘要）
python -m harvester search "cookie 登录态" --db harvester.db

# 跨 agent 上下文交接包（token 预算内塞给另一个 agent 接续工作）
python -m harvester pack --select "3,5-9" --tokens 2000 \
    --question "基于以上讨论继续设计 X" --out context_pack.md

# MCP server（stdio，newline-delimited JSON-RPC，零依赖）
# v0.45 起含「进化数据面」6 工具：主题/链/建议/卡片/产物，Agent 不必先起 HTTP
python -m harvester mcp-serve --sources sources.json --db harvester.db \
    --topics-meta topics_meta.db \
    --chain-root ~/.workbuddy/knowledge/topics \
    --artifacts-meta artifacts_meta.db \
    --suggestions-meta suggestions_meta.db \
    --cards-root <卡片目录>

# 只读 HTTP JSON API（v0.17）：schema 自检 fail loud + 内核级只读
# （mode=ro + authorizer 白名单）；默认 127.0.0.1，非回环 host 必须 --token
python -m harvester api-serve --db harvester.db [--port 8765] [--token <密钥>]
# 端点：/api/meta /api/facets /api/sessions /api/session/<sid> /api/session/<sid>/turn/<no>
#   v2（只增不改）：/api/triage /api/reports/{tools,errors,skills,agents} /api/cards
#   v0.19 additive：/api/meta 与全部 reports/triage/cards 端点带
#   db_fingerprint={sessions,steps,errors,db_mtime,generated_at}（产物判
#   陈旧用）；/api/reports/tools 工具行带 given_up/retried/raw_tools/
#   low_sample（?min_calls=）；/api/reports/agents 条目带 unresolved_count/
#   owner/status（--suggestions-meta <meta库> 启用 status 读取）
#   v0.20 additive：/api/sessions 支持 ?errors_only=1（只含错误步骤的会话，
#   total 同步过滤后数量）；/api/session/<sid> 带 error_steps 清单
#   （seq/ts/tool/error + errstats 归一 pattern/class，供前端批量导出按
#   「会话ID+异常类型」分类与跨会话模式去重）
python -m harvester api-serve --db harvester.db --suggestions-meta suggestions_meta.db
```

MCP 暴露 **10 个**只读工具：

| 面 | 工具 | 说明 | 与 HTTP 的关系 |
|---|---|---|---|
| 会话 | `list_sessions` / `search_history` / `read_session` / `pack_context` | 纲要 / FTS5 检索 / 分层读取 / 交接包 | 本地纲要与打包（无 HTTP 对应） |
| 进化数据 | `topic_list` | 主题注册表 + 每主题链数 | **同源** `/api/topics` |
| 进化数据 | `chain_read` | 主题的 chain 结构化（多链时含 `chains[]`） | **同源** `/api/topic/<id>/chain` |
| 进化数据 | `suggest_list` | 建议台账（建议句 → adopted/rejected） | 同源 `/api/reports/agents` 的状态面 |
| 进化数据 | `cards_list` | 卡片清单（frontmatter 摘要，不校验） | 同源 `/api/cards` 的清单面 |
| 进化数据 | `topic_export` | 主题结构化包（`harvester.topic/1`） | 同 CLI `topic export` 的 JSON 出口 |
| 进化数据 | `artifacts_list` | 产物清单（元数据＋体量，不含正文） | 本地 `artifacts_meta.db` |

**漂移门**：`TOOL_SOURCES`（`mcpserver.py`）声明每个工具的数据来源，
`tests/test_v45_mcp_tools.py` 从 `apiserve.py` **源码 AST** 抽 `/api/*` 能力集，
双向核对——接错端点、清单与实现脱节、或工具数缩水都会**变红**（含元测试）。
同源的两条还做**载荷对账**（MCP 输出必须与 `apiserve` 同名函数逐字段一致，
保证 MCP 侧没有二次加工）。

Claude Code 接入：`claude mcp add harvester -- python -m harvester mcp-serve
--db harvester.db --topics-meta topics_meta.db`（工作目录需在套件根）；其他宿主
把 command 指向 `python -m harvester mcp-serve` 即可。

**中文检索**：FTS5 unicode61 对 CJK 做 bigram 预改写（插入与查询两侧同步），
实测 2-5 字中文词 100% 命中；单 CJK 字前缀查询兜底。原文另存 raw 列，摘要
展示原文。索引体积约 1.9x（可接受）。⚠️ 验证检索必须走 `search()`/CLI 路径
（bigram 改写生效），手拼裸串直接 MATCH 不命中属预期。

## 蒸馏（聚合与知识库）

```bash
python -m harvester aggregate --all --out corpus.md           # 全部数据源实时聚合
python -m harvester aggregate --from exports --out corpus.md  # 从导出产物聚合
python -m harvester kb-init --root ~/.workbuddy/knowledge     # 知识库骨架（幂等）
python -m harvester kb-stats  --root ~/.workbuddy/knowledge   # 盘点

# 工具调用/失败率统计（服务「工具改进」与「Harness 踩坑」）
# 口径一（推荐）：先 index 再用 --db，读结构化 steps 表，含重试/放弃率
# 与按模型分布表（v0.15 起，sessions.model 列；仅 workbuddy-transcript 源有值）
# 错误明细为「根因聚合」形态（v0.20）：同构文本（路径/引号/数字差异）归并
# 出根因行 + 三分类标注；原文只留一条单行样例并截掉 old_string 输入回显，
# 杜绝用户文档片段（如「七步骨架」类）污染明细——完整原文按锚点回溯 steps 表
python -m harvester report-tools --db harvester.db --out tools_report.md
# 口径二：无索引时实时扫描（note 标记汇总，无重试/放弃统计）
python -m harvester report-tools --sources sources.json --out tools_report.md
# ⚠️ 两口径结论必须一致；若有出入，以口径一（--db，结构化 steps 表）为准，
#    口径二仅作无索引时的应急参考。
# 两口径均支持 --since 7（只看最近 N 天，按 steps.ts 近似截断）
#
# 统计口径（v0.19 固化，与 errstats.py 模块 docstring 一致）：
# - calls 只数 phase='call' 行；错误只出现在 phase='result' 行；
# - 工具名归一：同工具异写（小写+去下划线后同键）合并为 canonical，
#   如 edit/Edit、web_fetch/WebFetch、web_search/WebSearch；canonical 取
#   组内调用最多写法，raw 名在报告括号与 API raw_tools 字段可追溯；
# - 重试 = 错误后同会话同工具再次调用；放弃（未解决）= 无再次调用；
#   given_up = errors - retried；--since 截断处跨界的重试对会漏配对
#   （已知近似，跨期对比两侧用同一窗口）。

# 错误三分类（G2）：env（环境）/ tool_interface（用法）/ context（目标状态）
# + 开场/中途/收尾位置分桶 + 归一模式聚类（带锚点与原文；模式行附未解决数）
python -m harvester report-errors --db harvester.db --out errors_report.md

# AGENTS.md 条目建议（G2 闭环）：从错误模式产出候选条目（建议池，
# 每条附锚点+原文证据；**绝不直接改 AGENTS.md**，人工审阅后并入）
# v0.19：建议条目带 owner（harness/tool/workflow）与 unresolved_count，
# 按"未解决次数"降序——unresolved>=1 进"待修清单"，已自愈（=0）降级
# "观察区"；--meta 读建议状态 meta 库（status: pending/adopted/rejected）
python -m harvester suggest-agents --db harvester.db --min-count 3 \
    --out agents_suggestions.md --meta suggestions_meta.db

# 审阅结论落库（独立 meta 库 suggestion_status 表，不碰采集库；key=
# 建议池条目用 title，待人工归因模式用 pattern）
python -m harvester suggest-status --meta suggestions_meta.db \
    --key "Edit/Write 前必须先 Read 目标文件最新内容。" --status adopted

# 知识卡片（G3）：候选池 ↔ 主库归一，三条供卡通道详见 docs/CARD_WORKFLOW.md
# 从索引库会话一键起卡（锚点自动填，evidence 留白待补）
python -m harvester cards new --sid <search输出的sid> --turn N \
    --root ~/.workbuddy/knowledge/cards --type insight
# 校验：§8 frontmatter 规范 + 锚点查索引库 + 引文核对（v0.19：evidence
# 每行须能在锚点会话原文中逐字找到——空白归一后子串匹配，未命中出警告；
# 锚点 turn: null 出警告）；通过后人工并入 kb 主库
# 结论三分支（v0.16）：error=有问题不并入；warn=有警告先检查再定；
# 其余=可并入主库。PyYAML 可选——无它时降级解析器照常校验锚点
python -m harvester cards validate --root ~/.workbuddy/knowledge/cards \
    --db harvester.db

# 卡片 ↔ 主题注册表打通（v0.43，V3）：加 --topics-meta 即追加一层一致性核对
# （缺省不带 → 行为与旧版逐字一致，结果里不会多出任何字段）
python -m harvester cards validate --root ~/.workbuddy/knowledge/cards \
    --db harvester.db --topics-meta topics_meta.db

# Skill 行为画像（G4）：按 skill 聚合调用/触发任务/调用后行为链；
# --skill 深挖单技能 = 可喂给 Agent 蒸馏决策过程的会话清单；
# --min-calls 样本量阈值：calls<阈值的 skill 标 low-sample（仅供观察）
python -m harvester report-skill --db harvester.db
python -m harvester report-skill --db harvester.db --skill wechat-article-search \
    --min-calls 5

# OTel trace 统计（精确耗时 p50/p95、失败率、用户取消；与 steps 互相校验）
python -m harvester report-traces --out traces_report.md
```

**诊断→修改闭环（G2 全链路）**：`report-errors` 定位高频失败模式 →
`suggest-agents` 产出建议池 → 人工审阅并入 `~/.dsh/AGENTS.md`（或扩写为
§8 规范卡片 → `cards validate` 通过后入主库）。首份建议池见
`verify/agents_suggestions.md`（2026-10-06，9 条，第一条"Edit/Write 前先
Read"实测 102 次）；首张已验证卡片
`~/.workbuddy/knowledge/cards/kc-20261006-0001-edit-before-read.md`。

语料带 `<!-- SRC: 来源 | id | 标题 | 日期 -->` 溯源锚点；聚合之后的归类、
话题增补、反馈台账整理是语义工作，由 skill `session-knowledge-distill`
（v1.1+）按 `docs/DISTILL_PLAYBOOK.md` 执行。

## 源插件机制（ai-hist 式声明）

sources.json 的 `plugins` 节可声明"导出文件型"数据源（显式配置，不做隐式
网络访问）。当前注册表 **6 个**：`deepseek-export` / `chatgpt-export` /
`claude-export` / `yuanbao-raw` / `qianwen-raw` / `doubao-raw`。

```json
{"plugins": [{"id": "doubao-raw", "paths": ["C:/path/to/doubao_raw"]}]}
```

未知插件 id 显式报错并列出可选值，不静默忽略。

## 新增 Adapter 的规范

**正式契约见 `docs/ADAPTER_CONTRACT.md`**（DB schema / 适配器义务 /
收件箱协议 / 契约变更流程 / 上线前自检清单）。要点：

1. 继承 `BaseAdapter`，实现 `detect()` / `list_sessions()` / `load_session()`；
2. `detect()` 只做存在性检查，**绝不抛异常**，格式无法验证就返回 STUB/MISSING；
3. 声明 `claims_files`：单文件源（官方导出 zip/json）= True，可参与 sync
   收件箱竞标；目录型源（采集产物目录）= False，禁止认领单文件；
4. 输出统一收敛到 `SessionRecord` / `Message`（`models.py`）；
5. 文件读取走 `self._read_text()`（自带 CRLF→LF 归一化）；
6. 实装后在 `adapters/__init__.py` 把类从 `STUBS` 移入 `ACTIVE`，并同步
   `_PARAM_MAP` / `PLUGIN_IDS` 与 `verify/schema_canary.py` 指纹。

**铁律：实现不了就是实现不了，留接口、不臆测解析逻辑。**

网页端/客户端 Chat 的接入路径（按性价比排序）：① 官方数据导出（若提供）
→ ② 官方开放 API → ③ **登录态调官方 API / 截获官方响应**（三平台已实装）
→ ④ 浏览器自动化（兜底，脆弱）。基类见 `adapters/stubs.py` 的 `WebChatStub`。

## 环境注意事项

- **本机没有 `python` 命令时**：优先 `py -3`，或直接用已装解释器的绝对路径
  （套件本体零第三方依赖，任何官方 CPython 3.10+ 均可运行）。
- **verify/ 采集工具**：需 `requests` + `websocket-client`，用独立 venv；
  Windows 下 CDP 请求须显式 `proxies={"http": None}` 绕系统代理。
- **weblogin check 报 LOCKED**：Chromium 打开 Cookies 库时不授予共享权限
  （物理限制）。须完全退出所有浏览器进程后重试；仍失败即视为该库离线
  不可判定，勿反复重试。
- **AutoClaw 自带的 isolated Python 跑不了本套件**：safe_path 模式不认当前
  工作目录（普通 Python 正常），请用系统/独立解释器。
- 所有落盘文本统一 **UTF-8 + LF**（Windows 下 `write_text` 必须显式
  `newline="\n"`，CRLF 会破坏 frontmatter/行式解析）。
