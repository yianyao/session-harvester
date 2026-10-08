# HANDOFF — v0.22 方案执行交接文件

> 配套三件套：`PLAN-v0.22-unified.md`（唯一有效方案）→ **本文件**（事实/环境/红线台账）
> → `SOP-v0.22-execution.md`（逐步操作程序）。
> 新会话开工前必读本文件全部 + SOP-0。本文件中每条事实都带行号/命令级证据（H 编号），
> SOP 直接引用，**不要重新验证、不要重新理脉络**。

---

## 1. 项目地图

| 项 | 路径 / 值 |
|---|---|
| 后端仓库 | `C:\Users\yianyao\WorkBuddy\2026-10-05-08-38-51\session-harvester`，head=`1a58990`（v0.20） |
| 前端仓库 | `C:\Users\yianyao\WorkBuddy\2026-10-05-08-38-51\harvester-view`，head=`a845b17`（v2.3.1） |
| 真实索引库 | `session-harvester\harvester.db`（**只读纪律**：代码里全部 mode=ro + authorizer） |
| 卡片主库 | `%USERPROFILE%\.workbuddy\knowledge\cards\`（4 张：kc-20261006-0001~0004） |
| 候选池（空柜） | `session-harvester\cards_pending\`（0 文件，P0-4 待对齐） |
| 主题种子 | `%USERPROFILE%\.workbuddy\knowledge\topics\`（9 个 md，H17） |
| 建议状态库 | `session-harvester\suggestions_meta.db`（独立 meta 库范式，T 轨沿用） |
| 报告产物 | `session-harvester\docs\reports\G1_tools_report.md` 等 7 份 |
| 关键文档 | `docs\AGENTS.md`（项目纪律）、`docs\DESIGN_VS_ACTUAL.md`、`docs\ADAPTER_CONTRACT.md`、`docs\CARD_WORKFLOW.md` |

## 2. 运行环境（硬约束）

| 项 | 值 | 备注 |
|---|---|---|
| venv Python（**一切测试/CLI 用它**） | `C:\Users\yianyao\.workbuddy\binaries\python\envs\default\Scripts\python.exe` | 预装 pyyaml 6.0.3 / openpyxl / Pillow / numpy |
| 系统 Python | 存在但**无 pyyaml** | cards CLI 若用它 → 降级解析器分支生效（H9） |
| Node（JS 校验） | `C:\Users\yianyao\.workbuddy\binaries\node\versions\22.22.2-6\node.exe` | `new vm.Script(script)` 校验 index.html 内联 JS |
| git push | `git -c http.proxy=http://172.16.20.27:12603 push` | 直连/会话代理均不通 github.com |
| 本机 HTTP 冒烟 | `urllib.request.build_opener(urllib.request.ProxyHandler({}))` | 全局代理 127.0.0.1:33824 会劫持 localhost；起服务前 `netstat -ano | grep <port>` 确认干净 |
| 后端测试 | `python -m unittest discover -s tests`（在 session-harvester 下） | 基线 **246 例全绿** |
| view 测试 | 同上（在 harvester-view 下） | 基线 **23 例全绿** |
| 行尾纪律 | 所有 .py/.md/.html 保持 **LF**；`write_text(..., newline="\n")` | 提交前 `b'\r\n' in bytes` 扫一遍 |

## 3. 当前状态（截至 2026-10-08）

- 已完成：v0.19（归一/unresolved/指纹/owner-status/引文核对）、v0.20（G1 根因聚合 CLI 侧 +
  errors_only/error_steps 批量导出通路）、v2.2–v2.3.1（view G1-G5 渲染 + 过滤 + 批量导出
  md/JSON + G4 过滤 bug 修复）。
- 真实库基线（H20）：**1946 会话 / 30956 步 / 261 错误**；edit 归并 2601/103=4.0%、
  WebFetch 157/27=17.2%；errors_only=1 → 47 个异常会话；31 skill / 94 调用。
- 未开始：PLAN-v0.22 的 P0-1～P0-5、P1-2～P1-4、T0～T5 全部。
- P1-1（采纳闭环）与 T1 主题选定**需要用户参与**（见 §7）。

## 4. 已核实事实台账（SOP 引用源，勿重复验证）

