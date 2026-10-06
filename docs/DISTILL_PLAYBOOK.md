# DISTILL_PLAYBOOK — 会话语料蒸馏语义流程（可移植版）

> 本文档是 session-harvester 套件的组成部分，与宿主无关。
> 任何具备 LLM 能力的 agent（WorkBuddy / AutoClaw / VS Code Copilot / 其他）或人类操作者，
> 均可依据本文档完成「聚合语料 → 分类 → 知识库」的语义步骤。
> 机械步骤一律由套件命令承担，本文档只定义语义规则。

## 0. 路径约定（全部可参数化，无宿主耦合）

| 变量 | 含义 | 默认值 |
|---|---|---|
| `SUITE_ROOT` | 套件根目录（含 `harvester/` 包） | 本文件上两级目录 |
| `KB_ROOT` | 知识库根目录 | `SUITE_ROOT/knowledge` |
| `CORPUS` | 聚合语料文件 | `SUITE_ROOT/corpus/session_corpus.md` |
| `MEMORY_GLOB` | 会话日志来源 | 由 `harvester probe` 发现，见 `sources.json` |

所有写入操作仅限 `KB_ROOT` 与语料目录；**禁止修改任何数据源原始文件**。

## 1. 前置：生成语料（机械步骤，命令执行）

```bash
cd SUITE_ROOT
python -m harvester probe                    # 发现数据源 → sources.json
python -m harvester scan --sources sources.json --out outline
python -m harvester export --all --sources sources.json --out exports
# v0.5 起思考/工具调用条目默认包含（note 角色），蒸馏证据勿关闭；纯对话用 --no-with-notes
python -m harvester aggregate --from exports --out CORPUS
# 或跳过导出直接实时聚合：
python -m harvester aggregate --all --out CORPUS
python -m harvester report-tools --db harvester.db --out tools_report.md   # 结构化口径（steps 表，含重试/放弃率）
python -m harvester report-traces --out traces_report.md   # 运行时遥测（耗时 p50/p95、用户取消）
```

语料每段自带溯源锚点 `<!-- SRC: 工作区 | 文件 -->`。锚点计数应等于会话数，
`WARN` 锚点（无日期/无标题）需人工复核。

## 2. 确定增量范围（语义）

1. 读 `KB_ROOT/INDEX.md`，提取各话题 frontmatter 的 `period`（起止日期）。
2. 语料中日期晚于各话题 `period` 终点的段落 = 增量内容。
3. 全量重建仅在分类体系调整时进行；常规运行为增量追加。

## 3. 归类（语义）

- 先读 `KB_ROOT/INDEX.md` 的既有话题清单；增量内容优先归入既有话题。
- 仅当出现既有话题无法覆盖的**新领域**时，才新建话题文件并同步登记 INDEX。
- 归类判据优先级：**用户明确表达的意图 > 会话中的实际行为 > AI 自身总结**。
  三者冲突时以用户原话为准，并在条目中标注冲突。

## 4. 话题文件结构（稳定格式，勿改字段名）

每个话题文件必须含 frontmatter（`topic` / `period` / `updated`）与三节：

1. **理解轨迹** — 时间线表 `| 时间 | 事件 | 意义 |`，只追加不改写旧行。
2. **当前完整思路** — 整体刷新为最新认知体系（允许改写）。
3. **未决与线索** — 清除已解决项，补充新项；每项须可溯源（指向语料锚点）。

写作规范：本文件面向人阅读——通俗为先，艰涩术语附大白话对照。

## 5. 反馈台账（`KB_ROOT/feedback/tools-and-skills-feedback.md`）

- 条目按三区归位：宿主工具 / 外部工具 / 自建 skill。
- 每条必须含字段：**类型**（偏好/踩坑/改进意见）、**回灌状态**（已回灌/待回灌/观察）。
- 环境类坑（路径、编码、锁、进程语义）除入台账外，须核对是否已录入
  宿主的全局记忆文件（WorkBuddy 为 `~/.workbuddy/MEMORY.md`；其他宿主用其等价机制），缺则补。
- 本文件供 agent 与人扫读——结构稳定、术语精确、不写口语比喻。

## 6. 收口校验（机械 + 语义各半）

机械部分（命令/脚本可判）：
- `KB_ROOT` 全部 `.md` 文件 CRLF 字节数 = 0，UTF-8 可解码；
- `INDEX.md` 话题表与 `topics/` 实际文件一一对应（可用 `harvester kb-stats` 校验）；
- 话题 frontmatter `updated` 已更新为本次执行日期。

语义部分（需 LLM 判断）：
- 每条新增轨迹条目能回指到语料锚点；
- 台账新增条目的回灌状态与实际代码/文档状态一致。

完成后在执行环境的当日日志中留一行变更记录（宿主有日志机制则用之，无则跳过）。

## 7. 与 WorkBuddy skill 的关系

`~/.workbuddy/skills/session-knowledge-distill` 仅为本文档在 WorkBuddy 宿主内的
**薄绑定**（触发词 + 路径映射）。权威定义始终是本文件；
两者冲突时以本文件为准，并应回改 skill 保持同步。

## LLM 组件边界与抽样复核（v0.6 拍板）

语义蒸馏步骤（话题归类、思路提炼、画像归纳）由 LLM 承担，但必须满足
"可抛弃组件位"约束——LLM 不产生权威事实，只产生可被复核的标注：

1. **确定性优先**：能用 steps/report-tools/report-traces 算出的数字
   （调用次数、失败率、重试/放弃、耗时）一律以工具输出为准，LLM 不得
   改写或"修正"这些数字，只能引用并解释。
2. **锚点可回溯**：LLM 产出的每条结论必须带 SRC 锚点（session/turn/行），
   没有锚点的结论视为未验证，不得进知识库正文。
3. **抽样复核**：每批蒸馏结果随机抽 ≥10%（且 ≥3 条）人工/规则复核；
   复核发现的系统性偏差（如把工具总结当思考过程）记入 feedback 台账，
   并回流为下一轮蒸馏的约束。
4. **置信度标注**：LLM 不确定时输出 `confidence: low` 并把条目放进
   "待复核"节，而不是静默丢弃或静默采信。
