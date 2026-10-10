# -*- coding: utf-8 -*-
"""主题结构化导出（v0.29）——给 **Agent/机器** 消费的 topic.json。

定位：主题现在只有一种产物 chain（叙事复盘，给人读的散文）。机器要"拿一个主题
去干活"，需要的是**结构稳定、字段显式、可直接解析**的一份东西：

    harvester.topic/1
      topic     它是什么（id/名称/关键词/成员数/创建时间）
      activity  时间跨度与月度分布（这个主题什么时候活跃）
      members   成员会话逐条（sid/来源/标题/时间/证据尾注）—— Agent 据此下钻
      chains    该主题的思维链清单（一个主题可以有多条）
      anchors   各链的锚点合并视图（stage/span/chain/nodes{sid,turn,note}）
      sources   成员按来源的分布（跨平台证据）
      health    自检：被登记为零散的成员数应为 0
      howto     下一步可复制命令（pack/fine/chain-validate）—— 只给命令，不执行
      limits    已知限制（coarse 截断、artifact 无数据等口径）

只读：索引库与 meta 库都按 `mode=ro` 打开，不写任何库。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .dbmeta import db_fingerprint
from .topics import show_topic

SCHEMA = "harvester.topic/1"


def _load_chains(chain_root: Path | None, topic_id: str) -> list[dict]:
    if not chain_root or not Path(chain_root).is_dir():
        return []
    from .topicchain import load_chain
    out: list[dict] = []
    for p in sorted(Path(chain_root).glob("chain-*.md")):
        try:
            d = load_chain(p)
        except (ValueError, RuntimeError, OSError):
            continue
        fm = d["fm"]
        if not isinstance(fm, dict) or fm.get("topic_id") != topic_id:
            continue
        import re
        m = re.search(r"^#\s+(.+)$", d["body"], re.M)
        anchors = fm.get("anchors") or []
        out.append({
            "name": (m.group(1).strip() if m else None) or fm.get("topic")
                    or p.stem,
            "path": str(p),
            "stages": len(anchors),
            "nodes": sum(len(s.get("nodes") or []) for s in anchors
                         if isinstance(s, dict)),
            "prompt_version": fm.get("prompt_version"),
            "generated_from": fm.get("generated_from"),
            "_anchors": anchors,
        })
    return out


def topic_bundle(meta_path: Path, db_path: Path, topic_id: str,
                 chain_root: Path | None = None,
                 noise_sids: set[str] | None = None,
                 generated_at: str | None = None) -> dict:
    """把主题导出成 Agent 可消费的结构化包。主题不存在 → KeyError。"""
    meta_path, db_path = Path(meta_path), Path(db_path)
    topic = show_topic(meta_path, topic_id)
    noise = set(noise_sids or ())

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        members, sources, months = [], {}, {}
        for m in topic["members"]:
            sid = m["sid"]
            r = con.execute(
                "SELECT sid, source, session_id, title, created_at, "
                "updated_at FROM sessions WHERE sid=? OR session_id=? "
                "ORDER BY CASE WHEN sid=? THEN 0 ELSE 1 END LIMIT 1",
                (sid, sid, sid)).fetchone()
            if r is None:
                members.append({"sid": sid, "source": None, "title": None,
                                "created_at": None, "updated_at": None,
                                "evidence": m.get("evidence") or "",
                                "in_index": False})
                continue
            month = (r["created_at"] or "")[:7]
            if month:
                months[month] = months.get(month, 0) + 1
            sources[r["source"]] = sources.get(r["source"], 0) + 1
            members.append({
                "sid": r["sid"], "source": r["source"],
                "session_id": r["session_id"], "title": r["title"] or "",
                "created_at": r["created_at"] or "",
                "updated_at": r["updated_at"] or "",
                "evidence": m.get("evidence") or "", "in_index": True})
        fp = db_fingerprint(db_path, con=con)
    finally:
        con.close()

    dated = [m["created_at"] for m in members if m["created_at"]]
    # 排序：**已入索引库的在前**（按时间），不在索引库的（数据健康问题）排后
    # —— Agent 先看到能用的会话，末尾才是需要人处理的异常成员
    members.sort(key=lambda m: (not m["in_index"], m["created_at"] or "",
                               m["sid"]))
    chains = _load_chains(chain_root, topic_id)
    anchors = []
    for c in chains:
        for st in c.pop("_anchors", []):
            if not isinstance(st, dict):
                continue
            anchors.append({"chain": c["name"], "stage": st.get("stage"),
                            "span": st.get("span"),
                            "nodes": st.get("nodes") or []})
    missing = [m["sid"] for m in members if not m["in_index"]]

    return {
        "schema": SCHEMA,
        "generated_at": generated_at or _now(),
        "db_fingerprint": fp,
        "topic": {"id": topic["id"], "name": topic["name"],
                  "keywords": topic["keywords"], "created": topic["created"],
                  "members_count": len(members)},
        "activity": {"first": min(dated) if dated else None,
                     "last": max(dated) if dated else None,
                     "months": dict(sorted(months.items()))},
        "sources": dict(sorted(sources.items(), key=lambda kv: -kv[1])),
        "members": members,
        "chains": [{k: v for k, v in c.items()} for c in chains],
        "anchors": anchors,
        "health": {"members_not_in_index": missing,
                   "noise_registered_members":
                       sorted({m["sid"] for m in members} & noise)},
        "howto": _howto(topic["id"], topic["name"]),
        "limits": [
            "members 里 created_at 取自索引库 sessions；导出源可能无 created_at",
            "anchors 来自已发布 chain 的 frontmatter，未生成 chain 的主题为 []",
            "topic pack --level coarse 按预算截断（H27/H37），大主题需 "
            "--max-chars 放大或先按 --sid 下钻",
            "db_fingerprint 标注库快照：数据变了与逻辑变了要能分开归因",
        ],
    }


def _now() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _howto(topic_id: str, name: str) -> dict:
    """给 Agent 的可复制命令（**只给文本，任何地方都不执行**）。"""
    common = f"--id {topic_id} --meta topics_meta.db --db harvester.db"
    return {
        "pack_coarse": f"python -m harvester topic pack {common} --level coarse "
                       f'--out docs/reports/pack-{topic_id}.md',
        "pack_mid": f"python -m harvester topic pack {common} --level mid "
                    f"--out docs/reports/pack-{topic_id}-mid.md",
        "fine_one_session": "python -m harvester topic chain "
                            f"{common} --level fine --sid <成员sid>",
        "validate_chain": "python -m harvester chain-validate "
                          f"<chain>.md --db harvester.db --meta topics_meta.db",
        "note": f"主题「{name}」；pack 按档位给蒸馏包，fine 按成员会话下钻",
    }


def render_topic_json(bundle: dict) -> str:
    """稳定序列化：键序固定（sort_keys），UTF-8 不转义，供 Agent 解析。"""
    return json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
