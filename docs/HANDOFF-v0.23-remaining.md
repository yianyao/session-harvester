# HANDOFF — v0.23 待办收尾（#1 停用词表 / #2 元宝块类型防御）

> ⚠ **本文件已收官，勿再据此开工。** 其覆盖的 #1 与 #2 两项已于
> `0397974` 完成（见 `HANDOFF-v0.22-next.md` 的 H50）。**当前有效交接见
> `docs/HANDOFF-v0.23-next.md`**。本文件保留作历史记录（含当时的探针要求
> 与验收标准，可对照实际实现是否达成）。
>
> 交接时点：2026-10-09。上游 head：`4103512`（v0.23 三项已提交并推送前）。
> 前置阅读：`docs/HANDOFF-v0.22-next.md`（H1–H49 事实台账，**禁止重复验证**）；
> 本文件只覆盖**剩余两项待办**，且这两项均已由用户裁决（2026-10-09）：
> 「1、接受停用词表」「2、实现」（选项 B：实现 `_KNOWN_BLOCK_TYPES` 的防御）。
>
> 本文件是 agent 消费品：口径显式、断言可判定、不加口语化解释。

---

## 0. 已完成项（勿重做）

| 项 | 提交 | 状态 |
|---|---|---|
| #5 依赖清单 + `cli.run()` 入口修正 | `4103512` | ✅ 437 后端 / 24 view 全绿 |
| #15 T4 `after_stage`（三条归组路径） | `4103512` | ✅ 真库验证：95/95 命中全部标 `_after` |
| #14 交叉表补 skill 维 + `cross_skill` 端点字段 | `4103512` | ✅ 真库首行 `diagnose-windows-sandbox-acl × dsh = 66` |
| 文档同步（README / HANDOFF H49） | `4103512` | ✅ |

**基线门禁**：`437 例全绿`（venv 解释器，见 §3）。若低于此数，先查是否漏了
新增测试文件被 `discover` 收录。

---

## 1. 任务 A（#1）：停用词表

### 1.1 用户裁决与边界（必须遵守）

- 用户裁决：**接受停用词表**。
- **硬边界（用户红线，H24/PLAN §0.2）**：不得写死任何**主题名/会话/skill 名**。
  → 因此本项目**只放通用中文虚词与泛用动词**，**禁止**放具体作品的人名
  （如「丁樾」）。人名/专名由使用者在自己的私有停用词文件里追加。
- 该边界必须在文件头注释与本 handoff 中同时写明，避免后来者"顺手补全人名"。

### 1.2 已核实的事实（勿重测）

| # | 事实 | 证据 |
|---|---|---|
| A1 | 真实库 doc_freq 前 12 名被虚词与主角名占据 | `keywords --db harvester.db --top 12` 实测（H48） |
| A2 | `freq` 是"单条文本内出现次数×条数"累加，`doc_freq` 才等于"含该 gram 的消息条数" | `kwstats.build_stats` 双 Counter；`丁樾 freq=50577 / doc_freq=1421`（全库 6723 条 user 消息） |
| A3 | 停用词文件格式：每行一词，`#` 开头为注释；由 `load_stopwords()` 解析 | `kwstats.py:load_stopwords` |
| A4 | CLI 已有 `--stopwords PATH` 参数（`keywords` 子命令），**缺省为 None（不启用）** | `cli.py` keywords 子命令 |
| A5 | 停用词**同时作用于 `freq` 与 `doc_freq`**（过滤在 `extract_grams` 之后、两个 Counter 之前） | `build_stats` 循环内 `if g not in stop` |

### 1.3 实施方案（建议，可调整）

1. 新增 `harvester/data/stopwords_zh.txt`（**纯通用虚词**，约 100–200 条）：
   - 单字虚词：的 了 是 在 我 你 他 她 它 们 这 那 有 和 与 就 都 也 很 还 把 被 让 给 对 从 到 向 于 为 而 或 及 等 但 却 只 要 会 能 可 应 该 不 没 无 上 下 中 前 后 里 外 时 后 个 些 什 么 怎 样 …
   - 双字虚词：一个 自己 怎么 没有 出来 不是 什么 还是 就是 起来 上的 可以 这个 那个 因为 所以 如果 但是 然后 现在 已经 应该 可能 需要 进行 通过 我们 他们 你们 …
   - **不含**任何具体作品的人名/地名/专名。
