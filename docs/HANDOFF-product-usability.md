# HANDOFF — 产物有效性改造（P0/P1）与 view 实时同步

> 交接时点：2026-10-08。上游基线：session-harvester @ `9cd6ad1`（v0.18.1），
> harvester-view @ `60b5c3a`+（v2.1.1，main 已推送 GitHub）。
> 前置阅读：`docs/AUDIT-2026-10-08-quality-and-artifacts.md`（审核报告，主体属实
> 但自身 3 处有误，见 §2 修正）与本文 §3 口径事实（先读，避免重算）。

## 1. 目标（用户原话归纳）

1. 产物结果编排合理、真正用得上、更快更方便用上；
2. 产出物人机都可读；尽量减少人工操作步骤，只保留人的决策权限；
3. harvester-view 侧同步实时更新；
4. P0 三项 + P1 四项（来源：审核报告 §建议，经 §2 修正后采纳）。

## 2. 审核报告的 3 处修正（实施时以此为准，勿照抄审核原文）

| # | 审核原文 | 修正后事实 |
|---|---|---|
| 1 | web_fetch/WebFetch 合并 26.2% | **17.2%**（27 err / 157 calls，2026-10-08 实测；dsh web_fetch 59/17=28.8% + workbuddy-transcript WebFetch 98/10=10.2%） |
| 2 | "G1 不区分错误是否被吸收" | G1 **已有**"失败后续行为（重试/放弃）"节（G1_tools_report.md L359-363，218/43）。真实缺口：**建议池与工具排行未利用该维度**。P0-2 应表述为"接入"而非"新增" |
| 3 | 自愈 230/31 | 口径依赖配对规则：产物 218/43、审核 230/31、独立复算 240/21（2026-10-08 库）。实施时**必须先固定口径定义并写入文档**，数字才可比 |

## 3. 关键口径事实（复算必读，踩过的坑）

1. `steps` 表每次工具调用有**两行**：`phase='call'`（发起，status 为 NULL）+
   `phase='result'`（结果，status∈{completed,success,error,NULL}）。
   **calls 必须过滤 `phase='call'`；错误只出现在 result 行**。直接
   `GROUP BY tool` 会得到双倍 calls（2026-10-08 实测踩坑）。
2. 错误分母：`SELECT COUNT(*) FROM steps WHERE status='error'` = 261
   （result 行，2026-10-08 库）。工具统计走 `toolstats.py`
   `collect_stats_from_db`（L94 起），flow={retried, given_up, errors}。
3. 重试/放弃口径（toolstats 现行）：error 后同会话同工具再次 call=重试，
   否则=放弃（近似口径，跨 since_days 截断会漏配对——代码注释已声明）。
4. 建议池端点：`/api/reports/agents`（`apiserve.py api_reports_agents`
   L418），CLI `cmd_suggest_agents`（`cli.py` L449）。
5. 卡片校验：`cards.py validate_card`（L131）+ `_anchor_known`（L166）
   —— **当前只 `SELECT 1 FROM sessions WHERE session_id=? OR sid=?`，
   从不核对 evidence 引文是否出现在对应会话原文中**。

## 4. 任务 A：产物有效性改造（session-harvester）

### A1 [P0] 工具名归一（别名表）

- 位置：`toolstats.py collect_stats_from_db` 的 stats key 写入点（L137、
  L144 附近），加一层 `normalize_tool(raw) -> canonical`。
- 别名表制定流程（未完成的第一步）：
  `SELECT DISTINCT tool FROM steps ORDER BY 1` 取全集（2026-10-08 未取），
  逐个归类。已知同工具异写（大小写至少）：edit/Edit、read/Read、
  write/Write、glob/Glob、web_fetch/WebFetch、bash/Bash。
  归一策略建议：保留 canonical=库内出现次数最多的写法，其余映射过去；
  **保留 raw 名作为输出列**（`raw_tools`），可追溯。
- 归一后 G1 排名预期（供对账）：web_fetch 合并 17.2%（157/27）应排
  待修清单首位（小样本 browser_tool 48/10=20.8% 另行标记）。
- 验收：重生成 G1，edit 合并 = 2601 calls / 103 err = 4.0%；无任何
  大小写分裂行；单测对账数字更新 + 新增归一单测。

### A2 [P0] "未解决"维度接入建议池与排行

