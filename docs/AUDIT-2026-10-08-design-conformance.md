# 设计方案对照评估（第二次核查）

> 核查对象：`session-harvester`（工作区当前状态，2026-10-06 构建）
> 对照基准：《Agent 会话轨迹分析工具——设计方案 v1.0》（2026-10-05，桌面）
> 核查日期：2026-10-08 · 核查方式：**独立实跑 22 个子命令 + 179 单测 + 直接查 `harvester.db` 与真实卡片文件**
> 与工作区内已有文档的关系：`docs/DESIGN_VS_ACTUAL.md` 与 `docs/PLAN_DIFF_v1.0.md` 是自评；
> 本文是**第三方复核**，结论有出入处以本文的实测证据为准。

---

## 0. 先说我没做到的

1. **未逐个实跑全部 22 个子命令**。实跑并核对了：`report-tools`（两条口径）、`report-errors`、
   `report-skill`、`report-traces`、`suggest-agents`、`cards validate`、`triage`、`search`、
   `read`、`pack`、`mcp-serve`（4 个工具逐个调）、`adapters`、`scan`、`export`、`aggregate`、
   `index`、`kb-stats`。**未实跑**：`weblogin`（需浏览器）、`draft`、`cards new`、`sync`（会改工作区产物）。
2. **未核验 9 个 adapter 的解析保真度**。只核了 AutoClaw（上一轮已核，本轮复核 tool_result 已入库）
   与 WorkBuddy transcript 的字段形态；元宝 1223 / 千问 117 / 豆包 3 会话的采集体量是**读库计数**，
   没有抽样比对原网页。
3. **未核验 `report-errors` 的三分类正确率**。它自称 243/261 错误 100% 归类、unclassified=0。
   我核了**分母**（261 与 steps 表 `status='error'` 完全一致）和**结构**（分桶/锚点/归一聚类都在），
   **但没有逐条人工判断 261 条的错误类别是否分对**——"100% 归类"只证明规则覆盖了全部样本，
   不证明分对了。这是本次核查最大的未覆盖项。
4. **上一轮审计的 `docs/AUDIT-2026-10-05.md` 保留在工作区**，它描述的是**旧版本**（62 单测、
   WorkBuddy 只读 memory-log、bigram 未实装）。当前现实已大幅前进，凡与本文冲突处以本文为准。
   建议在该文件头加一行"历史版本，已被 v0.5+ 取代"，否则会误导后续读者。

---

## 1. 覆盖了方案中的哪些内容 / 多出啥 / 少了啥

### 1.1 覆盖度总表（方案 §3–§11）

