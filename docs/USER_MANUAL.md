# session-harvester 操作手册（小白版）

> 目标：不懂代码细节也能跟着做。每一步都写清楚【做什么】【怎么做】【看到
> 什么算成功】。原理性问题看 `README.md`，文件作用看
> `docs/PROJECT_STRUCTURE.md`，采集器/数据接口的正式规范看
> `docs/ADAPTER_CONTRACT.md`（想自己加数据源或改采集行为时才需要）。

---

## 0. 这是什么？

一个放在本地的"AI 对话历史仓库"。它把你电脑上各种 AI 工具的历史会话
（VS Code Copilot、AutoClaw、DeepSeek、腾讯元宝、通义千问、豆包……）
**收集起来、建好索引**，之后你可以：

- 🔍 **全文搜索**：比如"上次谁跟我聊过双人间和双床房的区别？"
- 📖 **逐回合翻阅**：像翻聊天记录一样下钻某个会话
- 🤝 **喂给别的 AI**：把相关历史打包（或通过 MCP）交给 Claude 等接续工作
- 📤 **导出成文件**：按来源/月份整理成 markdown + JSON
- 🧠 **蒸馏知识库**：提炼出思路过程、踩坑记录、用户画像

**它不会**：上传任何数据到网上、动你的账号密码、修改任何原始数据。

**两个关键概念**（后面会反复出现）：

| 名词 | 意思 |
|---|---|
| **adapter（适配器）** | "翻译官"。每种 AI 工具的数据格式不同，adapter 负责把它翻译成统一格式 |
| **索引库（harvester.db）** | 本地数据库文件。搜索/读取/MCP 都查它，建好后查得飞快 |

---

## 1. 开始前：确认两件事

### 1.1 确认有 Python（≥3.10）

按 `Win+R`，输入 `cmd` 回车，在黑窗口里输入：

```bat
python --version
```

- 显示 `Python 3.10` 以上 → ✅ 继续
- 显示"不是内部或外部命令"→ 试试 `py -3 --version`；还不行就装一个
  Python，或直接用本机已有的完整路径（本书以
  `C:\Users\yianyao\.workbuddy\binaries\python\envs\default\Scripts\python.exe`
  为例，下文用 `%PY%` 代称）：

```bat
set "PY=C:\Users\yianyao\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
%PY% --version
```

### 1.2 进入项目目录

```bat
cd /d C:\Users\yianyao\WorkBuddy\2026-10-05-08-38-51\session-harvester
```

> ⚠️ 之后所有命令都要在这个目录里执行（因为索引库、数据都在这）。

**验证一切正常**：跑一下测试（可选，约 1 分钟，应显示 `OK`）：

```bat
%PY% -m unittest discover tests
```

---

## 2. 日常使用：查历史会话（最常用，30 秒上手）

前提：索引库 `harvester.db` 已存在（本项目里已经有了）。

### 2.1 搜索

```bat
%PY% -m harvester search "双人间" --db harvester.db
```

**看到什么算成功**：列出命中条目，格式是
`[来源:会话id] 标题` + 带高亮的正文片段，第一行还会写"共 N 条命中"。

技巧：
- 多个词是"或"的关系：`search "cookie 登录态"` = 含任一词都命中
- 中文随便搜（2-5 个字的词 100% 命中，程序自动做了中文分词处理）

### 2.2 翻某个会话

先看目录（有哪些会话、各自编号）：

```bat
%PY% -m harvester scan
```

打开生成的 `outline/outline.md`，找到想要的会话前面的**序号**（比如 42），
然后：

```bat
%PY% -m harvester read 42 --turn 1      :: 只看第 1 回合
%PY% -m harvester read 42 --turn all    :: 看全部回合
%PY% -m harvester read 42               :: 不带 --turn 看整体概要
```

### 2.3 把历史喂给其他 AI（两种方式）

**方式一：交接包**（一次性，把相关历史存成文件发给别人/AI）：

