# SOP · harvester-view 呈现改造工程

> 版本：v1.5（2026-10-13 会话 6 续批：用户三条裁决 + 会话抑制的 view 侧）· 设计依据：PROPOSAL-harvester-view-presentation.md v1.1
> 配套文件：TODO-harvester-view-v2.md（进度台账）· PROJECT-IRON-RULES-v1.md（纪律）· HANDOFF-view-v2-kickoff.md（跨会话交接）
> **进度指针：▶ 当前阶段 = P4 进化层推进中（P4-1/P4-3 分析模块已建并出真库首份候选；P0+P1+P2+P3 + 用户实测反馈批 v0.49 全部完成）· 最后推进：2026-10-13（会话 8）**
> **会话 8 裁决与落地**：① 用户推翻"不建"建议——反复叮嘱 = 两层语义
> （其一：工具/skill/Agent 调用不能很好遵循指令、过了就忘，需要反复提醒 →
> **P4-1**；其二：对产出品的反复打磨、推敲琢磨 → **P4-3**），两模块已建
> （`actions/analysis/`，探针 v2 口径逐值继承，9 例测试先红后绿，真库候选
> 与探针恒等，nags 抽样 5/5 锚点回翻属实）；② suppress **去 sid 化**——
> 用户原话"我也不可能知道 sid 是哪些啊（一般人谁记这个）"→ 后端
> `suppress candidates` 只读候选清单（重复簇 64/候选 172 + 零 user 轮次
> 100，两阶段粗筛+全量复核；FTS 逐会话查询 463s 教训已写进函数）。
> **下一步第一件事 = ① **P4-4 双轨时间线**（消费 P4-3 边界带 + 转折词候选，主题上/下轨同屏对齐）；② 真库首次 `suppress add --apply`——
> **流程已去 sid 化**：`suppress candidates` 看标题点名 → 复制命令 →
> dry-run → --apply（备份已就位），或 view 会话详情「删除（抑制）」按钮。
> （P4-2 已完成，会话 8c：归宿建议池 `--pool-out`，班规 22/问题 4，
> owner 三值 + 稳定 key + suggest-status 复制就绪命令，机器判定仅建议。）**
> P3 完成（commit `f1130ab`+`5cf3115`+`e5c35b1`）：**P3-1** 注册表冻结（A4/B9，双向检查，表外 403）；
> **P3-2** B 档两段提交 + `actions_meta.db` 台账（预演零副作用、六条拒绝路径、destructive 带快照）；
> **P3-3** 今日工作台（四队列 + 三环，**每个数字带 source/definition**，`/api/overview` 实测 4.76s）；
> **P3-4** 命令台接 B 档（预演 → 确认框 → 才写 + 台账抽屉）；**P3-5** 版本握手黄条 + B 档禁用；
> **P3-6** 阶段门（逐卡走查做成会红的测试 `test_v38_stage_gate.py`）。
> **view 测试 84 → 104**；均已 push。
> **动线决策（已裁决 2026-10-12）**：默认落地页 = 「今日工作台」（提案 §2.2 的目标形态；
> 用户明示「保持」）。变更点只有 `index.html` 启动处三行（`state.loaded.home=true; switchTab("home")`，
> 且不再无条件 `loadAll()`——会话列表改为点「会话检索」时按需加载）。
> P4 开工提示：① **D8 已经把落点写死**——`actions/analysis/nags.py` / `revisions.py` 是 actions-serve 内的
> **独立分析模块**，只 import harvester、不改它；② 提案 §5 的 T4/T5 与 P4-1/P4-3 是同一件事，**先跑
> 一次性体积探针**（真库上量"一周语料里有多少跨会话叮嘱 / 反转候选"）再定工具体量——D 组新想法的
> 共同前置就是这条（`docs/HANDOFF-v0.47-next.md` §3.D 有用户原话与评断）；③ P4-4 双轨时间线需要
> **artifacts 版本 diff + 五档 timeline 同屏**，`/api/topic/<id>/timeline` 已透出上游数据，
> 但 artifacts 侧只有 `artifacts show`（本仓未透出，需要新端点）；④ 端口仍冻结 8765=apiserve / 8766=actions-serve。
> P0-3 文档位置：用户本轮明示四件套权威位置 = `session-harvester\_eng\`（留原地）。
> 文档位置管理：本文件与配套文件是暂存副本，权威位置以用户最新告知为准（铁律 #1）。每次会话开始必须先读铁律，再读本 SOP。

---

## 0. 冻结决策（任何会话不得重开讨论；要改必须用户明示批准）

| # | 决策 |
|---|---|
| D1 | 三进程架构：**view**（静态页+JS，harvester-view 仓）/ **apiserve**（session-harvester 现有只读数据面，18 端点，零改动）/ **actions-serve**（新增动作面，view 仓内） |
| D2 | **session-harvester 零代码改动**。全部新后端能力（命令注册、动作执行、timeline 透出、nags/revisions 分析）都进 actions-serve；触碰 harvester/*.py 或其 schema 须用户逐项批准 |
| D3 | actions-serve 位置：`harvester-view/actions/actionsserve.py`；端口 **8766**（8765 归 apiserve）；用 session-harvester venv 解释器运行；`sys.path` 指向 `../session-harvester` 以 import harvester 包 |
| D4 | 命令注册表 = view 侧**静态 `commands.json`**（适配 v0.46）+ 版本握手：actions-serve 上报 `harvester.__version__`，不匹配→命令台黄条+禁用 B 档 |
| D5 | 白名单 `actions_registry.json` 三档：**A** 只读直接执行 / **B** 写 meta 两段提交 / **C** 只生成命令永不执行 |
| D6 | 解释层无 LLM：模板结论+静态释义（glossary.json）；LLM 判断只存在于带 prompt_version 的 chain/蒸馏产物 |
| D7 | 只读铁律延续：harvester.db 一律 mode=ro；写只落 meta 库（suggestions/topics/artifacts 已有；新增 actions_meta.db 属 view 仓） |
| D8 | T4/T5（nags/revisions）以 actions-serve 内**独立分析模块**实现（只 import harvester、不改它），日后是否捐赠上游由用户另裁 |
| D9 | P1-3 卡片本体数据源（2026-10-12 用户裁决）：`/api/cards` 实测只回校验结果（path/errors/warnings），无 frontmatter 本体。**在 view 仓内自建解析器**（只读解析卡片 md 的 frontmatter，不碰 session-harvester），view 本地端口新增只读端点透出本体，渲染时与 /api/cards 校验结果合并。等价于 P2 actions-serve 的提前落位，P2 阶段归并进 actions/ 目录 |
| D10 | **P2-1 归并方式（2026-10-12 会话 3 落地）**：P1 的 `viewserver.py` 三端点（/view/cards、/view/glossary、/view/health）**原样并入** `actions/actionsserve.py`（路径/响应结构/CORS/POST 405 全部不变），顶层 `viewserver.py` **保留为转发薄壳**（`serve()`/`make_handler(cards_root, glossary)` 签名不变）。理由：既有测试与 `start-viewv2.cmd` 按该文件名调用，保留薄壳＝归并对调用方零破坏；`cardlib.py` **不搬家**（P1 已是被测试钉住的纯函数模块，搬家只增加触点、零收益） |
| D11 | **命令注册表的生成方式（2026-10-12 会话 3）**：`actions/commands.json` **不手抄**，由 `actions/commandgen.py` 走 `harvester.cli.build_parser()` 生成事实（命令名/参数名/类型/默认值/help），人手只写两样：参数数据源 `src`、三档权限 `tier`（含 `topic` 的读/写子动作分档）。`--check` 与套件里的比对钉住漂移。**能力入口**：`python -m actions.commandgen [--check]` |
| D12 | **A 档动作的范围（2026-10-12 会话 3 判断）**：P2 的 A 档白名单 = 「只读 harvester.db、或只在临时目录里造数据」的动作。故 `regress` 归 A（上游 docstring 明说真库一字节不动），而 `deadcode-scan` 也归 A；`mcp-serve`/`api-serve`/`weblogin *` 归 C（占端口常驻或要人亲手登录）。**长任务用后台作业 + 轮询，不用同步请求**（实测入队 18ms 返回，作业台账仅在内存、进程重启即清空——页面上诚实标注） |
| D13 | **注册表分层：事实由代码派生，判断由人写（2026-10-12 会话 4）**：`actions_registry.json` **不手写**，由 `registrygen.py` 合并 `commands.json`（真解析器生成的事实：命令/参数/类型/src/tier）+ `write_actions.json`（人写的判断：哪些写动作可写、写哪个库、风险等级、确认页人话、参数映射）。合并时**双向检查**：引用不存在的命令/参数 → 失败；动作声明了它**不消费**的参数 → 失败（`allow` 字段 = 该子动作真正消费的字段，照 cli.py 抄）。**执行器只认注册表**（表外 403）。 |
| D14 | **B 档 = 两段提交 + 台账，写入语义完全归上游（2026-10-12 会话 4）**：① 第一段只**预演**（本仓的只读 `plan()`：读上游同一批只读函数算 before/after，**不重放执行路径**），返回 `needs_confirm` + token + 「将写什么」人话；② 第二段带同一份 params + token 才调上游写函数。token 一次性/5 分钟/绑定 `action_id`+**参数指纹**+**预览指纹**（库在两段之间被改 → 拒绝并要求重新预览）。台账 `actions/actions_meta.db`：`action_runs`（预演也记）/`write_records`（含 **destructive 删除前快照**）/`confirms`。**预演必须零副作用**（有测试比对文件字节）。 |
| D15 | **安全口径（诚实写进代码与文档，不许含糊）**：本服务绑 127.0.0.1、**无鉴权**；请求里的 `client`/`X-View-Client` 是调用方**自报**标签（默认 `web-ui`），**不是身份认证**，只用于台账分辨来源；`confirm_token` 是**意图确认**凭据（防误触/防脚本盲调），**不是权限凭据**。跨机器暴露一律拒绝启动（沿用上游 `_check_host`）。**预演的诚实边界**：它报的是"当前状态 + 本动作会做的操作"，不是"执行后保证得到的结果"（算不准的写进 `preview.limits`）。 |

---

## 1. 环境事实（冷启动必读，动手前逐条核实）

| 项 | 值 |
|---|---|
| session-harvester 仓 | `C:\Users\yianyao\WorkBuddy\2026-10-05-08-38-51\session-harvester` · v0.46.0 · 基线提交 a7737cf · 761 测试全绿 |
| 权威解释器（含 PyYAML） | `C:\Users\yianyao\.workbuddy\binaries\python\envs\default\Scripts\python.exe` |
| harvester-view 仓 | `C:\Users\yianyao\WorkBuddy\2026-10-05-08-38-51\harvester-view` · v0.33 · 基线提交 c1c2c06 · 25 测试全绿 |
| view 代码形态 | 单页 `static/index.html`（**P2 后 102.6k 字符 / 2000+ 行**）+ `view.py` + `actions/`（actionsserve/commandgen/commands.json）+ `cardlib.py` + `viewserver.py`（兼容薄壳）+ `config.json` + `start-viewv2.cmd`；**动手前先通读** |
| apiserve | 18 端点；`do_POST=405` 全拒；mode=ro + authorizer；db_fingerprint 含 stale 库龄 |
| actions-serve（P2 新增） | 8766；端点：/view/cards /view/glossary /view/health /api/health /api/commands /api/actions（GET）+ POST /api/actions/run + /api/actions/job/<id> /api/topic/<id>/timeline /api/pattern-sessions **/api/overview** **/api/workitems?target=**（v0.49）；只读连接用 `mode=ro` + **同款内核级 authorizer**；作业台账**在内存**（重启即清空） |
| 工作项层（v0.49 新） | `actions/workitems.py`：**唯一真源**——target 一份明细 + 具名指标，指标值只由明细派生（`mode=items`/`sum`）。实测真库 **17 target / 25 指标全部对账通过**；`reconcile_ok=false` 页面响亮报警。**target id 必须 URL 安全**（`[A-Za-z0-9._:-]`，不许 `#`——它是片段符，会话 6 为此踩过一次） |
| 视图测试门（v0.49 共 5 个 node 门） | `node tests/render_smoke.js` / `render_cmd.js` / `render_home.js` / `boot_sequence.js` / **`render_work.js`**（处理单：对账报警 + 每条带建议 + 命令包摊平 + 送命令台）；套件侧新门 `tests/test_v40_workitems.py`（27）/ `test_v40_theme_contrast.py`（7）/ `test_v40_work_render.py`（2） |
| 命令注册表 | `actions/commands.json`（**生成物**，勿手改）：43 叶子命令 / 235 参数 / 13 动词卡 / 适配版本 0.46.0；重生成 `python -m actions.commandgen`，比对 `--check` |
| 关键端点 | /api/meta /api/facets /api/sessions(?errors_only) /api/triage /api/reports/{tools,errors,skills,agents} /api/topics /api/keywords /api/export-analysis /api/cards /api/session/<sid>[/turn/<n>] /api/topic/<id>/chain |
| 关键函数（actions-serve 将调用，均不改） | `topics.timeline(meta,id,db,level)` / `topicprep`（dedupe 边界带 0.80–0.90）/ `errstats.normalize_error` / `kwstats`（doc_freq n-gram）/ `agent_suggest` / `dbmeta.db_fingerprint` |
| meta 库 | suggestions_meta.db（表含 adopted_at）· topics_meta.db（14 主题/997 成员）· artifacts_meta.db（463 条）· keywords_meta.db（18.4MB） |
| 真库基线 | harvester.db 443MB：1979 会话 / 72990+ 消息 / 44005 步骤 / 381 错误 |
| 测试命令 | 主库：`& <venv> -m unittest discover -s tests`（785）；view：同解释器在 view 仓 `discover -s tests`（**62**）；JS 门：`node tests/render_smoke.js` + `node tests/render_cmd.js`；跨仓库真实载荷门：`python tests/check_real_payload.py`（15 ok / 1 n/a，退出码 3 = 未执行，**不是通过**） |

