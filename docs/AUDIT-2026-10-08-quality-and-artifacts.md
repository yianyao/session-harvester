# 工程质量 · 代码质量 · 产物有效性评审（v0.18.1）

> 评审对象：session-harvester @ `9cd6ad1`（v0.18.1，2026-10-07）
> 评审日期：2026-10-08 · 方式：**读源码 + 跑全部测试 + 独立 SQL 复算产物数字 + 逐份读生成产物**
> 与前两份文档的关系：`docs/AUDIT-2026-10-05.md`（历史存档）、
> `docs/AUDIT-2026-10-08-design-conformance.md`（设计对照）。
> 本文覆盖工程/代码质量与**产物有效性**，是三份里唯一做"产物数字可复现性审计"的。

---

## 0. 先说没做到的

1. **未逐条人工判断 261 条错误的分类正确性**。我核了分母（261 与 steps 表精确一致）、
   结构（分桶/锚点/归一聚类齐备）与规则优先级，但"100% 归类"只等于"规则覆盖了全部样本"，
   **不等于分对了**。这是本文最大的未覆盖项。
2. **未验证 9 个 adapter 的解析保真度**。只看了解析后的库与字段形态，未逐源抽样比对原始文件。
3. **未实跑**：`weblogin`（需浏览器）、`api-serve`（HTTP 服务）、`draft`、`cards new`、`sync`。
4. **未审计 `apiserve.py`**（v0.17 新增的只读 HTTP API），本轮时间集中在产物有效性上。

---

## 1. 工程质量与代码质量

### 1.1 规模与结构（实测）

| 项 | 实测 |
|---|---|
| 生产代码 | `harvester/` 36 文件 / **7681 行** |
| 测试代码 | `tests/` 17 文件 / **3538 行**（测试:生产 ≈ 0.46:1） |
| 测试结果 | **224 tests，OK，20.9s** |
| 数据源 | **9 个实装**（qianwen-raw / deepseek-export / yuanbao-raw / doubao-raw / vscode-copilot / workbuddy / workbuddy-transcript / autoclaw / dsh） |
| 库规模 | 1946 会话 / 55203 消息 / **30956 steps** / 334 MB |
| 版本控制 | git 干净，76 个已跟踪文件，**提交历史 9 条，语义清晰**（v0.15 → v0.18.1） |

**提交历史质量高**，值得单独肯定：`fix: triage cards_scanned 语义修正——未配置=None，空目录=0（原二者同现 0，UI 无法区分）`
这类提交信息说清了"原来错在哪、为什么错、改成什么"——这正是我看过的多数项目做不到的。

### 1.2 代码质量扫描（实测）

| 信号 | 结果 | 评价 |
|---|---|---|
| bare `except:` | **0** | 好 |
| `except Exception` | 21 处 | 可接受（多为 adapter 隔离，有注释说明"单条失败不拖垮整体"） |
| `# noqa` 抑制 | 34 处 | 可接受，且都附了理由（`# noqa: BLE001 - ...`） |
| 超长函数 | `cli.py:main` **231 行** | **最大技术债** |
| 次长函数 | `autoclaw._build_messages` 138 行 / `deepseek_export._ds_walk` 136 行 / `workbuddy_transcript.load_session` 135 行 / `triage.collect_triage` 129 行 / `dsh.load_session` 123 行 | 偏高但可读，适配器解析逻辑天然长 |
| TODO/FIXME（生产代码） | **0** | 好。仅文档里有两处（且是描述第三方工具的桩，不是本项目欠债） |
| 文件编码 / 行尾 | 全部 UTF-8 无 BOM + LF（连 `.gitignore` 也是） | 好，与项目 CRLF 纪律一致 |

### 1.3 发现的工程质量问题（按严重度）

**① 【中】没有任何依赖清单 —— 而项目自称零依赖，实际有两处可选依赖**

实测：`pyproject.toml` / `setup.py` / `requirements.txt` / `setup.cfg` **全部缺失**。

