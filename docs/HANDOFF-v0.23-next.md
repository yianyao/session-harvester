# HANDOFF — v0.23 收官与下一批（#8 主题思维链优先）

> ⚠ **本文件已收官，勿再据此开工。** 其覆盖的 #8 两项已由 **v0.24** 执行
> （用户 2026-10-10 指定两个主题：`吾好梦中救人` / `Skill 自学习进化与跨平台设计`）。
> **当前有效交接见 `docs/HANDOFF-v0.24-next.md`**；本文件保留作历史记录
> （H 台账正文仍在 `HANDOFF-v0.22-next.md` §2）。
> 测试基线同步：后端 **472 例全绿**（2026-10-10），前端 **24 例全绿**。
>
> 交接时点：2026-10-09 末。上游 head：`0397974`。
> 前置阅读顺序：`docs/HANDOFF-v0.22-next.md`（H1–H50 事实台账，**禁止重复验证**）
> → `PLAN-v0.22-unified.md` §0（分歧裁决）→ 本文件。
> **本文件取代 `HANDOFF-v0.23-remaining.md`**（其覆盖的 #1/#2 两项已于
> `0397974` 完成，旧文件保留作历史记录，勿再按它开工）。
>
> 本文件是 agent 消费品：口径显式、断言可判定、不加口语化解释。

---

## 0. 状态快照（勿重测）

| 项 | 值 |
|---|---|
| 后端 head | `0397974`（v0.23 五项全部落地），工作树干净 |
| 后端测试基线 | **472 例全绿**（v0.24 同步；venv 解释器，见 §4。原 457 例为 v0.23 时点） |
| 前端 head | `4ba4932`（view 工作树干净） |
| 前端测试基线 | **24 例全绿** |
| 门禁（无 PyYAML 解释器） | 预期 **FAILED (errors=34)**——**这是设计行为，不是坏了**（T3 拒绝降级解析） |
| 库规模 | 1964 会话 / 36647 steps / 294 错误（`2026-10-09 10:17:07` 时点） |

### v0.23 已完成项（勿重做）

| 项 | 提交 | 关键结论 |
|---|---|---|
| #5 依赖清单 + `cli.run()` 入口修正 | `4103512` | `pyproject.toml` extras：yaml/dsh/web/verify/all；**入口必须挂 `cli:run`**（挂 `main` 会让非零退出码静默变 0） |
| #15 T4 `after_stage` | `4103512` | `--margin-days` 只向后放宽；**三条归组路径一律按 `day > span_end` 判定**；真库 95/95 命中全标 `_after` |
| #14 交叉表补 skill 维 | `4103512` | `cross_stats_by_skill` + API `cross_skill`；真库首行 `diagnose-windows-sandbox-acl × dsh = 66` |
| #2 元宝块类型防御 | `0397974` | 白名单 9→11 种（`drawWithSearchGuid`/`doc_percent`）；真库解析 **8558→8572（+14）**、零未知类型 |
| #1 停用词表默认启用 | `0397974` | `data/stopwords_zh.txt`（313 条）；真库十项虚词残留 doc_freq **全为 0** |
| 附带修：`extract_grams` ASCII 整词 | `0397974` | 原 `["ab","bc","de","ef"]` 是 bug 固化，已改为整词 |

---

## 1. 下一批任务（按优先级）

### 1.1 【最高优先】#8 再出 2–3 条主题思维链

**为什么是第一优先**：现仅 **1 条** chain（`~/.workbuddy/knowledge/topics/chain-叙事节奏.md`）。
一条链只能讲故事；**T4 的 skill 进化 join 因此无法横向对照**——#13（T4 多例）
完全依赖本项。

**已核实的事实**：
- chain 现有 1 条：`chain-叙事节奏.md`，**306 行 / 7 阶段 / 30 节点 / 55 成员**；
  `chain-validate` 通过（`errors 0 warnings 0`）。
- 该链**只有 27 个成员挂了 turn 级锚点，其余 28 个仅标题级证据**（#12）。
- topics_meta.db 现有 **127 个主题**（10 个种子 + tp-20261008-010 叙事节奏 +
  117 个自动聚类候选，后者为 2026-10-09 用户认可后批量注册）。

**开工前置（必须由用户提供）**：**指定 2–3 个主题**（从现有 127 个中选，或新建）。
工具侧不写死主题（红线），故本项**不能自行开工**。

