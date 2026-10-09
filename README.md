# session-harvester — AI 会话采集·导出·检索·蒸馏一体化套件

从本机各 AI 工具（Agent harness / IDE 助手 / 网页 Chat）提取全部历史会话，
建成**可全文检索的本地库**，并支撑后续蒸馏（思路过程 / 工具改进 / Harness
踩坑 / 用户画像）。

当前资产：**9 个数据源已实装入库**，`harvester.db` 共 1946 会话 /
55203 消息 / 30956 工具步（截至 2026-10-07，随 sync 持续增长；
FTS5 中文可检索，sessions 含模型归属列）；另有 6 个桩位
留接口。

依赖边界（诚实声明）：

- **`harvester/` 包本体零第三方依赖**——纯 Python 3.10+ 标准库，单测同
  （unittest）。`python -m harvester ...` / `python -m unittest discover -s tests`
  中的 `python` 指代"你的解释器"。
- **`verify/` 采集工具需要 `requests` + `websocket-client`**（登录态直采管
  线，见下文），用独立 venv 运行，不污染包本体。
- 敏感文件（`weblogin_profile/`、`webchat.accounts.json`）与运行产物已列入
  `.gitignore`。

## 快速开始

### A. 已有索引库（最常见）

```bash
python -m harvester search "cookie 登录态" --db harvester.db   # 全文检索（<<>> 高亮）
python -m harvester read 42 --turn 1     # 分层读取：先纲要后下钻（1..N / last / all）
python -m harvester mcp-serve --db harvester.db               # MCP server，任意 agent 直查
```

### B. 从零全流程

```bash
# 0. 固化探测：扫描本机文件系统签名，发现所有 AI 会话数据源 → sources.json
python -m harvester probe

# 1. 查看各数据源探测状态（STUB 类附带登录态/获取指引）
python -m harvester adapters

# 2. 扫描并生成会话纲要（outline.md / outline.json：序号|来源|日期|标题|条数|概要）
python -m harvester scan

# 3. 按纲要序号导出（大类=来源分类，小类=月份；支持区间与逗号组合）
python -m harvester export --select "3,5-9" --out exports
python -m harvester export --all --out exports

# 4. 建全文索引后进入 A（检索是 search/MCP 的前置）
python -m harvester index --all --db harvester.db
```

### B+. 日常增量（推荐）：一键同步

```bash
python -m harvester sync      # 收件箱收割 -> 全量导出 -> 整库重建索引，末尾报"本次新增 N 个会话"
```

- **本地文件源**（workbuddy-transcript / dsh / autoclaw / vscode-copilot）：
  零人工，`sync` 直接扫文件系统增量入库；可挂系统定时任务每天跑。
- **官方导出源**（deepseek-export / chatgpt-export / claude-export）：
  唯一人工动作 = 把导出包（zip 或 json）丢进 `inbox/` 目录。`sync` 让各
  导出适配器竞标认领（结构校验，绝不臆测解析），归档到 `inbox/done/<来源>/`
  并入库；认领失败的文件留在原地报告，绝不静默丢弃。
- 幂等可重跑：导出文件名确定性覆盖，索引整库重建，新增判定=sid 集合差。
- 默认包含 note 消息（思考/工具步骤）——工具步骤藏在 note 的 raw 里，
  排除会让重建后的 steps 表为空、G1/G2/G4 分析失效；确要纯对话加
  `--no-notes`。
- 协议细节与采集/分析的正式接口边界见 **`docs/ADAPTER_CONTRACT.md`**。

导出产物结构：`<大类>/<年-月>/<时间戳>__<来源>__<标题>.{md,json}` +
`export_manifest.json`（清单+桩位状态）。思考过程/工具调用等非对话条目
**默认包含**（note 角色），蒸馏与 2.2/2.3 分析依赖这些证据，勿关闭；
确需纯对话加 `--no-with-notes`。

### B++. 日常增量（推荐）：update

```bash
python -m harvester update    # 收件箱收割 -> 只导出新增/变更会话 -> 整库重建索引
```