但代码里确实存在可选依赖分支：
- `cards.py` 的 PyYAML（无则降级到自写解析器）；
- `dsh.py` 的 zstd 四层降级（`compression.zstd → zstandard → zstd.exe → node`）；
- `weblogin.py` 的 playwright。

后果：v0.16 那次"降级解析器把 anchors 读成字符串 → 锚点校验静默跳过 0 个"的缺陷，
**根因环境就是没有依赖清单**——没有任何地方声明"这些是可选依赖、缺了会走降级路径"。
建议加一个最小 `pyproject.toml`，`[project.optional-dependencies]` 里列
`yaml` / `zstandard` / `playwright` 三组 extras。这同时解决"换设备怎么装"。

**② 【中】`cli.py:main` 231 行**

22 个子命令的 argparse 定义堆在一个函数里。建议拆成 `cli/` 包或在各模块内注册
（`def add_parser(sub)`），主函数只做 dispatch。这是当前唯一会随功能增加而线性恶化的结构问题。

**③ 【低】缺少端到端回归，只有单元测试**

17 个 `test_vNN.py` 全是零件级/特性级；`verify/` 下是一次性采集验证脚本（含 png/txt 日志），
不是可复跑的回归。实测无 `regress` 命令、无 golden 快照比对。
**这正是两轮评审能找到缺陷、而 224 个单测都没找到的原因**——单元测试的夹具比真实数据窄。

**④ 【低】`extras` 缺失导致降级路径缺乏"被测试的保障"**

v0.16 已在 `test_v16.py` 里加了"两解释器行为必须一致"的测试（移除了 `skipUnless`），
方向正确；但 PyYAML **存在**时的路径（`_HAS_YAML=True` 分支）在本机无法被测试覆盖
（本机未装 PyYAML）。建议 CI 跑两遍。

---

## 2. 是否实现了声明的功能

### 2.1 上一轮审计的三个 P0 缺陷：**全部真修了（已实测复核）**

| 缺陷 | 复核证据 | 结论 |
|---|---|---|
| `report-tools` 无 `--db` 时失败率虚报 50 倍 | 修复前 88.1% → **现在 note 口径 1.4%（239/16946）、steps 口径 1.7%（261/15531）**，两者同量级 | ✅ 真修 |
| `cards validate` 锚点校验 0 个 + 结论自相矛盾 | **现在"锚点校验 4 个，未命中 0 个"**，且 4 张卡全部"精确命中"（我用 SQL 独立验证了 4 个 `anchors.session_id` 都真实存在） | ✅ 真修 |
| FTS5 中文检索失效 | 2 字词（方案/识别/转换/登录）与 3 字词全部命中 | ✅ 已修（上一轮已确认） |
| MCP `pack_context` 元组契约错误 | 4 个 MCP 工具逐个调通，`pack_context` 输出正常无 dict 泄漏 | ✅ 真修 |

**注意一个诚实的细节**：note 口径（1.4%）与 steps 口径（1.7%）**数字仍不完全相等**。
我核了原因——note 口径覆盖更广（16946 次调用 / 79 工具，含 steps 表未收录的会话片段），
不是残留缺陷。但**目前没有测试断言两者相等**（`test_v16` 测的是"共用 `_OK_STATUS` 常量"），
建议补一条"同源夹具下两口径必须逐工具相等"的对账断言。

### 2.2 README 声明 vs 实测：一致

| README 声明 | 实测 | 判定 |
|---|---|---|
| 9 个数据源已实装 | `select distinct source` = **9 个** | ✅ |
| 1946 会话 | 1946 | ✅ |
| 54838 消息 | **55203**（README 略旧，差 365 条，属快照时点差） | 🔶 轻微 |
| 零第三方依赖 | 主体成立，但有 3 处可选依赖未声明（见 1.3①） | 🔶 |
| 224 单测 | 224 | ✅ |

**总体：声明的功能基本都实现了，且上一轮我指出的缺陷是真修而不是假修。**
这一点必须明确肯定——修复质量高，连"结论口径要区分警告"这种细节都改了。

---

## 3. 重点：生成产物对改进 harness / 工具 / skill 是否合理、完善

