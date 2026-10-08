# 知识卡片工作流（G3 供卡指南）

> 回答一个问题：**散落在 DeepSeek / 元宝 / 千问 / 豆包 / DSH / WorkBuddy
> 里的记录，怎么变成可检索、可复用的知识卡片？**

## 归一决策（2026-10-06 冻结）

```
机器可校验的候选池                      人工维护的主库
~/.workbuddy/knowledge/cards/    →    ~/.workbuddy/knowledge/
（harvester cards validate 把关）      （kb-init / playbook 形态）
```

- 卡片先进**候选池**，`cards validate` 通过（§8 规范 + 锚点真实命中索引库）
  后由人工并入主库——一池一库，不再双轨。
- validate 结论三分支（v0.16）：**error**=违反 §8 规范（缺字段/锚点 sid
  不在索引库等），修复前不得并入；**warn**=不致命但需复核（anchors 非列表
  形态、正文为空、无索引库致锚点未能校验）；两者皆无=可并入主库。
  PyYAML 可选——无它时降级解析器照常解析 anchors 并校验锚点。
- 锚点 = `[{session_id: "<adapter 级 id>", turn: N}]`，validate 会查
  `harvester.db` 确认会话真实存在，杜绝"编造来源"。

## 三条供卡通道

> 通道 0（v0.13 新增）：`harvester triage` 蒸馏队列 —— sync 之后对窗口内
> 新会话做确定性初筛（新错误 pattern / 旧坑重现 / Skill 行为链 / 高信号
> 会话），产出 `distill_queue.md`，每条候选附一键 `cards new` 命令。
> 机器只排队，人从队列挑条目起卡；与下面三条通道是"上游排队"关系，
> 不改变任何归一决策与并入纪律。

### 通道 0.5（v0.14 新增）：T2 蒸馏包 → Agent 草稿卡

队列里挑中某条候选后，让机器把喂料也备好：

```
python -m harvester draft --sid <队列锚点里的 sid> --type pitfall --out docs/distill_prompts/<名>.md
```

产出**自包含蒸馏包**（任务指令 + §8 卡片规范 + 带 `sid#seq` 锚点的
会话原文 + 产出要求），任何 Agent 拿到即可开工，不必手工拼上下文。

- Agent 草稿卡落 `~/.workbuddy/knowledge/cards/drafts/`（confidence 固定
  0.3；v0.22 P0-4：原 `cards_pending/` 空柜已废止——G3 校验对象就是主库，
  草稿用主库子目录隔离，`cards validate` 递归扫描不受影响）；
- `cards validate --root ~/.workbuddy/knowledge/cards --db harvester.db`
  照跑（锚点查库一样生效），通过后**仍由人工终审**才留在主库顶层；
- 已验证实例：`~/.workbuddy/knowledge/cards/kc-20261006-0004-file-changed-since-read.md`
  （pitfall，源自队列榜首"file changed since it was read"26 次的
  dsh 会话，validate 通过、锚点命中，**已终审并入主库**）。

### 通道 1：Harness 踩坑 → 建议池（全自动）

```
python -m harvester report-errors --db harvester.db     # 机器分类 243 错误
python -m harvester suggest-agents --db harvester.db --out verify/agents_suggestions.md
```

建议池条目（每条附实测次数 + 锚点 + 报错原文）→ 人工/Agent 扩写为
`type: pitfall` 卡片或直接并入 AGENTS.md。

### 通道 2：Skill 行为模式 → 深挖清单（G4 供卡）

```
python -m harvester report-skill --db harvester.db                  # 全景
python -m harvester report-skill --db harvester.db --skill <名>      # 深挖
```

深挖清单 = 该 skill 的每次调用点（锚点 + 触发任务 + 调用后行为链）。
把清单连同对应会话交给 Agent，让它蒸馏"什么场景用 / 怎么决策 /
标准动作序列"→ `type: workflow` 卡片。

### 通道 3：Chat 平台记录 → 人工/AI 摘录（DeepSeek / 元宝 / 千问 / 豆包）

这些会话没有工具遥测，走 `search` 找素材 → `cards new` 起卡 →
（人工或 AI）补 evidence 与正文：

```
python -m harvester search "关键词" --db harvester.db          # 定位会话，拿 [sid]
python -m harvester cards new --sid <sid> --turn N \
        --root ~/.workbuddy/knowledge/cards --type insight     # 起卡（锚点自动填）
# 补全 evidence（原会话证据原文）与正文
python -m harvester cards validate --root ~/.workbuddy/knowledge/cards --db harvester.db
```

## 直接把文件喂给 Agent（推荐组合）

把以下文件一起交给任意 Agent（WorkBuddy / DSH 均可）：

| 文件 | 作用 |
|---|---|
| `docs/reports/G2_agents_suggestions.md` | 建议池 → 让 Agent 扩写成 AGENTS.md 条目 |
| `docs/reports/G2_errors_report.md` | 错误三分类上下文 |
| `docs/reports/G1_tools_report.md` | 工具失败率 → 改进依据 |
| `report-skill --skill <名>` 输出 | 行为链 + 会话锚点 → 蒸馏 workflow 卡 |

给 Agent 的指令模板：

> 读这些报告。对建议池每条：结合锚点用 harvester read 读取原会话，
> 扩写成 AGENTS.md 条目（现象/做法两段，≤3 行）或 `type: pitfall` 卡片
> （frontmatter 参考 docs/CARD_WORKFLOW.md，evidence 必须是会话原文）。
> 产出写入新文件，不要直接改 AGENTS.md，等我审。

**纪律**：机器产出永远是"建议/草稿"，并库前必过人眼 + `cards validate`。

## 已验证实例（2026-10-06）

| 卡片 | 来源 | 通道 |
|---|---|---|
| kc-20261006-0001-edit-before-read（pitfall） | DSH 错误统计 | 通道 1 |
| kc-20261006-0002-card（insight，元认知干预技术） | 元宝会话 | 通道 3 |
| kc-20261006-0003-card（workflow，wechat-article-search 用法） | WorkBuddy skill 调用 | 通道 2 |
| kc-20261006-0004-file-changed-since-read（pitfall，已终审并入） | triage 队列榜首 → draft 蒸馏包 → Agent 草稿 | 通道 0.5 |

四张卡 validate 全过、锚点全部命中索引库——**全部通道（含 T2 蒸馏包）全链路可用**。