2. 在 `keywords` 子命令把该文件**接为默认值**：`--stopwords` 缺省指向
   `harvester/data/stopwords_zh.txt`；显式传 `--no-stopwords`（或传空串）
   可关闭，保证可复现与可覆盖。
   - 建议同时支持 `--stopwords` 传多个文件（用户私有表叠加），可实现为
     `action="append"`。
3. 报告头已声明 `停用词 N 条`（`render_report` 现有字段），无需改。

### 1.4 验收标准（可判定）

- **A-1**：`keywords --db harvester.db --top 20` 输出中，
  `一个/自己/怎么/没有/出来/不是/什么/还是/就是/起来` **全部不出现**。
- **A-2**：默认启用后 doc_freq 首位**不再是** `丁樾`（它应仍在前列，除非用户
  自加人名——这正是"人名由用户自定"边界的体现）。**注意**：若首位仍是主角名，
  属**预期**（不在通用表内），不得据此判定失败；断言应写"虚词已消失"，
  不要写"首位是人名以外"。
- **A-3**：`--no-stopwords` 时输出与改动前**逐字节一致**（回归保护）。
- **A-4**：新增测试 `tests/test_v23_stopwords.py`：
  - 默认表被加载（`params.stopwords > 0`）；
  - 给定 fixture 含「一个/自己」，二者不出现；
  - 显式关闭后二者出现；
  - **表内不含 CJK 三字以上的词**（防止后人误加人名/主题名——把红线变成断言）。
- **A-5**：`__pycache__` 与数据文件打包：`pyproject.toml` 的
  `[tool.setuptools]` 需补 `package-data`（`harvester = ["data/*.txt"]`），
  否则装包后默认表丢失。

### 1.5 已知坑与规避

- **别把停用词加进 `extract_grams`**：它作用于 n-gram 切片，停用词应在
  `build_stats` 里按 gram 过滤（现状即是，勿改结构）。
- **别用 ASCII 双引号嵌中文**（AGENTS.md 第 2 条）：写文件头注释用「」。
- **别写 here-string 生成文件**（AGENTS.md 第 1 条）：用 write 工具。

---

## 2. 任务 B（#2）：实现 `_KNOWN_BLOCK_TYPES` 防御

### 2.1 现状（已核实，勿重测）

- 定义在 `harvester/adapters/yuanbao_raw.py`，注释原文：
  「已核验的 content 块类型（其余类型防御式跳过+警告）」。
- 集合内容：`{"text", "searchGuid", "deepSearch", "deepSearchAgent",
  "pdf", "image", "prompt_url_card", "link_card", "step"}`。
- **全库无任何引用**（AST 扫描确认：`harvester/`+`tests/`+`harvester-view/`
  共 94 个 py 文件，该名字出现 1 次 = 定义处）。
- 即：**注释承诺的"跳过+警告"没有实现**。

### 2.2 用户裁决

选「实现」（而非删除）。理由（用户未展开，按最合理理解记录）：块类型白名单
是**数据质量守卫**——元宝改版新增/改名块类型时，静默丢内容比报错更危险；
把它变成可观测的警告，符合项目"fail loud / 不静默降级"纪律（同 P0-1）。

### 2.3 实施方案

1. 在 `yuanbao_raw.py` 解析 content 块的位置，对每个块类型做白名单判定：
   - 命中 → 现有逻辑；
   - 未命中 → **跳过该块**，并把 `(块类型, 出现次数)` 计入本 adapter 的统计。
2. 计数出口（按项目既有范式，**不新建表**）：
   - 优先：写进 `SessionRecord.extra`（如
     `extra["unknown_block_types"] = {"xxx": 3}`），使 `--with-notes`/导出
     产物自带该信息；
   - 若 `detect()` 也要暴露，可在 `DetectReport.detail` 追加一句
     「未知块类型 N 种」。
3. **警告必须可见**：CLI 侧在 `-v` 时打印 `[warn] yuanbao-raw: 未知 content
   块类型 xxx ×3（已跳过）`；**不要**只在 verbose 下静默——至少在产物
   `extra` 里留痕（这是可复核的落点）。
4. 计数需**可复现**：同输入两次运行计数必须相同（纯函数、无全局状态）。

### 2.4 验收标准（可判定）

- **B-1**：构造含 1 个已知块 + 1 个未知块的 fixture，解析后**已知块内容在**、
  **未知块内容不在**（真跳过，不是仅计数）。