这是本次评审的核心。结论先说：

> **产物方向正确、格式规范、证据链设计意识强，但存在 4 个系统性缺陷，
> 其中最严重的是"工具改名分裂"和"不区分错误是否被吸收"——
> 这两个直接导致"改进哪个工具"的结论不可靠。**

### 3.1 工具改进（G1）—— **4 个缺陷**

实测原始输出（`report-tools --db`）：

```text
| 工具 | 调用 | 成功 | 失败 | 失败率 |
| web_fetch |  59 |  42 |  17 | 28.8% |
| ls        |  62 |  50 |  12 | 19.4% |
| edit      | 885 | 829 |  56 |  6.3% |
| Edit      |1716 |1669 |  47 |  2.7% |   ← 同一个工具，两行
| read      |1304 |1295 |  21 | 1.6% |
| Read      |1948 |1948 |   0 | 0.0% |   ← 同一个工具，两行
```

**缺陷 1【严重】工具名大小写/别名分裂成 6 族，失败率被稀释且不可横向比较**

实测分裂情况（`LOWER(tool)` 合并后）：

| 工具族 | 分裂形态 | 分裂口径失败率 | **合并后失败率** | 合并总量 |
|---|---|---|---|---|
| edit | `Edit`(workbuddy) / `edit`(autoclaw+dsh) | 6.3% / 2.7% | **4.0%** | 2601 |
| read | `Read` / `read` | 1.6% / 0.0% | **0.6%** | 3265 |
| web_fetch | `web_fetch`(dsh) / `WebFetch`(workbuddy) | 28.8% / 10.2% | — | 163 |
| write | `Write` / `write` | 1.5% / 1.1% | **1.3%** | 1807 |
| glob | `Glob` / `glob` | 4.4% / 4.4% | 4.1% | 171 |
| grep | `Grep` / `grep` | 0.9% / 0.0% | 0.7% | 577 |

后果很实际：报告显示 `edit` 6.3% / `Edit` 2.7%，用户无法判断"Edit 工具到底多不可靠"，
也无法和 `read` 比。而**合并后 `web_fetch` 26.2% 才是真正该优先修的工具**——
它是被分裂埋掉的第一名。

**缺陷 2【严重】只数"出过多少次错"，不区分"错误是否被吸收"**

我独立复算：对每个 error 步骤，看同一会话内同工具后续是否最终成功——

```text
错误后同会话同工具最终成功（=错误被吸收，属正常重试）: 230
错误后再未成功（=真·未解决，才是该修的）              :  31
```

**报告把这两类混在一起统计。** 关键后果：`old_string`/`file-changed` 这条
"Edit 前必须先 Read" 的错误有 63 条（37+26），但绝大多数属于 agent 自己**已经按提示重试成功**的
——它是**正常的 fail-closed 行为**，不是缺陷。真正该看的是那 31 条"再未成功"的。

**这个切分不是我一厢情愿——我验证了它确实能分开噪声与真问题。**
按 errstats 的三分类规则复算两组的类别分布：

| 类别 | 已自愈(230) | 占比 | 未解决(31) | 占比 |
|---|---:|---:|---:|---:|
| **env**（环境硬限制） | 87 | 38% | **20** | **65%** |
| **tool_interface**（用法可即时纠正） | 114 | **50%** | 4 | **13%** |
| context | 14 | 6% | 0 | 0% |
| unclassified | 15 | 7% | 7 | **23%** |

**两组呈现清晰的反向分布**：已自愈的以 `tool_interface` 为主（50%，agent 读到提示就改对了），
未解决的以 `env` 为主（65%，环境限制重试也没用）。
且 `unclassified` 在未解决组里高出一倍（23% vs 7%）——**说不清原因的错误本来就更难自愈**，
这符合直觉，也说明该切分有诊断价值。

顺带得到一个"优先级重排"的实例：`tool_permission_revoked` 共 27 次，
但其中 **21 次已自愈、只有 6 次未解决**。报告把 27 次当成一个高频问题，
而真正值得处理的是那 6 次。