| 方案章节 | 覆盖度 | 判定依据（实测） |
|---|---|---|
| §5 中文检索（bigram 双侧改写 + 原文列） | **100%** | `search 方案/识别/转换/登录` 全部命中（2 字、3 字都通）；`messages` 表确实是 `unicode61` **+ 插入侧 bigram 预改写 + `raw` 原文列**；snippet 在 Python 侧用原文裁窗（避开二元组汤）。**比方案写得更好**——见 §3.4 |
| §4.1 WorkBuddy transcript adapter | **100%** | `workbuddy-transcript` 入库 **158 会话 / 18042 steps**；`user_query` 提取与注入上下文独立 note 都在 |
| §4.2 DSH adapter（zstd 分层降级、不依赖 dsh-tools） | **100%** | `dsh` 入库 **22 会话 / 8804 steps**；策略是 `compression.zstd → zstandard → zstd.exe → node` 分层可用性探测 |
| §4.2 OTel span 旁路统计 | **100%** | `report-traces`：430 trace 文件 / 47243 span / 6702 工具调用 / 失败 74（1.1%），含 p50/p95 |
| §6 report-tools（失败率/重试/放弃/--since） | **85%** | `--db` 口径**正确**（15531 调用 / 261 失败 / 1.7%）+ 重试/放弃 + `--since`；**缺绕行检测**；**回退口径有严重 bug**（§3.1） |
| §6 report-errors（三分类 + 位置分桶） | **95%** | 261 错误 = steps 表 `status='error'` 精确一致；env 108 / tool_interface 128 / context 25 / unclassified 0；开场 124 / 中途 78 / 收尾 59；归一聚类带锚点+原文 |
| §6 behavior --skill（G4） | **85%** | `report-skill`：31 skill / 94 调用 / 成功率 93%；含触发任务、调用后行为链（相邻去重）、锚点清单、`--skill` 深挖；**缺 motif 频率矩阵与群体视图** |
| §6 cards validate（§8 规范 + 锚点查库） | **70%** | 规范校验在跑；**但锚点校验实际执行 0 个，且结论自相矛盾**（§3.2） |
| §6 regress 回归语料 | **0%** | 确认不存在；179 单测是零件级，非端到端回归 |
| §7 LLM 错误归因 / 卡片生成 / 交叉验证 | **0%（有意）** | 按"可抛弃/等需要再建"未建；替代物是 playbook + skill 人工蒸馏 |
| §8 知识卡片 frontmatter 规范 | **80%** | 规范已冻结并被消费；4 张真实卡片存在；一池（cards_pending/knowledge cards）一库（kb）归一决策已定 |
| §3 steps 规范化表 | **60%** | `steps(sid, seq, ts, tool, phase, status, error, detail)` 实际存在且有 30956 行真实数据，`tool_call`/`tool_result` 已结构化（**不再是 note 文本正则**）；但**无 `role/kind` 五分类落列**，`sources` 表与 `schema_fingerprint` 未建 |
| §3 锚点 `(session_id, turn, message_id, step_id)` | **40%** | 实际是 `sid + seq`，报告行/卡片锚点形如 `dsh:--D--...zstd#263`。可回指会话内步序，**无 turn/message_id** |
| §9 M1（双 adapter + 建库 + Top3 失败工具） | **超额** | 1946 会话 / 55203 消息 / 319.3 MB 库；跨 9 源 |
| §10 隐私硬约束 | **0%（未触发）** | 单用户；库中无身份字段。扩到 20 人前必须补 |
| §11 风险 1（DSH 0.3 漂移 → schema 指纹） | **30%** | 指纹未入 `sources` 表；AutoClaw 有 PRAGMA 列守卫 + `verify/schema_canary.py`，**DSH 无指纹兜底** |

**估算总覆盖：方案 §3–§9 条目加权约 65–70%**，其中方案的核心决策（厚语料、薄分析、确定性优先）
被执行且诊断层主干全部落地。

### 1.2 多出来的（方案里没有，但确实在做）

这些是我的独立发现，工作量不小，且方向正确：

1. **9 源格局**（方案只要双源）。实测入库分布：
   `yuanbao-raw 1223 / deepseek-export 316 / workbuddy-transcript 158 / qianwen-raw 117 /
   workbuddy 78 / dsh 22 / autoclaw 20 / vscode-copilot 9 / doubao-raw 3`。
   网页 Chat 三平台自建直采管线（元宝/千问/豆包）**完全在设计之外**。
2. **`suggest-agents` 闭环**——把错误模式机械翻译成 AGENTS.md 候选条目。实测产出
   `agents_suggestions.md` 共 **9 条建议 + 9 条待人工归因**，**每条都带真实锚点与原文证据**。
   例如第 1 条「Edit/Write 前必须先 Read 目标文件最新内容」附「实测 112 次」+ DSH 会话 `#263` 原文。
   这是方案 §7 想要的"LLM 归因"的**确定性替代品**，且不需要 LLM。
3. **`triage` 蒸馏队列**——新错误 pattern / 旧坑重现 / Skill 行为链 / 高信号会话的确定性初筛，
   并给出下一步命令（`cards new --sid ... --type pitfall`）。实测能区分
   「file changed since it was read」新 pattern（26 次）与「file has not been read」（23 次）。
4. **`draft` 自包含蒸馏包**（会话原文 + 卡片规范 + 指令喂 Agent）、**`sync` 一键幂等同步**、
   **`report-traces`**、**MCP server**（4 工具已逐个调通）、**`pack` 交接包**。
5. **`sessions.model` 列**——按模型归属的统计已入库（`DESIGN_VS_ACTUAL.md` 第 144 行末仍写着
   "模型级归属未入库"，**该句已过期**；我实测 `sessions` 表确有 `model` 列，且有
   `render_model_table` 与 `collect_model_stats`）。