- **增量口径**（2026-10-09 用户裁决）：不全量重取历史轨迹，只获取未获取的
  ——新 sid 导出；`updated_at` 变化（续聊）重导该会话；其余跳过。
- **库即水位**：高水位 = 索引库 `sessions(sid, updated_at)`，无独立状态
  文件；首跑无库 = 天然全量。
- `sync` 保持全量语义，作为对账/rebuild 基线（怀疑漏数据时跑一次 sync 对账）。

### 主题注册（T 轨）：发现簇 → 注册 → 页面出现

主题**不是固定清单**：由聚合数据中发现簇后注册进 meta 库，注册即出现在
view「主题」tab；代码零写死主题名。三个命令：

```bash
python -m harvester topic-candidates --db harvester.db --out docs/reports/   # 聚类推荐候选簇（只产候选，不改注册表）
python -m harvester topic register --meta topics_meta.db --name "主题名" --keywords "k1,k2"   # 认可后注册
python -m harvester topic add --meta topics_meta.db --id <tp-id> --sids "<sid1>,<sid2>" --evidence "出处"   # 挂成员
python -m harvester chain-validate "C:/.../chain-长文.md"   # 主题 chain 长文独立校验（members/stages/nodes）
```

- 注册库默认读 `topics_meta.db`（与 harvester.db 同目录）；**view 的
  「主题」tab 需要 api-serve 启动时带 `--topics-meta <topics_meta.db>`**
  （`start.cmd` 已内置），未配置时该 tab 空表并提示 hint，不炸。
- 批量注册场景（117 簇级别）参考 `scripts/register_candidates_20261009.py`
  ——解析候选报告后逐簇调 `topics.register_topic` + `add_members`。

## 全命令速查表

> `python -m harvester <命令> --help` 看完整参数。默认都读当前目录
> `harvester.db`；分析口径统一用 `messages.raw`。

### 采集与同步

| 命令 | 用途 |
|---|---|
| `probe` | 固化探测：扫描本机数据源签名并生成 sources.json |
| `adapters` | 显示各数据源探测状态（OK/STUB/MISSING） |
| `scan` | 扫描数据源并生成可导出会话纲要 |
| `export` | 按纲要序号导出会话（`--select`/`--all`） |
| `sync` | 一键同步（全量）：收件箱收割 → 导出 → 重建索引，幂等；对账/rebuild 基线 |
| `update` | 一键同步（增量）：只导出新 sid/updated_at 变化的会话；日常更新用这条 |
| `weblogin check` | 探测各网页 Chat 产品浏览器登录态 |
| `weblogin init-config` | 生成账号密码配置模板（预留接口） |
| `weblogin prepare <产品>` | 打开自动化浏览器完成人工登录 |

### 索引与检索

| 命令 | 用途 |
|---|---|
| `index` | 构建 FTS5 全文索引 |
| `search` | 全文检索历史会话 |
| `read` | 按纲要序号读会话（`--turn` 下钻） |
| `pack` | 产出跨 agent 上下文交接包 |
| `mcp-serve` | MCP stdio server：Agent 直查历史 |

### 分析报告（消费索引库）

| 命令 | 用途 |
|---|---|
| `report-tools` | 工具调用/失败率统计 |
| `report-errors` | 错误三分类报告（含 数据源×model×类别 交叉表） |
| `report-chains` | 工具链失败翼报告（长回合/连击/循环/空转） |
| `report-traces` | OTel trace 工具统计（耗时/失败率/取消） |
| `report-skill` | Skill 行为画像（G4）：按 skill 聚合调用/行为链 |
| `report-skill-join` | skill 进化 join：chain 锚点 × skill 调用锚点交叉表（T4） |
| `keywords` | n-gram 关键词统计（只统计 messages.raw；落 keywords_meta.db） |
| `suggest-agents` | 从错误模式生成 AGENTS.md 候选条目（建议池，不直改） |
| `suggest-status` | 建议池状态落库（pending/adopted/rejected） |
| `export-analysis` | 统一分析导出器：sessions/tools/errors/skills/triage × md/JSON 同源（view 导出按钮走这条） |