**未解决集合的真实 TOP 模式**（这才是该修的清单）：

```text
   6  tool_permission_revoked
   5  browser_instance_unknown          ← harness 应立即重建实例
   3  subagent depth 2 exceeds maxDepth 1
   2  host_bridge_declared_error
   2  skill_asset_invalid / skill_asset_not_executable
   2  web fetch failed
   2  glob error: Search path does not exist
   1  tool call timed out after 30000ms ← 超时阈值可能偏低
   1  unknown job <id>
```

建议在 `steps` 配对基础上加 `recovered` 字段，报告分列"已自愈（正常重试）/ 未解决"，
**只把后者列进待修清单**。

**缺陷 3【中】小样本未标记，噪声与信号同权**

实测 **22 个工具总量 <5**。报告里 `skill_run_asset 3/3 = 100.0%`、
`subagent_wait 3/4 = 75.0%` 被排在最前面，而 `Edit 103/2601 = 4.0%` 排在后面。
**3 次 100% 失败不构成"该修"的证据**，103 次才是。建议加"样本量 / 置信区间"列，
或默认过滤 N<10（可 `--min-calls` 开关）。

**缺陷 4【中】报告没有数据快照标识，数字无法复现**

实测报告文件的 mtime 与实际库状态不对齐：

```text
G2_agents_suggestions.md   2026/10/06 14:39   ← 报告
docs/reports/distill_queue.md 2026/10/06 17:11
harvester.db               2026/10/06 17:54   ← 库比报告新
```

我把建议池的数字逐条回查数据库：

| 建议池声称 | 实际可复现 | 判定 |
|---|---|---|
| #1 Edit 前置错误 102 次 | 并集 **118**（或我尝试的多种组合都给不出 102） | ❌ 不可复现 |
| #2 tool_permission_revoked **27** 次 | 27 | ✅ |
| #3 web fetch failed **22** 次 | **16** | ❌ 不可复现 |
| #7 输出超限 **7** 次 | 7 | ✅ |

**9 条里 3 条数字对不上。** 且无法判断是"算错"还是"库已增长（报告过期）"——
**因为报告里没有库的会话数/steps 数/生成时刻快照**。
对一个"每条结论都必须可溯源"的工具来说，这是硬伤：
可复现性是这个工具存在的理由。建议报告头强制写入
`库指纹：sessions=1946, steps=30956, db_mtime=..., generated_at=...`。

### 3.2 Harness 改进（G2）—— **方向好，但一半是"工作流建议"而非"harness 修复"**

**做得好的**：9 条建议**每条都带真实锚点 + 原文证据 + 频次**，且格式统一、
明确声明"只是建议池，不自动写 AGENTS.md"。质量远高于一般自动化报告。

但我逐条读后，发现**建议的落点被混淆了**——用户的需求是"改善 Agent Harness 本身"，
而 9 条里有相当一部分其实是"教 agent 怎么绕开 harness 的毛病"：

| # | 建议 | 真正该归谁 |
|---|---|---|
| 1 | Edit/Write 前必须先 Read | **工作流**（agent 纪律）——合理 |
| 2 | 权限被撤销的工具不要原地重试 | **工作流**，但根因是 harness 的 revoke 机制无缓存，应同时提 harness |
| 3 | 网络类失败先重试一次 | **harness**（缺自动重试）；写成 agent 纪律是让 agent 替 harness 兜底 |
| 4 | 写 ACL 失败不要反复重试 | **harness/环境**（Win32 5 应给出可操作提示） |
| 5 | 引用旧路径前先确认存在 | **工作流**——合理 |
| 6 | browser 实例失效先重建 | **harness**（应自动重建实例） |
| 7 | 输出超限先收窄再取 | **harness**（应自动分页/落盘，现在让 agent 自己想办法） |
| 8 | 沙箱拦截不等于命令有错 | **harness**（sandbox-center 的错误信息本身无信息量："missing actual resource subject"） |
| 9 | 子代理深度受限改平铺 | **工作流**——合理 |

