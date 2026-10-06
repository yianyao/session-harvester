# 项目目录结构图（各文件及其作用）

> 更新于 2026-10-06（v0.9 收口后）。配套阅读：`README.md`（功能总览）、
> `docs/USER_MANUAL.md`（小白操作手册）。

```text
session-harvester/
│
├── README.md                     ← 项目总览：功能、快速开始、数据源状态、直采管线
├── .gitignore                    ← 排除敏感文件（weblogin_profile/、webchat.accounts.json）与运行产物
├── sources.json                  ← probe 命令的产物：本机数据源探测结果，scan/export/index 自动读取
├── harvester.db                  ← FTS5 全文索引库（sessions 含 model 列=v0.15；消息数随 sync 增长），search/read/MCP 的数据底座
├── inbox/                        ← 收件箱（v0.12）：人工导出包（zip/json）丢进来，sync 自动竞标认领
│   └── done/<adapter-id>/        ← 已导入归档 = 持久化源声明（删除目录即移除该源，直觉可逆）
├── exports/                      ← sync/export 的落盘产物（<大类>/<年-月>/*.md+json）
├── cards_pending/                ← T2 草稿卡（v0.14）：Agent 起草落这里，confidence 0.3；validate 通过后人工并入主库
├── docs/distill_prompts/         ← 蒸馏包产物（draft 命令输出，自包含可分发）
│
├── harvester/                    ══ 核心包（纯 Python 标准库，零第三方依赖）══
│   ├── __main__.py               ← python -m harvester 的入口
│   ├── cli.py                    ← 命令行分发：18 个子命令（probe/scan/export/sync/index/search/…）
│   ├── models.py                 ← 统一数据模型 SessionRecord / Message（所有 adapter 的输出契约）
│   │
│   ├── discovery.py              ← 【发现】扫描文件系统签名，找出本机 AI 会话数据源 → sources.json
│   ├── outline.py                ← 【纲要】scan：列出会话大纲（序号|来源|日期|标题|条数|概要）
│   ├── exporter.py               ← 【导出】export：按"来源大类/月份小类"落盘 md+json
│   ├── sync.py                   ← 【同步】sync：收件箱收割→全量导出→整库重建索引（幂等，v0.12）
│   ├── indexing.py               ← 【检索】index/search：FTS5 全文索引（CJK bigram 预改写）
│   ├── reader.py                 ← 【读取】read --turn：分层下钻读单个会话
│   ├── pack.py                   ← 【交接】pack：token 预算内的跨 agent 上下文交接包
│   ├── mcpserver.py              ← 【MCP】mcp-serve：stdio JSON-RPC server，暴露 4 个工具
│   ├── distill.py                ← 【蒸馏】aggregate / kb-init / kb-stats：语料聚合与知识库骨架
│   ├── toolstats.py              ← 【报告】report-tools：工具调用/失败率统计（steps 表口径）
│   ├── tracestats.py             ← 【报告】report-traces：OTel span 统计（耗时 p50/p95、取消率）
│   ├── triage.py                 ← 【队列】triage：蒸馏队列 T1——新错误pattern/旧坑重现/Skill行为链/高信号会话（v0.13）
│   ├── drafting.py               ← 【草稿】draft：T2 蒸馏包——会话原文+卡片规范+指令 → 自包含 md 喂 Agent（v0.14）
│   ├── weblogin.py               ← 【兜底】weblogin：登录态探测/配置模板/自动化浏览器三级流程
│   │
│   └── adapters/                 ← 数据源适配层（11 个实装 + 6 个桩位）
│       ├── __init__.py           ← 注册表：ACTIVE 实装列表 / STUBS 桩位 / _PARAM_MAP / PLUGIN_IDS
│       ├── base.py               ← BaseAdapter 契约（detect/list_sessions/load_session）+ DetectReport
│       ├── stubs.py              ← 6 个桩位（DeepSeek 桌面版/元宝/豆包/千问/Trae/AutoClaw 用户目录），
│       │                            detect 给出 MISSING/STUB + 获取指引，绝不臆测解析
│       ├── workbuddy.py          ← WorkBuddy 工作区每日日志（agent 自述总结，🔶 部分源）
│       ├── workbuddy_transcript.py ← WorkBuddy 真实逐回合轨迹（projects/*.jsonl，含子代理）
│       ├── vscode_copilot.py     ← VS Code Copilot Chat（session-store.db + chatSessions/*.jsonl）
│       ├── autoclaw.py           ← AutoClaw 桌面版（runtime.sqlite；带 schema 守卫，改版降级 STUB）
│       ├── dsh.py                ← DSH / DeepSeek Harness（session.v4.jsonl.zstd 多 frame 解压）
│       ├── deepseek_export.py    ← DeepSeek 官方导出（conversations.json；mapping/fragments DAG 主链）
│       ├── official_export.py    ← ChatGPT + Claude 官方导出（同源结构，2026-10 交叉核验）
│       ├── yuanbao_raw.py        ← 腾讯元宝直采数据（corpus/yuanbao_raw；deepSearch → [think]）
│       ├── qianwen_raw.py        ← 通义千问直采数据（corpus/qianwen_raw；469 轮 schema 核验）
│       └── doubao_raw.py         ← 豆包直采数据（corpus/doubao_raw；content_block 9 种映射）
│
├── verify/                       ══ 采集工具（需独立 venv：requests + websocket-client）══
│   ├── cdp_driver.py             ← 最小 CDP 驱动：Page.navigate / Runtime.evaluate（千问/豆包用）
│   ├── yuanbao_receiver.py       ← 元宝/千问通用本地接收器（v2.1，永久保留）：POST /list/<n>、
│   │                                /detail/<cid> → 落盘 JSON；python yuanbao_receiver.py [port] [outdir]
│   ├── yuanbao_detail_harvest.py ← 元宝采集器：agent-browser 页内 fetch 官方 API → POST 给接收器
│   ├── qianwen_detail_harvest.py ← 千问采集器：CDP 页内 fetch（next_token/pos 游标分页）→ 接收器
│   ├── doubao_capture.py         ← 豆包侦察期抓包器（Network 域截响应体 → doubao_capture.json）
│   ├── doubao_harvest.py         ← 豆包采集器（v2）：app-driven capture，单读线程设计，
│   │                                截 recent_conv + chain/single，滚动触发更早消息
│   └── schema_canary.py          ← schema 指纹探针：全量采集前校验新鲜样本（PASS/DRIFT，exit 1）
│
├── corpus/                       ══ 直采原始数据（采集时刻冻结的快照，只增不改）══
│   ├── yuanbao_raw/              ← 元宝：1223 detail + 25 list（52 MB）
│   ├── qianwen_raw/              ← 千问：117 detail + 13 list（16 MB）
│   └── doubao_raw/               ← 豆包：3 detail + 1 list（232 KB）
│       # 命名：detail_<会话id>.json = 会话全文；list_<n>.json = 会话列表页
│
├── tests/                        ══ 单元测试（unittest，零依赖）══
│   ├── test_pure.py              ← 早期核心测试（discovery/outline/exporter 基础行为）
│   ├── test_v03.py ~ test_v05.py ← v0.3 检索/读取/MCP、v0.4 导出与 AutoClaw、v0.5 FTS bigram
│   ├── test_v06.py               ← v0.6 transcript/DSH/steps/traces
│   ├── test_v07.py               ← v0.7 DeepSeek 官方导出（真实 ZIP 样本）
│   ├── test_v08.py               ← v0.8 yuanbao_raw adapter（12 用例）
│   └── test_v09.py               ← v0.9 qianwen_raw + doubao_raw adapter（17 用例）
│       # 全套：python -m unittest discover tests（当前 132 个，全绿）
│
├── docs/                         ══ 文档 ══
│   ├── USER_MANUAL.md            ← 小白操作手册（本套件怎么用，一步步）
│   ├── PROJECT_STRUCTURE.md      ← 本文件
│   ├── ADAPTER_CONTRACT.md       ← 采集/分析两层正式接口契约（DB schema/适配器义务/收件箱协议，v0.12）
│   ├── CARD_WORKFLOW.md          ← 知识卡片工作流（供卡通道 0/0.5/1/2/3 + 喂 Agent 指令模板）
│   ├── DESIGN_VS_ACTUAL.md       ← 设计方案（轨迹分析工具设计方案）逐节比对台账
│   ├── RECON_CHAT_EXPORTS_v1.0.md ← 四平台导出通道侦察报告（端点/schema/坑，含备用通道）
│   ├── DISTILL_PLAYBOOK.md       ← 蒸馏语义流程权威定义（宿主无关，agent 照做）
│   ├── COMPARISON.md             ← 与同类工具（ai-hist 等）的对比
│   ├── PLAN_DIFF_v1.0.md         ← 计划差异记录
│   ├── AUDIT-2026-10-05.md       ← 早期审计报告
│   └── reports/                  ← 可复跑报告产物（G1/G2/G3 证据、cards 校验、蒸馏队列等）
│
├── demo/                         ← 演示样例
└── .zwork/                       ← 工作区附件缓存（非套件组成部分）
```