### 蒸馏与知识库

| 命令 | 用途 |
|---|---|
| `aggregate` | 聚合会话为语料（蒸馏喂料） |
| `triage` | 蒸馏队列：新错误 pattern/旧坑重现/Skill 行为链/高信号会话 |
| `draft` | 蒸馏包：会话原文+卡片规范+指令 → 自包含 md 喂 Agent |
| `artifacts` | 产物提取：Write/Edit args 回源 → artifacts_meta.db |
| `cards validate` | 校验卡片目录（§8 规范 + 锚点真实命中索引库） |
| `cards new` | 从索引库会话生成卡片脚手架（evidence 留白） |
| `kb-init` | 建知识库骨架（幂等） |
| `kb-stats` | 知识库盘点 |

### 主题注册（T 轨）

| 命令 | 用途 |
|---|---|
| `topic-candidates` | 自动聚类候选推荐：只产候选簇报告，不改注册表 |
| `topic register/add/remove/list/show/…` | 主题注册表：认可候选后注册进 topics_meta.db（注册即出现在 view 主题 tab） |
| `chain-validate` | topic-chain 长文独立校验（frontmatter + 锚点可回溯） |

### 服务

| 命令 | 用途 |
|---|---|
| `api-serve` | 只读 HTTP JSON API（默认 127.0.0.1:8765；非回环 host 必须 --token；`--topics-meta/--cards-root/...` 启用对应端点） |

## 套件结构

```text
                     ┌─ 采集管线（verify/，见「网页 Chat 直采」节）
                     │   元宝/千问/豆包 登录态直采 → corpus/*_raw/
                     ▼
采集（probe）──► 纲要（scan）──► 选择导出（export）──► 检索/读取/交接（index/search/read/pack）
   discovery.py      outline.py        exporter.py         indexing.py / reader.py / pack.py
        │
        └──► 聚合语料（aggregate）──► 知识库（kb-*）──► MCP（mcp-serve，agent 运行时直查）
                distill.py              distill.py        mcpserver.py

语义归类/增补按 docs/DISTILL_PLAYBOOK.md 执行（宿主无关；WorkBuddy 的 skill
`session-knowledge-distill` 仅为该 playbook 的薄绑定）。
```

| 模块 | 文件 | 职责 |
|---|---|---|
| 发现 | `discovery.py` | 文件系统签名扫描，产出 sources.json |
| 采集 | `adapters/` | 各数据源 Adapter（实装 11 个 + 桩位 6 个） |
| 直采管线 | `verify/` | 元宝/千问/豆包登录态采集器 + schema 探针（venv 依赖） |
| 导出 | `exporter.py` | 按来源大类/月份小类落盘（md+json） |
| 检索 | `indexing.py` | FTS5 全文索引与查询（零依赖） |
| 读取 | `reader.py` | 分层读取：read → --turn 逐回合下钻 |
| 交接 | `pack.py` | token 预算内的跨 agent 上下文交接包 |
| MCP | `mcpserver.py` | stdio MCP server：任意 agent 运行时直查历史 |
| 诊断 | `toolstats.py` / `errstats.py` | 工具失败率/重试放弃；错误三分类+位置分桶（均含按 Agent/数据源分组） |
| 行为画像 | `behstats.py` | report-skill：按 skill 聚合调用/触发任务/调用后行为链（G4 确定性主干） |
| 建议闭环 | `agent_suggest.py` | AGENTS.md 候选条目生成（建议池，人工并入） |
| 卡片库 | `cards.py` | §8 frontmatter 校验（validate）+ 会话起卡脚手架（new）；工作流见 docs/CARD_WORKFLOW.md |
| 蒸馏队列 | `triage.py` | triage：新会话确定性初筛排队（新错误pattern/旧坑重现/Skill行为链/高信号会话，T1） |
| 蒸馏包 | `drafting.py` | draft：会话原文+卡片规范+指令 → 自包含 md 喂任意 Agent 起草（T2 确定性一半；草稿落 cards_pending/，validate 照跑，并入人工） |
| 蒸馏 | `distill.py` | 语料聚合、知识库骨架、盘点（机械部分） |
| 语义提炼 | skill `session-knowledge-distill` | 通读语料后的归类、话题增补、反馈台账（agent 步骤） |

