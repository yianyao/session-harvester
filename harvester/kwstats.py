# -*- coding: utf-8 -*-
"""n-gram 关键词统计（keywords / report-keywords 子命令）——P2-2。

SOP §SOP-P2-2：n-gram（2/3-gram）词频 + 可选停用词；**只对
messages.raw 统计**（H3 契约第一个既有适用点；H38：raw 恒为原文，
text 列部分源为 bigram 不可用作统计）。落独立 meta 库
（keywords_meta.db：keyword_runs + keyword_stats 两表，保留 run
历史，端点读最新 run）+ 只读端点 /api/keywords（additive）。

通用性（用户红线）：不写死任何主题/会话/skill——统计范围由
--sid / --topic（读 topics 注册表 members 展开）决定，任何主题、
任何会话集合皆可用。

中文无分词器：对 raw 去除空白与标点后按连续"词字符段"
（CJK/字母/数字）滑窗切 n-gram；空白与标点是硬边界，不桥接。
停用词文件：每行一个词条，# 开头为注释。
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .dbmeta import db_fingerprint

_WORD = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]+")

SCHEMA = """
CREATE TABLE IF NOT EXISTS keyword_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  generated_at TEXT NOT NULL,
  params TEXT NOT NULL,
  db_fingerprint TEXT);
CREATE TABLE IF NOT EXISTS keyword_stats(
  run_id INTEGER NOT NULL REFERENCES keyword_runs(id),
  n INTEGER NOT NULL,
  gram TEXT NOT NULL,
  freq INTEGER NOT NULL,
  doc_freq INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(run_id, n, gram));