6. **文档纪律**：`ADAPTER_CONTRACT.md`、`PROJECT_STRUCTURE.md`、`USER_MANUAL.md`、
   `CARD_WORKFLOW.md`、`RECON_CHAT_EXPORTS_v1.0.md` 等 10 个文档，加上 15 个测试文件 179 个测试。

### 1.3 少了的 / 弱的

| 缺口 | 来源 | 影响 |
|---|---|---|
| **`report-tools` 回退口径虚报失败率 88%**（真值 1.7%） | 实测发现 | **阻断性**：G1 的核心产物在两个口径下给出量级不同的答案 |
| **`cards validate` 锚点校验实际执行 0 个，且结论自相矛盾** | 实测发现 | G3 闭环的证据链未被真正校验 |
| 绕行检测（连续相同工具调用） | GR §6 | G1 次要项 |
| 群体行为 motif 频率矩阵 | GR §6/§1.2-G4 | G4 后一半 |
| `regress` 端到端回归语料 | GR §6 | 改蒸馏/诊断逻辑无法判断变好变坏 |
| `sources` 表 + `schema_fingerprint` | GR §3/§11-1 | DSH 0.3 漂移无兜底 |
| 锚点到 turn/message_id 级 | GR §3 | 卡片证据回指粒度粗，人工核对要翻整会话 |
| LLM 提炼组件（归因/卡片/交叉验证 ≥80%） | GR §7 | 按设计"可等需要再建"，**但既然诊断层是确定性的，这个缺口比设计预想的轻** |
| 多用户纪律（user_hash / 知情同意） | GR §10 | 未触发；一旦纳入 20 人语料即阻断 |

---

## 2. 实施了哪些功能，手段怎么样

### 2.1 做得好的（手段评价）

**① bigram 检索的实现比方案更优 —— 方案本身有错，实际交付修正了它。**
方案 §5 说「`bm25()`/`snippet()` 正常可用」。实际交付**没有**依赖 FTS5 的 `snippet()`：
`indexing.py` 的 `_snippet()` 在 Python 侧用 `raw` 原文列裁窗并做高亮。为什么这是对的——
`messages` 的索引列存的是 bigram 改写文本，FTS5 的 `snippet()` 只能看到改写后的列，
输出必然是二元组汤；方案那句"正常可用"是**基于未实测的假设**。实测 `search` 输出的
`…致的简短，可以尝试以下几种不同风格的精简方案：` 干净可读，证明这条路走通了。

**② 结构化优先、文本回退的双口径设计，方向正确。**
`steps` 表把 `tool_call`/`tool_result` 从 note 文本升级为结构化的 `phase`/`status`/`error`，
并按 `_step_of()` 兼容两种 raw 形态（`kind` 与 `entry_type`）。这是方案 §3 想要的，
实现方式聪明——不新建 `tool_calls`/`tool_results` 两表，而是合并成 `steps` 一张表用 `phase` 区分。
实测 `steps` 有 30956 行真实数据（call 15531 / result 15425）。

**③ 错误分析是我见过最扎实的一层。**
`report-errors` 的分母与 `steps` 表 `status='error'` **精确一致（261）**；
三分类有明确的规则优先级（规则序=优先级）；归一聚类把路径/引号/数字替换为占位符，
所以「cannot edit `X`: file changed since it was read」能把 26 次不同文件聚成一条 pattern；
每条 pattern 都挂真实锚点。`suggest-agents` 更进一步把它翻译成可执行的 AGENTS.md 条目。
**这构成了一条完整、可审计、无 LLM 的证据链。**

**④ `report-traces` 提供了独立交叉校验源。**
WorkBuddy 的 OTel trace 给出工具失败率 **1.1%**（74/6702），与 `steps` 表口径的 **1.7%** 同量级。
两个互相独立的遥测通道彼此印证——这恰好是方案 §7.3 想要的"交叉验证"，而且**不需要 LLM**。

**⑤ 工程纪律到位。** 179 单测 11.6s 全绿（2 skipped）；`--db` 缺数据时显式 `[warn]` 并回退而非静默；
`suggest-agents` 明确"绝不直接改 AGENTS.md"，只产出建议池。这些都符合你自己的 AGENTS.md 纪律。