## 当前数据源支持状态

| 状态 | 数据源 | 说明 |
|---|---|---|
| ✅ OK | WorkBuddy 会话轨迹（v0.6） | `~/.workbuddy/projects/*/*.jsonl`（约 268MB 真实逐回合轨迹，含子代理）；`<user_query>` 提取真实发言、harness 注入上下文与思考/工具调用均独立为 note |
| ✅ OK | DSH / DeepSeek Harness（v0.6） | `~/.dsh/sessions/*/*/session.v4.jsonl.zstd`（多 frame zstd；解压按 compression.zstd→zstandard→zstd.exe→node 分层，全部不可用则 STUB）；approval/sandbox 策略产出 note（2.3 信号） |
| ✅ OK | 腾讯元宝（登录态直采，v0.8）/ 通义千问（CDP 直采，v0.9）/ 豆包（app-driven capture，v0.9） | 无官方导出、正文在服务端 → 登录态调官方 API 或截获官方响应，schema 即服务端原始 JSON；详见「网页 Chat 直采管线」节 |
| ✅ OK | VS Code Copilot Chat | `session-store.db`（SQLite）+ 空窗口 jsonl + 工作区 `chatSessions/*.jsonl`（补丁日志重构，v0.4） |
| ✅ OK | AutoClaw 桌面版 | `runtime.sqlite`；request 按用户轮次去重、思考/工具调用独立为 note、answer 去重、UTC→本地时区（v0.4）；schema 列结构守卫，改版自动降级 STUB |
| ✅ OK | DeepSeek / ChatGPT / Claude 官方导出文件 | 解析各家「数据导出」产出的 conversations.json（ZIP/目录/单文件均可）；**无需账号密码、零风控**。DeepSeek 真实导出（mapping/fragments DAG，2026-10-06 真机核验，316 会话全量通过）支持分支主链选择与 THINK/FILE/SEARCH/TOOL_* 证据提取。获取方式见 `harvester adapters` 输出 |
| 🔶 部分 | WorkBuddy 工作区日志 | 59 个工作区的每日日志。**注意：这是 agent 自述的工作总结，不是对话轨迹**——真实逐回合轨迹在 `~/.workbuddy/projects/`（上一行 v0.6 已实装） |
| 🔶 STUB | DeepSeek 桌面版 / 元宝 / 豆包 / 千问 / Trae / AutoClaw 用户目录 | 桩位留接口；元宝/豆包/千问的正文本体在服务端，已由直采管线覆盖（见下） |
| ⚪ MISSING | AutoClaw 自部署实例 | 需用户提供数据位置后接入 |

## 网页 Chat 直采管线（元宝 / 千问 / 豆包）

三平台均无官方导出、会话正文本体在服务端。统一原则：**不碰账号密码、
不做风控对抗，在登录态下让页面/浏览器自己调官方端点，采服务端原始 JSON**，
产物落 `corpus/{yuanbao,qianwen,doubao}_raw/`。adapter 消费本地归档，
与服务端解耦。

### 标准流程（采集 → 校验 → 入库）

```bash
# 1) 启动带调试端口的 chrome（持久 profile 保持登录态），后台任务托住进程
#    chrome.exe --remote-debugging-port=<9333|9334> --remote-allow-origins=* \
#        --user-data-dir=<持久目录> about:blank
#    （元宝管线早期走 agent-browser 页内 fetch，无需调试端口）

# 2) 跑对应采集器（小样本先行：先 list 阶段或首个会话）
#    元宝:  verify/yuanbao_receiver.py（本地接收器）+ verify/yuanbao_detail_harvest.py
#    千问:  verify/cdp_driver.py + verify/qianwen_detail_harvest.py
#    豆包:  verify/doubao_harvest.py

# 3) schema 指纹校验（PASS 才放行全量；DRIFT 停下 diff，退出码 1）
python verify/schema_canary.py <yuanbao|qianwen|doubao> <新鲜样本.json>

# 4) 全量采集完成后入库。注意：--all 不包含官方导出文件，两源并存必须显式带上
python -m harvester index --all \
    --deepseek-file "D:/Download/deepseek_data-2026-10-06.zip" --db harvester.db
```