- flow（retried/given_up）已存在，改动点：
  1. `api_reports_agents` / `cmd_suggest_agents`：建议条目新增字段
     `unresolved_count`（同 pattern 的 given_up 数）并按其降序重排；
     仅 `unresolved_count>=1` 进入"待修清单"，自愈条目降级为"观察区"。
  2. G1 工具行增加 `given_up` 列（ToolStats 已有 flow 汇总，需落到
     per-tool——按 (sid,tool) 配对逻辑下沉到 collect 循环内）。
  3. **口径定义写入** `errstats.py` 模块 docstring 与 README：什么叫
     重试、什么叫放弃、截断漏配对如何处理。
- 已知实例（对账用）：tool_permission_revoked 共 27（autoclaw 源），
  审核切分 21 自愈 / 6 未解决。
- 验收：建议池每条带 unresolved_count；待修清单 TOP 与审核报告的
  browser_instance_unknown(5) 等未解决集合方向一致。

### A3 [P0] 库快照指纹（所有产物强制）

- 指纹字段：`{sessions, steps, errors, db_mtime, generated_at}`。
- 注入点：`apiserve.py` 全部 `api_reports_*`（L348/381/398/418）与
  `api_triage`、`api_cards` 的 meta；CLI 侧 `_emit_report`（cli.py L570）
  统一写产物头。additive：只加字段，不动既有字段。
- 验收：G1-G4/triage/cards 产物头均含指纹；view 端 meta 行显示
  `库: 1946 会话/261 错误 @ 10-08 09:5x`。

### A4 [P1] 其余

1. 建议条目加 `owner` 字段（harness/tool/workflow 三值）与 `status`
   （pending/adopted/rejected）——status 落库新表 `suggestion_status`
   （建议池本身无状态存储，这是唯一需要写库的点；注意只读铁律的边界
   ——该表属于元数据，不属于采集库，**放独立 meta.db** 或 cards 库）。
2. `cards.py` 引文核对升级：evidence 文本必须在 anchors 指向会话的
   messages 原文中出现（messages 表全文检索或 LIKE；跨源 content 格式
   差异先抽样 9 源各 1 卡再定匹配规则）；`turn: null` 出警告。
3. `report-skill` 加 `--min-calls`（cli.py L472 + apiserve L398），
   输出加 `calls` 样本量列，<阈值行标 `low-sample`。
4. G2 的 harness 类建议（自动重试/自动重建/分页）属于上游 harness 本身
   （WorkBuddy），不在本项目实现——建议池标注 owner=harness 即可。

## 5. 任务 B：harvester-view 实时同步

- `static/index.html`：`renderTools`（G1 表）适配归一后行结构（新增
  given_up/low-sample 列徽章，复用现有 `.badge` 样式）；建议池渲染
  unresolved_count 徽章 + owner pill；meta 行追加库指纹（A3 字段）。
- 按钮语义已就位（manual-only ✓ 反馈，v2.1.1），勿回退。
- 静态文件即改即生效，但 api-serve 侧 py 改动必须重启两个服务窗口
  （README/FAQ 已注记）。

## 6. 已完成（本轮，勿重做）

- 启动器 uv 支持：`start.cmd` / `start-system.cmd` 探测链
  py -3 → python → `uv python find`（取解释器绝对路径）→ 版本复查
  ≥3.10；start.cmd 保留 WorkBuddy 自带解释器兜底。四分支存根验证通过
  （仅 uv 环境/正常 PATH/全无报错/兜底）。uv 管理解释器不在 PATH，
  `uv python find` 不带版本参数返回默认解释器路径——勿写死 3.10。
- `session-harvester/AGENTS.md`：上下文自检规则已写入。
- `.gitattributes` 已含两启动器的 `eol=crlf` 特例（新增 .cmd 必须补）。

## 7. 工作方式与铁律

- 批处理：纯 ASCII + CRLF，python `write_bytes` 生成；echo 文案禁双引号。
- 测试：`...envs/default/Scripts/python.exe -m unittest discover -s tests`
  （session-harvester 224 例基线；harvester-view 23 例基线）。
- additive API 纪律 + 测试对账 + monkeypatch 守卫（见 AGENTS.md）。
- view 的 JS 校验：提取 `<script>` → node vm.Script；index.html 保持 LF。
- 推送：`git -c http.proxy=http://172.16.20.27:12603 push`（会话代理对
  github 502，WinINET 系统代理可用；凭据 helper 已修复勿动 .gitconfig）。