**执行流程（确定性部分）**：
```bash
# 1) 出蒸馏包（分层预算，coarse 约 10K 字符）
python -m harvester topic pack --id <topic_id> --level coarse \
    --out docs/reports/topic-pack-<名>-coarse.md
# 2) 交给 Agent（新会话）通读 pack + 按需拉 fine 档，产出 chain 长文
python -m harvester topic chain --id <topic_id> --level fine --sid <sid>
# 3) 独立校验（非 §8 卡片校验）
python -m harvester chain-validate <chain>.md --db harvester.db --meta topics_meta.db
```
**验收**：chain 长文通过 `chain-validate`；每个"为什么改"节点锚点可跳回原文；
同库快照重跑产物 diff 可归因（仅时间戳行差异，H28）。

**注意（本次教训）**：chain 的 `anchors[].stage.span` 必须与该阶段真实日期一致；
T4 的 `after_stage` 会把"阶段结束后"的调用单独标注，但**若 span 本身写错，
标注也会跟着错**。

---

### 1.2 #12 补 chain 证据覆盖（27/55 → 提升）

**问题**：叙事节奏链 **55 成员中仅 27 个有 turn 级锚点**，其余 28 个只有标题级。
chain 的"待补与限制"提了此事，但**正文读起来像全部有证据**（易误导）。

**两条可选路径（需用户裁决其一）**：
- (a) 补锚点：对 28 个成员逐个定位关键 turn，扩充 `anchors.stages[].nodes`；
- (b) 显式标注：在正文相应位置写明"该阶段结论仅标题级证据"，不改锚点。

**(b) 成本低且更诚实**，建议先做 (b)，(a) 作为增量。

**验收**：`chain-validate` 通过；且正文中每个阶段的证据等级可被读者区分
（有节点 = turn 级；无节点 = 标题级）。

---

### 1.3 #11 `regress` 端到端回归语料

**问题**：**不存在**（CLI 无该子命令）。方案 §6 唯一"替代物不等价"的缺口。
**证据**：本项目连续多轮第三方复查找到的缺陷（MCP pack_context 元组错、
`_is_error_status` 虚报 50 倍、GBK 解码、doc_freq 口径、ASCII 切片、
skill 名 command 污染）**全部逃过了 457 个单测**——因为单测夹具比真实数据窄。

**做法建议**：
1. 固定一个**小样本语料目录**（10–20 个会话的导出产物，入 git 或本地固定路径）；
2. 新增 `regress` 子命令：依次跑 `index → report-tools → report-errors →
   suggest-agents → cards validate → keywords`，产出可比对快照（H28 口径：
   只差时间戳行）；
3. 用 `db_fingerprint` 标注快照归属，避免"数据变了"与"逻辑变了"混淆。

**验收**：`regress` 在样本语料上两次运行产出**除时间戳外逐字节一致**的产物；
且**故意改坏一个统计口径时 `regress` 必须红**（否则等于没断言）。

---

### 1.4 #9 chain 元结论回写 AGENTS.md

**现状**：叙事节奏链的「元结论：这条链上可迁移的五条」**仍只躺在那份 chain 文档里**，
未进入 `~/.dsh/AGENTS.md`。闭环终点应是"下次开工自动生效"。

**做法**：走既有机制——`suggest-agents` 产建议 → `suggest-status` 落
`adopted`（`suggestions_meta.db` 现为 7 adopted / 1 rejected）。
**注意**：`suggest-status` 的 key = **建议句原文**（含句号），见 H31。

**验收**：五条元结论对应的建议条目在 `suggestions_meta.db` 中状态为 `adopted`；
AGENTS.md 内容由**人**决定并写入（工具绝不代改，红线）。

---

### 1.5 #10 主题增删合并（需用户语义判断）

**现状**：117 个自动聚类候选主题已注册，但部分关键词是**标题字符 bigram 碎片**
（如 `em/pr/ut/dy/ep`），语义价值低。

**注意**：这与 keywords 的 ASCII 切片 bug **同源但不同处**——本项是
`candidates.py` 的**标题**字符 bigram，**不是** `kwstats`（后者已修为整词）。
若要让候选关键词可读，需另行改造 `candidates` 的标题特征提取，属**独立任务**。

**验收**：合并/删除后的主题表经用户确认；`/api/topics` 渲染正常。

---

### 1.6 #13 T4 多例（依赖 #8）

现仅 `pdf-text-extractor` **1 例**（含 LLM 语义判定 + 用户复核）。
要得出"哪类 skill 在哪个创作阶段有效"的结论，需 **≥3 例**。

---

## 2. 已知且**接受**的固有代价（不要在下一轮当 bug 修）