### 三平台分述（端点与坑，均真机核验）

**腾讯元宝（v0.8，1223 会话）**：agent-browser 页内 fetch
`POST /api/user/agent/conversation/list|v1/detail`。分页参数在**顶层**
`{limit, offset}`——嵌套 `pagination:{offset}` 会被服务端静默忽略（恒回
第一页）。本地接收器按 `POST /list/<n>`、`/detail/<cid>` 落盘（no-cors
会丢弃自定义请求头，批次号必须走 URL 路径）。deepSearch 块的
`contents[].msg` 即思考过程 → `[think]` note。

**通义千问（v0.9，117 会话 / 469 轮）**：CDP 页内 fetch
`POST /api/v2/session/page/list`（body 顶层 `{next_token}` 游标）+
`GET /api/v1/session/msg/list`（`have_next_page` 时以 `pos=<末条 pos>`
续拉）。响应侧 `multi_load/iframe` 为正文，`plan_cot/post` 与
`bar/workflow` 的 `bar_thinking` 步骤为思考 → `[think]`；`signal/bar/
paa/survey` 4 类元数据跳过。`error_code` 成功值是 int 0（falsy）。

**豆包（v0.9，3 会话）**：请求经 **Web Worker** 发出且带 msToken/a_bogus
签名（重放报 712012002），页面级 fetch/XHR 钩子捕获为 0——唯一可行路径是
**app-driven capture**：让页面自己发请求，CDP Network 域截获响应体
（`verify/doubao_harvest.py`，单读线程设计）。端点：`/im/chain/recent_conv`
（会话列表）+ `/im/chain/single`（逐会话消息，滚动触发更早，按 message_id
去重）。`user_type` 1=用户 / 2=bot；content_block 映射：10000 正文、
10040 思考标题（`[think]`，**思考正文走流式通道不落盘**，adapter 仅记
标题）、10082 澄清 `[ask]`、10019 文件 `[file-op]`、10030 产物
`[artifact]`、10025 网搜 `[search]`、2074 生成图 `[image]`。

### 平台改版应对：采集器失效的真实边界与预案

- **存量不坏**：`corpus/*_raw/` 是采集时刻冻结的原始 JSON 快照，adapter
  解析本地归档、与服务端解耦——改版只影响"增量采集"，已入库数据永续可用。
- **前置探针**：见流程第 3 步 `verify/schema_canary.py`。指纹定义与
  adapter 消费的字段同步维护（adapter 新消费某字段时同步加进 REQUIRED）。
- **修复成本实证**：改版通常是端点参数/字段名小改（diff 修复），非重写——
  千问从零侦察到 469 轮全量入库 1 天、豆包含签名绕行半天。adapter 对未知
  块类型警告跳过，平台"新增"块大概率无感通过。
- **兜底通道**：豆包有账号级官方导出申请（约 14 天）、千问有官方数据管理
  导出、元宝有 toolkit/MHTML——自建管线挂掉时按
  `docs/RECON_CHAT_EXPORTS_v1.0.md` §2.1/§3.1/§4.1 切换。

### 兜底：weblogin 登录态三级流程（直采管线下游备用）

```bash
python -m harvester weblogin check         # 第1级：探测浏览器登录态（cookie 统计）
python -m harvester weblogin init-config   # 第2级：生成账号配置模板（明文仅存本机）
python -m harvester weblogin prepare deepseek-desktop   # 第3级：用户在可见窗口手动登录
```