**也就是 9 条里约 5 条（3/4/6/7/8）本质是 harness 缺陷，被写成了 agent 纪律。**
后果：agent 会一直替 harness 兜底，而 harness 永远不会被修。
**建议报告增加"责任方"字段（harness / 工具 / 工作流）**——
这是把"发现"变成"改进"的关键一步，也是设计文档 G2 的本意。

**另外**：建议池**没有任何采纳状态跟踪**（无"已并入/已否决"列）。
方案 §9 的止损线是"M1 报告产出后一个月内未触发任何实际修改则项目停止"，
但**当前没有任何机制能机械化判定"是否触发了实际修改"**。建议加一列 `status` + 并入日期。

### 3.3 知识卡片（G3）—— **形态优秀，证据校验有缺口**

我逐张读了 4 张卡。**质量出乎意料地好**，尤其 `kc-...-0004`：
不只描述现象，还给出了**触发机制**（"Edit 的 old_string 匹配基于上次 Read 时的快照；
Read 之后文件被外部改动则快照过期，harness 用此错误 fail-closed 保护"）、
4 条分层做法、并自我标注 `confidence: 0.3（草稿）`。这确实是可复用的知识资产。

三种 type 都覆盖到了：`pitfall`(0001/0004) / `insight`(0002 元认知干预技术) / `workflow`(0003 wechat skill 用法)。

**但有两个缺口**：

**① 证据校验只验"会话存在"，不验"引文真的在那一回合"**

`cards.py` 的锚点校验逻辑是 `_anchor_known(sid)` → 查 `sessions` 表该 sid 是否存在。
**它从不打开会话去核对 `evidence` 里的原文是否真的出现在 `turn` 处。**
后果：一张卡片可以引用一段**根本不存在**的原文，只要 session id 拼对就能通过 validate。
对一个把"证据原文"列为强制字段的规范，这是最该补的一环。
我实测确认 4 个 session_id 都真实存在（这点没问题），
**但"原文是否真在 #263 处"这层校验目前不存在**。

**② `turn` 字段不稳定**

实测：`0004` 是 `turn: null`，`0001/0002/0003` 是具体数字。同一规范下两种形态，
且 `null` 时锚点失去定位能力（只能定位到会话）。建议 `cards new` 必须落具体 seq，
`validate` 把 `turn: null` 列为警告。

### 3.4 Skill 改进（G4）—— **骨架合理，但每 skill 样本太小，且未与 skill 文件挂钩**

`report-skill` 输出：31 个 skill / 94 次调用 / 成功率 93%（我独立复算 **96%**，
差异来自报告把 3 次未配对的计为"无结果"而非失败——分母口径可接受）。
它给出了**触发任务**、**调用后工具链**、**锚点清单**，`--skill` 深挖输出可蒸馏清单。骨架是对的。

**缺口**：31 个 skill / 94 次调用 = **平均每个 skill 只有 3 次调用**。
`delivery-artifact` 有 20 次算多的，多数 skill 个位数。
**3 次调用无法支撑"该 skill 哪里要改"的结论**——和 3.1 缺陷 3 同源。
建议报告对 N<5 的 skill 只列不评，并显式标注"样本不足，结论不可用"。

**另一个缺口**：产出的建议**没有回写到 skill 文件本身**
（例如"wechat-article-search 应先搜索验证再问范围"这条 workflow 知识，
最有用的落点是该 skill 的 SKILL.md，而不是知识库里的一张卡）。
设计文档 §6 也没要求这一点，但这是"改善 skill"的真正闭环。

### 3.5 产物有效性总评

**合理的部分（占多数）**：
- 全确定性、无 LLM，可复现的设计意图正确；
- 每条结论都带**会话级锚点 + 原文证据**，可人工核对到具体步骤；
- 三类产物（工具体检 / AGENTS 建议池 / 知识卡片）格式冻结、分工清晰；
- `triage` 能区分"新 pattern"与"已知卡片"，`--since` 窗口可用；
- 卡片质量确实达到"可并入主库"的水平。

