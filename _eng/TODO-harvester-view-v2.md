# TODO 台账 · harvester-view 呈现改造工程

> 版本：v1.4（2026-10-13 会话 6：用户实测反馈批 v0.49，4 项全 ☑）· 配套：SOP-harvester-view-v2.md（阶段定义与冻结决策）
> 用法（铁律 #3）：开工一项 → 状态 ☐ 改 ▶；完成并自验 → ▶ 改 ☑ + 附证据（测试输出/产物路径/提交号）。
> **进度：28 / 38（P0×3 + P1×6 + P2×6 + P3×6 + **v0.49×4** + P4-1/P4-2/P4-3）· 当前阶段：P4 进化层推进中（P4-4 双轨时间线待开工）· 最后推进：2026-10-13（会话 8c）**
> 文档位置：权威位置 = `session-harvester\_eng\`（用户 2026-10-12 本轮明示，留原地；铁律 #1）。
> 新增决策 D9：P1-3 卡片本体数据源 = view 仓内自建解析器（用户裁决；详见 SOP §0 D9）。
> P2 会话提交：`bfca273` + `6ad5cec` + `9d469ea` + `6f187ee` + `d4d52bc` + `b1fa24f`。
> **P3 会话提交：`f1130ab`（P3-1/2 后端）+ `5cf3115`（P3-4/5 前端）+ `e5c35b1`（P3-3 工作台 + P3-6 走查门）**；均已 push。
> **view 测试 34 → 62（P2）→ 84（P3 前半）→ 104（P3 收尾）→ 144（v0.49）**；session-harvester **零改动**（`git status` 跟踪文件干净）。
> **动线决策（已裁决 2026-10-12）**：默认落地页 = 「今日工作台」（用户明示保持）。
> ⚠ **v0.49 起有一条跨仓耦合**：`test_v35_actions.test_no_drift_against_real_parser`
> 会在**上游新增 CLI 子命令**时红（实测：后端加 `suppress` 子命令后即红）。
> 这是**正确行为**——修法是 `python -m actions.commandgen` 重生成 `commands.json`
> 并把新命令归并进 `commandgen.COMMAND_CARD`。

---

## P0 开工检查（会话冷启动时执行，可重复执行）

- [x] P0-1 读铁律 → 读 SOP 进度指针 → 核对两仓 git 基线与状态（session-harvester @a7737cf 干净；harvester-view @c1c2c06 干净；有漂移先停下问用户）　**证据（2026-10-12）**：view @c1c2c06 干净 ✓；session-harvester @`02824e3`（v0.47 批次，同日上游推进，非四件套记载的 a7737cf）跟踪文件干净，仅 `_eng/` 与 `docs/PROPOSAL…md` 未跟踪；用户已在会话内确认按此基线开工
- [x] P0-2 跑两仓测试确认基线（主库 761 / view 25，权威 venv）；环境事实表逐条核实（解释器路径、端点数、meta 库在位）　**证据（2026-10-12）**：主库 **785 例 OK**（338.6s，venv 解释器；761 是 v0.46 数字，当日已推到 v0.47 批次 +24，见快照）· view **25 例 OK**（20.0s）· 解释器/端点数/meta 库均在位（README「测试基线」权威数字 = 785）
- [x] P0-3 确认工程文档权威位置（用户已告知的移动后路径），修正 SOP/TODO 头部指针　**证据（2026-10-12）**：用户本轮明示四件套权威位置 = `session-harvester\_eng\`（留原地），SOP 头部指针已修正

## P1 解释层（6 项 · 触点仅 harvester-view 仓）

- [x] P1-1 glossary.json 骨架：术语卡≥20 + 模式人话表≥10 + 面板导语≥5；index.html 加载器与悬停卡　**验收**：任一术语在页面可见处有虚线下划线，悬停出"人话+真实例子"；JSON 通过结构校验　**证据（2026-10-12，commit 2c1e060）**：`static/glossary.json` 实际 24 术语 / 14 模式人话（2026-10-12 真库 Top14 实测收录）/ 8 导语；加载器 `loadGlossary()`+悬停弹层 `#gl-pop`+`applyTermTooltips()`（虚线下划线 `.gterm`）；JSON 结构校验过；未收录模式不显示人话（不造数）
- [x] P1-2 结论卡组件：消费 /api/reports/{tools,errors,agents} 现有字段；每面板头部结论+三按钮　**验收**：G1/G2/建议池三页各有一句模板结论，数字与 API 实测一致（人工核对一条）；「在命令台打开」按钮先占位　**证据（2026-10-12，commit 2c1e060）**：`renderRepConcl()` 纯模板现算，tools/errors/agents 三面板各一句主结论+判语+三按钮（看根因=页内锚、起草班规=跳 agents、命令台=P2 占位黄条）；人工核对：tools 结论「调用 2588/失败 261（10.1%）」与 `/api/reports/tools` 实测同源同值；数字全部当次响应现算，环比缺基线字段诚实不做（注释留口径）
- [x] P1-3 卡片本体列表：标题/结论/证据片段/锚点/置信度，替换计数封条　**验收**：/api/cards 的 4 张卡片全部可见本体；锚点可跳转到会话视图　**证据（2026-10-12，commit 2c1e060）**：**D9 裁决落地**——`cardlib.py`（受限 frontmatter 解析，真实形态自检过）+ `viewserver.py`（8766 只读，POST=405）；`renderCards` 改为「上游校验徽章 × 本体」按路径合并，真机 4/4 卡本体可见（标题/结论/证据/锚点/置信度），锚点走 `gotoTopicAnchor` 直达回合；校验缺失/本体缺失双空态都显式；另加 `start-viewv2.cmd` 三进程一键启动
- [x] P1-4 「怎么读」导语挂接（数据来自 P1-1）　**验收**：≥5 个面板有可折叠导语，内容来自 glossary 而非散落文案　**证据（2026-10-12，commit 2c1e060）**：`panelIntro()` 消费 glossary.panels，实际挂接 8 面板（sessions/triage/tools/errors/agents/skills/cards/topics）≥5 ✓；内容全部来自 glossary.json，页面无散落文案
- [x] P1-5 错误筛选 chips：/api/sessions?errors_only=1 + error_steps 的 class×pattern 过滤　**验收**：按「用法类」筛出会话列表且数字与 /api/reports/errors 的 381 总数对账一致　**证据（2026-10-12，commit 2c1e060）**：报告页 chips=模式行 class 过滤（数字=事件条数，与 by_class 同值）；会话页 chips（勾「只看异常」后）数字=全库 `/api/reports/errors` 的 by_class（缺省=全库，**实测 days=0 是 0 天窗口 ≠ 全库，已按缺省参数修正**）；会话级核对用 error_steps 懒加载+缓存（进度黄条）；真实载荷门三重对账：chips 全部 381 = by_class 求和 381 = patterns 求和 381（=AGENTS.md 快照总数）
- [x] P1-6 阶段门　**验收**：view 测试 25+新增全绿（含真实载荷用例）；JS 门绿；小白三问自验记录三条；**session-harvester 零改动复核（git diff 空）**　**证据（2026-10-12，commit 2c1e060+`d975639`）**：view **34 例全绿**（25+9，21.6s）；JS 门 `vm.Script` 绿（script 67023 字符）；render_smoke 59 断言绿；真实载荷门 15 ok / 1 n/a（含 381 三重对账）；**主仓 `git status` 跟踪文件零改动**（仅未跟踪 `_eng/`、`docs/PROPOSAL…md` 工程文档暂存）；**小白三问自验**：① 30 秒主结论=报告/队列页头部结论卡一句话+判语 ✓；② 下一步=结论卡三按钮+「怎么读」导语 ✓；③ 乱点不可逆=P1 全部只读，班规起草明示「本视图绝不写入任何配置文件」，8766 服务 POST 全 405 ✓