安全约定：账号密码文件只存本机不上传；不做网络凭据传输；登录由用户本人
在可见窗口完成。工具不代填表单（各产品登录页结构无公开承诺，不硬猜）。
**有官方导出的（DeepSeek/ChatGPT/Claude）一律优先走导出文件通道。**

## 检索 · 索引 · MCP

```bash
# 索引四种来源（可组合）
python -m harvester index --all --db harvester.db          # 实时扫描全部数据源
python -m harvester index --from exports --db harvester.db # 从导出产物建索引
python -m harvester index --deepseek-file conversations.json --db harvester.db
python -m harvester index --export-file chatgpt-export=C:/x/conversations.json \
    --export-file claude-export=C:/y/conversations.json --db harvester.db  # 可多次

# 全文检索（多词 OR；命中带 <<>> 高亮摘要）
python -m harvester search "cookie 登录态" --db harvester.db

# 跨 agent 上下文交接包（token 预算内塞给另一个 agent 接续工作）
python -m harvester pack --select "3,5-9" --tokens 2000 \
    --question "基于以上讨论继续设计 X" --out context_pack.md

# MCP server（stdio，newline-delimited JSON-RPC，零依赖）
python -m harvester mcp-serve --sources sources.json --db harvester.db

# 只读 HTTP JSON API（v0.17）：schema 自检 fail loud + 内核级只读
# （mode=ro + authorizer 白名单）；默认 127.0.0.1，非回环 host 必须 --token
python -m harvester api-serve --db harvester.db [--port 8765] [--token <密钥>]
# 端点：/api/meta /api/facets /api/sessions /api/session/<sid> /api/session/<sid>/turn/<no>
#   v2（只增不改）：/api/triage /api/reports/{tools,errors,skills,agents} /api/cards
#   v0.19 additive：/api/meta 与全部 reports/triage/cards 端点带
#   db_fingerprint={sessions,steps,errors,db_mtime,generated_at}（产物判
#   陈旧用）；/api/reports/tools 工具行带 given_up/retried/raw_tools/
#   low_sample（?min_calls=）；/api/reports/agents 条目带 unresolved_count/
#   owner/status（--suggestions-meta <meta库> 启用 status 读取）
#   v0.20 additive：/api/sessions 支持 ?errors_only=1（只含错误步骤的会话，
#   total 同步过滤后数量）；/api/session/<sid> 带 error_steps 清单
#   （seq/ts/tool/error + errstats 归一 pattern/class，供前端批量导出按
#   「会话ID+异常类型」分类与跨会话模式去重）
python -m harvester api-serve --db harvester.db --suggestions-meta suggestions_meta.db
```

MCP 暴露 4 个工具：`list_sessions` / `search_history` / `read_session` /
`pack_context`。Claude Code 接入：`claude mcp add harvester -- python -m
harvester mcp-serve --db harvester.db`（工作目录需在套件根）；其他宿主把
command 指向 `python -m harvester mcp-serve` 即可。

**中文检索**：FTS5 unicode61 对 CJK 做 bigram 预改写（插入与查询两侧同步），
实测 2-5 字中文词 100% 命中；单 CJK 字前缀查询兜底。原文另存 raw 列，摘要
展示原文。索引体积约 1.9x（可接受）。⚠️ 验证检索必须走 `search()`/CLI 路径
（bigram 改写生效），手拼裸串直接 MATCH 不命中属预期。

## 蒸馏（聚合与知识库）