### 2.2 做得不好的（手段评价）

**① 两条口径的成败判定逻辑不一致，且没有单一真值源。**
`_is_error_status()`（steps 口径）把 `success/completed/ok` 都算成功；
`collect_stats()`（note 口径）只把 `success` 算成功。同一个概念在两处两套定义，
没有共享常量、没有对账测试。**这就是 §3.1 那个 50 倍偏差的根因。**

**② 依赖降级路径是"静默走样"而不是"显式失败"。**
`cards.py` 的 PyYAML 降级解析器把 `anchors` 读成字符串，于是锚点校验被 `isinstance(anchors, list)`
静默跳过，报告却打印"全部卡片满足 §8 规范，可并入主库"。这与你自己 AGENTS.md 第 16 条
「不要把'跳过'伪装成'通过'」直接冲突。**降级必须让下游能区分"未执行"与"不适用"。**

**③ 结论口径没有把"警告"纳入。**
`render_report` 只在 `summary["error"]` 时改结论，`warn` 不进结论。而"锚点非列表形态"
本身**就是 §8 规范违背**（规范要求 `anchors: [{session_id, ...}]`），却被降级成警告，
于是 4 张卡全部带规范警告时仍输出"可并入主库"。

**④ 已有文档与代码状态漂移（会误导后续维护）。**
- `DESIGN_VS_ACTUAL.md` 标题写"2026-10-06 v0.10 收口状态"，但正文里同时出现
  **132 个单测 / 148 测试全绿 / 157 测试全绿**三个数字；实测是 **179**。
  三个数字都是历史阶段的遗留，混在一份文档里。
- 同文档第 144 行末"模型级归属未入库"**已过期**（`sessions.model` 已存在）。
- README 写"1949 会话 / 54838 消息"，实测库是 **1946 会话 / 55203 消息**（快照时点不同，
  但没有任何"截至某时点"的标注，无法核对）。
- `docs/AUDIT-2026-10-05.md`（我上一轮的产物）描述的是旧版本，未标注已过时。

**⑤ `steps` 表的 `status` 语义没有文档化，且各源取值不统一。**
实测 `(phase, status)` 只有 4 种组合：`call/None`(15531)、`result/completed`(13458)、
`result/success`(1706)、`result/error`(261)。即 **`status` 只在 result 相有意义，call 相恒为 NULL**；
且 `completed`（WorkBuddy/DSH）与 `success`（AutoClaw）是**同义不同词**。
`steps` 的表注释写的是"`status` -- result 相：ok / error / 源端原文"，说的是 `ok`，
实际数据里**一个 `ok` 都没有**。注释与数据不符，正是文本回退口径踩坑的土壤。

---

## 3. 三个实测确认的缺陷（含证据与修法）

### 3.1 【阻断性】`report-tools` 无 `--db` 时失败率虚报 50 倍

**现象**（两条命令，同一个库）：

```text
$ python -m harvester report-tools                       # README 口径二
- 工具数: 78 | 总调用: 15587 | 总失败: 13726（88.1%）
| web_fetch | 59  | 0   | 65   | 110.2% |     ← 失败数 > 调用数
| pwsh      | 1709| 0   | 1712 | 100.2% |     ← 同上
| Read      | 1953| 0   | 1953 | 100.0% |     ← 100% 失败

$ python -m harvester report-tools --db harvester.db      # README 口径一（推荐）
- 工具数: 78 | 总调用: 15531 | 总失败: 261（1.7%）
| edit      | 885 | 829 | 56 | 6.3% |
| Edit      | 1716| 1669| 47 | 2.7% |
```

**根因**（`harvester/toolstats.py:71`，note 回退口径）：

```python
if status == "success":        # ← 只认 success
    st.success += 1
else:                          # ← completed / ok 全部落进失败
    st.error += 1
```

而真实数据里 result 相的状态分布是：

| status | 条数 | 占比 |
|---|---:|---:|
| `completed` | 13458 | 87.2% |
| `success` | 1706 | 11.1% |
| `error` | 261 | 1.7% |