---

## 2. 标准动作循环（每一项工作都按此走，铁律 #3/#4 的执行体）

1. 读铁律 → 读本 SOP 进度指针 → 读 TODO 对应项（含验收标准）；
2. 实现（只碰该项声明的触点文件）；
3. 自验：跑该项验收命令 + 所在阶段测试门；
4. **立即**更新 TODO：状态 ☐/▶ → ☑，附证据（测试结果/产物路径/提交号）；
5. 更新本 SOP 头部进度指针与阶段内小节状态；
6. 阶段收尾门通过后更新 HANDOFF（做完什么/证据/下一步/环境变化）；
7. 上下文不足或会话将尽 → 提前收尾，按铁律 #5 生成/更新 handoff，不开新战线。

---

## 3. 阶段定义

### P1 解释层（已完成 ✓ 2026-10-12；commit 2c1e060+d975639）

目标：小白三问过关——每个面板有结论、有解释、有下一步。**六项全部完成带证据（见 TODO 台账），出口门已过：view 34 例绿 / JS 门绿 / 真实载荷 15 ok（381 三重对账）/ 主仓零改动复核干净 / 三问自验记录在 TODO P1-6。**
落地差异记录：P1-3 按裁决 D9 用 view 仓内自建解析器 + viewserver(8766) 实现（提案原假设「/api/cards 已含本体」与实际不符）；P1-2 环比因缺基线字段诚实未做；会话页 chips 的会话级核对用 error_steps 懒加载（上游无聚合端点）。

