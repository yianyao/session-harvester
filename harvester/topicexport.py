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
import re
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
                          "<chain>.md --db harvester.db --meta topics_meta.db",
        "note": f"主题「{name}」；pack 按档位给蒸馏包，fine 按成员会话下钻",
    }


def render_topic_json(bundle: dict) -> str:
    """稳定序列化：键序固定（sort_keys），UTF-8 不转义，供 Agent 解析。"""
    return json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


# ── 人读那一半（v0.33）：一页速览 ──────────────────────────────────────
_H2 = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.M)

#: 从 chain 正文里取这两节。取不到就**明说没生成**，不用成员标题凑内容
_SECTION_ALIASES = {
    "结论": ("元结论", "结论"),
    "未决": ("待补", "限制", "未决"),
}

#: 单节引用上限（一页要能读完；超了指向 chain 全文）
SECTION_CAP = 1200


def _h2_sections(body: str) -> list[tuple[str, str]]:
    """按二级标题切 chain 正文 → [(标题, 正文)]。"""
    marks = list(_H2.finditer(body or ""))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(marks):
        if len(m.group(1)) != 2:
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        out.append((m.group(2), body[m.end():end].strip()))
    return out


def _pick_section(sections: list[tuple[str, str]],
                  prefixes: tuple[str, ...]) -> tuple[str, str] | None:
    for title, text in sections:
        if any(title.startswith(p) for p in prefixes):
            return title, text
    return None


def _bar(v: int, mx: int, width: int = 20) -> str:
    return "█" * max(1, round(v / mx * width)) if mx else ""


def render_topic_md(bundle: dict, chain_root: Path | None = None) -> str:
    """把主题渲染成**一页**给人读的速览。

    与 `render_topic_json` 的分工：JSON 要给机器（含逐条成员，可能上千行）；
    这一页只回答四件事——**是什么 / 跨多久 / 关键转折 / 结论与未决**，
    **不列成员**（列了就不是一页了）。

    「关键转折 / 结论 / 未决」只从**已发布 chain** 来：
      - 没传 `chain_root` → 说明"没去读"（未执行）；
      - 传了但没有该主题的链 → 说明"确实还没有"（不适用）。
    两者混为一谈会让人以为主题没内容，故在页面上分开写。
    """
    t, act, h = bundle["topic"], bundle["activity"], bundle["health"]
    L = [f"# {t['name']}", "",
         f"> 主题 `{t['id']}` 一页速览（人读）；机器读那一半："
         f"`python -m harvester topic export --id {t['id']}`",
         f"> 生成 {bundle['generated_at']}；库指纹 `{bundle['db_fingerprint']}`", "",
         "## 是什么", "",
         f"- 关键词：{'、'.join(t['keywords']) or '（无）'}",
         f"- 成员：**{t['members_count']}** 个会话；注册于 {t['created']}"]
    if bundle["sources"]:
        L.append("- 来源：" + "、".join(
            f"{k} {v}" for k, v in bundle["sources"].items()))
    missing = h["members_not_in_index"]
    L.append(f"- 健康：不在索引库 **{len(missing)}** 个"
             + (f"（如 {missing[0]}）" if missing else "（无）"))
    if h["noise_registered_members"]:
        L.append(f"- ⚠ 被登记为零散的成员 **{len(h['noise_registered_members'])}** 个"
                 "（应为 0：既入主题又登记零散是自相矛盾）")
    L.append("")

    L += ["## 跨多久", ""]
    if act["first"]:
        months = act["months"]
        mx = max(months.values()) if months else 0
        L.append(f"- {act['first'][:10]} ~ {act['last'][:10]}"
                 f"，覆盖 **{len(months)}** 个月")
        recent = sorted(months.items())[-12:]
        if len(months) > 12:
            L.append(f"- 最近 12 个月（另有更早 {len(months) - 12} 个月）")
        for k, v in recent:
            L.append(f"  - {k} {_bar(v, mx)} {v}")
    else:
        L.append("- **无法判定**：成员都没有 `created_at`")
    L.append("")

    chains = bundle.get("chains") or []
    L += ["## 关键转折", ""]
    if not chains:
        if chain_root is None:
            L.append("- **未执行**：本次没传 `--chain-root`，没去读已发布的 chain"
                     "（不等于「该主题没有链」）")
        else:
            L.append("- **尚未生成**：该主题还没有已发布的 chain"
                     f"（已查 `{chain_root}`）")
        L += [f"- 生成入口：`{bundle['howto']['pack_coarse']}` → 写链 → "
              f"`{bundle['howto']['validate_chain']}`", ""]
    else:
        anchors = bundle.get("anchors") or []
        for c in chains:
            L.append(f"### {c['name']}")
            L.append(f"- {c['stages']} 个阶段 / {c['nodes']} 个锚点节点"
                     + (f"；prompt `{c['prompt_version']}`"
                        if c.get("prompt_version") else ""))
            mine = [a for a in anchors if a["chain"] == c["name"]]
            for a in mine:
                L.append(f"  - {a['span']} ｜ {a['stage']}"
                         f"（锚点 {len(a['nodes'])}）")
            if not mine:
                L.append("  - （该链 frontmatter 无 anchors）")
            L.append("")

    for label in ("结论", "未决"):
        L += [f"## {label}", ""]
        found_any = False
        for c in chains:
            try:
                body = Path(c["path"]).read_text(encoding="utf-8",
                                                 errors="replace")
            except OSError:
                continue
            got = _pick_section(_h2_sections(body), _SECTION_ALIASES[label])
            if not got:
                continue
            found_any = True
            title, text = got
            if len(text) > SECTION_CAP:
                text = text[:SECTION_CAP].rstrip() + \
                    f"\n\n…（截断；全文见 `{c['path']}`）"
            L += [f"### 来自《{c['name']}》的「{title}」", "", text, ""]
        if not found_any:
            why = ("未传 `--chain-root`（未执行）" if chain_root is None
                   else "该主题的 chain 正文里没有对应小节（不适用）")
            if chains and chain_root is not None:
                why = "已发布的 chain 正文里没有取到对应小节（不适用）"
            L += [f"- 未生成：{why}。**不用成员标题凑内容**——人读那一页宁可空着。",
                  ""]

    L += ["## 下一步", ""]
    for k, v in bundle["howto"].items():
        L.append(f"- {v}" if k == "note" else f"- `{v}`")
    L += ["", "## 口径与限制", ""]
    L += [f"- {x}" for x in bundle["limits"]]
    return "\n".join(L) + "\n"
