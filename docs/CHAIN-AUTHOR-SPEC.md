# chain 长文起草规范（v0.24，供起草 Agent 消费）

> 本规范是 skill/agent 消费品：口径显式、断言可判定。**不确定就标注待补，不得编造。**

## 0. 你的产物

一篇 topic-chain 长文（Markdown，UTF-8，LF 换行），写到规范末尾指定的输出路径，
并通过独立校验器 `chain-validate`（errors=0）。**除该文件外不要修改任何仓库文件，
不要 git commit。**

## 1. 输入（全部为只读素材）

| 素材 | 说明 |
|---|---|
| 蒸馏包 `pack-*-full.md` | coarse 档全量时间线：每会话 `首 user` + `末 assistant`（各截断 400 字符）+ 库快照指纹 |
| 回合索引 `turns-*.md` | **锚点唯一来源**：逐会话列 user 回合 `T1…TN`（turn 编号权威口径）、时间戳、raw 原文前若干字符 |
| 去重报告 `dedupe-*.md` | H40 口径的会话级重复簇与独立代表清单 |
| 参考长文 `C:\Users\yianyao\.workbuddy\knowledge\topics\chain-叙事节奏.md` | **文风与结构范本**（同项目既有 chain，306 行）；只读，勿改 |
| 补充下钻 | `& $venv -X utf8 -m harvester topic chain --id <topic_id> --level fine --sid <sid>` 可看单会话全文（需要时再用） |

venv 解释器（**一切命令用它**，含 PyYAML）：
`C:\Users\yianyao\.workbuddy\binaries\python\envs\default\Scripts\python.exe`

工作目录：`C:\Users\yianyao\WorkBuddy\2026-10-05-08-38-51\session-harvester`

## 2. 锚点规则（最易错，逐条遵守）

1. 锚点只能取自**回合索引里真实存在的 `T<n>`**；`turn` 是 user 消息的 1‑based 序号。
   **回合索引里没有的 turn 不许写**。
2. 锚点里的 `sid` 必须是该主题的成员；**`user 回合 0` 的会话不可挂任何锚点**
   （校验器会判越界）。
3. 正文锚点写法与参考长文一致：`（{sid, turn N}）`，紧跟被引用的具体主张之后。
   同一段落多个锚点就并列写多个。
4. 每个 stage 至少 1 个节点；建议每阶段 3–6 个节点，全篇 20–40 个节点。
5. **不要在 frontmatter 里编造节点**：`anchors[].nodes[]` 的每一项都要在正文里被用到。
6. 引用会话内容时，只能引用你在 pack / turns 索引里**亲眼看到**的文字；
   转述要标"（据标题）"这类限定，不得把标题级证据写成 turn 级证据。

## 3. frontmatter（校验器必填项，缺一即红）

```yaml
---
topic: <主题名，与 topics_meta.db 中一致>
topic_id: <tp-xxxxxxxx-xxx>
members:
  - <每个成员 sid 一行，全量列出（含被去重并入的会话）>
dedup:
  line: bigram-Jaccard>=0.90
  reps: <独立代表数>
  duplicates: <被并入数>
  report: docs/reports/dedupe-<X>.md
anchors:
  - stage: 阶段一·<短名>
    span: YYYY-MM-DD ~ YYYY-MM-DD      # 必须与该阶段成员会话的真实日期一致
    nodes:
      - sid: <成员 sid>
        turn: <整数>
        note: <该回合说明了什么，≤30 字>
  - stage: ...
generated_from:
  db_mtime: "2026-10-09 10:17:07"      # 从 pack 的 db_fingerprint 原文照抄
  sessions: 1964
  steps: 36647
  errors: 294
  generated_at: "<pack 里的 generated_at>"
prompt_version: v0.24-chain-<主题slug>
---
```

- `members` 用**全集**（该主题注册成员），`dedup.reps` 用去重后的独立代表数。
- `span` 与节点日期必须自洽；**若某阶段结论只有标题级证据，在正文里显式写明**。

## 4. 正文结构（照参考长文的节奏写）

1. 一段引言：交代主题是什么、时间跨度、成员口径、证据等级、生成方式。
2. 若干 `## 阶段N · <短名>（YYYY-MM-DD ~ MM-DD）` 小节，3–7 个阶段：
   - 叙述"**怎么想的 → 怎么变的 → 为什么**"；
   - 具体到原话与动作（引用 user 原话用「」），不写空泛评价；
   - 段末用加粗 **为什么…** 收束该阶段的转变原因（可取舍）。
3. `## 元结论：这条链上可迁移的 N 条`：3–6 条，每条挂阶段出处。
4. `## 去重与证据口径`：写明 `dedup` 口径、边界带处置、锚点不迁移原则、
   哪些结论仅标题级证据（**显式标注**，别让读者以为全都有 turn 级证据）。
5. `## 待补与限制`：coarse 档截断处、artifact 档无数据、未逐回合深读的范围。
6. 篇幅参考：220–330 行；宁可短而实，不要长而空。

## 5. 语言与文风

- 中文；术语精确；引用原文用「」；代码/标识符用 ASCII 引号。
- **不要用 ASCII 双引号嵌中文**（歧义）。
- 不写"综上所述""众所周知"这类填充；每句尽量带信息。
- 标题不加编号外的修饰符号，阶段标题格式与参考长文一致。

## 6. 验收（必须自己跑到绿）

```powershell
$venv = "C:\Users\yianyao\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
& $venv -X utf8 -m harvester chain-validate <你的文件> --db harvester.db --meta topics_meta.db
```
- 输出须为 `ok: True` / `errors 0`；`warnings` 可为空。
- 校验器报的每条 error 都要修（常见：turn 越界、sid 不在 members、stage 无节点、
  frontmatter 缺字段）。
- 修完重跑，直到 errors=0。**不要通过删 stage 来规避**（每阶段必须留证据）。

### 6.1 引文保真度（`chain-validate` 验不了这一项，必须单独跑）

```powershell
& $venv -X utf8 -m harvester chain-audit "<你的 chain 文件>" --db harvester.db --no-anchors
```
- 抽取正文所有 `「」`，与**成员会话 `messages.raw` 与 `sessions.title`** 比对
  （自动归一空白与 Markdown 强调标记，兼容硬折行）。**`未命中 > 0` 即不合格（退出码 1）。**
- 两类未命中的修法：
  1. 真引文被压缩/改写 → 改成与 raw 逐字一致（可截断，用 `……` 标明）；
  2. 本不是数据引文（小节名/自造术语/概括）→ 改用 **加粗** 或去掉引号。
- 嵌套引用统一 `『』`。

### 6.2 素材必须取 `messages.raw`

**取 `text` 列会毁掉引文**：`text` 是 FTS 用的字符 bigram 切片
（原文「提交文件」→ `text` 为「提交 交文 文件」），拼起来像"逐字双写"，
**raw 里没有这种现象**（全库 6723 条 user 消息命中 0）。
- 需要逐回合全文时用 `topic turns --id <topic_id> --meta topics_meta.db --full`；
- `topic chain --level fine` 自 v0.24 起也走 raw（H52 已修 text 优先缺陷）；
- **永远不要为 bigram 现象发明"还原规则"**——那会把真实的叠字改坏。

## 7. 回报格式（≤15 行）

- 产物路径、总行数、阶段数、节点数、members 数、`chain-validate` 最终输出；
- 各阶段 span 与节点数一览；
- 你**没能做到**的事（仅标题级证据的范围、未深读的会话）显式列出；
- 你**不确定**的地方（例如某阶段归属存疑）。