| # | 事实 | 证据 |
|---|---|---|
| H1 | **steps 表没有 args 列**（cols=sid,seq,ts,tool,phase,status,error,detail）；工具入参仅以 400 字 preview 形态存在于 detail | `PRAGMA table_info(steps)`；`adapters/dsh.py:360` `"detail": str(args)[:400]` |
| H2 | reasoning 已入库：note 角色 | `dsh.py:342-344`；`workbuddy_transcript.py:232` |
| H3 | `messages.text` 是 bigram 索引改写文本（如"又来 来了 ！"），原文在 `raw`；API 已 raw 优先 | 评审实测 6079 条 user 消息为 bigram 形态；`apiserve.py:297` `r["raw"] if r["raw"] else r["text"]`。**一切统计/提取禁用 text 列** |
| H4 | 全库无跨会话时间序实现（`ORDER BY messages.ts` 零命中）；read/draft 均单会话 | 全库 grep |
| H5 | draft 单会话预算 24K 字符，跨主题必爆 | `drafting.py:24` `_MAX_CHARS = 24000` |
| H6 | 卡片 type 枚举 `{insight, pitfall, workflow}` | `cards.py:29` |
| H7 | 建议池证据截断 100 字符 | `agent_suggest.py:161/175` `raw[:100]` |
| H8 | 降级 frontmatter 解析器只认标量+流式序列：块标量 `evidence: \|` 读成字面 `'\|'`（长度 1 非空→不报错）；块序列 anchors 读成空串 | `cards.py:115-128` |
| H9 | **pyyaml 环境差异 → 静默降级**：venv 有 pyyaml 6.0.3（块标量正常解析），系统 python 无（降级分支）。单测全跑在 venv → 降级分支从未被测试覆盖（恒真测试实证）。评审 C 用系统解释器实测 evidence_checked=0 | 实测两解释器 import yaml 结果 |
| H10 | 引文核对：`len(ln)>=6` 过滤（`:244`）；turn:null 警告（`:240`）在提前 return（`:233`）之后**永不可达** | `cards.py:233-264` |
| H11 | `cards new` 产物自带占位符（`evidence: \|` + `<粘贴原会话证据原文>` + `「<待补：…>」`），validate 对占位符零检查 → 空壳卡结论"可并入主库" | `cards.py:405-425` + 评审 C 实跑 |
| H12 | 卡片池目录错位：cards_pending 0 文件；真实 4 卡在 knowledge\cards；`start.cmd:46` `--cards-root` 指空目录 | 实测 ls |
| H13 | model 覆盖 474/1946（24%）；dsh/autoclaw/vscode_copilot **均无 model 抽取**（grep 无 providerData），仅 workbuddy_transcript 抽了；261 错误中 160 归"(空)" | grep + DB 实测 |
| H14 | 高步操作/循环/连击检测 0%（全库无相关实现） | 全库 grep |
| H15 | 跨建议/跨卡无去重：同一"Edit 前置"根因 = suggest-agents 2 条 + triage 2 行 + kc-0001/kc-0004 两卡 | 评审实测 |
| H16 | 根因聚合只接了 CLI：`toolstats.py:395 aggregate_error_roots` 仅被 `:452`（render_report）调用；`apiserve.py:399 _tool_rows` 在 `:432` 直吐 `st.errors` → **view 里 G1 仍是未聚合明细（Edit 约 79 行）** | grep |
| H17 | topics 9 个 md 文件在 knowledge\topics，无主题→会话成员表、无程序化关联 | 实测 ls |
| H18 | 「叙事节奏」簇：41 会话 / 550 消息 / 18 不同标题，标题时间序即演进链（节奏→氛围→克制→人物→设定→逻辑→意象）；月度分布 2025-06:5 / 2025-07:35 / 2026-07:1 | 评审 SQL 实测 |
| H19 | file-history-snapshot 实测基本为空（trackedFileBackups:{}）——稿件演化从 tool args + 会话文本挖，不走文件快照 | 评审实测 |
| H20 | 真实库基线数字（见 §3） | DB 实测 |
| H21 | 两仓库 head 与测试基线（见 §1/§2） | git log + unittest |
| H22 | 历史教训：G4 过滤器曾因 `hitQ(s.skill, s.q)` 引用错变量而永不生效（v2.3.1 已修）——**过滤器接线必须配"过滤真的能滤"的断言** | view git log |
| H23 | 评审一（早期）"A1–A3 待做"已过时——v0.19 已完成，勿重复 | git log |