| 现象 | 性质 | 依据 |
|---|---|---|
| CJK 跨词边界 bigram（`上的`/`了一`/`这是`/`也不`） | **无分词器的固有代价**，可补表改善、无法根除 | v0.23 #1 实测；README 已写明 |
| 主角名（丁樾/南星）居 keywords 首位 | **预期**：人名不在通用表内，由用户私有表叠加 | 红线：不写死主题/人名 |
| `_KNOWN_BLOCK_TYPES` 白名单外的块被跳过+告警 | **设计意图**（fail loud） | #2 已实现 |
| 无 PyYAML 时 29 个测试报错 | **设计意图**：T3 拒绝降级解析 | H9 / README 依赖边界 |
| T4 中 95/95 命中全标 `_after` | **真实结论**：该主题成员为导出型源、阶段七 span 内无 skill 调用 | H25 |

---

## 3. 用户已裁决的**不做项**（勿再提议）

- `update`/`sync` 日常运行节奏（#3）：保留不动，用户自定频率。
- 是否再扩源（#4）：保留不动，维持 9 源格局。
- `view` 内不放聚类/时间线/思维链逻辑（红线 3，长期有效）。
- 不为 args 全量回传去改采集库 schema（PLAN §0.2-C）。
- 不建 LLM 错误归因组件（确定性三分类已 100% 覆盖真实库；详见
  `AUDIT-2026-10-08-quality-and-artifacts.md` §建议 P2-10）。

---

## 4. 环境速查（沿用，勿重验）

| 项 | 值 |
|---|---|
| venv Python（**一切测试/CLI 用它**） | `C:\Users\yianyao\.workbuddy\binaries\python\envs\default\Scripts\python.exe`（3.13.14 + PyYAML 6.0.3） |
| 后端测试 | `& $venv -m unittest discover -s tests` → 预期 **472 例 OK**（v0.24 同步） |
| 前端测试 | 同解释器，在 `harvester-view/` 下 → 预期 **24 例 OK** |
| meta 库（均 gitignore） | `topics_meta.db` / `suggestions_meta.db` / `artifacts_meta.db` / `keywords_meta.db` |
| 卡片主库 | `~/.workbuddy/knowledge/cards/`（`--cards-root` 已对齐，P0-4） |
| chain 正式位 | `~/.workbuddy/knowledge/topics/chain-<topic>.md` |
| 元宝源数据 | `corpus/yuanbao_raw/detail_*.json`（1223 个；**块类型探针须读这里，不是 messages.raw**） |
| 提交信息 | **用 `-F 文件`**，勿用 PowerShell here-string（AGENTS.md 第 1 条；本次已踩且规避） |

**测试数变化时必须同步三处**：`README.md`（依赖边界段）、本文件、`HANDOFF-v0.22-next.md` §0。

---

## 5. 本次会话的方法学留档（供后续 agent 复用）

1. **探针必须对齐被测 parser 的真实数据落点**。#2 第一版探针查 `messages.raw`
   得"0 未知类型"，实为"0 条可解析"造成的**假通过**；改读
   `corpus/yuanbao_raw/detail_*.json` 才发现 2 种漏列类型、14 块内容将丢失。
   → **"0 异常"必须先排除"0 样本"。**
2. **改动前先跑"只统计不改变"的探针**，尤其在会丢数据的路径上。用户裁决
   "扩充白名单"正是基于探针给出的两种块的内容形态。
3. **断言要能说清"什么情况会红"**，并在提交前故意破坏一次确认。本次对
   `after_stage`、doc_freq 排序、ASCII 整词、元宝白名单四项均做了可证伪验证。
4. **既有断言可能是 bug 的固化**：`extract_grams("abc def",2)==["ab","bc","de","ef"]`
   就是 ASCII 切片 bug 的固化。遇到"测试要求保留错误行为"时要改写并注明理由。
5. **新写的代码可能自己引入同类 bug**：#14 的 skill 索引未按 `SKILL_TOOLS`
   过滤，导致 pwsh 的 `command` 载荷整条变成 skill 名。**新维度接入时必须复用
   既有白名单/口径，不要另写一份解析。**

---

## 6. 开工流程

1. 读本文件 → `HANDOFF-v0.22-next.md`（H1–H50）→ `PLAN-v0.22-unified.md` §0。
2. 基线自查：`git log --oneline -1`（预期 `0397974` 或其后）；
   后端 **457** / 前端 **24** 全绿。
3. **#8 需用户先指定主题**；其余项（#11/#12）可自行开工。
4. 测试先行 + 可证伪验证 + 文档三处同步 + `-F 文件` 提交。