> 注：`exports/`（导出产物）与 `outline/`（纲要产物）目前位于 `verify/`
> 下（历史原因：曾在该目录执行命令）。它们是**运行产物**，可随时删除重建，
> 位置由命令执行时的 cwd 决定，不必拘泥。

## 数据流一图

```text
[本机数据源]                      [网页 Chat 三平台]
 VS Code/AutoClaw/DSH/…            元宝/千问/豆包（服务端，无官方导出）
      │                                   │
      │ discovery.py 扫描                 │ verify/ 采集器（登录态）
      ▼                                   ▼
 sources.json                     corpus/*_raw/（原始 JSON 快照）
      │                                   │
      └────────────► adapters/ ◄──────────┘
                     │ 统一收敛为 SessionRecord/Message
                     ▼
              harvester.db（FTS5 索引）
                     │
      ┌──────────────┼──────────────┐
      ▼              ▼              ▼
   search/read    pack/MCP      aggregate/kb-*
   （人查/agent 查）（跨 agent 交接）（蒸馏语料与知识库）
```

## 关键约定

- **adapter 与归档解耦**：`corpus/*_raw/` 是采集时刻的冻结快照，平台改版
  只影响"下次采集"，存量数据与 adapter 永续可用。
- **adapter 消费什么，canary 就校验什么**：adapter 新增字段消费时，同步
  更新 `verify/schema_canary.py` 的指纹定义。
- **落盘文本一律 UTF-8 + LF**：Windows 下写文件必须显式 `newline="\n"`。
- **包本体零依赖，verify/ 不零依赖**：采集工具用独立 venv，不污染套件。
