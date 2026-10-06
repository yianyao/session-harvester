# Chat 导出数据侦察报告（DeepSeek / 元宝 / 豆包 / 千问）

版本: v1.0 (2026-10-06)
目的: 为 session-harvester 新增 4 个网页 Chat 平台 adapter 做 schema 侦察与工作量评估。
方法: 侦察基于各导出工具的 GitHub 源码（源码级核验），标注置信度；凡未拿到真实导出文件者一律按项目铁律"留接口不臆测"处理。

---

## 0. 结论速览

| 平台 | 推荐导出通道 | 产物 | schema 掌握度 | adapter 方案 | 预估工作量 |
|---|---|---|---|---|---|
| DeepSeek | 官方数据导出（已实装 v0.7）+ Exporter 扩展 | conversations.json / API 原始 JSON | **官方导出=真机核验已实装**；扩展=需真机样例 | mapping 形态已并入 deepseek_export | 已完成（扩展形态 0.5 天待样例） |
| 腾讯元宝 | **登录态直采（已实装 v0.8）**；toolkit/MHTML 备用 | API 原始 JSON（corpus/yuanbao_raw） | 真机核验已实装 | yuanbao_raw.py | 已完成 |
| 豆包 | **app-driven capture（已实装 v0.9）**；MemorySeek/官方导出备用 | IM 响应原始 JSON（corpus/doubao_raw） | 真机核验已实装 | doubao_raw.py | 已完成 |
| 通义千问 | **CDP 登录态直采（已实装 v0.9）**；官方导出 ZIP 备用 | API 原始 JSON（corpus/qianwen_raw） | 真机核验已实装（469 轮全量） | qianwen_raw.py | 已完成 |

共性判断：四家导出产物都**只有对话正文 + 思考过程，无任何工具 I/O**——
覆盖蒸馏目标 2.1（思路过程）/ 2.4（用户画像），完全不覆盖 2.2 / 2.3。

---

## 1. DeepSeek

### 1.1 官方数据导出（✅ v0.7 真机核验后实装 mapping 形态）
**真机样例**（2026-10-06，`deepseek_data-2026-10-06.zip`：user.json + conversations.json 36MB）：
- 顶层数组 316 会话，键：`id / title / inserted_at / updated_at(ISO+08:00) / mapping`
- mapping 节点：`{id, parent, children[], message}`，root 的 message=null；
  **无 messages 数组、无 current_node**——与社区描述的形态 A 完全不同
- message：`{model, inserted_at, fragments[]}`，role 由 fragment type 推断：
  REQUEST(1916)/RESPONSE(2126)/THINK(1863)/FILE(144)/SEARCH(50)/TOOL_SEARCH(66)/TOOL_OPEN(324)
- 分支：63/316 会话含分支（重新生成/改写重问），最大深度 150；
  主链策略=每分支点取子树最新消息时间的 child，分支点计数入 `extra["branch_points"]`
- 实装结果：全量 316 会话 0 空会话 0 lossy，主链 3253 条 user+assistant 消息入库，
  中文 bigram 检索命中 THINK note（`harvester.db` 604 会话/43100 消息）
- 注意：非选中分支的消息不入库（branch_points 可还原分支规模）；"服务器繁忙"类
  错误回复按原文保留（不臆测过滤）

### 1.2 DeepSeek Exporter 扩展（agoramachina，2026-03 活跃维护）
- Chrome/Firefox 扩展，走 API 而非 DOM，支持批量 ZIP、分支感知。
- JSON = "Complete data including all branches and metadata"，即 DeepSeek 会话 API 的
  原始响应（含全部消息版本与分支）。
- 社区对该 API 的常见描述为 `{data: {biz_data: [...]}}` 外层，但**两种描述相互矛盾，
  未获源码级确认** → 按铁律不预写解析。等用户用扩展导出 1-2 个真实 JSON 后，
  在 `deepseek_export._iter_conv_messages` 增加第二形态识别（扩展现有防御式框架，
  预计 0.5 天，含样例固化为测试 fixture）。

用户操作：安装扩展 → Browse All Chats → Export All（JSON 格式，勿选 MD）。

---

## 2. 腾讯元宝