## 5. 红线（违反 = 返工）

1. **采集库 schema 冻结**：harvester.db 有 EXPECTED_SCHEMA 指纹守卫，**禁止加列/改表**；
   新状态类数据一律写独立 meta 库（suggestions_meta.db 范式）。
2. **API additive only**：api_version=1 不动；新增字段只加不删不改语义；view 需兼容旧上游
   （字段缺失时降级）。
3. **view 三不**：不执行命令（触发=复制命令）、不做分析逻辑（去重/聚类/蒸馏/归因）、
   仅转发 GET。
4. **messages.text 禁用于统计**（H3）；一律 raw 优先、text 兜底。
5. **P0-1 未完成前不得动 T3（链产物校验）**——校验器假报成功会污染一切下游（v0.22 §3）。
6. 测试防"恒真"：**每个新断言必须先演示会红**（先写失败测试再修码）；过滤器类改动必须
   断言"过滤后数量 < 过滤前"（H22 教训）。
7. 同一文件多处修改**逐条顺序 Edit**（并行 Edit 互相覆盖）；跨文件可并行。
8. 提交信息格式沿用：`feat v0.21: <标题>` + 正文分条；两仓库分别提交推送；
   G1/G2 报告在 .gitignore 内不随提交（本地重生成）。

## 6. 关键代码位置速查（P0 主战场）

| 位置 | 现状 | P0 动作 |
|---|---|---|
| `cards.py:103-128` _parse_frontmatter | 降级分支只认标量+流式序列 | 补块标量 `\|`/`\|-`/`>` 与块序列 |
| `cards.py:142-163` validate_card | 无占位符检查 | 占位符→错误级（H11 模式清单见 SOP-P0-1） |
| `cards.py:233/240` _evidence_warnings | turn:null 不可达 | 挪到提前 return 之前 |
| `cards.py:244` | `len>=6` 过滤 + checked=0 时结论仍"通过" | checked==0 显式"未核对"，不算通过 |
| `apiserve.py:399-433` _tool_rows / api_reports_tools | 直吐 st.errors | 调 aggregate_error_roots，additive 加 `roots` |
| `toolstats.py:395` aggregate_error_roots | 已实现（CLI 用） | 复用，不改语义 |
| `harvester-view\static\index.html` | renderTools:755 / renderSkills:881 / renderCards:970 / rerenderReport:1263-1272 / repFilter:750 | G1 优先渲染 roots；空态带过滤前后计数 |
| `harvester-view\start.cmd:46` | --cards-root 指 cards_pending | 对齐真实池 |
| `adapters/dsh.py` `autoclaw.py` `vscode_copilot.py` | 无 model 抽取 | 照 workbuddy_transcript.py 取 providerData.model |
| `agent_suggest.py:161/175` | raw[:100] 截断 | 放开/折行（P1-2 一并） |

## 7. 用户交互协议（这些事必须问，不许自作主张）

1. **P1-1 采纳闭环**：建议池逐条 adopted/rejected 的最终裁量权在用户；执行方只准备
   `suggest-status` 命令与对比报告。
2. **T1 主题选定**：真实小说主题 + 首批 3–5 个成员会话由用户指定；「叙事节奏」41 会话
   是否全量导入、按作品还是按技法聚（H18 聚类粒度），由用户裁决后才能进 T2。
3. **方案冲突**：回到 PLAN-v0.22 §0 裁决与 §5 红线；仍悬而未决才问用户，**不要重新
   展开架构讨论**（三轮评审已收敛，重开=烧 token）。
4. 常规实现选择（函数命名、内部结构、测试组织）不必问，按 AGENTS.md 纪律自行决定。

## 8. 新会话开工流程

1. 读 `PLAN-v0.22-unified.md`（方案+裁决）→ 本文件（事实+红线）→ `SOP-v0.22-execution.md`（程序）。
2. 跑 SOP-0 启动检查单（版本对账 + 两套测试基线 + 环境自检）。
3. 按 SOP 顺序执行：P0-1 → P0-2∥P0-3 → P0-4 → P0-5 → P1-1（用户）→ T0∥P1-2/4 →
   T1 → P1-3 → T2 → T3 → P2-1 → T4 → P2-2 → T5。
4. 每完成一个任务：按 SOP-1 节律收尾（回归+推送+日志），再开下一个。