**13,458 条 `completed` 被全部计成失败**，正是 WorkBuddy transcript 与 DSH 两个主力源的形态。
测试没抓住的原因：`tests/test_v06.py` 的夹具**只造了 `status="success"` 与 `status="error"`**，
没有一条 `completed`——夹具比真实数据窄，断言在一个不可能失败的世界里恒真。

**独立反证**：`report-traces` 从 WorkBuddy 的 OTel trace 独立测得工具失败率 **1.1%**（74/6702）。
两条独立遥测通道都指向 1–2%，**88.1% 只能是计算错误**。

**一行修法**：让两个口径共用同一判定。

```python
# toolstats.py
_OK_STATUS = ("success", "completed", "ok")

def _is_error_status(status: str) -> bool:
    return status not in _OK_STATUS

# collect_stats 里改为：
if status in _OK_STATUS:
    st.success += 1
else:
    st.error += 1
```

**配套（必须做，否则同类问题会再犯）**：
1. 加一个**对账测试**：同一夹具分别跑 `collect_stats`（note 口径）与 `collect_stats_from_db`
   （steps 口径），断言两者的 calls/success/error **完全相等**。这是"两条口径必须一致"的机器化约束。
2. 夹具必须包含 `status="completed"`、`status=None`（call 相）、`status="unknown"` 三种真实形态，
   并**故意破坏一次**（把 `completed` 从 `_OK_STATUS` 拿掉）确认断言会红——符合你 AGENTS.md 第 4 条。
3. README 第 244-245 行要么修好，要么在口径二那行加显式警告。**现在"口径二"是坏的那个，
   但 README 把它和推荐口径并列陈述，没有任何提示。**

### 3.2 【G3 闭环】`cards validate` 锚点校验 0 个，结论自相矛盾

**现象**（实测，`--root ~/.workbuddy/knowledge/cards --db harvester.db`）：

```text
- 卡片 4：通过 0｜警告 4｜错误 0
- 锚点校验 0 个，未命中索引库 0 个        ← 一个锚点都没查
## kc-20261006-0001-edit-before-read.md
- [警告] anchors 非列表形态（§8 要求 [{session_id, ...}] 列表）      ← 报了规范违背
...
结论：全部卡片满足 §8 规范，可并入主库。    ← 与上面的警告和"警告 4"直接矛盾
```

**根因**（两个叠加）：

1. **PyYAML 未安装**（本机实测 `ModuleNotFoundError: No module named 'yaml'`），
   于是 `cards.py:45-51` 的降级解析器把 `anchors` 这一行读成**字符串**。
   而 `validate_cards:122` 的锚点校验被 `if isinstance(anchors, list)` 挡住 → **静默跳过，计数 0**。
   → 修法：降级解析器遇到行内 `[...]` 流式序列要真的解析成 list（或用 `json`/手写最小解析）；
   **并且当锚点校验被跳过时，必须输出 `warns` 或单独的"未执行"计数**，不能只是 `anchor_checked=0`。
2. **结论只认 error 不认 warn**（`cards.py:169-173`）。而"anchors 非列表形态"**本身就是 §8 规范违背**，
   应升为 error；退一步，结论也必须区分"有警告"。

**卡片文件本身其实是对的**：`kc-20261006-0001` 的 frontmatter 写的是
`anchors: [{session_id: "dsh:--C--...zstd", turn: 263}]`——合法的 YAML 流式序列，
**用 PyYAML 解析会得到 list**。所以这是**校验器的缺陷，不是卡片的缺陷**。

**影响**：`DESIGN_VS_ACTUAL.md` 把"首张已验证卡片 kc-20261006-0001"当作诊断→修改闭环的终点证据，
而这张卡的**锚点从未被真正查库验证过**（`anchors` 未解析、`_anchor_known` 未执行）。
闭环的最后一环目前是空的。

**修法**：
1. 修降级解析器（约 15 行）；
2. `warn` 影响结论文案，或把 `anchors 非列表` 升级为 error；
3. 加测试：一张**真实形态**的卡片（`anchors: [{session_id: "...", turn: N}]`），
   在**无 PyYAML** 环境下也必须 `anchor_checked >= 1`；再造一个 `anchors` 指向不存在 sid 的卡片，
   断言 `anchor_misses >= 1`。