```bat
%PY% -m harvester pack --select "3,5-9" --tokens 2000 --question "继续设计 X" --out context_pack.md
```

**方式二：MCP 接入**（让 Claude Code 等工具随时能查你的历史）：

```bat
%PY% -m harvester mcp-serve --db harvester.db
```

接好之后，AI 那边就多了 4 个能力：列会话 / 搜历史 / 读会话 / 打包上下文。

---

## 3. 从零全流程（换新电脑 / 重建索引时用）

四步，顺序执行：

```bat
:: 第 1 步：探测本机有哪些 AI 数据源（产出 sources.json）
%PY% -m harvester probe

:: 第 2 步：看看每个源的状态（✅ OK 可用 / 🔶 部分 / STUB 桩位留接口）
%PY% -m harvester adapters

:: 第 3 步：生成会话目录
%PY% -m harvester scan

:: 第 4 步：按序号导出（比如第 3、5-9 个会话）或全部导出
%PY% -m harvester export --select "3,5-9" --out exports
%PY% -m harvester export --all --out exports

:: 第 5 步：建索引（之后就能用第 2 节的搜索了）
%PY% -m harvester index --all --db harvester.db
```

导出的文件长这样：`exports/Agent/2026-09/时间戳__来源__标题.md`（同目录
还有同名 `.json` 结构化原文）。

> 💡 思考过程、工具调用记录**默认包含**（标注为 note），这对后续蒸馏很
> 重要，别用 `--no-with-notes` 关掉。

---

## 3.5 日常增量：一条命令 + 丢文件（推荐）

第 3 节是"从零重建"才用的。平时想让库保持最新，只需要：

```bat
%PY% -m harvester sync
```

它自动做三件事：把本机各 AI 工具的新会话导出 → 重建索引 → 告诉你
**"本次新增 N 个会话"**。本地工具（WorkBuddy / DSH / AutoClaw / VS Code）
**零人工**，挂个系统定时任务每天跑一次都行。

**唯一需要动手的场景**：DeepSeek / ChatGPT / Claude 这类只有"官方导出
包"的平台。做法就一步——

1. 平台里申请导出数据，收到 zip 后**丢进项目的 `inbox/` 文件夹**；
2. 跑一次 `sync`。它会自动认领（识别是哪家的包、校验格式）、归档到
   `inbox/done/`、导入索引库，下次 sync 不会再问你。

认领不了的文件会**留在 inbox/ 原地并报告原因**（绝不会悄悄弄丢），
人工看一眼删掉或换格式即可。想移除某个导出源，删掉
`inbox/done/<对应平台>/` 文件夹再 sync 即可。

---

## 4. 重新采集三平台最新会话（元宝/千问/豆包）

这三家没有官方导出功能，本项目用"登录态直采"：**你在浏览器里登录，程序
让页面自己调官方接口，把返回的原始数据存下来**。不碰密码、不对抗风控。

### 4.1 准备采集环境（本机已就绪，换新电脑才需要）

采集工具需要两个额外依赖（`requests`、`websocket-client`），它们只装在
独立 venv 里、不污染套件本体：

```bat
:: 在项目目录下创建虚拟环境并安装依赖（仅第一次需要）
python -m venv venv
venv\Scripts\pip install requests websocket-client

:: 之后采集命令都用 venv 的解释器跑：
venv\Scripts\python.exe verify\doubao_harvest.py
```

### 4.2 采集（以豆包为例，千问/元宝同理）

```bat
:: 第 1 步：启动一个带调试端口的 Chrome（保持登录状态）
::         第一次会打开浏览器，你手动登录豆包；之后 profile 记住登录态
"C:\...\chrome.exe" --remote-debugging-port=9334 --remote-allow-origins=* --user-data-dir=C:\path\doubao_profile about:blank

:: 第 2 步：跑采集器（页面会自动跳转、滚动，别关浏览器窗口）
%PY% verify\doubao_harvest.py

:: 第 3 步：校验数据格式（看到 PASS 才继续）
%PY% verify\schema_canary.py doubao corpus\doubao_raw\detail_xxx.json

:: 第 4 步：入库（--all 不含 DeepSeek 导出文件，两个都要带）
%PY% -m harvester index --all --deepseek-file "D:\Download\deepseek_data-2026-10-06.zip" --db harvester.db
```