### 2.0 ✅ 已实装（v0.8）：登录态直采 API 原始 JSON（真机核验 2026-10-06）
第三方导出工具均不好用（toolkit V2 的 fetch list/detail 实为 TODO 桩，实际靠
页面响应拦截，构建门槛高且只能 MD）。改走**自建采集管线**：
- agent-browser 驱动已登录页面，页面内 fetch 官方 API（带 cookie，无需破解）：
  - 列表 `POST /api/user/agent/conversation/list`，body **顶层** `{limit, offset}`
    （⚠️ 嵌套 pagination 会被服务端静默忽略、恒回第一页——实测踩坑）
  - 详情 `POST /api/user/agent/conversation/v1/detail`，body `{conversationId}`
  - 产物：`corpus/yuanbao_raw/{list_<n>.json, detail_<id>.json}`；接收器
    `verify/yuanbao_receiver.py`（no-cors POST，自定义头会被浏览器丢弃、
    批次号必须走 URL 路径——第二个实测坑）
- **真机 schema（1223 会话核验）**，对侦察报告的三处修正：
  1. speaker 取值是 **'human'**（非 'user'）| 'ai'；
  2. 分页是顶层 `{limit, offset}`（非嵌套 pagination）；
  3. 思考过程不在独立 think 块，而在 **deepSearch/deepSearchAgent 块的
     contents[].msg**（"已深度思考(用时N秒)"标题下）
- content 块 10 种：text(msg)/searchGuid(引用docs)/deepSearch(深度思考)/
  deepSearchAgent(Agent思考)/pdf/image/link_card/prompt_url_card/step；
- adapter：`yuanbao_raw.py`（human/ai→user/assistant，非 text 块降为 note，
  块序保真：附件/思考/搜索 note 就地插在正文前）。

### 2.1 通道 A：chat-export-toolkit（gandli，V2 架构，元宝 L1 完整支持）
⚠️ 2026-10-06 复核：V2 代码 fetchConversationDetail/list 为 TODO 桩，依赖
页面响应拦截；本机使用反馈"不好用"。已被 2.0 自建管线取代，此节仅存档。
Tampermonkey 脚本，拦截元宝 API → 标准化 → 导出 JSON/MD/DOCX。55 个 golden 测试。

**标准化 JSON schema（源码级核验，src/types/index.ts + exporters/json.ts）：**
```jsonc
{
  "id": "<conversationId>",           // 元宝会话 id
  "title": "...",                      // sessionTitle || title
  "createdAt": 1730000000000,          // Unix ms
  "updatedAt": 1730000000000,
  "messages": [{
    "id": "<convId>_msg_<index>",
    "role": "user|assistant|system|tool|unknown",   // speaker ai→assistant
    "content": { "text": "...", "metadata": {"turnIndex": 0, "blockCount": 2} },
    "timestamp": 1730000000000,        // Unix ms
    "metadata": {"platform": "yuanbao", "originalIndex": 0,
                 "originalSpeaker": "ai", "blockCount": 2}
  }],
  "metadata": { "platform": "yuanbao", "participantCount": 2,
                "messageCount": 10,
                "originalData": { ... 元宝原始 API 响应全文 ... } }
}
```
- **思考过程处理方式（关键坑）**：think 块被以引用块形式**内嵌进 text**：
  `> [Think] 标题\n> 正文...`。adapter 需按该格式拆分思考与正文。
- **第二个坑**：normalizer 对 text 块执行了 `adjustHeaderLevels(+1)`——导出的
  Markdown 标题层级被+1 改写，**text 不是原文**。恢复原文需走
  `metadata.originalData`（原始 API 响应全文保留在会话级 metadata 里）。

**元宝原始 API schema（源码级核验，adapters/yuanbao-types）：**
```
data.conversationId | conversation_id | convId | ...（多键兜底）
data.sessionTitle | data.title
data.convs[]: turn
turn.speaker: 'ai' | 'user' | 'human'
turn.createTime: Unix ms；turn.index: 序号
turn.speechesV2[].content[]: block
block.type == 'text'  → block.msg（Markdown 正文）
block.type == 'think' → block.title + block.content（str 或嵌套 block 数组）
```