**不完善的部分（按改进优先级）**：
1. **工具改名分裂**（6 族）→ 失败率结论不可靠；
2. **不区分错误是否自愈**（230 vs 31）→ 优先级排序错；
3. **报告无库快照**→ 数字不可复现（9 条里 3 条对不上）；
4. **建议无"责任方"** → harness 缺陷被写成 agent 纪律，harness 永不被修；
5. **建议无采纳状态** → 止损线无法机械判定；
6. **小样本未标记**（22 个工具 N<5）→ 噪声与信号同权；
7. **卡片证据只验会话存在**，不验引文位置；`turn` 可为 null；
8. **skill 结论未回写 skill 文件**。

---

## 4. 建议（按投入产出比）

### P0 — 直接决定"改进哪个工具"的结论是否可信

1. **工具名归一**（约 30 行 + 一张别名表）。在 `steps` 写入时或读取时把
   `LOWER(tool)` + 别名映射（`pwsh`/`Bash`/`exec`/`PowerShell` → 执行类）统一。
   **这一条单独就能把被埋掉的第一名 `web_fetch 26.2%` 顶到报告最前面。**
2. **加"错误是否自愈"维度**。在 steps 配对基础上标 `recovered`，
   报告分列"已自愈（正常重试）/ 未解决"。**只把后者列进"待修清单"。**
3. **报告头强制写库快照指纹**（sessions/steps 数 + db mtime + generated_at）。
   顺带解决"数字对不上到底是算错还是过期"。

### P1 — 把"发现"变成"改进"

4. **建议条目加 `责任方` 字段**（harness / 工具 / 工作流）与 `status` 列（待审/已并入/已否决/观察）。
   harness 类缺陷应当输出成**harness 的 issue 清单**，而不是 agent 纪律。
5. **卡片证据校验升级到"引文核对"**：打开会话，确认 `evidence` 原文出现在 `turn` 处；
   `turn: null` 列为警告。这是 §8 规范"强制证据"的应有之义。
6. **`--min-calls` 过滤 + 样本量列**（默认 N<5 只列不评）。

### P2 — 工程

7. **加最小 `pyproject.toml`**，用 `optional-dependencies` 声明
   `yaml`/`zstandard`/`playwright` 三组 extras，并让 CI 跑"有依赖/无依赖"两遍。
8. **`cli.py:main` 拆分**（231 行 → 各模块 `add_parser`）。
9. **补端到端回归**：固定 10–20 个会话做 golden 快照，
   一条命令跑完 index→report-tools→report-errors→suggest-agents→cards validate 并 diff。
   本次评审能找到的 4 个产物缺陷**全都逃过了 224 个单测**，只有它能兜住。
10. **补一条对账断言**：同源夹具下 note 口径与 steps 口径逐工具相等
    （现在只断言了两者共用常量）。

### P3 — 战略提醒

11. **停止扩源，把深度做在产物有效性上。** 现在 9 源 / 1946 会话已远超需要，
    而"改进哪个工具"这个最核心的问题，还因为工具名分裂和自愈未区分而**答不准**。
    先把 3.1 的 4 个缺陷修完，再考虑接第 10 个数据源。

---

## 附录：本次评审的关键复现命令

```powershell
$PY = "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
cd C:\Users\yianyao\WorkBuddy\2026-10-05-08-38-51\session-harvester
$env:PYTHONIOENCODING = "utf-8"

& $PY -m unittest discover -s tests                       # 224 tests OK
& $PY -m harvester report-tools                            # note 口径 1.4%
& $PY -m harvester report-tools --db harvester.db          # steps 口径 1.7%（看到 Edit/edit 两行）
& $PY -m harvester report-errors --db harvester.db
& $PY -m harvester report-skill --db harvester.db
& $PY -m harvester cards validate --root "$env:USERPROFILE\.workbuddy\knowledge\cards" --db harvester.db

# 产物数字可复现性（本文核心发现）
& $PY -c "import sqlite3;c=sqlite3.connect('harvester.db');print([ (r[0],r[1]) for r in c.execute(\"select error,count(*) from steps where error like '%tool_permission_revoked%' group by error\")])"
```