## P2 打通层（6 项 · 新增 actions-serve；session-harvester 仍零改动）

- [x] P2-1 actions-serve 骨架（8766 端口）：/api/health 上报 harvester.__version__；A 档执行器（进程内线程调 cmd_* 函数，非 shell）；先放 3 个只读动作（report-tools/report-errors/topic-list）　**验收**：页面点执行→结果抽屉就地显示，与 CLI 直跑输出一致（抽一条对账）；session-harvester git 仍干净　**证据（2026-10-12，commit bfca273）**：`actions/actionsserve.py` 起服于 8766；**P1 的 viewserver 三端点（/view/cards /view/glossary /view/health）原样归并**（路径/结构/CORS/POST 405 不变，`viewserver.py` 退化为兼容薄壳，签名不变）；`/api/health` 报 `harvester_version=0.46.0` ⟷ `commands_adapter_version=0.46.0`、`version_ok=true`；`POST /api/actions/run` → 202+job_id、`GET /api/actions/job/<id>` 轮询；**对账实测**：动作 `report_errors{hours:30}` 与上游 `GET /api/reports/errors?days=30` 的 JSON **逐字段全等**（真库 errors=360 / steps=41807 / sessions=56 / patterns=53），A 档白名单 4 个动作（+regress）与 `/api/actions` 一致；非白名单 action_id → **403**、多余参数丢弃、days 非数字 → 400、契约版本不符 → 409；**主仓 `git status` 跟踪文件零改动**（仅 `_eng/`、`_research/`、`docs/PROPOSAL…md` 未跟踪）
- [x] P2-2 /api/commands 透出静态 commands.json（40 命令/13 动词卡/参数数据源标注，按提案 §5.4/§5.5 抄录）　**验收**：commands.json 覆盖 `harvester --help` 全部 40 子命令无遗漏（脚本比对）；每参数标注数据源或标"自由文本"　**证据（2026-10-12，commit bfca273/6ad5cec）**：**不手抄，由真解析器生成**——`actions/commandgen.py` 走 `harvester.cli.build_parser()`，产出 **43 条叶子命令 / 235 参数（145 有数据源标注）/ 13 动词卡**；口径更正：`--help` 列 40 个**顶层名**，叶子命令 43 条（`weblogin` 3 个 + `cards` 2 个嵌套子动作各算一条，裸 `weblogin` 不是叶子）；`python -m actions.commandgen --check` 逐命令逐参数比对（漂移即退 1），`tests/test_v35_actions.py` 把同一比对钉进套件；**参数数据源**分 6 类（none/chips/server/topics_api/sessions_api/cards_api/exports_dir/auto_out），路径类参数（db/meta/*_root）全部 `server`=页面不暴露
- [x] P2-3 timeline 透出：GET /api/topic/<id>/timeline?level=（进程内调 topics.timeline 五档）　**验收**：任选一主题 coarse/mid 两档 JSON 返回且与 CLI 直跑字段一致；无 chain 主题不再需要"复制命令"　**证据（2026-10-12，commit bfca273）**：真机实测主题 `tp-20261008-001`（10 成员/0 链）——title **37ms**（rows=10）、coarse **2373ms**（rows=10）、mid **2407ms**（rows=19）；顶层键与上游 `topics.timeline` 同形（topic/level/members/budget/rows/missing/db_fingerprint + 本仓 additive 的 `view_actions_api`/`topic_id`）；错误路径：非法 level → **400**、fine 档缺 sid → **400**、不存在的主题 → **404**、缺 topics_meta → **503**（**参数校验先于环境校验**，否则"参数写错"会伪装成"服务缺能力"）；可选 `--corpus-watermark` 开启库龄判定（实测 0.063s，`stale` 由"当前语料 mtime ⟷ 上次索引水位"算出，缺省关＝`stale:null` 未判定）；**无 chain 主题照常有时间线**（实测该主题链数 0）
- [x] P2-4 动词面板 v0：A 档动作卡+参数选择器（主题下拉/会话多选带入/时间窗 chips）+结果抽屉　**验收**：report-tools 从选参到出结果全程零键盘输入　**证据（2026-10-12，commit 9d469ea）**：index.html 新增 Tab「命令台」（13 卡 → 命令 chips → 参数选择器 → 命令行预演 → 执行 → 结果抽屉）；参数渲染规则＝`src=chips` 给预设、`src=topics_api/sessions_api` 由「从库里选」面板拉真库清单点选、**`src=server` 一个输入框都不渲染**（只交代"由服务端起服时定"）；零键盘走查：选「工具体检」→ 选 `report-tools` → 点时间窗 chips → 点执行 → 右侧抽屉出结果（全程无输入）；**新渲染门 `tests/render_cmd.js`（23 断言）+ `tests/test_v35_cmd_render.py` 抓到一个真缺陷并已修**：`updateCmdPreview` 在"命令级 B 档且无对应动作"（= 43 条命令里的绝大多数）时读 `undefined.est_sec` **整页抛错**（P2-1 白名单只有 4 个动作，故这是默认路径）；已按"有没有可执行动作"分三支渲染并留回归断言（C 档/B 档/A 档无动作三条路径都不许崩）
- [x] P2-5 数字皆门第一批：错误模式→会话列表→回合原文三跳　**验收**：任一 pattern 三跳可达原文；沿途数字逐级对账一致（模式次数=样例会话数之和）　**证据（2026-10-12，commit 9d469ea）**：新增 `GET /api/pattern-sessions`（复刻上游 `errstats` 的 resolved 口径：错误之后同会话同工具是否再被调用）；**一跳**＝G2 模式行的次数变成可点的门（`data-pat2`）；**二跳**＝命中会话清单（逐会话次数/未解决/工具，页面上**显式对账**"逐会话次数之和 = 模式总数"，不一致就印"对账断了，先别下结论"）；**三跳**＝复用既有 `gotoAnchor` 按**错误原文逐字**扫回合并高亮（**刻意不拿 steps.seq 当 turn**：两者不是同一口径，硬拼会安静地跳错回合）；**真库全量对账实测：57 个归一模式逐个比对，报告 count 与二跳 count 不一致 = 0**（`FS_STALE_VERSION 72/72/11 会话`、`FS_NOT_OBSERVED 46/46/8`）；口径更正：**样例数（errstats 上限 3）≠ 会话数**，原验收句"模式次数=样例会话数之和"不成立，已按"逐会话次数之和=模式总数"落地（真数字）
- [x] P2-6 阶段门　**验收**：只读动作 0 复制粘贴走查；长任务无阻塞（用 keywords 全库跑验证后台+轮询）；session-harvester 零改动复核　**证据（2026-10-12，commit 9d469ea）**：**只读动作零复制粘贴**——新增 `regress` 动作（A 档，全程临时库），端到端实测 `exit_code=0 / 67 断言 / 0 失败 / 8 步全过`，**故意漂移验证**：`corpus=drift` 必须变红（实测 `exit_code=1 / 7 断言 / 3 失败 / 1 步 fail + 7 步 not_run`）——两种语料都绿就说明这个动作只是装饰，已钉进套件；**长任务无阻塞**：`POST /api/actions/run` 入队**18–19ms** 即返回 202 job_id（regress 1.2s、report_errors 220ms 都在后台线程），前端 `pollCmdJob` 400ms 轮询并显示"已用 Xs / 预估 ~Ys"，页面可关回来还能查（作业台账在内存，**诚实标注**进程重启即清空）；**view 测试 62 例全绿**（34→62）、JS 门两套绿（render_smoke 全过 / render_cmd 23 条全过）、真实载荷跨仓库门 **15 ok / 1 n/a / 0 FAIL**；**主仓 `git status` 跟踪文件零改动**（后端 deadcode-scan 复跑 `harvester/` `tests/` 0/0/0，exit=0）

## P3 裁决层（6 项：4 ☑ / 2 未开工）

- [x] P3-1 actions_registry.json v1 冻结：40 命令→13 卡→三档（update/sync=B；mcp-serve/api-serve/weblogin=C；regress=A 标重活）　**验收**：与 `harvester --help` 逐命令比对无遗漏无重复；三档归属与提案 §5.4 一致　**证据（2026-10-12，commit f1130ab）**：`actions/registrygen.py` + `actions/write_actions.json` → `actions_registry.json`（**A 档 4 / B 档 9（可逆 7、破坏性 2）/ 13 卡**）；**事实从 `commands.json`（真解析器生成）派生，人只写判断**（写哪个库/风险/确认页人话）；合并时**双向检查**（都会 fail loud）：引用不存在的命令/子动作 → 失败；动作声明了它**不消费**的参数 → 失败（首版这条就抓到我自己的错：`topic` 是聚合命令，按"命令全部参数"反查会让 `topic add` 声明 `--sid`）；`--check` + `test_v36_write_actions.py` 钉住漂移；**执行器只认注册表**（表外 403、C 档 403）；`/api/health.registry_problems` 起服自检
- [x] P3-2 B 档两段提交 + actions_meta.db 台账；view .gitignore 补 actions_meta.db　**验收**：suggest-status 采纳一条需 token 两段；台账记录含谁/何时/动作/参数/耗时；B 档无 token 直执被 403 拒　**证据（2026-10-12，commit f1130ab）**：`actions/runlib.py`（台账 + token）+ `actions/writeactions.py`（9 个动作的**只读预演** + 执行）；`POST /api/actions/run` 分档：B 档第一段只预演返回 `needs_confirm`+token+「将写什么」人话+目标库+风险，第二段带同一份 params+token 才写；**token 一次性/5 分钟过期/绑定 action_id+参数指纹+预览指纹**——真机实测四条拒绝全通（复用 409 `已用过（一次性凭据）`、改参数 409 `参数与预览时不一致`、过期、串动作），且**库在两段之间被改也拒**（预览指纹）；**预演零副作用**有测试钉住（plan 前后 meta 库字节不变）；台账 `action_runs`（**预演也记一行**）/`write_records`（before/after + **destructive 必带删除前快照**）/`confirms`，只读端点 `/api/actions/ledger`；`.gitignore` 已补 `actions/actions_meta.db*`；**口径诚实写明**：本服务绑回环且无鉴权，`client` 是调用方**自报**标签（非身份认证），token 是**意图确认**凭据（非权限凭据）
- [x] P3-3 今日工作台：四队列+闭环健康三根环　**验收**：四队列数字与各源端点逐一对账（建议池 22/候选簇/0 成员主题 1/待验证）；三环积压量口径在页面可见　**证据（2026-10-12，commit e5c35b1）**：`actions/overview.py` + `GET /api/overview` 现算四队列与三环，**每个数字带 `source`（哪个上游函数算的）与 `definition`（怎么算的）**，页面逐条显示口径（验收原文「三环积压量口径在页面可见」→ 已满足）；**前端只渲染服务端给的数、不二次聚合**（有渲染门钉住）；队列数字可点深链到对应页签（数字皆门第一跳）。**实测口径（2026-10-12 真库）**：待裁决 **0**（台账 24 条 = adopted 22 + rejected 2，已全裁决）/ 待修补 **6** 条建议（17 次未解决）/ 待蒸馏 **30** 个新错误模式（+31 条 skill 行为链 + 0 张待做卡片）/ 待验证 **9** 条「不下结论」；环1 381/22/9、环2 32/31、环3 13 主题/3 chain/4 卡片待确认；`GET /api/overview` 实测 **4.76 s**（现算）。**口径更正 3 处**：① 任务书举例"建议池 22"实为**待裁决 0**（22 是已采纳数，两回事）；② "候选簇"本轮实测两个口径（triage 新错误模式 30 / `topic-candidates` 13 簇），队列取**新错误模式**并在 definition 里写明；③ **"0 成员主题 1"已不成立**（v0.47 删掉空主题后实测 0 个）→ 未采用该判据
- [x] P3-6 阶段门　**验收**：B 档全留痕走查；C 档零执行走查；「乱点不可逆」逐卡走查记录在案；session-harvester 零改动复核　**证据（2026-10-12，commit e5c35b1）**：**逐卡走查做成了会红的测试**（`tests/test_v38_stage_gate.py` 8 例）——① A 档必须只读且**不得有写库声明**；② B 档必须有 risk/target（限三个 meta 库）/两段提交声明/必填参数人话 label；③ **destructive 的 detail 必须写明会丢什么**（清单钉死 = `topic_delete`+`topic_merge`，且预演必带删除前快照）；④ **C 档命令不得出现在任何可执行动作里**；⑤ **只有"有动作的命令"才可能被执行**（UI 只有两个 enable 分支，其余一律禁用 + 写明理由）；⑥ 动作面**只有一个写入口**（POST /api/actions/run，其余 POST 405）。**B 档全留痕**已由 P3-2 真机验证（runs 21 含 9 行 preview / writes 8 含 destructive 4 全带快照）。**主仓零改动复核**：`git status` 跟踪文件干净、`git diff --stat` 空、deadcode-scan `harvester/`+`tests/` **0/0/0**。**已裁决（2026-10-12，用户明示「保持：首页 = 今日工作台」）**——不再是挂账项。
- [x] P3-4 命令台完整 UI：13 动词卡+三徽章+确认弹窗+长任务轮询　**验收**：triage（~9min）可后台执行、页面可关、完成有通知；每卡三徽章齐全且预估耗时为实测值　**证据（2026-10-12，commit 5cf3115）**：写动作按 **action_id** 进动词卡（真缺陷驱动：B 档 id 不是命令名，按命令名查表会静默什么都不做）；选中写动作 → 参数来自注册表、**路径类零输入框**、按钮改「预演（先看清楚，再确认才写）」；点预演 → **确认框**逐行渲染服务端 `human`「将写什么」+ 目标库绝对路径 + 风险（破坏性红框 + 明写会丢信息）+ 凭据有效期；点确认才写，结果抽屉出 run_id/用时/summary 并刷新**右侧台账抽屉**（写记录/风险/谁写的 + 可展开"含只预演未确认"的动作列表）；取消明确说"没有写任何库"并在台账留 preview 行；长任务仍走 P2 的后台轮询（入队 18ms 返回、页面可关）。`tests/render_cmd.js` 23 → **34 断言**
- [x] P3-5 版本握手黄条：harvester 版本≠commands.json 适配版本→黄条+B 档禁用　**验收**：手动改 commands.json 适配版本号触发黄条；A 档不受影响；恢复后黄条消失　**证据（2026-10-12，commit 5cf3115）**：判据**只有服务端一处**（`/api/health.version_ok`，页面不自己比版本号，否则两处判据会分叉）；`renderVersionBar()` 在 `version_ok===false` 时出黄条并禁用 B 档按钮（文案"版本不匹配：B 档已禁用"）；`render_cmd.js` 四条断言覆盖：黄条出现且说明 B 档禁用、`writeDisabled()` 为真、写动作按钮禁用、**A 档仍可执行**、恢复 `version_ok` 后黄条消失
- [x] P3-6 阶段门　**验收**：B 档全留痕走查；C 档零执行走查；「乱点不可逆」逐卡走查记录在案；session-harvester 零改动复核　**证据（2026-10-12，commit e5c35b1）**：**逐卡走查做成了会红的测试**（`tests/test_v38_stage_gate.py` 8 例）——① A 档必须只读且**不得有写库声明**；② B 档必须有 risk/target（限三个 meta 库）/两段提交声明/必填参数人话 label；③ **destructive 的 detail 必须写明会丢什么**（清单钉死 = `topic_delete`+`topic_merge`，且预演必带删除前快照）；④ **C 档命令不得出现在任何可执行动作里**；⑤ **只有"有动作的命令"才可能被执行**（UI 只有两个 enable 分支，其余一律禁用 + 写明理由）；⑥ 动作面**只有一个写入口**（POST /api/actions/run，其余 POST 405）。**B 档全留痕**已由 P3-2 真机验证（runs 21 含 9 行 preview / writes 8 含 destructive 4 全带快照）。**主仓零改动复核**：`git status` 跟踪文件干净、`git diff --stat` 空、deadcode-scan `harvester/`+`tests/` **0/0/0**。**已裁决（2026-10-12，用户明示「保持：首页 = 今日工作台」）**——不再是挂账项。

> **动线决策：已获用户裁决（保持首页 = 今日工作台）**。原说明：本轮把**默认落地页**从
> 「会话检索」改成了「今日工作台」（提案 §2.2 的目标形态：首页 = 今日工作台）。
> 变更点只有 `index.html` 启动处三行（`state.loaded.home=true; switchTab("home")`
> + 导航 `home` 按钮带 `class="on"`），并且**不再无条件 `loadAll()`**（会话列表
> 改为点「会话检索」时按需加载）。用户若不接受，把 `switchTab("home")` 改回
> `loadAll()` 即可（工作台仍作为普通页签保留）。

## 用户实测反馈批 v0.49（会话 6 · 4 项 · 触点仅在 harvester-view 仓）

- [x] V49-1 **处理单（唯一真源）**：`actions/workitems.py` + `GET /api/workitems?target=`；`/api/overview` 改为**只消费**这一份（数字由明细按 `mode` 派生）　**验收**：工作台上每个数字都能点开**正好那一批**明细，条数相等；`reconcile_ok=false` 页面响亮报警　**证据（2026-10-13）**：真库实测 **17 target / 25 指标全部对账通过**（探针 `docs/reports/probe-workitems-live.py` 输出「对不上的指标数 = 0」）；`tests/test_v40_workitems.py` 27 例（含 `test_every_queue_count_equals_its_item_list` = 用户所报缺陷的**回归门**）
- [x] V49-2 **队列明细行逐行指向 target**：`DETAIL_MAP` + `build_queue_details`；两个聚合数显式标 `aggregate`　**验收**：卡片下每一行数字要么能点开自己的明细、要么标注"聚合数（不派活）"，不许第三种　**证据（2026-10-13）**：`test_queue_detail_value_equals_target_metric_value` + `test_aggregate_rows_are_labelled_not_pretending`；真库 9 行指向 target、2 行标聚合（噪声登记 163 / 未评估 15）
- [x] V49-3 **配色修复**（用户原话「今日工作台浅底浅色，看得眼花」）　**验收**：两种模式下文字/底色对比度 ≥4.5:1；`var(--x)` 必须真被定义；写死浅底又不钉前景的规则一律红　**证据（2026-10-13）**：根因 = 11 处写死浅底（dark 模式下 `--ink` 翻浅色字 → 对比度 **1.1:1**）+ **13 处 `var(--sub)` 而 `--sub` 从未定义**；`tests/test_v40_theme_contrast.py` 7 例全绿；变异 1/2 变红（见 `docs/reports/mutation-check-v049.py`）
- [x] V49-4 **闭环健康每一段可点 + 有建议**（用户原话「当前不能跳转，也无建议」）　**验收**：三环 11 段全部有 `target` 与 `suggestion`，与四队列走同一批渲染函数　**证据（2026-10-13）**：`render_home.js` 断言 `data-wgo` 计数 = 3（夹具）+ 每段带建议；真库 `test_every_ring_arm_count_equals_its_item_list` 逐段对账
- [x] V49-5 阶段门（本批收尾）　**验收**：view 测试全绿 + 5 个 node 门全绿 + 变异复核 5/5 被抓 + 上游零改动　**证据（2026-10-13）**：view **144 例**；5 个 node 门全绿；`mutation-check-v049.py` **5/5 全部被抓且已还原**；session-harvester 除**用户批准的后端线**（会话删除/抑制，独立子代理）外零改动
  - ⚠ **本批未覆盖 / 已知范围**：① 处理单的命令包里 `chain-validate` 是**按文件**给的真实路径（同目录库用文件名、非同目录用全路径），若 `chain-root` 下扫不到文件则给 `<chain 长文路径>` 占位并写明——**占位不能直接跑**；② 真库对账测试在缺真库时 `skip`（`skip ≠ 通过`），故空语料下的对账逻辑另有一组**不依赖真库**的用例；③ 「聚合数不派活」的判定是**人写的** `_AGGREGATE` 表，新增数字忘了登记会被 `build_queue_details` 的 KeyError 挡下（响亮），但"该不该算聚合数"仍需人判断

- [x] V49-6 **会话删除（抑制）的 view 侧**（用户批准清单 ①–⑦、⑧ 不做；会话 6 续批）　**验收**：不新造写路径（B 档两段提交 + 台账 + destructive 快照）；抑制表写对库（`topics_meta`）；口径逐字写明「仓内副本 / 不删采集源 / 只取决于语料是否还在 / 不设恢复时限」；端到端只在临时库上跑　**证据（2026-10-13）**：新增 `session_suppress`（destructive）+ `session_unsuppress`（reversible），**B 档 9 → 11**（可逆 8、破坏性 3）；`GET /api/suppressed`（只读、**缺表不建表**）；会话页详情加「删除（抑制）」、筛选区加「已抑制」→ **服务端全量清单**（不做分页筛选，各条带「恢复」）；`tests/test_v41_suppress_view.py` **17 例**（含端到端：抑制 → 三表 0 行 + 抑制表有行 + exports 副本被删；恢复 → 抑制行没了且**数据不回来**；真库大小+mtime 不变）；`render_cmd.js` +14 条断言；破坏性清单与 B 档计数两处钉子**按流程显式复核后更新**并写明理由
  - ⚠ **本批未覆盖 / 已知范围**：① **真库首次 `suppress --apply` 仍未做**（须先备份 410 MB 库 + `PRAGMA quick_check`；见后端交接 §4 第 3 条）；② 抑制的「可恢复」**没有探测能力**（只删仓内副本、不查采集源；用户 ⑧ 裁决不设时限，故只如实说"取决于语料是否还在"）；③ 全套件 stderr 有 `ConnectionAbortedError`/`ConnectionResetError` 噪声（HTTP 测试服务器在客户端提前断开时记的日志；套件 `Ran 164 / OK`，**本轮未追查**——若日后要清，应确认是哪个用例的 teardown）

- [x] V49-7 **查漏批：配置缺口 + 欠账清偿**（会话 6 续批二）　**验收**：把"明说没覆盖/没做"的欠账与**没人发现但真会咬人**的配置缺口逐条清偿，且都有会红的门　**证据（2026-10-13）**：① **真缺陷**——`start-viewv2.cmd` 没传 `--exports-root` → 运行中服务 `exports_root=None` → view 侧抑制**清不掉 exports 旧副本**（旧副本还在 → 下次 sync 把会话收回来）；已补参数 + 两道门（读启动脚本断言该参数在；断言"没配时预演必须响亮警告"）；② **prune 真库耗时已实测**（1983 json / 冷 2.06 s、热 0.67 s / 0 个读不出 sid → 每次 sync 多约 0.7 s，**不需要**改 manifest）；③ **真库备份 + `quick_check` = ok**（提案 ⑦ 前置）；④ **P4 体积探针已跑**，结论 = 先加"排除模板/注入/正文粘贴"的过滤再建模块
  - ⚠ **仍未做**：**真库首次 `suppress --apply`**（须用户点名 sid；备份已就位）；**P4-1/P4-3 模块未建**（探针结论是"先过滤、先出候选清单"）

## P4 进化层（6 项）

- [x] P4-1 nags.py：用户轮次指令/抱怨词句 × 跨会话聚类 → 候选带锚点（独立模块只 import harvester）　**验收**：对真库出首份候选清单；抽样 5 条锚点回翻原文属实；**session-harvester 零改动复核**
  - ✅ **已建（2026-10-13 会话 8，用户裁决"肯定要做"——反复叮嘱之层一：指令遵循提醒）**：`actions/analysis/nags.py`（探针 v2 口径逐值继承：注入剥离/剥后过短/长粘贴 600/模板 ≥8/归一键 80/相似归并 0.55）。真库首份候选：`docs/reports/p4-nags-candidates-v52.md`（26 组，与探针对账恒等）；**抽样 5/5 锚点回翻原文属实**；只读门（源码无写库语句）+ session-harvester `git status` 零改动复核 ✓；测试 `tests/test_v42_p4_analysis.py` 9 例（先红后绿）。
  - ▶ **前置探针已完成（2026-10-13 会话 7，按交接 §4 第 4 条判据"先过滤、先出候选清单、不直接建模块"）**：`docs/reports/probe-p4-nags-filtered.py`（v2，双通道）→ 候选清单 `docs/reports/p4-nags-candidates-2026-10-13.md`。过滤账目：7011 轮 → 剥 system-reminder 注入 233 → 剥后过短 1388 → P4-1 通道再滤长粘贴 1318 → 命中词句 434 → 逐字模板（≥8 次）滤 1 组 → 相似归并后**跨 ≥2 会话 26 组全列**（≥3 会话仅 2 组）。**人工回翻原文**：≥3 会话的 2 组均为**任务模板**（"写作理论提炼助手精读文件"×7 会话、"整理谈话内容"×3 会话），非行为叮嘱；2 会话组里人工捞出**真叮嘱 2–3 条**（「做完了，注意要更新文档（仓库结构说明/操作手册…）」「踩到的坑记到记忆中以后不要再踩」——全在 harvester 项目自身会话里）。**量级结论：真叮嘱个位数 → 按工具化铁律不建 nags.py**；这几条更适合人工直接进 AGENTS.md/班规（P4-2 思路），或留档等语料增长再议。**复探针教训**：v1 把粘贴过滤错套到 P4-3（打磨带的主信号恰是长稿子反复贴，滤掉后 87 对缩成 32 对且浓度被摊平）——三层过滤只属 P4-1，已修。
- [x] P4-2 nags → 建议池第二输入源：md/JSON 产出 + 两类归宿标注（班规条目/harness 问题清单，owner 三值）　**验收**：产出文件可被 suggest-status 流程消费（或人工裁决留痕）；样例含「注意上下文空间」「DSH 跨会话就忘」类条目的归宿判定示例　**证据（2026-10-13 会话 8c）**：`actions/analysis/nags.py` 增 `classify_destination/build_pool/render_pool_md` + CLI `--pool-out/--meta`（additive）；owner 三值沿 agent_suggest v0.19 冻结定义；curated 判据含 TODO 点名两例（「上下文空间」→班规/workflow，「跨会话」→问题清单/harness）+「能不能」→tool 问题清单；每条带文本派生稳定 key（sha1 前 12）+ 复制就绪 suggest-status 命令（**绝不代执行**——红线）+ 人工裁决留痕列；test_v43_nags_pool.py 11 例先红后绿（key 跨运行稳定/JSON 可序列化/只读门/CLI 冒烟）；真库首份 `docs/reports/p4-nags-pool-2026-10-13.{md,json}`（班规 22 / 问题清单 4，判定仅建议已知局限：「能不能」curated 对「看能不能修改一下」这类非诉求句误判，人改判即可）
- [x] P4-3 revisions.py：相邻轮次 Jaccard 边界带（0.80–0.90，topicprep 口径勿改）+ 转折词 → 打磨/反转候选带锚点　**验收**：真库出首份候选；抽样核对"打磨"确实是同一论点改写而非巧合相似；转折词命中样例 ≥3 个
  - ✅ **已建（2026-10-13 会话 8，用户裁决——反复叮嘱之层二：产出打磨推敲）**：`actions/analysis/revisions.py`（边界带直接 import `topicprep` 阈值；**只剥注入、允许粘贴**——探针教训；转折词只认轮次开头）。真库首份候选：`docs/reports/p4-revisions-candidates-v52.md`（95 对/63 会话 + 转折词 9 轮，与探针 v2 恒等）；样例人工过目：边界带 Top 对均为同一稿子微调重复送审（打磨属实），转折词命中全是"改成这样：…"式真实打磨指令（≥3 ✓）。
- [ ] P4-4 主题工作台双轨时间线：讨论流+产物流+打磨带+反转点标记（先候选后确认升级）　**验收**：任选有 artifacts 的主题上/下轨同屏对齐；打磨带与 P4-3 候选一致；反转点标记可点击看两轮原文对比
- [ ] P4-5 skill 调用点挂轨道（消费 report-skill-join 数据）　**验收**：pdf-text-extractor 案例在时间线上可见调用点与成败
- [ ] P4-6 阶段门　**验收**：两条新管线首份报告归档；确认流可用；session-harvester 零改动复核

## P5 打磨（4 项）

- [ ] P5-1 「5 分钟之旅」五页引导（用 Edit-前-Read 真实班规闭环）　**验收**：新手未读文档能跟随五页完成一次完整闭环浏览
- [ ] P5-2 三身份视觉打磨 + 数据新鲜度常驻条　**验收**：机械事实/AI 判断/人的裁决三种块肉眼可分；新鲜度条随指纹轮询变色（灰→黄）
- [ ] P5-3 空态与确认流统一：全部"复制命令"空态改为"选参数→执行"；确认弹窗文案统一　**验收**：全站 grep 无残留"复制命令"空态（C 档按钮除外）
- [ ] P5-4 工程收尾　**验收**：TODO 全清；handoff 终版；小白三问全站抽查；用户验收清单交付

## 横切检查项（每阶段门必含，不单独计数）

- [x] X-1 session-harvester 零改动复核：`git -C session-harvester status/diff` 干净　**证据（2026-10-12 P2 会话）**：`git status --short` 只有未跟踪的 `_eng/`、`_research/`、`docs/PROPOSAL-harvester-view-presentation.md`（均非本会话产物）；跟踪文件 `git diff --stat` 空
- [x] X-2 两仓测试绿（主库 761 不变 / view 25+新增）　**证据（2026-10-12 P2 会话）**：主库 **785 例 OK**（328.6s，未改动故与上批同值）、主库死代码两根 **0/0/0**；view **62 例 OK**（34→62，+28）
- [x] X-3 JS 门 + 跨仓库集成门不回退　**证据（2026-10-12 P2 会话）**：`render_smoke.js` ALL PASSED｜新增 `render_cmd.js` 23 断言 ALL PASSED｜真实载荷门 `check_real_payload.py` **15 ok / 1 n/a / 0 FAIL**（含 381 三重对账）
- [x] X-4 版本标注一致（commands.json 适配版本 = 真实 harvester.__version__）　**证据（2026-10-12 P2 会话）**：`adapter_version=0.46.0` == `harvester.__version__=0.46.0`，`/api/health.version_ok=true`；对账由 `test_health_reports_version_handshake` 钉住（改一侧不改另一侧即红）
- [x] X-5 涉 UI 阶段的小白三问自验记录　**证据（2026-10-12 P2 会话）**：① 30 秒说主结论——命令台左栏 13 张动词卡每张自带"N 命令（M 可直接执行）"；② 下一步点什么——选卡→选命令→填参数→执行，按钮文案直接写"执行（进程内调用 harvester，非 shell）"，不可执行时写明**为什么**（C 档占端口/需人登录、B 档要两段提交、A 档白名单还没有）；③ 乱点不可逆——命令台只执行 A 档白名单（4 个动作）且**全部只读**（regress 全程临时库），路径类参数一个输入框都不渲染，B/C 档只有命令行文本，`POST` 只认 `/api/actions/run` 一个入口

---

## 变更记录

| 日期 | 变更 | 批准 |
|---|---|---|
| 2026-10-12 | v1.0 首版（34 项：P0×3 + P1×6 + P2×6 + P3×6 + P4×6 + P5×4 + 横切 5 不计数） | 待用户开工确认 |
| 2026-10-12 | P1 会话：P0×3 ☑ + P1×6 ☑（commit 2c1e060+`d975639`）；测试基线修正 785；D9 新增。**未推送：origin/main 落后本地 2 提交（github:443 三次连接失败，沙箱无网）** | 铁律 #3 证据留痕 |
| 2026-10-12 | P2 会话：**P2×6 全 ☑ + 横切 5 项全 ☑**（commit `bfca273`+`6ad5cec`+`9d469ea`）；view 测试 34→**62**；新增 `actions/` 包（actionsserve/commandgen/commands.json）；口径更正 3 处（顶层命令 40 vs 叶子 43；样例数≠会话数；P2-1 动作清单实为 4 个含 regress）；**抓到并修掉一个真缺陷**（命令台非 A 档路径整页抛错） | 铁律 #3 证据留痕 |
| 2026-10-12 | **SOP §4 统一阶段收尾门（P2）逐项过**：① P2 全 ☑ 带证据 ✓ ② 两仓测试绿（主库 785 / view 62）✓ ③ JS 门 + 跨仓库集成门不回退（render_smoke / render_cmd / check_real_payload 15 ok）✓ ④ session-harvester 零改动复核 ✓ ⑤ commands.json 版本标注一致（version_ok=true）✓ ⑥ HANDOFF 已更新 ✓ ⑦ 小白三问自验在案 ✓ | 铁律 #3/#4 |
| 2026-10-12 | P3 会话：**P3-1/P3-2/P3-4/P3-5 完成**（commit `f1130ab`+`5cf3115`）；view 测试 62→**84**（+22）；新增 `actions/` 三个模块（registrygen/runlib/writeactions）+ 两个数据文件（actions_registry.json/write_actions.json）；**新增决策 D13–D15**；口径诚实写明三条（client 非鉴权 / token 非权限 / 预演报的是"当前状态+将做的操作"而非"保证结果"）。**P3-3（今日工作台）与 P3-6（阶段门）未开工**——按铁律 #5 在可提交点收尾 | 铁律 #3 证据留痕 |
| 2026-10-12 | P3 真机验证（三进程 + 真 HTTP + 台账落临时库）：两段提交四条拒绝路径全通；**用 `topic_delete` 清掉验证造出的 4 个空主题**（每步带删除前快照），真库回到 **13 主题 / 0 残留**；台账 runs 21（9 行 preview）/ writes 8（destructive 4 全带快照）| 铁律 #4 证据留痕 |
| 2026-10-12 | P3 收尾会话：**P3-3 今日工作台 + P3-6 阶段门完成**（commit `e5c35b1`）；view 测试 84→**104**；新增 `actions/overview.py` + `GET /api/overview` + `tests/render_home.js` + `test_v37_overview.py` / `test_v37_home_render.py` / `test_v38_stage_gate.py`。**口径更正 3 处**（建议池 22 实为待裁决 0；候选簇两口径取 triage；"0 成员主题 1"已不成立）。**门抓出两个我自己的错**（`given_up` 未在 API 透出导致口径冲突；逐命令走查判据写错红了 27 条）| 铁律 #3 证据留痕 |
| 2026-10-12 | **SOP §4 统一阶段收尾门（P3）逐项过**：① P3 全 ☑ 带证据 ✓ ② 两仓测试绿（主库 785 / view **104**）✓ ③ 三个 JS 门 + 跨仓库集成门不回退 ✓ ④ session-harvester 零改动复核（diff 空、deadcode 0/0/0）✓ ⑤ 版本标注一致 ✓ ⑥ HANDOFF 已更新 ✓ ⑦ 小白三问自验在案 ✓（**动线已裁决：默认落地页 = 今日工作台，用户 2026-10-12 明示「保持」**）| 铁律 #3/#4 |
| 2026-10-13 | 会话 7：P4-1 前置探针（过滤版 v2）+ 候选清单产出（`docs/reports/p4-nags-candidates-2026-10-13.md`）；人工回翻核实 ≥3 会话候选均为任务模板，真叮嘱个位数 → **建议不建 nags.py**（待用户裁决）；两仓待推清零（后端 2 提交在本会话推成功，此前四次网络失败）；探针自身两错被人工复核抓出（turn_hits 初值、粘贴过滤错套 P4-3）| 铁律 #3 证据留痕 |
| 2026-10-13 | 会话 8：**用户裁决推翻"不建"建议（反复叮嘱=两层语义：①指令遵循提醒→P4-1；②产出打磨推敲→P4-3，都要建）**→ `actions/analysis/nags.py` + `revisions.py` 落地（9 例测试先红后绿；真库候选与探针恒等 26 组/95 对/9 轮；nags 抽样 5/5 锚点回翻属实）；**suppress 去 sid 化**（用户："一般人谁记这个"）→ 后端 `suppress candidates` 只读候选清单（64 簇/172 条 + 零 user 轮次 100 条，25.6s；两阶段粗筛+全量复核防 OOM，FTS 逐会话查询 463s 教训）→ view COMMAND_CARD 收录 + commands.json 重生成（漂移门先红）；README 命令表同步 | 铁律 #3/#6 |
| 2026-10-13 | 会话 8b/8c：**可移植性三问**（knowledge/topics 是否存库？三脚本区别？机器无关确认）→ 判定宿主数据不入库、修两处真硬编码（commands.json 烤进本机路径 → `~` 形式归一；start.cmd 兜底解释器 → %USERPROFILE%，view 479d09f）；**P4-2 归宿建议池落地**：nags.py 增 classify_destination/build_pool/render_pool_md + CLI --pool-out（owner 三值沿 agent_suggest v0.19；curated 判据含 TODO 点名两例；稳定 key + 复制就绪 suggest-status 命令，绝不代执行；test_v43 11 例先红后绿；真库首份 p4-nags-pool-2026-10-13.{md,json} 班规 22/问题 4）| 铁律 #1/#3 |