4. 把 PyYAML 写进 README 的依赖边界（当前 README 只说 playwright 是可选的）。

### 3.3 【一致性】README 的默认用法是坏的那个

README 241-246 行把两条口径并列：

```text
# 口径一（推荐）：先 index 再用 --db，读结构化 steps 表，含重试/放弃率
python -m harvester report-tools --db harvester.db --out tools_report.md
# 口径二：无索引时实时扫描（note 标记汇总，无重试/放弃统计）
python -m harvester report-tools --sources sources.json --out tools_report.md
```

口径一标了"推荐"，口径二没有任何风险提示。但**口径二当前给出 88% 的错误数字**。
一个照 README 敲第二条命令的用户会拿到一份完全错误的工具体检报告——
而 G1 正是方案的第一个目标。修 3.1 后此条自动缓解，但**仍应加"两口径结果应当一致"的提示**。

### 3.4 附：上一轮审计的三个缺陷，两个已修，一个已解释

| 上一轮缺陷 | 现状 |
|---|---|
| MCP `pack_context` 传 int 当 `(rec, no)` | **已修**。`mcpserver.py:99` 现为 `[(self._load_by_no(n)[0], n) for n in nos]`；我逐个调了 4 个 MCP 工具，`pack_context` 输出 1947 字符、`### [1] 会话（预算 250）` 正确，无 outline dict 泄漏 |
| FTS5 中文检索失效 | **已修**，且修得比我的建议更好——不是换 tokenizer，而是 `unicode61` + 插入侧 bigram 预改写 + `raw` 原文列 + Python 侧裁窗。2 字词（方案/识别/转换/登录）与 3 字词全部命中 |
| `weblogin check` 恒 LOCKED | 未变（物理限制），但**现在是 9 源格局里的兜底通道，重要性已被稀释**，不再是问题 |

---

## 4. 建议

### P0 — 立刻修（都是小改动，但影响结论可信度）

1. **修 `toolstats.py` 的成败判定**（1 行 + 共用常量），并按 §3.1 补对账测试与真实形态夹具。
   **这是唯一一条会让现有报告结论反转的缺陷，优先级最高。**
2. **修 `cards.py` 的降级解析器与结论口径**（约 20 行），并补"无 PyYAML 也必须校验锚点"的测试。
   把"未执行"与"通过"在输出里分开——这是你自己 AGENTS.md 第 16 条的要求。
3. **在 README 标注弃用/风险**：口径二修好前加警告行；`docs/AUDIT-2026-10-05.md` 头部加
   "历史版本，已被 v0.5+ 取代"。
4. **给 `DESIGN_VS_ACTUAL.md` 做一次数字对账**：把 132/148/157 三个测试数统一为当前值，
   删除已过期的"模型级归属未入库"，并给"1949 会话/54838 消息"加截至时点。
   **文档里的数字必须能被一条命令复现**，否则会像这次一样互相矛盾。

### P1 — 补上缺口里最要紧的两个

5. **`regress` 端到端回归语料**。这是方案 §6 里唯一"替代物不等价"的缺口，
   也是本次发现的两个缺陷**都能被它抓住**的原因（两者都在单测覆盖之外）。
   做法：按工具覆盖率分层抽样 10–20 个会话固定落盘，一条命令跑完全流程
   （index → report-tools → report-errors → suggest-agents → cards validate），
   产出可比对的快照并 diff。**优先于补 LLM 组件。**
6. **锚点细化到 turn/message_id 级**。现在 `#263` 这种 seq 锚点在人复核时要翻整会话；
   方案 §3 的 `(session_id, turn, message_id, step_id)` 值得做到。有了它，
   `triage` 输出的"动作"命令能直接定位，卡片 evidence 也能自动带出原文。

### P2 — 按需

7. **绕行检测**（连续相同工具调用计数）——G1 最后一个小项，纯确定性统计。
8. **`sources` 表 + `schema_fingerprint`**：只为 DSH 0.3 漂移兜底。补 canary 覆盖 DSH 即可，
   不必上完整 sources 表（AutoClaw 的 PRAGMA 守卫 + canary 已是成熟模式，照抄）。