| 步骤 | 内容与触点 |
|---|---|
| P1-1 | `static/glossary.json` 骨架：术语卡 ≥20（env/tool_interface/context/unresolved/行为链/归一聚类/边界带/low-sample…每条=一句人话+系统内真实例子）+ 模式人话表 ≥10（取 errstats 高频 pattern）+ 面板导语 ≥5；index.html 加载器+悬停卡 |
| P1-2 | 结论卡组件：纯 JS 模板渲染，消费 /api/reports/{tools,errors,agents} 的 counts/unresolved/patterns 字段；每面板头部=主语+数字+环比+判语+三按钮（看根因/起草班规/在命令台打开——前两个 P1 先链到锚点，第三个 P2 接） |
| P1-3 | 卡片本体列表：消费 /api/cards，渲染标题/结论/证据片段/锚点/置信度，替换"通过 N 张"计数封条 |
| P1-4 | 「怎么读」导语挂接（数据来自 P1-1 glossary） |
| P1-5 | 错误筛选 chips 接线：/api/sessions?errors_only=1 + session 级 error_steps（class×pattern 过滤）——数据已有，纯 UI |
| P1-6 | 阶段门：小白三问自验记录进 TODO；view 测试补真实载荷用例；JS 门绿 |

**P1 出口门**：TODO P1 全 ☑ 带证据；view 25+新增 测试绿；两仓 git 干净；handoff 更新。