```bash
python -m harvester aggregate --all --out corpus.md           # 全部数据源实时聚合
python -m harvester aggregate --from exports --out corpus.md  # 从导出产物聚合
python -m harvester kb-init --root ~/.workbuddy/knowledge     # 知识库骨架（幂等）
python -m harvester kb-stats  --root ~/.workbuddy/knowledge   # 盘点

# 工具调用/失败率统计（服务「工具改进」与「Harness 踩坑」）
# 口径一（推荐）：先 index 再用 --db，读结构化 steps 表，含重试/放弃率
# 与按模型分布表（v0.15 起，sessions.model 列；仅 workbuddy-transcript 源有值）
# 错误明细为「根因聚合」形态（v0.20）：同构文本（路径/引号/数字差异）归并
# 出根因行 + 三分类标注；原文只留一条单行样例并截掉 old_string 输入回显，
# 杜绝用户文档片段（如「七步骨架」类）污染明细——完整原文按锚点回溯 steps 表
python -m harvester report-tools --db harvester.db --out tools_report.md
# 口径二：无索引时实时扫描（note 标记汇总，无重试/放弃统计）
python -m harvester report-tools --sources sources.json --out tools_report.md
# ⚠️ 两口径结论必须一致；若有出入，以口径一（--db，结构化 steps 表）为准，
#    口径二仅作无索引时的应急参考。
# 两口径均支持 --since 7（只看最近 N 天，按 steps.ts 近似截断）
#
# 统计口径（v0.19 固化，与 errstats.py 模块 docstring 一致）：
# - calls 只数 phase='call' 行；错误只出现在 phase='result' 行；
# - 工具名归一：同工具异写（小写+去下划线后同键）合并为 canonical，
#   如 edit/Edit、web_fetch/WebFetch、web_search/WebSearch；canonical 取
#   组内调用最多写法，raw 名在报告括号与 API raw_tools 字段可追溯；
# - 重试 = 错误后同会话同工具再次调用；放弃（未解决）= 无再次调用；
#   given_up = errors - retried；--since 截断处跨界的重试对会漏配对
#   （已知近似，跨期对比两侧用同一窗口）。

# 错误三分类（G2）：env（环境）/ tool_interface（用法）/ context（目标状态）
# + 开场/中途/收尾位置分桶 + 归一模式聚类（带锚点与原文；模式行附未解决数）
python -m harvester report-errors --db harvester.db --out errors_report.md

# AGENTS.md 条目建议（G2 闭环）：从错误模式产出候选条目（建议池，
# 每条附锚点+原文证据；**绝不直接改 AGENTS.md**，人工审阅后并入）
# v0.19：建议条目带 owner（harness/tool/workflow）与 unresolved_count，
# 按"未解决次数"降序——unresolved>=1 进"待修清单"，已自愈（=0）降级
# "观察区"；--meta 读建议状态 meta 库（status: pending/adopted/rejected）
python -m harvester suggest-agents --db harvester.db --min-count 3 \
    --out agents_suggestions.md --meta suggestions_meta.db

# 审阅结论落库（独立 meta 库 suggestion_status 表，不碰采集库；key=
# 建议池条目用 title，待人工归因模式用 pattern）
python -m harvester suggest-status --meta suggestions_meta.db \
    --key "Edit/Write 前必须先 Read 目标文件最新内容。" --status adopted

# 知识卡片（G3）：候选池 ↔ 主库归一，三条供卡通道详见 docs/CARD_WORKFLOW.md
# 从索引库会话一键起卡（锚点自动填，evidence 留白待补）
python -m harvester cards new --sid <search输出的sid> --turn N \
    --root ~/.workbuddy/knowledge/cards --type insight
# 校验：§8 frontmatter 规范 + 锚点查索引库 + 引文核对（v0.19：evidence
# 每行须能在锚点会话原文中逐字找到——空白归一后子串匹配，未命中出警告；
# 锚点 turn: null 出警告）；通过后人工并入 kb 主库
# 结论三分支（v0.16）：error=有问题不并入；warn=有警告先检查再定；
# 其余=可并入主库。PyYAML 可选——无它时降级解析器照常校验锚点
python -m harvester cards validate --root ~/.workbuddy/knowledge/cards \
    --db harvester.db

# Skill 行为画像（G4）：按 skill 聚合调用/触发任务/调用后行为链；
# --skill 深挖单技能 = 可喂给 Agent 蒸馏决策过程的会话清单；
# --min-calls 样本量阈值：calls<阈值的 skill 标 low-sample（仅供观察）
python -m harvester report-skill --db harvester.db
python -m harvester report-skill --db harvester.db --skill wechat-article-search \
    --min-calls 5

# OTel trace 统计（精确耗时 p50/p95、失败率、用户取消；与 steps 互相校验）
python -m harvester report-traces --out traces_report.md
```