三平台对应关系：

| 平台 | 采集器 | 产物目录 |
|---|---|---|
| 腾讯元宝 | `verify\yuanbao_receiver.py` + `yuanbao_detail_harvest.py` | `corpus\yuanbao_raw\` |
| 通义千问 | `verify\cdp_driver.py` + `qianwen_detail_harvest.py` | `corpus\qianwen_raw\` |
| 豆包 | `verify\doubao_harvest.py` | `corpus\doubao_raw\` |

> 🛡️ **平台改版了怎么办？** 老数据不会坏（已存好的快照永久有效），只是
> "下次采集"可能失败。先跑第 3 步的 canary 校验：DRIFT 就停下来对比
> 差异，通常小修即可；真修不动还有兜底（豆包可申请官方导出、千问有官方
> 数据管理导出），详见 RECON 报告。

---

## 5. 蒸馏：把对话变成知识库

```bat
:: 先跑蒸馏队列：机器自动排出"最近哪些坑/哪些 skill 值得做卡"（每条附一键起卡命令）
%PY% -m harvester triage --since 7 --cards-root C:\Users\yianyao\.workbuddy\knowledge --out distill_queue.md

:: 挑中某条候选后，生成"蒸馏包"（会话原文+卡片规范+指令打包）丢给任意 AI 起草：
%PY% -m harvester draft --sid <队列锚点里的sid> --type pitfall --out docs\distill_prompts\某坑.md
:: AI 产出的草稿卡在 cards_pending\ 里，校验通过后你人工拍板才并入主库
%PY% -m harvester cards validate --root cards_pending --db harvester.db

:: 聚合所有会话成一份大语料（带来源锚点，方便溯源）
%PY% -m harvester aggregate --all --out corpus.md

:: 生成知识库骨架（不会覆盖已有内容）
%PY% -m harvester kb-init --root C:\Users\yianyao\.workbuddy\knowledge

:: 看知识库现状
%PY% -m harvester kb-stats --root C:\Users\yianyao\.workbuddy\knowledge

:: 统计工具调用成功率（哪些工具常失败 → 改进依据）
:: 报告末尾自带两张分组表：按 Agent（数据源）分布、按模型分布（v0.15 起，
:: 会话主模型取自行级 providerData 众数，DeepSeek 官方导出与 WorkBuddy 轨迹都有）
%PY% -m harvester report-tools --db harvester.db --out tools_report.md

:: 统计真实执行耗时（p50/p95、取消率，来自运行时遥测）
%PY% -m harvester report-traces --out traces_report.md
```

### 5.1 看看自己（和 AI）哪里老踩坑：诊断三步

这是本套件最有意思的用法——从历史轨迹里找出"反复出现的坑"，
自动写成给 AI 的备忘条目：

```bat
:: 第 1 步：错误体检。把所有失败分成三类：
::   env            = 环境问题（权限被撤、沙箱拦截、网络）——AI 没法自己解决
::   tool_interface = AI 用错了工具（先改后读、盲猜行号）——改用法就能消
::   context        = 目标变了（文件被删、路径失效）——需要重新侦察
:: 还会按"开场/中途/收尾"统计错误出现在会话的哪个阶段
%PY% -m harvester report-errors --db harvester.db

:: 第 2 步：生成建议池。AI 会把高频踩坑模式写成"备忘条目草稿"，
::   每条都附上是在哪个会话第几步踩的坑 + 报错原文
%PY% -m harvester suggest-agents --db harvester.db --out agents_suggestions.md

:: 第 3 步：人工审阅。打开 agents_suggestions.md，挑出真实有用的条目，
::   自己复制进 AGENTS.md（工具【永远不会】自动改你的 AGENTS.md）