### P2 打通层（已完成 ✓ 2026-10-12；commit bfca273 + 6ad5cec + 9d469ea）

目标：命令台可跑、数字可下钻到原文。**六项全部完成带证据（见 TODO 台账）**，
出口门已过：view **62 例绿**（34→62）/ JS 门两套绿（render_smoke + 新增 render_cmd 23 断言）/
真实载荷跨仓库门 15 ok·1 n/a·0 FAIL / **session-harvester 零改动复核干净**。

| 步骤 | 内容与触点 | 状态 |
|---|---|---|
| P2-1 | actions-serve 骨架：`actions/actionsserve.py`（8766）；P1 三端点归并（D10）；GET /api/health（版本握手）；A 档执行器（进程内线程，非 shell）+ 作业轮询；先放只读动作 | ✅ 4 个动作（report_tools/report_errors/topic_list/**regress**），与上游 JSON 逐字段全等实测 |
| P2-2 | /api/commands：静态 `actions/commands.json`（D11 由真解析器生成）+ 参数数据源标注 + 三档归属 | ✅ 43 叶子命令 / 235 参数（145 标注）/ 13 卡；`--check` 与套件比对钉住漂移 |
| P2-3 | GET /api/topic/<id>/timeline?level=（进程内调 `topics.timeline` 五档） | ✅ 真机 title 37ms / coarse 2373ms / mid 2407ms；无 chain 主题照常有线；可选 `--corpus-watermark` 判库龄 |
| P2-4 | 动词面板 v0：命令卡 + 参数选择器（库里来）+ 结果抽屉 | ✅ 新 Tab「命令台」；路径类参数零输入框；命令行预演；**修掉一个整页抛错的真缺陷** |
| P2-5 | 数字皆门第一批：错误模式 → 会话列表 → 回合原文三跳 | ✅ 真库 57 模式逐个对账，不一致 = 0；第三跳复用 gotoAnchor（不拼 turn 号） |
| P2-6 | 阶段门：零复制粘贴走查 / 长任务无阻塞 / 零改动复核 | ✅ 入队 18ms 返回；drift 语料必须变红已实测 |

**P2 落地差异记录**：
1. **P2-1 的动作清单与任务书不同**：任务书写"先放 3 个只读动作"，实际放 4 个——多出 `regress`，
   因为 P2-6 要求"验证后台+轮询不阻塞"，需要一个**真能跑几秒**的动作（report-* 实测 220–310ms 太短，
   证明不了轮询路径）。
2. **口径更正（顶层 40 vs 叶子 43）**：任务书写"40 命令全覆盖"，`harvester --help` 列的确实是 40 个
   **顶层名**，但 `weblogin`（3 个）与 `cards`（2 个）有嵌套子动作、裸名不可执行，叶子命令实为 **43 条**。
   注册表只收叶子（可执行的形态），覆盖判据改为"与真解析器逐条一致"而非"数到 40"。
3. **口径更正（样例数 ≠ 会话数）**：P2-5 验收句原写"模式次数 = 样例会话数之和"。实测**不成立**：
   `errstats` 每个模式最多带 **3 条**样例，`FS_STALE_VERSION` 有 72 次 / 11 个会话。已改为
   "逐会话次数之和 = 模式总数"并据此实现第二跳。
4. **timeline 的库龄判定缺省关**：它要扫一遍语料盘（实测 0.08s，但盘在网络上会变慢），
   P2-3 的验收是"无 chain 主题也能看时间线"，不该被库龄拖住 → `--corpus-watermark` 显式开启。
5. **CLI 的 `topic` 没有 `timeline` 子动作**（实测）：五档时间线是库函数 `topics.timeline`，
   由本仓端点透出。注册表**不造**这条跑不通的命令，并留断言钉住。

### P3 裁决层（已完成 ✓ 2026-10-12；commit f1130ab + 5cf3115 + e5c35b1）

| 步骤 | 内容与触点 | 状态 |
|---|---|---|
| P3-1 | `actions_registry.json` v1：命令 → 13 动词卡 → 三档归属冻结（D13：事实从真解析器派生，人只写判断） | ✅ A 4 / B 9（可逆 7、破坏性 2）；双向检查；执行器只认注册表 |
| P3-2 | B 档两段提交（confirm_token + 将写摘要）+ `actions_meta.db` 台账（D14/D15） | ✅ 预演零副作用 + 六条拒绝路径 + destructive 快照 + `/api/actions/ledger` |
| P3-3 | 今日工作台：四队列 + 闭环健康三根环（首页） | ✅ `/api/overview` 现算，**每数带 source/definition**；实测 4.76 s |
| P3-4 | 命令台完整 UI：动词卡 + 三徽章 + 确认弹窗 + 长任务轮询 | ✅ 写动作按 action_id 进卡；预演 → 确认框 → 才写；台账抽屉 |
| P3-5 | 版本握手黄条：版本 ≠ 适配版本 → 黄条 + B 档禁用（A 档不受影响） | ✅ 判据只有服务端一处；四条断言含"A 档仍可执行" |
| P3-6 | 阶段门：B 档全留痕；C 档零执行；乱点不可逆逐卡走查 | ✅ 走查做成会红的测试（`test_v38_stage_gate.py` 8 例） |

**P3 落地差异记录**：
1. **任务书写"B 档无 token 直执被 403 拒"，实际实现为「无 token = 返回预演 + token」（200）**
   ——403 留给"表外动作 / C 档动作"。理由：B 档的第一次 POST 本身就是**合法且必要**的第一段，
   不是越权；直接把无 token 打成 403 会让两段提交没法起步。**越权判据**改为：
   不带 token 的第二段（即带 `confirm_token` 但无效）→ 409；表外/ C 档 → 403。
2. **P3-1 的"与 `harvester --help` 逐命令比对"改为"与真解析器逐命令逐参数比对"**：
   `--help` 只有 40 个顶层名，而叶子命令 43 条（P2 已更正过同一处口径）。
3. **新增 `allow` 字段（D13）**：光有"注册表声明的参数都存在"不够——聚合命令（`topic`）
   会让 `topic add` 声明 `--sid` 也通过检查。故每个写动作必须显式声明"该子动作真正消费的
   字段"，反面（声明了不消费的）直接 fail loud。这条规则首版就抓到我自己写错的一处。
4. **破坏性动作的"可回滚"只到快照为止**：`topic_delete` 的删除前快照进台账，
   但**没有一键恢复**（恢复要人工照快照重建）。故确认框里写的是"会丢信息"而不是
   "可撤销"——不夸大口径。
5. **P3-3 的口径更正 3 处**（任务书举例与真库不符，以实测为准）：
   ① "建议池 22" → 那是**已采纳数**，**待裁决实为 0**（台账 24 条 = adopted 22 + rejected 2）；
   ② "候选簇"两个口径（triage 新错误模式 30 / `topic-candidates` 13 簇）→ 队列取
   **新错误模式**并在 `definition` 里写明取的是哪个；
   ③ "0 成员主题 1" **已不成立**（v0.47 删空主题后实测 0 个）→ 该判据未采用。
6. **`/api/reports/errors` 的 pattern 行没有 `given_up` 字段**（实测）：照它算"未解决"会得到
   **0**，而真实是 **31 次 / 16 个模式**（`errstats.pattern_stats.given_up` 口径）。
   工作台改用 `pattern_stats`，并在测试里钉住这个差异（那条 `api_total == 0` 断言同时是
   "上游若补了字段、本口径就该复核"的提醒）。**同类风险提示**：`api_reports_tools` 的
   `flow.given_up` 是另一套聚合，别与 pattern 口径混用。

### P4 进化层（估 3–5 天）

| 步骤 | 内容与触点 |
|---|---|
| P4-1 | `actions/analysis/nags.py`：反复叮嘱挖掘——user 轮次命中指令/抱怨词句 → kwstats 式跨会话聚类 → 候选清单带锚点（只读 harvester.db，零改 harvester） |
| P4-2 | nags 候选 → 建议池第二输入源：产出 md/JSON 供人裁决，标注两类归宿（班规条目 / harness 问题清单，复用 owner 三值） |
| P4-3 | `actions/analysis/revisions.py`：打磨/反转候选——相邻轮次 Jaccard 边界带（0.80–0.90，topicprep 口径）+ 转折词（不对/算了/推翻/作废）→ 机械候选带锚点 |
| P4-4 | 主题工作台双轨时间线：上轨讨论流（五档 timeline）+ 下轨产物流（artifacts 版本 diff）+ 打磨带连线 + 反转点标记（先候选后经确认升级） |
| P4-5 | skill 调用点挂轨道（report-skill-join 数据消费，轨道边挂注） |
| P4-6 | 阶段门：两条新管线各出首份候选报告；确认流可用 |

### P5 打磨（估 1–2 天）

| 步骤 | 内容 |
|---|---|
| P5-1 | 「5 分钟之旅」：用真实班规闭环（Edit 前先 Read：112 次错误→建议→采纳→A/B）做五页引导 |
| P5-2 | 三身份视觉打磨（机械事实/AI 判断/人的裁决）+ 数据新鲜度常驻条 |
| P5-3 | 空态统一（选参数→点执行，不再"复制命令"）+ 确认流文案统一 |
| P5-4 | 工程收尾：TODO 全清、handoff 终版、用户验收清单 |

---

## 4. 统一阶段收尾门（每阶段末逐项勾）

> **P3 阶段门已过（2026-10-12，会话 5）**：下面 7 项逐条勾选，证据见 TODO「P3-6」与
> 「横切检查项」。下一阶段（P4）收尾时**重新逐项勾**，不是一次性打勾。
> 动线决策：**默认落地页 = 今日工作台**（2026-10-12 用户明示「保持」）——不再是挂账项。

- [x] 该阶段 TODO 项全部 ☑ 且带证据（P3×6）
- [x] 两仓测试绿（主库 **785** 不变；view **104**＝62+22+20）
- [x] JS 门绿（render_smoke + render_cmd + **render_home**）；跨仓库集成门不回退
- [x] session-harvester 零改动复核：`git status` 干净、`git diff` 空、deadcode 0/0/0
- [x] actions_registry/commands.json 版本标注与真实 harvester 版本一致（version_ok=true）
- [x] HANDOFF 已更新（完成项+证据/下一步/环境变化）
- [x] 涉 UI 的阶段：小白三问自验记录在案（见 TODO P3-6 与 X-5）

## 5. 已知坑与红线提示（历史教训，勿重踩）

- index.html 已是 **102.6k** 单文件（P1 时 61.5k → P2 后 102.6k）：改前确认备份点，
  **批量改动用「Python 补丁脚本 + 每对 old/new 精确断言恰好匹配一次」落盘**（不用 PowerShell
  字符串替换），改完**必跑 JS 门**。P2 实测教训：补丁脚本自己也会被改坏——脚本被截断后
  `main()` 消失，**运行它没有任何输出却退出码 0**，看起来像"已应用"而实际没写盘；
  故补丁脚本必须打印 `已写盘 + 字符数变化`，且应用后**独立复核关键函数是否存在**（本轮靠 grep 抓到）；
- apiserve 是只读面：**不得**往它加执行能力、不得复用其端口；动作面只在 actions-serve；
- 长任务（triage ~9min / sync ~5min / regress 1.2s）：后台线程+轮询+预告耗时，绝不阻塞页面。
  **入队必须立刻返回 job_id**（实测 18ms），结果靠轮询拿；
- meta 库路径在起服务时已定，命令台参数区**不暴露**路径类参数。**服务端也必须自己过滤**
  （`_clean_params` 只认注册表声明过的键）——只靠前端自觉等于没防；
- 夹具必须覆盖真实形态（历史 P0 教训：completed 状态缺失导致口径假真）。P2 补充：
  **合成库的列名必须照真库**（本轮夹具先写成 `message_count`/`messages.seq`/`steps.raw`，
  真库三张表都没有这些列——先 `PRAGMA table_info` 再写夹具）;
- 渲染类改动必须有真实载荷测试（view 跨仓库集成门先例，H48）。P2 补充：**新加的 JS 函数
  必须被某个门 extract 到**——`const` 顶层常量 extract 不出来（正则只认 `function`），
  故"命令 → 动作"的映射表特意写成函数 `cmdActionMap()`；
- 用户判据原话是规格（noisetriage 先例）：涉及判据的需求，逐字引用进注释。