**adapter 方案（yuanbao_toolkit.py）**：优先从 `metadata.originalData.convs` 走原始
schema 还原原文（拆 think 块为 note 消息），originalData 缺失时降级到标准字段
（text 拆 `> [Think]` 引用块，声明 lossy）。复用 official_export 的
locate/load/extract 基建（顶层单对象，不走 conversations 数组，需要小改）。

### 2.2 通道 B：浏览器另存 MHTML（KoSukeWork/YuanBao_mhtml_parser）
- 元宝网页 Ctrl+S 另存 .mhtml，无风控、不依赖第三方脚本存活。
- 解析器输出（README dataclass 级核验）：
  `{title, url, messages: [{sender: 'user'|'assistant', content, timestamp, thinking}], created_time}`
- 单会话一文件；正文经 HTML→文本清洗，**非原文 Markdown**。
- adapter 方案（yuanbao_mhtml.py）：自己实现 MHTML 解析（quoted-printable +
  boundary 分帧，Python 标准库可覆盖）+ 该 JSON 作为备选输入。价值：作为
  toolkit 通道的兜底（脚本失效时浏览器另存永远可用）。优先级低于通道 A。

用户操作（通道 A）：装 Tampermonkey → 构建脚本（或用扩展版）→ 元宝页面右下角
导出 → 选 **JSON** 格式。

---

## 3. 豆包

### 3.0 ✅ 已实装（v0.9）：app-driven capture（真机核验 2026-10-06）

第三方扩展（MemorySeek 等）用户实测不可用后改自建。**关键结论：**
豆包请求经 **Web Worker** 发出（页面级 fetch/XHR 钩子捕获为 0），且带
msToken/a_bogus 签名——重放捕获到的 URL+body 报 `712012002 不支持编码类型`。
唯一可行路径：**让应用自己发请求，CDP Network 域在浏览器级截获响应体**
（`verify/doubao_harvest.py`）。

端点与协议（IM 信封 `{cmd, uplink_body, sequence_id, channel:2, version:"1"}`，
业务数据在 `downlink_body.*`）：
- `POST /im/chain/recent_conv`（cmd 3200）→ 会话列表
  `cells[].conversation`（name/create_time/update_time 均为 epoch 秒字符串）
- `POST /im/chain/single`（cmd 3100）→ 逐会话消息 `messages[]`；
  `anchor_index=9007199254740991` 起、direction:1、limit:20，向上滚动触发
  更早消息，按 message_id 去重合并

消息 schema（3 会话真机核验）：`user_type` **1=用户 / 2=bot**；
`index_in_conv`（字符串数字）排序；content_block 9 种：
10000 text（正文）、10040 thinking（**仅标题，正文走流式通道不落盘**）、
10091 elapsed、10082 interaction_ask、10019 file_operation、10030 artifact、
10025 search_query_result、2074 creation、未知→警告。adapter 映射为
[think]/[ask]/[file-op]/[artifact]/[search]/[image] note，块序保真。

采集产物：`corpus/doubao_raw/{list_0.json, detail_<cid>.json}`。

### 3.1 通道 A：MemorySeek（manjaro1124，MIT，2026-02 活跃）
> 用户实测（2026-10-06）：第三方扩展均不好用，已弃用，走 3.0 自建管线。

Chrome 扩展（开发者模式加载），网络拦截豆包 API + DOM 双路，全量遍历历史对话。

**chat_data.json schema（exporter.js 源码级核验）：**
```jsonc
{
  "conversations": [{
    "title": "...",
    "messages": [{ "role": "user|assistant", "content": "..." }]
  }],
  "pageTitle": "...",           // 单会话扫描时的兜底
  "currentMessages": [...]      // 同上
}
```
- JSON 是 `JSON.stringify(data)` 原样导出——interceptor 捕获的字段可能比
  exporter 用到的多（时间戳等），adapter 需防御式容错读取。
- **无时间戳字段（按 exporter 用到的字段）**；ZIP 内 images/ 目录含本地化图片。
- MD/HTML 均从同一 data 派生，**只要 JSON，不要 MD**。