- **B-2**：`extra["unknown_block_types"]` 含未知块名与正确次数。
- **B-3**：**既有真实会话解析结果不回归**——对真实库跑一次导出/索引后，
  元宝会话的消息数与改动前一致。**做法建议**：改动前后各跑
  `report-tools`/`facets` 或直接 `SELECT COUNT(*) FROM messages WHERE
  sid LIKE 'yuanbao-raw%'` 对账（当前值以实测为准，勿凭记忆写死）。
  - ⚠ **若真实数据里已存在未知块类型**，B-3 会真实变化——此时**不是回归**，
    而是**首次暴露**。必须先跑一次"仅统计不跳过"的探针，把真实未知块类型
    列出来并**记进 H 台账**，再决定：已知块类型表是否要扩充（扩充=改语义，
    需用户裁决），才动手跳过。
- **B-4**：新增 `tests/test_v23_yuanbao_blockguard.py`，覆盖 B-1/B-2 与
  "未知块不导致异常"。

### 2.5 关键风险（必须先探测）

**最大风险不是代码，而是"真实数据里是否已有未知块类型"。**
若元宝当前实际产出 5 种块而白名单只有 9 种之中的 4 种，实现防御会**立刻
改变已有语料**（丢内容），而这是不可逆的语义变更。

→ **强制前置步骤**：先写一个只统计、不改变的探针（或临时加
`--dry-run` 语义），对全量 `yuanbao-raw` 会话统计 `content` 块类型分布，
把结果**先记入 H 台账并交用户确认**，再实现跳过。

---

## 3. 环境速查（沿用，勿重验）

| 项 | 值 |
|---|---|
| venv Python（**一切测试/CLI 用它**） | `C:\Users\yianyao\.workbuddy\binaries\python\envs\default\Scripts\python.exe`（3.13.14 + PyYAML 6.0.3） |
| 后端测试 | `& $venv -m unittest discover -s tests` → 预期 **437 例 OK** |
| 前端测试 | 同解释器，在 `harvester-view/` 目录下 → 预期 **24 例 OK** |
| 无 PyYAML 解释器 | DSH 自带 python → **29 errors（预期，门禁）**，不是坏了 |
| meta 库 | `topics_meta.db` / `suggestions_meta.db` / `artifacts_meta.db` / `keywords_meta.db`（均 gitignore） |
| 未提交产物 | `docs/reports/`（gitignore，本地留存） |

**测试数变化时必须同步**：README（依赖边界段 "417 例全绿" 字样已改 437）+
本 HANDOFF + `HANDOFF-v0.22-next.md` §0 表格。

---

## 4. 明确不做（用户裁决）

- `update`/`sync` 日常节奏（#3）：**保留不动**，由用户自行决定运行频率。
- 是否再扩源（#4）：**保留不动**，维持现 9 源格局。
- `view` 内不放聚类/时间线/思维链逻辑（红线 3，长期有效）。
- 不为 args 全量回传去改采集库 schema（PLAN §0.2-C 裁决）。

---

## 5. 下一批（本次未启动，按建议顺序）

| # | 事项 | 备注 |
|---|---|---|
| 8 | **再出 2–3 条 chain（不同主题）** | **回报最高**：现仅 1 条 chain，T4 无法横向对照 |
| 9 | chain 元结论回写 AGENTS.md | 走 `suggest-status` 落 adopted |
| 10 | 117 个自动聚类主题的增删合并 | 需用户语义判断；部分关键词为标题字符 bigram 碎片 |
| 11 | `regress` 端到端回归语料 | 方案 §6 唯一"替代物不等价"缺口 |
| 12 | chain 证据覆盖：55 成员仅 27 个有 turn 锚点 | 28 个仅标题级证据，正文未显式标注 |
| 13 | T4 多例（现仅 `pdf-text-extractor` 1 例） | 依赖 #8 |

---

## 6. 开工流程

1. 读本文件 → `HANDOFF-v0.22-next.md`（H1–H49）→ `PLAN-v0.22-unified.md` §0 裁决。
2. 基线自查：`git log --oneline -1`（预期 `4103512` 或其后）；
   跑后端 437 / 前端 24。
3. **先跑 §2.5 的块类型探针**（任务 B 的强制前置），结果记 H 台账后交用户确认。
4. 任务 A 与任务 B 相互独立，可并行；**各自测试先行**，并做一次"故意破坏
   确认断言会红"（AGENTS.md 第 4 条）。
5. 收尾：文档同步（README/HANDOFF）+ 新 H 编号 + 提交（提交信息用
   `-F 文件`，**不要用 PowerShell here-string**，AGENTS.md 第 1 条）。
