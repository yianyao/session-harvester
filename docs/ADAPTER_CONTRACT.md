# 适配器契约（ADAPTER_CONTRACT）

> v1.0 · 2026-10-06。本文档是**采集层与分析层之间唯一的正式接口**。
> 两层代码互不 import：采集层只负责把各平台会话写入索引库，分析层只读索引库。
> 只要遵守本契约，采集器可以单独拆包、换语言重写、增删替换，分析层零改动。

## 0. 分层总览

```
┌─ 采集层（adapters/ + probe/scan/export/weblogin/sync）─┐
│  职责：把"平台私有格式"变成"索引库里的行"                  │
└──────────────────────┬───────────────────────┘
                       ▼  唯一交接物：harvester.db（SQLite）
┌─ 分析层（errstats/behstats/toolstats/cards/agent_suggest）─┐
│  职责：对行做统计、聚类、画像、出报告。禁止 import 采集层。    │
└──────────────────────────────────────────────┘
```

## 1. 数据库 Schema（正式接口）

三张表，定义见 `harvester/indexing.py` 的 `SCHEMA`。采集层是**唯一写入方**；
`index` / `sync` 为整库重建口径（DELETE 后重插），分析层不得写入。

### sessions（会话主表）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| sid | TEXT | PRIMARY KEY | `{source}:{session_id}`，全局唯一 |
| source | TEXT | NOT NULL | 适配器 id（如 `dsh`、`deepseek-export`） |
| session_id | TEXT | NOT NULL | 源内会话标识 |
| title / category / created_at / updated_at / file | TEXT | | 元数据；category=适配器大类 |
| model | TEXT | 可空 | 会话主模型（v0.15）。当前仅 workbuddy-transcript 源提供（行级 providerData.model 众数，会话中途换模型按使用最多者归属）；其他源为 NULL——**新适配器若有模型信息，请在 `SessionRecord.extra["model"]` 提供同名键** |

### messages（消息表，FTS5 虚表）

| 列 | 说明 |
|---|---|
| sid | 归属会话（UNINDEXED） |
| role | `user` / `assistant` / `note`（note=思考过程、搜索结果等非正文） |
| ts | 源端时间戳（ISO 或源原文，可为空） |
| text | CJK bigram 改写后的索引文本（unicode61 分词） |
| raw | 原文（UNINDEXED，展示用） |

### steps（工具步骤表——G1/G2/G4 统计的唯一数据源）

| 列 | 约束 | 说明 |
|---|---|---|
| sid, seq | | 归属会话 + 会话内顺序（从 0 递增） |
| tool | NOT NULL | 工具/命令名（解析口径见下） |
| phase | NOT NULL | `call`（入参）或 `result`（出参） |
| status | | result 相：`ok` / `error` / 源端原文 |
| error | | 错误码或错误摘要（≤200 字符） |
| detail | | 入参（call 相）/ 出参摘要（result 相） |

**工具步骤提取规则**（`indexing._step_of`）：消息 `raw` 需为 dict，且
`kind` 或 `entry_type` ∈ {`tool_call`, `tool_result`}；工具名取
`raw.tool` 或 `raw.data.toolName`。chat 平台（无工具遥测）不产生 steps 行——
这是记录在案的口径边界，不是缺陷。

**成功判定口径单一真值源**（v0.16 起）：`toolstats._OK_STATUS =
("success", "completed", "ok")`。steps 口径（report-tools）与 note 口径
（实时扫描）**必须共用此常量**，禁止各写各的字面量——历史上两口径各认
一套成功值，导致同一库上失败率 88.1% vs 1.7% 的虚报。改动口径只改常量，
并跑 `tests/test_v16.py` 的对账测试（两口径对同一 fixture 必须同值）。

**只读 HTTP API（api-serve，v0.17 起）**：`apiserve.EXPECTED_SCHEMA`
是本文件 DB schema 的消费方冻结清单。改表时必须同步该清单，否则
api-serve 启动自检会 fail loud（设计如此，防静默 schema 漂移）。

## 2. 适配器契约（采集器必须遵守）

新增/重写一个采集器，必须：

1. **继承 `BaseAdapter`**（或对齐其鸭子类型）并实现 `detect() / list_sessions() / load_session()`。
2. **detect() 永不抛异常**：任何失败（路径缺失、JSON 非法、结构不认识）
   一律降级返回 `MISSING` / `STUB`，绝不用异常打断扫描。
3. **绝不臆测解析**：只接受已核验的数据形态；未核验形态 → STUB +
   hints 指引（"把样例提供给维护者"），未识别的条目跳过并记入
   `extra["warnings"]`（lossy 不抛异常）。
4. **detect()==OK 的最低标准**：数据源存在 **且** 结构通过校验 **且**
   至少解析出 1 个会话。空数组/垃圾对象不得返回 OK。
5. **`claims_files` 声明源形态**（v0.12 起）：
   - `True`（默认）：单文件源（官方导出 zip/json），**可**参与 sync 收件箱竞标；
   - `False`：目录型源（采集产物目录），**禁止**认领单文件——其 detect()
     若经默认候选回退在别处返回 OK，会造成错误认领（实测案例：yuanbao-raw）。
6. **幂等友好**：同源重跑产出确定的 session_id 与文件名；导出 JSON 顶层
   必含 `source / session_id / title / messages[]`（供 `index_exports` 消费）。
7. **新适配器登记**：单文件型加入 `adapters/__init__.py` 的 `PLUGIN_IDS`；
   目录型加入 `ACTIVE`；未上线的占位加入 `STUBS`。

## 3. sync 收件箱协议（人工采集的最小化接口）

- `inbox/*.zip|*.json` → 待导入。sync 让全部 `claims_files=True` 的插件
  逐个 `detect()` 竞标，首个 OK 者认领，归档到 `inbox/done/<adapter-id>/`。
- `inbox/done/<adapter-id>/*` → **路径即源声明**。每次 sync 重新扫描注入
  `sources.plugins`（内存态，不回写 sources.json）；归档文件被改坏则跳过并警告。
- 认领失败的文件**留在 inbox/ 原地**并在报告中列名——绝不静默丢弃。
- 删除某个导出源 = 从 `inbox/done/` 删除对应目录，直觉可逆。

## 4. 契约变更流程

- **加列**：分析层 SQL 用显式列名，新增列须有默认值/可空，分析层不感知即兼容。
- **改语义**（如 status 枚举值增删）：必须先改本文档并在各分析模块头注
  同步登记版本号，再动代码。
- **重大重排**（表结构重设计）：契约版本 +1；旧采集器产出经适配视图
  （SQL VIEW）过渡，分析层切视图不切表。

## 5. 最小自检清单（新采集器上线前）

- [ ] 真机样本 detect() == OK，session_count 与肉眼清点一致
- [ ] 垃圾文件/空数组 → STUB，不 OK、不抛异常（竞标安全）
- [ ] load_session 对不存在 id 抛 KeyError；对结构残缺会话 lossy 注明不抛异常
- [ ] 导出 JSON 经 `index_exports` 入库后，三表行数符合预期
- [ ] `report-tools` / `report-errors` 跑通且源归属正确（source 列 = 适配器 id）
- [ ] 换行符 LF、UTF-8 无 BOM；代码过 `python -m unittest discover tests`