### 3.2 其他通道
- AI Exporter（Chrome 商店，闭源）：MD/JSON，勾选批量导出，仅普通文字消息。
- 豆包官方数据导出：账号级全量申请，等待期约 14 天，产出原始 JSON——
  可作"保底"全量通道，schema 未知，样例到位后并入防御式解析。

adapter 方案（doubao_memoryseek.py）：消费 ZIP 内 chat_data.json 或裸 JSON，
`{conversations: [...]}` 顶层数组型复用 extract_conversations 兜底即可。

用户操作：加载扩展 → 扫描当前页 / 全量采集 → 导出 **JSON**（ZIP）。

---

## 4. 通义千问

### 4.0 ✅ 已实装（v0.9）：CDP 登录态直采 API 原始 JSON（真机核验 2026-10-06）

117 会话 / 469 轮全量采集入库。端点（页面内 fetch，公共查询串含
`ut=<uuid>`）：
- `POST /api/v2/session/page/list`，body 顶层 `{next_token}` 游标分页
- `GET /api/v1/session/msg/list?session_id=...&page_size=100`，
  `have_next_page` 时以 `pos=<末条 pos>` 续拉

轮结构：request `text/plain`（正文）/`doc,url`+`image,url`（resource_infos
附件）/`text/hidden`（跳过）；response `multi_load/iframe`（正文）、
`plan_cot/post` + `bar/workflow` 的 `bar_thinking`（思考 → [think] note）、
`signal/bar/paa/survey` 4 类元数据跳过。adapter：`qianwen_raw.py`，
产物 `corpus/qianwen_raw/`，采集器 `verify/qianwen_detail_harvest.py`。

### 4.1 官方数据管理导出（备用通道）

- 官方唯一通道：网页端 设置 → 「数据管理」→ 导出我的对话记录 → 邮箱收 ZIP →
  解压含 `index.html` + `record.json`。
- **可信度警示**：该描述仅见于多篇互相转述的内容农场文章（17golang / php.cn），
  无阿里官方文档佐证，record.json 内部 schema 完全未知。
- CSDN 上"Qwen3.5 Export History"等文章描述的是本地部署 WebUI 的导出，
  与通义千问官网无关，不可混用。
- 按"先核验后实现"铁律：**本轮不动千问，QianwenStub 保持 STUB**，detect hints
  里补充指引。用户拿到真实 record.json 后提供前 100 行即可实装（预估 1 天）。

---

## 5. 落地计划（2026-10-06 更新：三平台直采全部完成）

已完成：DeepSeek 官方导出（v0.7）→ 元宝直采（v0.8）→ 千问 CDP 直采 +
豆包 app-driven capture（v0.9）。
剩余备选（按需）：deepseek_export 第二形态（Exporter 扩展样例）、
元宝 toolkit/MHTML 兜底、千问官方 record.json 兜底——均待真实样例。

统一接入点：各 adapter 进 `ACTIVE` + `_PARAM_MAP`/`PLUGIN_IDS`（`paths` 参数），
category 归入「网页/客户端Chat」，与 ChatGPT/Claude 官方导出同目录。

## 6. 用户侧需要做的事（导出格式要求）

| 平台 | 工具 | 关键要求 |
|---|---|---|
| DeepSeek | 官方数据导出（已支持） | ZIP/目录/单文件均可 |
| 元宝 / 千问 / 豆包 | **均已自建直采管线（见 §2.0/§3.0/§4.0），用户无需手动导出** | 浏览器登录态 + verify/ 采集脚本 |

（下表仅作历史备查：若自建管线失效再走第三方导出。）

| 平台 | 备用工具 | 关键要求 |
|---|---|---|
| DeepSeek | DeepSeek Exporter 扩展（agoramachina） | 导出格式选 **JSON** |
| 元宝 | chat-export-toolkit 油猴脚本 | 导出格式选 **JSON**（含 originalData 的那种，非 MD/DOCX） |
| 豆包 | MemorySeek 扩展 | 导出 **JSON**（ZIP，解压取 chat_data.json） |
| 千问 | 官方 数据管理 导出 | ZIP 解压后把 record.json 样例（前 100 行）提供过来 |

共同铁律：**一律 JSON，绝不要 Markdown**——MD 丢结构、丢元数据、丢思考块边界。