:: Skill 行为画像：看每个 skill 被用了几次、干什么用、用完之后 AI 接着做了什么
%PY% -m harvester report-skill --db harvester.db
:: 深挖某一个 skill（输出会话清单，可交给 AI 蒸馏"决策过程"）
%PY% -m harvester report-skill --db harvester.db --skill wechat-article-search
```

顺手可以生成"知识卡片"（带出处、可校验的小知识文件）。**DeepSeek /
元宝 / 千问 / 豆包的聊天记录也能做卡**——先 `search` 找到会话拿 [sid]，
再一键起卡（锚点自动填好），补上证据原文即可：

```bat
:: 从某个会话生成卡片草稿（--type 可选 pitfall / workflow / insight）
%PY% -m harvester cards new --sid <search输出的sid> --turn 1 ^
        --root C:\Users\yianyao\.workbuddy\knowledge\cards --type insight

:: 校验卡片格式是否合格（锚点是否真能在历史库里查到）
%PY% -m harvester cards validate --root C:\Users\yianyao\.workbuddy\knowledge\cards --db harvester.db
```

> 报告里"通过"的卡片可以放心并入知识库主库；有"错误"的按提示补字段。
> 三条供卡通道（踩坑建议池 / skill 行为深挖 / 聊天记录摘录）的完整说明见
> `docs/CARD_WORKFLOW.md`——也可以把报告文件直接喂给 AI 帮你批量起草。

聚合语料之后的**归类、提炼**是语义工作，交给 AI 照着
`docs/DISTILL_PLAYBOOK.md` 执行（WorkBuddy 里用 skill
`session-knowledge-distill`）。

---

## 6. 常见问题排查

| 现象 | 原因 | 解法 |
|---|---|---|
| `'python' 不是内部或外部命令` | Windows 没配 PATH | 用 `py -3`，或解释器完整路径（见 §1.1） |
| search 搜中文 0 命中 | 手拼 SQL 直接查了 FTS 表（没走分词改写） | 一律走 `search` 命令 / MCP 的 `search_history`，别手拼 SQL |
| search 结果搜出来是怪词 | 展示层 bug（正常应显示原文） | 升级套件；raw 列里存有原文 |
| `index --all` 后少了 DeepSeek 数据 | `--all` 不含官方导出文件 | 加 `--deepseek-file "路径.zip"`（本项目实测踩过） |
| weblogin check 报 LOCKED | 浏览器开着，Cookies 库被锁 | 完全退出所有浏览器（含后台）再试；不行就是该库判定不了，别反复试 |
| AutoClaw 里跑不了本套件 | 它自带的 Python 开了 safe_path | 用系统 Python 或独立解释器跑 |
| 采集器超时 / CDP 连不上 | chrome 调试实例没起 / 被杀 | 重新启动带 `--remote-debugging-port` 的 chrome，并保证它**常驻**（别放在会退出的脚本里启动） |
| CDP 报 403 Forbidden | chrome 没加 `--remote-allow-origins=*` | 启动参数加上 |
| 采集到一半 schema 报错 | 平台改版了 | 跑 `schema_canary.py` 看差异清单，按 §4.2 处理 |

---

## 7. 30 秒备忘卡

```bat
cd /d <项目目录>
%PY% -m harvester sync                                  :: 一键更新库（新会话自动入库）
%PY% -m harvester search "关键词" --db harvester.db     :: 搜
%PY% -m harvester scan                                  :: 列目录
%PY% -m harvester read 42 --turn 1                      :: 看第 42 个会话第 1 回合
%PY% -m harvester index --all --db harvester.db         :: 重建索引
%PY% -m harvester mcp-serve --db harvester.db           :: 给 AI 接上
```

记住两句话：**平时更新就跑 `sync`，官方导出包丢进 `inbox/` 就行；**
**数据都在 `harvester.db`，一切查询围绕它；原始快照在 `corpus/`，
只增不改，是永远的后路。**
