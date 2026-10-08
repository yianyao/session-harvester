# 产物提取 pass 设计（artifacts extract）

> PLAN-v0.22-unified T0-② 裁决产物（§0.2-C：不改采集库 schema，独立提取 pass）。
> 配套契约：`ADAPTER_CONTRACT.md` §1 的 text-raw 契约与 T 轨锚点格式。
> 状态：设计稿（实施前冻结）。2026-10-08。

## 1. 目标与范围

从**原始数据源**解析 Write/Edit 类工具调用的完整入参（`old_string →
new_string`），产出**稿件演化 diff 序列**，写入独立 meta 库
`artifacts_meta.db`。供 T 轨 `topic chain --level artifact`（版本对照）与
T3 思维链"为什么改这一稿"节点消费。

为什么必须独立 pass：索引库 `steps` 表**没有 args 列**（H1，schema 指纹
守卫冻结，红线 §5.1），工具入参仅以 400 字 preview 形态存在于 `detail`
（`dsh.py` 索引路径 `str(args)[:400]`）；adapter 产出 Message.raw 中的
`detail` 同样截断。完整 args 只在原始数据源里。

范围（MVP）：**Edit 类**（含 old_string/new_string 的编辑调用）与 **Write
类**（创建整文件的调用）。MVP 只接 dsh 源 + 导出型 JSON 源（yuanbao/
qianwen/deepseek-export，结构同源实测后登记）；autoclaw/vscode 源二期。

## 2. 输入：按源形态分路

源路径解析沿用 `sources.json` + `adapters/__init__.py` 的插件登记，**不
重新发明探测**。每源一个提取函数，统一签名：

```
extract_<source>(session_id) -> list[dict]
# 每项 {ts, tool, file_path, old_text, new_text}
# old_text/new_text：完整字符串，绝不截断；Write 类 old_text=None
```

| 源 | 原始形态 | Write/Edit args 位置 | 实测状态 |
|---|---|---|---|
| dsh | `~/.dsh/sessions/*/*/session.v4.jsonl.zstd` | `tool/call` 行 `data.arguments`（JSON 串，键 `file_path/new_string/old_string`） | ✅ 2026-10-08 实测完整非截断 |
| 导出型（yuanbao-raw 等） | exports/ 下 JSON（`source/session_id/messages[]`） | messages[].tool_calls / 各源字段待逐一实测登记 | ⏳ 实施前逐源核验，未核验源不接（适配器契约 §2.3） |
| autoclaw | runtime.sqlite `neutral_session_entries` | tool_call 条目 payload（路径待实测） | 二期 |
| vscode_copilot | 补丁日志 jsonl 重放 state | requests[].tool_calls（路径待实测） | 二期 |

判定"Edit 类"的口径：args 中同时含 `old_string` 与 `new_string` 键；
"Write 类"：含 `file_path` 与 `new_string`（或 `content`）且无 `old_string`。
未命中两口径的调用一律跳过——**绝不臆测解析**（契约 §2.3）。

## 3. 输出：artifacts_meta.db（独立 meta 库）

```
CREATE TABLE IF NOT EXISTS artifacts (
    sid       TEXT NOT NULL,   -- 索引库 sessions.sid（source:session_id）
    seq       INTEGER NOT NULL,-- 该会话内提取序（0 起，按源时间序）
    ts        TEXT,            -- 调用时间（to_local_ts 口径）
    tool      TEXT NOT NULL,   -- Edit / Write / ...（源侧原名）
    file_path TEXT,            -- 目标文件
    old_text  TEXT,            -- Edit 前文；Write 为 NULL
    new_text  TEXT,            -- Edit 后文 / Write 全文
    PRIMARY KEY (sid, seq)
);
```

- 独立 meta 库（suggestions_meta.db 范式），采集库 harvester.db **零改动**
  （红线 §5.1）。
- 锚点：`(sid, seq)` 即 T 轨锚点 `{sid, seq}`；turn 由消费侧按
  `reader.split_turns` 口径从索引库推导（本表不冗余存储，避免第二套
  回合口径——契约 §1 T 轨锚点条款）。
- 重建策略：全量重建（`DELETE FROM artifacts` 后重提），幂等；不追增量——
  源会话本身会变（活会话），diff 序列以重建时点为准，`db_fingerprint`
  记录对应快照。

## 4. CLI 与消费

```
python -m harvester artifacts extract --sid <sid> [--db harvester.db]   # 单会话
python -m harvester artifacts extract --topic-sids <file>               # 批量（T1 注册表成员）
python -m harvester artifacts show --sid <sid> [--limit N]              # 版本对照预览
```

- `topic chain --level artifact`（SOP-T2）消费本表：按 seq 展示同文件
  版本序（时间序即版本序，H19：不走文件快照）。
- 与索引库对账：提取到的 sid 必须存在于 `harvester.db.sessions`，否则
  跳过并计数（防止 meta 库与索引库漂移）。

## 5. 验收（对应 PLAN §4 T0）

对任一 Write/Edit 密集会话产出 ≥1 条完整 diff：`old_text` 与 `new_text`
均非截断（长度可 >400，且与原始 args 逐字一致）。合成注入用例：构造含
>400 字 old_string 的 fixture 行，提取结果必须完整——防 400 字截断回潮。

## 6. 明确不做

- 不改 harvester.db schema（红线 §5.1）；
- 不做 diff 算法/相似度计算（消费侧按需做，本 pass 只忠实记录）；
- 不为未实测登记的源写提取函数（契约 §2.3 禁臆测）。