**诊断→修改闭环（G2 全链路）**：`report-errors` 定位高频失败模式 →
`suggest-agents` 产出建议池 → 人工审阅并入 `~/.dsh/AGENTS.md`（或扩写为
§8 规范卡片 → `cards validate` 通过后入主库）。首份建议池见
`verify/agents_suggestions.md`（2026-10-06，9 条，第一条"Edit/Write 前先
Read"实测 102 次）；首张已验证卡片
`~/.workbuddy/knowledge/cards/kc-20261006-0001-edit-before-read.md`。

语料带 `<!-- SRC: 来源 | id | 标题 | 日期 -->` 溯源锚点；聚合之后的归类、
话题增补、反馈台账整理是语义工作，由 skill `session-knowledge-distill`
（v1.1+）按 `docs/DISTILL_PLAYBOOK.md` 执行。

## 源插件机制（ai-hist 式声明）

sources.json 的 `plugins` 节可声明"导出文件型"数据源（显式配置，不做隐式
网络访问）。当前注册表 **6 个**：`deepseek-export` / `chatgpt-export` /
`claude-export` / `yuanbao-raw` / `qianwen-raw` / `doubao-raw`。

```json
{"plugins": [{"id": "doubao-raw", "paths": ["C:/path/to/doubao_raw"]}]}
```

未知插件 id 显式报错并列出可选值，不静默忽略。

## 新增 Adapter 的规范

**正式契约见 `docs/ADAPTER_CONTRACT.md`**（DB schema / 适配器义务 /
收件箱协议 / 契约变更流程 / 上线前自检清单）。要点：

1. 继承 `BaseAdapter`，实现 `detect()` / `list_sessions()` / `load_session()`；
2. `detect()` 只做存在性检查，**绝不抛异常**，格式无法验证就返回 STUB/MISSING；
3. 声明 `claims_files`：单文件源（官方导出 zip/json）= True，可参与 sync
   收件箱竞标；目录型源（采集产物目录）= False，禁止认领单文件；
4. 输出统一收敛到 `SessionRecord` / `Message`（`models.py`）；
5. 文件读取走 `self._read_text()`（自带 CRLF→LF 归一化）；
6. 实装后在 `adapters/__init__.py` 把类从 `STUBS` 移入 `ACTIVE`，并同步
   `_PARAM_MAP` / `PLUGIN_IDS` 与 `verify/schema_canary.py` 指纹。

**铁律：实现不了就是实现不了，留接口、不臆测解析逻辑。**

网页端/客户端 Chat 的接入路径（按性价比排序）：① 官方数据导出（若提供）
→ ② 官方开放 API → ③ **登录态调官方 API / 截获官方响应**（三平台已实装）
→ ④ 浏览器自动化（兜底，脆弱）。基类见 `adapters/stubs.py` 的 `WebChatStub`。

## 环境注意事项

- **本机没有 `python` 命令时**：优先 `py -3`，或直接用已装解释器的绝对路径
  （套件本体零第三方依赖，任何官方 CPython 3.10+ 均可运行）。
- **verify/ 采集工具**：需 `requests` + `websocket-client`，用独立 venv；
  Windows 下 CDP 请求须显式 `proxies={"http": None}` 绕系统代理。
- **weblogin check 报 LOCKED**：Chromium 打开 Cookies 库时不授予共享权限
  （物理限制）。须完全退出所有浏览器进程后重试；仍失败即视为该库离线
  不可判定，勿反复重试。
- **AutoClaw 自带的 isolated Python 跑不了本套件**：safe_path 模式不认当前
  工作目录（普通 Python 正常），请用系统/独立解释器。
- 所有落盘文本统一 **UTF-8 + LF**（Windows 下 `write_text` 必须显式
  `newline="\n"`，CRLF 会破坏 frontmatter/行式解析）。
