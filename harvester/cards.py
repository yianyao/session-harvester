# -*- coding: utf-8 -*-
"""知识卡片库（cards 子命令）——G3 的确定性一半。

对应《轨迹分析工具设计方案》§8：卡片 = 带 YAML frontmatter 的 Markdown，
frontmatter 规范冻结字段 id/title/type/tags/anchors/evidence/confidence/
created。设计把 cards extract（调 LLM 生成卡片）划入可抛弃的提炼层——
本模块只实装确定性部分 **validate**：锚点有效性 + 证据/字段完整性校验，
让冻结的规范至少有一个机器消费者。

与 kb/playbook 的归一决策（2026-10-06）：
- cards 目录 = 机器可校验的**候选池**（suggest-agents / 人工摘录产出）；
- ~/.workbuddy/knowledge（kb-init/playbook）= 人工维护的**主库**；
- 卡片通过 validate 后由人工并入主库——两套形态一池一库，不再双轨。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

try:  # PyYAML 为可选依赖：有则完整解析，无则降级逐行提取标量
    import yaml  # type: ignore
    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False

REQUIRED_TYPES = {"insight", "pitfall", "workflow"}
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# v0.21 P0-1（H11）：cards new 脚手架占位符登记处。
# 新增脚手架占位符必须同步登记（scaffold_card 模板改动时）。
_PLACEHOLDER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("evidence 含 <粘贴 占位", re.compile(r"<粘贴")),
    ("字段含 <待补 占位", re.compile(r"<待补")),
    ("title 为脚手架兜底标题", re.compile(r"^（补标题）$")),
)
_BLOCK_LITERAL_RE = re.compile(r"^\|[-+]?$|^>[-+]?$")


def _split_top(s: str, sep: str = ",") -> list[str]:
    """按顶层分隔符切分（忽略引号内与 {}/[] 嵌套内的 sep）。"""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "{[":
            depth += 1
            buf.append(ch)
        elif ch in "}]":
            depth -= 1
            buf.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf)
    if tail.strip():
        parts.append(tail)
    return parts


def _coerce_scalar(v: str):
    v = v.strip()
    if v in ("null", "~"):
        return None  # YAML null 语义（turn: null 等），与 PyYAML 对齐
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def _parse_flow_seq(s: str) -> list | None:
    """解析 YAML 行内流式序列 [{k: v, ...}, ...] / [a, b]。

    只覆盖卡片 frontmatter 实际出现的形态（session_id 值内含冒号，
    取第一个冒号为键分隔）；解析失败返回 None，调用方保留原字符串。
    存在意义：PyYAML 缺席时锚点校验仍必须可执行——降级不等于放弃校验。
    """
    inner = s.strip()[1:-1].strip()
    if not inner:
        return []
    items: list = []
    for part in _split_top(inner):
        part = part.strip()
        if part.startswith("{") and part.endswith("}"):
            d: dict = {}
            for kv in _split_top(part[1:-1]):
                if ":" not in kv:
                    return None
                k, _, v = kv.partition(":")
                d[k.strip()] = _coerce_scalar(v)
            items.append(d)
        else:
            items.append(_coerce_scalar(part))
    return items


def _parse_frontmatter(text: str) -> tuple[dict | None, str]:
    """返回 (frontmatter dict 或 None, 正文)。无 frontmatter 返回 (None, 全文)。"""
    m = _FM_RE.match(text)
    if not m:
        return None, text
    raw = m.group(1)
    if _HAS_YAML:
        try:
            data = yaml.safe_load(raw)
            return (data if isinstance(data, dict) else None), text[m.end():]
        except yaml.YAMLError:
            return None, text[m.end():]
    # 降级：顶层 "key: value" 标量；行内 [..] 流式序列做最小解析
    # （anchors: [{session_id: "...", turn: N}, ...]），保证无 PyYAML
    # 时锚点校验照常执行。
    # v0.21 P0-1（H8）：补块标量（|/|-/|>/>) 与块序列（缩进 "- " 项）——
    # cards new 脚手架 evidence 用块标量，此前降级分支读成 '|' 字面量，
    # 占位卡与真实证据一律静默失效。
    data: dict = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        km = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not (km and not line.startswith((" ", "-", "\t"))):
            i += 1
            continue
        key, value = km.group(1), km.group(2).strip()
        if value in ("|", "|-", "|+", ">", ">-", ">+"):
            block: list[str] = []
            base_indent: int | None = None
            j = i + 1
            while j < len(lines):
                ln = lines[j]
                if not ln.strip():
                    block.append("")
                    j += 1
                    continue
                indent = len(ln) - len(ln.lstrip())
                if indent == 0:
                    break
                if base_indent is None:
                    base_indent = indent
                block.append(ln[base_indent:] if indent >= base_indent
                             else ln.strip())
                j += 1
            while block and not block[-1]:
                block.pop()
            data[key] = "\n".join(block)
            i = j
            continue
        if value == "":
            seq: list = []
            j = i + 1
            while j < len(lines):
                sm = re.match(r"^\s+-\s+(.*)$", lines[j])
                if not sm:
                    break
                item = sm.group(1).strip()
                if item.startswith("{") and item.endswith("}"):
                    # _parse_flow_seq 期望 [...] 包裹，单 flow map 项包一层复用
                    parsed = _parse_flow_seq(f"[{item}]")
                    seq.append(parsed[0] if parsed else item)
                elif ":" in item:
                    k, _, v = item.partition(":")
                    seq.append({k.strip(): _coerce_scalar(v)})
                else:
                    seq.append(_coerce_scalar(item))
                j += 1
            data[key] = seq if seq else ""
            i = j
            continue
        if value.startswith("[") and value.endswith("]"):
            parsed = _parse_flow_seq(value)
            data[key] = parsed if parsed is not None else value
        else:
            data[key] = value
        i += 1
    return data, text[m.end():]


def validate_card(path: Path) -> tuple[list[str], list[str], dict | None]:
    """校验单卡。返回 (errors, warnings, fm)。"""
    errors: list[str] = []
    warns: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ["非 UTF-8 编码"], [], None
    fm, body = _parse_frontmatter(text)
    if fm is None:
        return ["frontmatter 缺失或不可解析（§8 规范要求 YAML 头）"], [], None
    for field in ("id", "title", "type", "anchors", "evidence",
                  "confidence", "created"):
        if field not in fm or fm[field] in ("", None, "[]", "{}"):
            errors.append(f"缺少必填字段 {field}")
    ctype = str(fm.get("type", ""))
    if ctype and ctype not in REQUIRED_TYPES:
        errors.append(f"type={ctype!r} 不在 {sorted(REQUIRED_TYPES)}")
    anchors = fm.get("anchors")
    if anchors is not None and not isinstance(anchors, list):
        warns.append("anchors 非列表形态（§8 要求 [{session_id, ...}] 列表）")
    if not str(fm.get("evidence", "")).strip() and "evidence" in fm:
        errors.append("evidence 为空（规范：必须带证据原文）")
    if not body.strip():
        warns.append("正文为空")
    try:
        conf = float(fm.get("confidence", -1))
        if not 0.0 <= conf <= 1.0:
            errors.append(f"confidence={conf} 超出 0.0-1.0")
    except (TypeError, ValueError):
        if fm.get("confidence") is not None:
            errors.append(f"confidence={fm.get('confidence')!r} 不是数值")
    # v0.21 P0-1（H11）：脚手架占位符 = 错误级（此前零检查 → 空壳卡
    # 结论"可并入主库"）。
    for label, pat in _PLACEHOLDER_PATTERNS:
        hay = [str(fm.get("evidence") or ""), body, str(fm.get("title") or "")]
        if any(pat.search(h) for h in hay):
            errors.append(f"[占位符] {label}（cards new 脚手架未补全）")
    if _BLOCK_LITERAL_RE.match(str(fm.get("evidence") or "").strip()):
        errors.append("[占位符] evidence 仍是块标量字面量（降级解析或未填）")
    return errors, warns, fm


def _anchor_known(sid: str, db: Path | None,
                  con: sqlite3.Connection | None = None) -> bool | None:
    """锚点 session_id 是否在索引库中。无库时返回 None（跳过校验）。

    con：外部连接（api-serve 传入 mode=ro + authorizer 连接）；缺省
    自开普通连接（CLI 兼容）。传入时 db 参数被忽略。
    """
    if con is None:
        if db is None or not db.is_file():
            return None
        con = sqlite3.connect(str(db))
        own = True
    else:
        own = False
    try:
        row = con.execute("SELECT 1 FROM sessions WHERE session_id=? OR sid=?",
                          (sid, sid)).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return None
    finally:
        if own:
            con.close()


def _norm_ws(s: str) -> str:
    """空白归一（与 view 端锚点定位同思路）：换行/连续空白压成单空格。"""
    return " ".join(s.split())


def _session_content(sid: str, db: Path | None,
                     con: sqlite3.Connection | None = None) -> str | None:
    """取锚点会话的全文（messages.raw 优先、退 text，跨源口径与
    apiserve._session_messages 一致）。会话不在库 → None。"""
    if con is None:
        if db is None or not db.is_file():
            return None
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
        own = True
    else:
        own = False
    try:
        row = con.execute("SELECT sid FROM sessions WHERE session_id=? OR sid=?",
                          (sid, sid)).fetchone()
        if row is None:
            return None
        rows = con.execute("SELECT raw, text FROM messages WHERE sid=? "
                           "ORDER BY rowid", (row["sid"],)).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        if own:
            con.close()
    return "\n".join((r["raw"] if r["raw"] else r["text"]) or "" for r in rows)


def _session_title(sid: str, db: Path | None,
                   con: sqlite3.Connection | None = None) -> str | None:
    """取锚点会话原标题。会话不在库 → None（无法比对则不判）。"""
    if con is None:
        if db is None or not db.is_file():
            return None
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
        own = True
    else:
        own = False
    try:
        row = con.execute("SELECT title FROM sessions "
                          "WHERE session_id=? OR sid=?", (sid, sid)).fetchone()
        return row["title"] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        if own:
            con.close()


def _evidence_warnings(fm: dict, warns: list[str], db: Path | None,
                       con: sqlite3.Connection | None = None
                       ) -> tuple[int, int]:
    """引文核对（v0.19 additive，只产警告不改错误判定）：evidence 每行
    （空白归一后 >=6 字）必须能在 anchors 指向会话的原文中逐字找到；
    找不到 → 警告（adapter 改写/跨源格式差异可能造成误报，故降为警告）。
    anchors 的 turn 为 null → 警告（锚点定位精度不足）。
    返回 (checked, misses)：核对行数与未命中行数（供 summary 汇总）。"""
    evidence = str(fm.get("evidence") or "")
    anchors = fm.get("anchors")
    # v0.21 P0-1（H10）：turn:null 警告必须在提前 return 之前——
    # evidence 为空/anchors 非列表不应吞掉锚点精度警告。
    if isinstance(anchors, list):
        for a in anchors:
            if isinstance(a, dict) and a.get("turn") is None:
                warns.append(f"锚点 {a.get('session_id')} turn 为 null"
                             "（定位精度不足，建议补回合号）")
    if not evidence.strip() or not isinstance(anchors, list):
        return (0, 0)
    checked = misses = 0
    cache: dict[str, str | None] = {}
    lines = [_norm_ws(ln) for ln in evidence.splitlines()]
    lines = [ln for ln in lines if len(ln) >= 6]
    sids = [str(a.get("session_id")) for a in anchors
            if isinstance(a, dict) and a.get("session_id")]
    if not sids:
        return (0, 0)
    corpus = ""
    for sid in dict.fromkeys(sids):  # 去重保序
        if sid not in cache:
            cache[sid] = _session_content(sid, db, con=con)
        got = cache[sid]
        if got is None:
            warns.append(f"锚点 {sid} 会话原文不可得，evidence 未核对")
            continue
        corpus += "\n" + _norm_ws(got)
    for i, ln in enumerate(lines, 1):
        checked += 1
        if corpus and ln not in corpus:
            misses += 1
            warns.append(f"evidence 第 {i} 行未在锚点会话原文中找到"
                         "（原文可能经 adapter 改写，请人工复核）")
    return (checked, misses)


# ---- V3：卡片 ↔ 主题注册表打通（只读交叉核对） ----
#
# 用途：卡片与主题此前是两套互不可见的产物——卡片锚点指着会话，会话在主题
# 注册表里有没有归属，卡片侧一无所知（真实卡 kc-20261006-0001/0002 的锚点
# 就在 tp-20261010-004/003 成员里，但没有任何出口说得出来）。本段把注册表
# 的成员资格**只读**读进来做一致性核对：既不自动登记主题，也不猜主题归属。
#
# 口径（跨库一致性检查的三分法，见 README「卡片校验与主题注册表打通」）：
#   卡片声明主题 且 声明值不在注册表     → 错误（卡片指了一个不存在的主题）
#   卡片声明主题 且 锚点会话不是其成员   → 错误（卡片挂的会话与声明的主题不一致）
#   卡片未声明主题 且 锚点会话已在某主题 → 警告（只提示，不替人改卡）
#   卡片声明主题 且 锚点会话确是其成员   → 通过（报告出该主题）
# 不一致一律报「错误」而非自动修正：改哪边（改卡还是改注册表）是语义裁决。


def _load_topic_membership(topics_meta: Path,
                           db: Path | None = None) -> dict:
    """读主题注册表，建 sid → [主题…] 成员索引（**只读**，mode=ro）。

    返回 {"map", "noise", "topics", "member_rows"}：
    - map：注册成员 sid → [{"id", "name"}, …]（同 sid 属多主题时按主题 id 排序）；
    - noise：零散登记（`sessions_noise`）sid 集合——它们**未被裁决进任何主题**，
      与"漏归主题"不是一回事，故不产"未声明主题"警告；
    - topics：注册表里主题条数（含零成员的主题）；
    - member_rows：成员登记行数（同一 sid 属两个主题会计两行）。

    **sid 双形态（v0.43 实测缺口）**：注册表成员一律以 `sessions.sid`
    （带源前缀，如 `workbuddy-transcript:xxx`）登记；而卡片锚点可能是
    `sessions.session_id`（裸 id，`cards new` 脚手架就取这个值）——
    只按 sid 精确比对会**静默漏判**（真实卡 kc-20261006-0003/0004 的锚点
    其实都在主题成员里，却被判"未登记"）。给了 db 时顺带查索引库，
    把每个成员的 `session_id` 也登记为同一主题的别名键（不写任何库）。

    fail loud：库不存在 / 不是 SQLite / 缺 topics 表 / members 列不是 JSON
    → 抛异常，绝不静默当成"没有主题"返回空索引（那会把一次没执行的检查
    伪装成检查通过——空注册表与不可读注册表必须能分开）。
    """
    meta_path = Path(topics_meta)
    if not meta_path.is_file():
        raise FileNotFoundError(
            f"主题注册表不存在: {meta_path}（cards 的主题一致性核对无法执行）")
    try:
        con = sqlite3.connect(f"file:{meta_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:  # pragma: no cover - 连接失败极罕见
        raise RuntimeError(f"主题注册表不可读: {meta_path}: {exc}") from exc
    try:
        try:
            rows = con.execute("SELECT id, name, members FROM topics "
                               "ORDER BY id").fetchall()
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                f"主题注册表缺 topics 表或不可读: {meta_path}: {exc}") from exc
    finally:
        con.close()
    member_map: dict[str, list[dict]] = {}
    member_rows = 0
    for tid, name, members in rows:
        try:
            parsed = json.loads(members or "[]")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"主题注册表 members 不是合法 JSON（主题 {tid}）: {exc}") from exc
        if not isinstance(parsed, list):
            raise ValueError(f"主题注册表 members 非列表（主题 {tid}）: "
                             f"{type(parsed).__name__}")
        for m in parsed:
            sid = str((m or {}).get("sid") or "") if isinstance(m, dict) else ""
            if not sid:
                continue
            member_rows += 1
            _add_topic_key(member_map, sid, tid, name)
    alias_keys = _add_sid_aliases(member_map, db)
    from .consolidate import noise_sids
    noise = noise_sids(meta_path)
    return {"map": member_map, "noise": _add_noise_aliases(noise, db),
            "topics": len(rows), "member_rows": member_rows,
            "alias_keys": alias_keys}


def _add_topic_key(member_map: dict[str, list[dict]], key: str, tid: str,
                   name: str) -> None:
    """把 (tid, name) 挂到 key 下（同一 key 同一主题只登记一次，保持 id 序）。"""
    if not key:
        return
    lst = member_map.setdefault(key, [])
    if any(t["id"] == tid for t in lst):
        return
    lst.append({"id": tid, "name": name})
    lst.sort(key=lambda t: t["id"])


def _add_sid_aliases(member_map: dict[str, list[dict]],
                     db: Path | None) -> int:
    """把注册成员的 `sessions.session_id` 形态登记为别名键（只读）。

    注册表用 `sessions.sid`（带源前缀）；卡片锚点可能是 `session_id`
    （裸 id）。两种形态指向同一会话时都必须能解析到主题，否则"卡片挂了
    成员会话"会被误判成"未登记"（真实卡 kc-20261006-0003/0004 实测）。
    db 为 None（未给索引库）→ 跳过并返回 0，此时只有 sid 形态可解析
    （报告里用 topic_alias_keys 显式计数，不许静默当全覆盖）。
    返回新增的别名键数。
    """
    if db is None or not Path(db).is_file() or not member_map:
        return 0
    keys = list(member_map)
    try:
        con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:  # pragma: no cover
        return 0
    try:
        added = 0
        for i in range(0, len(keys), 400):  # 躲 SQLite 变量数上限
            chunk = keys[i:i + 400]
            ph = ",".join("?" * len(chunk))
            try:
                rows = con.execute(
                    f"SELECT sid, session_id FROM sessions "
                    f"WHERE sid IN ({ph}) OR session_id IN ({ph})",
                    chunk + chunk).fetchall()
            except sqlite3.OperationalError:  # pragma: no cover - 旧库无列
                return added
            for sid, session_id in rows:
                owners = [o for o in (sid, session_id)
                          if o and o in member_map]
                if not owners:
                    continue
                for alias in (sid, session_id):
                    if not alias or alias in member_map:
                        continue
                    for o in owners:  # 同一会话的两种写法映射到同一主题
                        for t in member_map[o]:
                            _add_topic_key(member_map, alias, t["id"],
                                           t["name"])
                    added += 1
        return added
    finally:
        con.close()


def _sid_forms(db: Path, sids: set[str]) -> set[str]:
    """把一批 sid 扩成「sid ∪ session_id」两形态（只读；无库/无列 → 原样）。

    与 `_add_sid_aliases` 同一缺口的两侧：注册表与零散登记都用
    `sessions.sid`，而卡片锚点可能是裸 `session_id`——零散判定也要
    双形态，否则"锚点是已登记零散会话"会被误报成"完全没有关联"。
    """
    if not sids or db is None or not Path(db).is_file():
        return set(sids)
    keys = list(sids)
    try:
        con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:  # pragma: no cover
        return set(sids)
    try:
        out = set(sids)
        for i in range(0, len(keys), 400):
            chunk = keys[i:i + 400]
            ph = ",".join("?" * len(chunk))
            try:
                rows = con.execute(
                    f"SELECT sid, session_id FROM sessions "
                    f"WHERE sid IN ({ph}) OR session_id IN ({ph})",
                    chunk + chunk).fetchall()
            except sqlite3.OperationalError:  # pragma: no cover - 旧库无列
                return set(sids)
            for sid, session_id in rows:
                for v in (sid, session_id):
                    if v:
                        out.add(str(v))
        return out
    finally:
        con.close()


def _add_noise_aliases(noise: set[str], db: Path | None) -> set[str]:
    """零散 sid 集合的双形态展开（见 `_sid_forms`）。"""
    if not noise or db is None:
        return set(noise)
    return _sid_forms(Path(db), set(noise))


def _anchor_sids(fm: dict) -> list[str]:
    """卡片 anchor 的 session_id 列表（去重保序；非 dict 锚点跳过）。"""
    anchors = fm.get("anchors")
    if not isinstance(anchors, list):
        return []
    return list(dict.fromkeys(
        str(a.get("session_id")) for a in anchors
        if isinstance(a, dict) and a.get("session_id")))


def _declared_topic(fm: dict) -> str:
    """卡片声明的主题（`topic_id` 优先、`topic` 次之；都没有 → 空串）。

    **不把 `topic` 列入必填字段**：§8 冻结字段清单未含它，强制必填会
    让既有卡全红——声明主题是可选动作，只在声明了才核对一致性。
    """
    for key in ("topic_id", "topic"):
        v = fm.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _topic_consistency(fm: dict, membership: dict) -> tuple[list[str], list[str],
                                                            dict]:
    """单卡的「卡 ↔ 主题」一致性核对。返回 (errors, warnings, info)。

    errors/warnings 追加进调用方的列表（错误=可判定的不一致，须人工裁决）；
    info 供 additive 结果字段与 summary 对账：

    - declared_topic_id：卡片声明的主题（`topic_id` 优先、`topic` 次之）；
    - topics：锚点命中的注册主题 [{id, name}]；
    - undeclared：**存在**锚点既不在任何主题成员里、也不是已登记零散
      （混合卡——部分锚点命中主题、部分没命中——也算）；
    - noise_only：卡片有锚点，且**全部**锚点都是已登记零散会话。

    警告分两类分别追加（混合卡两条都出），任何锚点都不静默丢失：
    「未声明主题但已登记在主题 X」= 建议补 topic_id；
    「锚点未登记任何主题/零散」= 卡片与注册表暂无关联。
    """
    errors: list[str] = []
    warns: list[str] = []
    member_map: dict[str, list[dict]] = membership["map"]
    noise: set[str] = membership["noise"]
    declared = _declared_topic(fm)
    sids = _anchor_sids(fm)
    hits: dict[str, dict] = {}
    undeclared = False
    for sid in sids:
        topics = member_map.get(sid) or []
        for t in topics:
            hits.setdefault(t["id"], t)
        if not topics and sid not in noise:
            undeclared = True
    noise_only = bool(sids) and not hits and not undeclared
    if declared:
        if declared not in {t["id"] for t in hits.values()}:
            known_ids = sorted({t["id"] for t in hits.values()})
            if not sids:
                errors.append(
                    f"卡片声明主题 {declared}，但没有 anchors 可供核对"
                    "（无法确认该主题是否成立）")
            elif not known_ids:
                errors.append(
                    f"卡片声明主题 {declared}，但锚点会话均不在该主题成员列表"
                    "（卡片挂的会话与卡片声称的主题不一致；改卡或改注册表"
                    "由人工裁决）")
            else:
                errors.append(
                    f"卡片声明主题 {declared}，但锚点会话实际属 {known_ids}"
                    "（卡片挂的会话与卡片声称的主题不一致）")
    elif hits:
        names = [f"{t['id']}（{t['name']}）" for t in
                 (hits[k] for k in sorted(hits))]
        warns.append("卡片未声明主题，锚点会话已登记在主题 " + "、".join(names)
                     + "（可选：在 frontmatter 补 topic_id 声明归属）")
    if not declared and undeclared:
        warns.append("卡片锚点会话未登记于任何主题（也未登记零散）——"
                     "卡片与主题注册表暂无关联可核对")
    if not declared and noise_only:
        warns.append("卡片锚点会话均为已登记零散会话——未被裁决进任何主题，"
                     "故无主题归属可核对")
    return errors, warns, {
        "declared_topic_id": declared or None,
        "topics": [{"id": hits[k]["id"], "name": hits[k]["name"]}
                   for k in sorted(hits)],
        "reviewed": True,
        "undeclared": undeclared,
        "noise_only": noise_only,
    }


def validate_cards(root: Path, db: Path | None = None,
                   con: sqlite3.Connection | None = None, *,
                   topics_meta: Path | None = None
                   ) -> tuple[list[dict], dict]:
    """校验目录下全部卡片。返回 (results, summary)。

    results 每项 {path, errors, warnings}；summary 含 ok/warn/error 计数与
    anchor_misses（锚点不在索引库的卡片数）。
    con：外部连接（同 _anchor_known 口径），锚点校验复用调用方连接。

    topics_meta（v0.43 additive，**缺省行为与不传完全一致**）：给了主题
    注册表路径时追加一层「卡 ↔ 主题」一致性核对（口径见本模块顶部
    `_topic_consistency`）。此时每张卡片的结果多带 `declared_topic_id` /
    `topics`（锚点命中的注册主题），summary 多带 `topic_*` 计数键。
    同时给了 db 时，注册成员的 `sessions.session_id` 写法也会被解析
    （见 `_add_sid_aliases`）；只给 topics_meta 不给 db 时**只有
    `sessions.sid` 精确形态可解析**，报告里的 `topic_alias_keys` 为 0。
    传了但库不可读 → 抛异常（fail loud，不退化成"没有主题"的假通过）。
    """
    membership: dict | None = None
    if topics_meta is not None:
        if db is not None and Path(topics_meta).resolve() == Path(db).resolve():
            raise ValueError(f"--topics-meta 不能与 --db 指向同一文件: "
                             f"{topics_meta}")
        membership = _load_topic_membership(Path(topics_meta), db)
    results: list[dict] = []
    n_ok = n_warn = n_err = 0
    anchor_miss = 0
    anchor_checked = 0
    evidence_checked = 0
    evidence_misses = 0
    placeholder_cards = 0
    evidence_unchecked = 0
    topic_anchors = 0
    topic_anchor_hits = 0
    topic_undeclared = 0
    topic_noise_anchors = 0
    for p in sorted(root.rglob("*.md")):
        if p.name.upper() in ("INDEX.md", "README.md"):
            continue
        errors, warns, fm = validate_card(p)
        had_evidence = bool(str((fm or {}).get("evidence") or "").strip())
        c = 0
        topic_info: dict | None = None
        if membership is not None and fm is not None:
            # 主题核对与索引库无关：没有 --db 时也要能核对注册表归属
            # （注册表自证成员资格），故独立于下面的 db/con 分支。
            terrs, twarns, topic_info = _topic_consistency(fm, membership)
            errors.extend(terrs)
            warns.extend(twarns)
            for sid in _anchor_sids(fm):
                topic_anchors += 1
                if membership["map"].get(sid):
                    topic_anchor_hits += 1
                elif sid in membership["noise"]:
                    topic_noise_anchors += 1
            if topic_info["undeclared"]:
                topic_undeclared += 1
        if fm and (db is not None or con is not None):
            anchors = fm.get("anchors")
            if isinstance(anchors, list):
                for a in anchors:
                    sid = a.get("session_id") if isinstance(a, dict) else None
                    if sid:
                        anchor_checked += 1
                        known = _anchor_known(str(sid), db, con=con)
                        if known is False:
                            anchor_miss += 1
                            errors.append(f"锚点 session_id={sid} 不在索引库")
                        elif known is None:
                            warns.append(f"锚点 {sid} 未能校验（无索引库）")
                # 引文核对（v0.19 additive）：evidence 必须出自锚点会话原文
                c, m = _evidence_warnings(fm, warns, db, con=con)
                evidence_checked += c
                evidence_misses += m
                # v0.21 P0-1（H11）：title == 锚点会话原标题 = 脚手架痕迹
                title = str(fm.get("title") or "").strip()
                if title and not any(e.startswith("[占位符]") for e in errors):
                    sids = [str(a.get("session_id")) for a in anchors
                            if isinstance(a, dict) and a.get("session_id")]
                    for sid in dict.fromkeys(sids):
                        st = _session_title(sid, db, con=con)
                        if st is not None and _norm_ws(st) == _norm_ws(title):
                            errors.append(
                                "[占位符] title 与锚点会话原标题相同"
                                "（cards new 脚手架痕迹，须改为描述性标题）")
                            break
        if had_evidence and c == 0:
            evidence_unchecked += 1
        rel = str(p.relative_to(root))
        entry = {"path": rel, "errors": errors, "warnings": warns}
        if topic_info is not None:
            entry["declared_topic_id"] = topic_info["declared_topic_id"]
            entry["topics"] = topic_info["topics"]
        results.append(entry)
        if any(e.startswith("[占位符]") for e in errors):
            placeholder_cards += 1
        if errors:
            n_err += 1
        elif warns:
            n_warn += 1
        else:
            n_ok += 1
    summary = {"cards": len(results), "ok": n_ok, "warn": n_warn,
               "error": n_err, "anchor_checked": anchor_checked,
               "anchor_misses": anchor_miss,
               "evidence_checked": evidence_checked,
               "evidence_misses": evidence_misses,
               "evidence_unchecked": evidence_unchecked,
               "placeholder_cards": placeholder_cards}
    if membership is not None:
        summary.update({
            "topic_reviewed": True,
            "topics": membership["topics"],
            "topic_member_rows": membership["member_rows"],
            "topic_alias_keys": membership["alias_keys"],
            "topic_anchors_checked": topic_anchors,
            "topic_anchor_hits": topic_anchor_hits,
            "topic_anchor_noise": topic_noise_anchors,
            "topic_undeclared_cards": topic_undeclared})
    return results, summary


def render_report(results: list[dict], summary: dict, root: Path) -> str:
    lines = [f"# 知识卡片校验报告（{root}）", ""]
    if not results:
        lines += ["目录下没有卡片。产出方式：", "",
                  "- suggest-agents 的建议池条目人工扩写后落卡；",
                  "- 或按 §8 规范手写（frontmatter: id/title/type/tags/"
                  "anchors/evidence/confidence/created）。", ""]
        return "\n".join(lines)
    lines += [
        f"- 卡片 {summary['cards']}：通过 {summary['ok']}｜警告 "
        f"{summary['warn']}｜错误 {summary['error']}",
        f"- 锚点校验 {summary['anchor_checked']} 个，"
        f"未命中索引库 {summary['anchor_misses']} 个",
        f"- 引文核对 {summary.get('evidence_checked', 0)} 行，"
        f"未命中原文 {summary.get('evidence_misses', 0)} 行", "",
    ]
    if summary.get("topic_reviewed"):
        lines += [
            f"- 主题核对（--topics-meta）：注册主题 "
            f"{summary.get('topics', 0)} 个／成员登记 "
            f"{summary.get('topic_member_rows', 0)} 行（另有 "
            f"{summary.get('topic_alias_keys', 0)} 个 session_id 别名键）；"
            f"锚点 {summary.get('topic_anchors_checked', 0)} 个中命中成员 "
            f"{summary.get('topic_anchor_hits', 0)} 个、命中零散登记 "
            f"{summary.get('topic_anchor_noise', 0)} 个；"
            f"未声明主题的卡片 {summary.get('topic_undeclared_cards', 0)} 张",
            "",
        ]
    else:
        lines += [
            "- 主题核对：**未执行**（未给 --topics-meta）——本次结论只覆盖 "
            "§8 规范与锚点，**未**核对「卡片 ↔ 主题注册表」一致性",
            "",
        ]
    for r in results:
        if r["errors"] or r["warnings"]:
            lines.append(f"## {r['path']}")
            for e in r["errors"]:
                lines.append(f"- [错误] {e}")
            for w in r["warnings"]:
                lines.append(f"- [警告] {w}")
            lines.append("")
    if summary.get("placeholder_cards"):
        lines.append("结论：存在未补全的脚手架占位卡"
                     f"（{summary['placeholder_cards']} 张），禁止并入主库。")
    elif summary["error"]:
        lines.append("结论：存在错误卡片（不满足 §8 冻结规范），"
                     "修复前不得并入主库。")
    elif summary.get("evidence_unchecked"):
        unchecked = summary["evidence_unchecked"]
        if summary["warn"]:
            lines.append(f"结论：{unchecked} 张卡片 evidence 未核对"
                         "（无 PyYAML 或原文不可得），不得视为通过；"
                         f"另有 {summary['warn']} 张带警告需复核。")
        else:
            lines.append(f"结论：{unchecked} 张卡片 evidence 未核对"
                         "（无 PyYAML 或原文不可得），不得视为通过。")
    elif summary["warn"]:
        lines.append(f"结论：无错误，但 {summary['warn']} 张卡片带警告"
                     "——复核警告项后再并入主库。")
    else:
        lines.append("结论：全部卡片满足 §8 规范，可并入主库。")
    return "\n".join(lines).rstrip() + "\n"


# ---- 卡片脚手架（cards new 子命令） ----

_TYPE_HEADINGS = {
    "pitfall": ("## 现象", "## 做法"),
    "workflow": ("## 适用场景", "## 步骤"),
    "insight": ("## 结论", "## 依据"),
}
_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def scaffold_card(db: Path, sid_query: str, out_dir: Path,
                  ctype: str = "pitfall", title: str | None = None,
                  turn: int | None = None,
                  dupe_warnings: list[str] | None = None) -> Path:
    """从索引库会话生成卡片脚手架（frontmatter 锚点真实、evidence 留白）。

    sid_query 匹配 sessions.sid 或 session_id。id 自动编号
    kc-YYYYMMDD-NNNN（扫描 out_dir 既有卡片取号）。 ctype 必须在
    REQUIRED_TYPES 内。
    dupe_warnings（P1-2 additive，可选）：传入 list 则收集跨卡查重警告——
    该会话错误步骤的 normalize_error 归一键（唯一权威键，禁第二把）在
    既有卡（排除本次生成卡）evidence/正文中命中时，警告"疑似已有卡"，
    口径与 triage 的"疑似已有卡"一致（宽松归一子串比对）。
    """
    if ctype not in REQUIRED_TYPES:
        raise ValueError(f"type={ctype!r} 不在 {sorted(REQUIRED_TYPES)}")
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT sid, session_id, title, created_at, source FROM sessions "
            "WHERE sid=? OR session_id=? ORDER BY CASE WHEN sid=? "
            "THEN 0 ELSE 1 END LIMIT 1", (sid_query, sid_query, sid_query)
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise KeyError(f"索引库中找不到会话: {sid_query}")
    session_id = row["session_id"] or row["sid"]
    title = title or row["title"] or "（补标题）"
    today = time.strftime("%Y%m%d")
    created = time.strftime("%Y-%m-%d")
    # id 自动编号：扫描既有卡片
    out_dir.mkdir(parents=True, exist_ok=True)
    max_n = 0
    for p in out_dir.rglob(f"kc-{today}-*.md"):
        m = re.search(rf"^kc-{today}-(\d+)", p.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    slug = _SLUG_RE.sub("-", title).strip("-").lower()[:28].strip("-") or "card"
    card_id = f"kc-{today}-{max_n + 1:04d}-{slug}"
    turn_part = str(turn) if turn is not None else ""
    anchors = (f"[{{session_id: \"{session_id}\","
               f" turn: {turn_part or 'null'}}}]")
    h1, h2 = _TYPE_HEADINGS[ctype]
    text = (
        "---\n"
        f"id: {card_id}\n"
        f"title: {title}\n"
        f"type: {ctype}\n"
        "tags: []\n"
        f"anchors: {anchors}\n"
        "evidence: |\n"
        "  <粘贴原会话证据原文，一行起，缩进两格>\n"
        "confidence: 0.5\n"
        f"created: {created}\n"
        "---\n"
        f"{h1}\n\n<待补：这条记录说明了什么问题/结论>\n\n"
        f"{h2}\n\n<待补：怎么做/依据是什么>\n\n"
        f"来源：`{row['sid']}#{turn or ''}`（{row['source']}），"
        f"由 `cards new` 脚手架生成，evidence 与正文人工补全后跑 "
        f"`cards validate`。\n"
    )
    path = out_dir / f"{card_id}.md"
    if dupe_warnings is not None:
        dupe_warnings.extend(_dupe_check(db, row["sid"], out_dir))
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _dupe_check(db: Path, sid: str, cards_root: Path) -> list[str]:
    """跨卡查重（P1-2，SOP 第 3 条）：本会话错误步骤经 normalize_error
    归一（唯一权威键）后与既有卡 evidence/正文做宽松子串比对（归一口径
    与 triage._norm_for_match 一致：lower + 折叠空白），命中即警告疑似
    已有卡。只告警不阻断——语义级判断仍留给人（triage 去重诚实声明）。"""
    from .errstats import normalize_error
    from .triage import _norm_for_match
    con = sqlite3.connect(str(db))
    try:
        errs = [r[0] for r in con.execute(
            "SELECT error FROM steps WHERE sid=? AND status='error' "
            "AND error IS NOT NULL", (sid,)).fetchall()]
    finally:
        con.close()
    keys = {_norm_for_match(normalize_error(e or "")) for e in errs}
    keys.discard("")
    if not keys or not Path(cards_root).is_dir():
        return []
    warnings: list[str] = []
    for p in sorted(Path(cards_root).rglob("*.md")):
        try:
            text = _norm_for_match(p.read_text(encoding="utf-8"))
        except OSError:
            continue
        for k in sorted(keys):
            if k in text:
                warnings.append(
                    f"疑似已有卡 {p.stem}（错误模式「{k}」已在其 "
                    f"evidence/正文命中；请先审阅旧卡，避免同根因重复做卡）")
                break
    return warnings