9. **motif 频率矩阵 / 群体视图**——但**在没有第二个用户之前，群体视图没有数据**，
   优先级应当低于 §6。建议等真的纳入多人语料再建，否则是空转。
10. **LLM 归因组件**：**我建议继续不建**。理由：`report-errors` 已 100% 覆盖真实库
    （261 条全归类、unclassified=0），`suggest-agents` 已产出带证据的可执行建议；
   而 `report-traces` 提供了确定性交叉校验。LLM 在此处的边际价值低，
   且会引入方案 §11-4 的"归因反向错误"风险。**确定性已经把这条链路走完了。**

### P3 — 一个战略性提醒

11. **别再扩源了，把深度做在已有 9 源上。** 现在元宝 1223 会话语料已入库，
    但 `report-skill` 的 94 次调用里几乎没有网页 Chat 的贡献（Chat 平台无工具遥测）。
    **采集广度已经远超方案，而诊断深度还有 §1.3 的表里 6 个缺口。**
    你自己在方案 §11-7 写的"厚语料薄分析"纪律，现在的风险恰恰是**语料太厚**——
    1946 会话 / 55203 消息里，真正能产出 G1/G2 证据的是 `steps` 表的 30956 行，
    而这只来自 4 个 agent harness 源（workbuddy-transcript/dsh/autoclaw/vscode-copilot）。
    下一步的价值在**从这 30956 行里多榨出东西**（绕行、motif、锚点细化），
    而不是再多接一个数据源。

---

## 附录 A：本次核查的可复现命令

```powershell
$PY = "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
cd C:\Users\yianyao\WorkBuddy\2026-10-05-08-38-51\session-harvester
$env:PYTHONIOENCODING = "utf-8"

# 基线：179 测试全绿（11.6s，2 skipped）
& $PY -m unittest discover -s tests

# 缺陷 3.1：两条口径给出量级不同的答案
& $PY -m harvester report-tools                      # 88.1% ← 错
& $PY -m harvester report-tools --db harvester.db    #  1.7% ← 对
& $PY -m harvester report-traces                     #  1.1% ← 独立交叉校验

# 缺陷 3.2：锚点校验 0 个，结论自相矛盾
& $PY -m harvester cards validate --root "$env:USERPROFILE\.workbuddy\knowledge\cards" --db harvester.db
& $PY -c "import yaml"                                # ModuleNotFoundError ← 降级路径被触发

# 已修项的复核
& $PY -m harvester search 方案 --db harvester.db      # 2 字中文词命中
& $PY -m harvester suggest-agents --db harvester.db   # 产出 agents_suggestions.md
& $PY -m harvester triage --db harvester.db
```

## 附录 B：关键文件与行号索引

| 事项 | 位置 |
|---|---|
| 缺陷 3.1 根因：note 口径只认 `success` | `harvester/toolstats.py:71` |
| steps 口径判定（正确） | `harvester/toolstats.py:82-83` |
| 夹具缺 `completed` 形态 | `tests/test_v06.py:193-210` |
| 缺陷 3.2 根因：降级解析器 | `harvester/cards.py:45-51` |
| 锚点校验被 `isinstance(list)` 挡住 | `harvester/cards.py:122` |
| 结论只认 error 不认 warn | `harvester/cards.py:169-173` |
| bigram 预改写（实现优于方案） | `harvester/indexing.py:71-102` |
| 插入侧双侧改写 | `harvester/indexing.py:222` / `283` |
| 查询侧同步改写 | `harvester/indexing.py:326-339` |
| Python 侧原文 snippet | `harvester/indexing.py:351-393` |
| MCP pack_context（已修） | `harvester/mcpserver.py:99` |
| README 两条口径并列（缺警告） | `README.md:241-246` |
| 过期数字 132/148/157 | `docs/DESIGN_VS_ACTUAL.md` |
| 历史版本审计（需标注） | `docs/AUDIT-2026-10-05.md` |
| 真实卡片（4 张，规范正确） | `~/.workbuddy/knowledge/cards/` |
| 建议池产出（9 条 + 9 待归因） | `agents_suggestions.md` |
| walkthrough DB | `harvester.db`（1946 会话 / 55203 消息 / 30956 steps / 319.3 MB） |