"""

#: keyword_stats 期望列——doc_freq 为 v0.23 additive（P2-2 排序口径修正）。
#: 旧 meta 库缺该列 → 读时显式报错要求重跑，不静默按 freq 排序（口径漂移更危险）。
_STATS_COLUMNS = ("run_id", "n", "gram", "freq", "doc_freq")


def extract_grams(text: str, n: int) -> list[str]:
    """单条文本的 n-gram：按词字符段滑窗，空白/标点为硬边界。"""
    out: list[str] = []
    for seg in _WORD.findall(text or ""):
        if len(seg) < n:
            continue
        for i in range(len(seg) - n + 1):
            out.append(seg[i:i + n])
    return out


def load_stopwords(path: Path | None) -> set[str]:
    if not path or not Path(path).is_file():
        return set()
    words: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.add(line)
    return words


def expand_topic_sids(topic_ids: list[str],
                      topics_meta: Path | None) -> list[str]:
    """从 topics_meta 注册表展开 --topic 的成员 sid（数据驱动）。"""
    if not topic_ids:
        return []
    if not topics_meta or not Path(topics_meta).is_file():
        raise SystemExit("--topic 需要 --topics-meta 指向注册库")
    con = sqlite3.connect(f"file:{Path(topics_meta)}?mode=ro", uri=True)
    try:
        sids: list[str] = []
        for tid in topic_ids:
            row = con.execute("SELECT members FROM topics WHERE id=?",
                              (tid,)).fetchone()
            if row is None:
                raise SystemExit(f"topic 不存在: {tid}")
            members = json.loads(row[0]) if row[0] else []
            sids += [m["sid"] if isinstance(m, dict) else m
                     for m in members]
        return sids
    finally:
        con.close()


def build_stats(db: Path, meta: Path, ns: list[int] | None = None,
                role: str = "user", sids: list[str] | None = None,
                topic_ids: list[str] | None = None,
                topics_meta: Path | None = None,
                stopwords_path: Path | None = None) -> dict:
    """统计 messages.raw 的 n-gram 词频并写入 meta 库（新 run）。

    返回 summary（含 rows=全部 gram 按 freq 降序），供报告与端点消费。
    """
    ns = sorted({int(x) for x in (ns or [2, 3])})
    stop = load_stopwords(stopwords_path)
    scope = list(sids or []) + expand_topic_sids(topic_ids or [],
                                                 topics_meta)
    scope = list(dict.fromkeys(scope))  # 去重保序

    con = sqlite3.connect(f"file:{Path(db)}?mode=ro", uri=True)
    try:
        sql = "SELECT raw FROM messages WHERE raw IS NOT NULL"
        args: list = []
        if role != "all":
            sql += " AND role=?"
            args.append(role)
        if scope:
            sql += f" AND sid IN ({','.join('?' * len(scope))})"
            args += scope
        counters = {n: Counter() for n in ns}    # 词频：gram 在该条文本内的出现次数
        doccounters = {n: Counter() for n in ns}  # 文档频：含该 gram 的文本条数
        total_msgs = 0
        for (raw,) in con.execute(sql, args):
            total_msgs += 1
            for n in ns:
                grams = [g for g in extract_grams(raw, n) if g not in stop]
                if not grams:
                    continue
                counters[n].update(grams)
                # 单条文本内同一 gram 只记一次（doc_freq 的语义）
                doccounters[n].update(set(grams))
    finally:
        con.close()

    try:
        fp = db_fingerprint(Path(db), con=None)
    except sqlite3.Error:
        fp = None  # 指纹失败不阻断统计（对齐 _emit_report 容错口径）
    fp_json = json.dumps(fp, ensure_ascii=False) if fp else None
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    params = {"ns": ns, "role": role, "sids_used": len(scope),
              "topic_ids": list(topic_ids or []),
              "stopwords": len(stop), "scope": scope}
    meta = Path(meta)
    meta.parent.mkdir(parents=True, exist_ok=True)
    mcon = sqlite3.connect(meta)
    try:
        # 旧 schema 迁移（v0.23）：CREATE IF NOT EXISTS 不会给已存在的表补列，
        # 故先探列；缺 doc_freq 则重建该表（run 历史同表，一并重置——统计
        # 产物可重生成，口径正确优先于保留旧 run）。
        try:
            existing = {r[1] for r in mcon.execute(
                "PRAGMA table_info(keyword_stats)")}
        except sqlite3.DatabaseError:
            existing = set()
        if existing and not set(_STATS_COLUMNS) <= existing:
            mcon.execute("DROP TABLE keyword_stats")
        mcon.executescript(SCHEMA)
        cur = mcon.execute(
            "INSERT INTO keyword_runs(generated_at, params, db_fingerprint)"
            " VALUES (?,?,?)",
            (now, json.dumps(params, ensure_ascii=False), fp_json))
        run_id = cur.lastrowid
        for n in ns:
            # 排序口径：doc_freq 优先（"在多少条会话文本里被提到"），freq 次之
            # （提得多）。旧实现按 freq 排 → 长会话里反复出现的人名压过真主题词。
            ordered = sorted(counters[n].items(),
                             key=lambda kv: (-doccounters[n][kv[0]], -kv[1], kv[0]))
            mcon.executemany(
                "INSERT OR REPLACE INTO keyword_stats"
                "(run_id, n, gram, freq, doc_freq) VALUES (?,?,?,?,?)",
                [(run_id, n, g, f, doccounters[n][g]) for g, f in ordered])
        mcon.commit()
    finally:
        mcon.close()

    rows = [{"n": n, "gram": g, "freq": f, "doc_freq": doccounters[n][g]}
            for n in ns for g, f in sorted(
                counters[n].items(),
                key=lambda kv: (-doccounters[n][kv[0]], -kv[1], kv[0]))]
    return {"generated_at": now, "db_fingerprint": fp, "params": params,
            "total_msgs": total_msgs, "rows": rows}


def load_stats(meta: Path | None, n: int = 2, limit: int = 50) -> dict:
    """读最新 run 的指定 n 词频（端点 / 高频词捞取消费）。"""
    if not meta or not Path(meta).is_file():
        return {"rows": [], "hint": "keywords_meta 未生成"
                                     "（先跑 harvester keywords）"}
    meta = Path(meta)
    con = sqlite3.connect(f"file:{meta}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"keyword_runs", "keyword_stats"} <= tables:
            return {"rows": [], "hint": "keywords_meta 缺表（重新生成）"}
        cols = {r[1] for r in con.execute("PRAGMA table_info(keyword_stats)")}
        if not set(_STATS_COLUMNS) <= cols:
            missing = sorted(set(_STATS_COLUMNS) - cols)
            return {"rows": [],
                    "hint": f"keywords_meta 为旧 schema（缺 {','.join(missing)}）"
                            "——请重跑 harvester keywords 重建"}
        run = con.execute(
            "SELECT id, generated_at, params, db_fingerprint FROM "
            "keyword_runs ORDER BY id DESC LIMIT 1").fetchone()
        if run is None:
            return {"rows": [], "hint": "keywords_meta 无 run 记录"}
        rows = [{"gram": r["gram"], "freq": r["freq"], "doc_freq": r["doc_freq"]}
                for r in con.execute(
                    "SELECT gram, freq, doc_freq FROM keyword_stats "
                    "WHERE run_id=? AND n=? "
                    "ORDER BY doc_freq DESC, freq DESC, gram LIMIT ?",
                    (run["id"], int(n), int(limit)))]
        return {"run": {"id": run["id"], "generated_at": run["generated_at"],
                        "params": json.loads(run["params"]),
                        "db_fingerprint": run["db_fingerprint"]},
                "n": int(n), "limit": int(limit), "rows": rows}
    finally:
        con.close()


def render_report(summary: dict, top: int = 50) -> str:
    """统计结果的 markdown 报告（口径声明在头：raw 列，H3/H38）。"""
    p = summary["params"]
    ns = p["ns"]
    lines = [
        "# n-gram 关键词统计（report-keywords，P2-2）",
        "",
        f"- 口径：**只统计 messages.raw**（H3 契约；H38：raw 恒为原文）｜"
        f"role={p['role']}｜范围 {p['sids_used']} 个会话"
        + (f"（--topic {','.join(p['topic_ids'])}）" if p["topic_ids"] else "")
        + f"｜停用词 {p['stopwords']} 条｜消息 {summary['total_msgs']} 条",
        f"- n-gram 档位：{ns}｜生成 {summary['generated_at']}",
        "",
    ]
    for n in ns:
        rows = [r for r in summary["rows"] if r["n"] == n][:top]
        lines += [f"## {n}-gram Top {len(rows)}", "",
                  "| gram | doc_freq | freq |", "|---|---:|---:|"]
        lines += [f"| {r['gram']} | {r.get('doc_freq', 0)} | {r['freq']} |"
                  for r in rows]
        lines.append("")
    lines += [
        "",
        "- 排序口径（v0.23）：**doc_freq 优先**——doc_freq = 含该 gram 的消息条数"
        "（\"在多少条会话文本里被提到\"），freq = 出现总次数。",
        "- 旧实现按 freq 排序：长会话里反复出现的人名（如主角名）会压过真正的"
        "主题词；现 rank 用 doc_freq。人名/专名仍建议写入停用词文件（--stopwords）。",
    ]
    return "\n".join(lines)
