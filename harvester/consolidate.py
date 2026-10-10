# -*- coding: utf-8 -*-
"""主题梳理流水线（v0.26）——把"语义聚合 + 剔除零散"变成可重复的固定一环。

设计（沿用本项目"确定性一半 / Agent 一半"的分工，tools 不写死主题）：

    采集（update/sync） → topic-candidates（词面候选）
      ↓
    **topic-consolidate --plan-out**：产「梳理包」——现有主题信号表 +
      零散会话候选（按显式规则，非语义判断）+ 可直接填的执行模板
      ↓
    Agent/人 填空：哪些并成一个主题、哪些舍弃、零散会话登记
      ↓
    **topic-consolidate --apply**：确定性执行（完整性校验 + 文件级原子 + 快照）

两条红线：
- **采集库只读**：零散会话只登记进 meta 库（`sessions_noise` 表），不删任何会话；
- **语义判断不落进代码**：工具只按"显式规则"缩小候选范围，并集/改名/舍弃的
  归组一律来自人填的 plan。

文件级原子：apply 全程操作 meta 库的**临时副本**，全部成功才 `os.replace`
覆盖真文件；中途任何异常 → 真文件逐字节未变。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

from .topics import (add_members, delete_topic, ensure_topics_db,
                     list_topics, merge_topics, register_topic, rename_topic,
                     show_topic)

PLAN_VERSION = 1

#: 「零散会话候选」的显式规则（工具只做筛，不做判断；**三条必须同时成立**）
NOISE_RULES = (
    ("单轮短问答", "user 回合数 = 1 且首条 user 正文 < 阈值字符"),
    ("无主题归属", "不属于任何已注册主题的成员"),
    ("无工具步", "steps 表里该会话 0 步（纯聊天，没驱动过工具）"),
)

#: 候选阈值默认值（字符）。用户 2026-10-10 裁决"只登记最窄的一批" → 用 --noise-max-chars 调小
NOISE_MAX_CHARS = 40

_NOISE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions_noise (
    sid     TEXT PRIMARY KEY,
    reason  TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL
);
"""


# ── 零散会话登记（meta 库，不碰采集库） ────────────────────────────────
def ensure_noise_table(meta_path: Path) -> Path:
    ensure_topics_db(meta_path)
    con = sqlite3.connect(str(meta_path))
    try:
        con.executescript(_NOISE_SCHEMA)
        con.commit()
    finally:
        con.close()
    return Path(meta_path)


def register_noise(meta_path: Path, rows: list[dict]) -> int:
    """登记零散会话（sid + reason，幂等）。返回新增条数。"""
    ensure_noise_table(meta_path)
    con = sqlite3.connect(str(meta_path))
    try:
        n = 0
        for r in rows:
            cur = con.execute(
                "INSERT OR IGNORE INTO sessions_noise (sid, reason, created) "
                "VALUES (?,?,?)",
                (r["sid"], r.get("reason", ""), time.strftime("%Y-%m-%d %H:%M")))
            n += cur.rowcount
        con.commit()
        return n
    finally:
        con.close()


def list_noise(meta_path: Path, create: bool = False) -> list[dict]:
    """读零散登记。缺表时返回空列表（除非 create=True 才建表）。

    默认**只读**：消费方（candidates/keywords）持有的是只读连接，
    不能因为读一下就顺手建表。
    """
    if not Path(meta_path).is_file():
        return []
    if create:
        ensure_noise_table(meta_path)
    con = sqlite3.connect(f"file:{Path(meta_path)}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        has = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND "
            "name='sessions_noise'").fetchone()
        if not has:
            return []
        return [dict(r) for r in con.execute(
            "SELECT sid, reason, created FROM sessions_noise ORDER BY sid")]
    finally:
        con.close()


def noise_sids(meta_path: Path | None) -> set[str]:
    """零散会话 sid 集合（消费方过滤用；未配置/缺表 → 空集，行为不变）。"""
    if not meta_path:
        return set()
    return {r["sid"] for r in list_noise(Path(meta_path))}


# ── 梳理包（确定性一半） ───────────────────────────────────────────────
def build_plan_packet(meta_path: Path, db_path: Path | None = None,
                      chain_root: Path | None = None,
                      member_preview: int = 3,
                      noise_max_chars: int = NOISE_MAX_CHARS) -> str:
    """产「梳理包」：主题信号表 + 零散会话候选 + 待填的执行模板。

    信号全部可核对：成员数、关键词、前 N 个成员标题、活跃区间、已有 chain 数。
    """
    topics = list_topics(meta_path)
    chains: dict[str, list[str]] = {}
    if chain_root and Path(chain_root).is_dir():
        import re
        for p in sorted(Path(chain_root).glob("chain-*.md")):
            txt = p.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^topic_id:\s*(\S+)\s*$", txt, re.M)
            h1 = re.search(r"^#\s+(.+)$", txt, re.M)
            if m:
                chains.setdefault(m.group(1), []).append(
                    h1.group(1).strip() if h1 else p.stem)

    lines = ["# 主题梳理包（topic-consolidate --plan-out）", "",
             f"- 现有主题 **{len(topics)}** 个；",
             "- 你要做的：在下方「执行模板」里填 归组 / 舍弃 / 零散会话，",
             "  然后 `topic-consolidate --apply <plan.yaml>`（缺省 dry-run）。",
             "- 口径：语义归组由你判断，工具**只做确定性并集**，不写死主题。", ""]

    known: set[str] = set()
    mcon = sqlite3.connect(f"file:{Path(meta_path)}?mode=ro", uri=True)
    mcon.row_factory = sqlite3.Row
    try:
        for r in mcon.execute("SELECT members FROM topics"):
            known |= {m["sid"] for m in json.loads(r["members"])}
    finally:
        mcon.close()

    rows: list[dict] = []
    titles_of: dict[str, list[str]] = {}
    noise_rows: list[dict] = []
    con = None
    if db_path and Path(db_path).is_file():
        con = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
    for t in topics:
        d = show_topic(meta_path, t["id"])
        sids = [m["sid"] for m in d["members"]]
        rows.append({"id": t["id"], "n": len(sids),
                     "kw": "、".join(t["keywords"][:3])[:24],
                     "chains": len(chains.get(t["id"], [])),
                     "name": t["name"][:28]})
        titles_of[t["id"]] = []
        if con is not None:
            for sid in sids[:member_preview]:
                r = con.execute("SELECT title FROM sessions WHERE sid=? OR "
                                "session_id=? LIMIT 1", (sid, sid)).fetchone()
                if r:
                    titles_of[t["id"]].append((r["title"] or "")[:26])

    lines += ["## 主题信号表", "",
              "| id | 成员 | 关键词 | 已有链 | 名称 | 成员标题（前 "
              f"{member_preview}） |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['n']} | {r['kw']} | {r['chains']} | "
                     f"{r['name']} | {' / '.join(titles_of[r['id']])} |")

    if con is not None:
        # 零散会话候选：**三条规则同时成立**才算候选（规则名见 NOISE_RULES）
        #   ① 单轮短问答：user 回合 = 1 且首条正文 < 阈值
        #   ② 无主题归属：不是任何已注册主题的成员
        #   ③ 无工具步：steps 0 步（纯聊天，没驱动过任何工具/文件操作）
        # 只上前两条会把"短提问但真干了活"的会话误判成噪声（实测：
        # "Skill编写规范提炼""检查skill并提出建议"都只 11~13 字符，却是真工作）
        for r in con.execute(
                "SELECT s.sid, s.title, s.created_at, COUNT(m.rowid) n, "
                "(SELECT raw FROM messages WHERE sid=s.sid AND role='user' "
                " ORDER BY rowid LIMIT 1) fu, "
                "(SELECT COUNT(*) FROM steps WHERE sid=s.sid) nstep "
                "FROM sessions s JOIN messages m ON m.sid=s.sid "
                "WHERE m.role='user' GROUP BY s.sid"):
            fu = (r["fu"] or "").strip()
            if r["n"] == 1 and len(fu) < noise_max_chars \
                    and r["sid"] not in known and r["nstep"] == 0:
                noise_rows.append({
                    "sid": r["sid"],
                    "reason": f"单轮短问答（{len(fu)} 字符 < {noise_max_chars}）"
                              f"·无主题归属·零工具步",
                    "title": (r["title"] or "")[:40],
                    "created_at": (r["created_at"] or "")[:10]})
        con.close()

    lines += ["", f"## 零散会话候选（{len(noise_rows)} 个，规则显式、供你筛）",
              "",
              f"规则（**三条同时成立**）：user 回合 = 1 且首条正文 < "
              f"{noise_max_chars} 字符；不属于任何主题；**steps 0 步**"
              f"（纯聊天，没驱动过工具）。",
              "登记只写 meta 库（`sessions_noise`），**采集库只读、不删会话**。",
              f"阈值可用 `--noise-max-chars` 调（当前 {noise_max_chars}）。",
              "⚠ 只上「单轮短问答」会把**短提问但真干了活**的会话误判成噪声"
              "（实测「Skill编写规范提炼」11 字符却是真工作）——所以必须有零工具步"
              "这一条。", ""]
    for r in noise_rows[:80]:
        lines.append(f"- `{r['sid']}` （{r['created_at']}）{r['title']}")
    if len(noise_rows) > 80:
        lines.append(f"- …另有 {len(noise_rows) - 80} 个（用 --noise-out 落全量）")

    lines += ["", "## 执行模板（复制成 plan.yaml 后填空）", "",
              "```yaml", "version: 1",
              "# 新建类目（key 供 groups.target 引用）", "new_topics: []",
              "#  - key: T1",
              "#    name: 类目名",
              "#    keywords: [k1, k2]",
              "renames: []", "#  - {id: tp-xxx, name: 新名}",
              "groups: []",
              "#  - target: tp-xxx        # 或 new: T1",
              "#    from: [tp-a, tp-b]",
              "discard: []", "#  - {id: tp-yyy, reason: 单点问答}",
              "noise: []",  "#  - {sid: 'qianwen-raw:xxx', reason: 单轮查词}",
              "keep: []",   "#  - {id: tp-zzz, reason: 待定，本轮不动}",
              "```", "",
              "**完整性要求**：库内每个主题都必须出现在 target/from/discard/"
              "keep 之一，否则 apply 拒绝执行（防漏网）。"]
    return "\n".join(lines) + "\n"


# ── plan 读取与校验 ────────────────────────────────────────────────────
def load_plan(path: Path) -> dict:
    import yaml
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(d, dict):
        raise ValueError("plan 必须是 mapping（见梳理包的执行模板）")
    return d


def validate_plan(plan: dict, have_ids: set[str]) -> list[str]:
    """返回错误清单（空 = 可执行）。完整性 + 冲突检查，全部 fail loud。"""
    errs: list[str] = []
    if int(plan.get("version") or 0) != PLAN_VERSION:
        errs.append(f"plan.version 必须是 {PLAN_VERSION}")

    new = plan.get("new_topics") or []
    new_keys = [n.get("key") for n in new]
    if len(new_keys) != len(set(new_keys)):
        errs.append("new_topics 的 key 有重复")
    for n in new:
        if not n.get("key") or not n.get("name"):
            errs.append(f"new_topics 条目缺 key/name: {n}")

    renames = plan.get("renames") or []
    for r in renames:
        if r.get("id") not in have_ids:
            errs.append(f"renames 引用了不存在的主题: {r.get('id')}")
        if not (r.get("name") or "").strip():
            errs.append(f"renames 条目缺 name: {r}")

    if len({r.get("id") for r in renames}) != len(renames):
        errs.append("renames 有重复 id")

    targets: list[str] = []
    sources: list[str] = []
    for g in plan.get("groups") or []:
        tgt = g.get("target")
        if not tgt and g.get("new"):
            if g["new"] not in new_keys:
                errs.append(f"groups.target 指向未定义的 new key: {g['new']}")
        elif tgt:
            if tgt not in have_ids:
                errs.append(f"groups.target 不存在: {tgt}")
            targets.append(tgt)
        else:
            errs.append(f"groups 条目既无 target 也无 new: {g}")
        for s in g.get("from") or []:
            if s not in have_ids:
                errs.append(f"groups.from 不存在: {s}")
            sources.append(s)

    discards = [d.get("id") for d in (plan.get("discard") or [])]
    keeps = [k.get("id") for k in (plan.get("keep") or [])]
    for i in discards + keeps:
        if i not in have_ids:
            errs.append(f"discard/keep 引用了不存在的主题: {i}")

    # 只改名（不参与任何并组）的主题同样是"已归位"，必须计入覆盖面
    renamed = [r.get("id") for r in renames if r.get("id") in have_ids]
    covered = set(targets) | set(sources) | set(discards) | set(keeps) \
        | set(renamed)
    total = (len(targets) + len(sources) + len(discards) + len(keeps)
             + len(renamed))
    if len(covered) != total:
        errs.append("同一个主题被同时归入多条（target/from/discard/keep/"
                    "renames 有重叠）")
    missing = sorted(have_ids - covered)
    if missing:
        errs.append(f"未归置的主题 {len(missing)} 个（必须逐一归位）: "
                    + ", ".join(missing[:10])
                    + (" …" if len(missing) > 10 else ""))

    noise = plan.get("noise") or []
    if not isinstance(noise, list):
        errs.append("noise 必须是列表")
    for n in noise:
        if not n.get("sid"):
            errs.append(f"noise 条目缺 sid: {n}")
    if len({n.get("sid") for n in noise}) != len(noise):
        errs.append("noise 有重复 sid")
    return errs


# ── 执行（文件级原子） ─────────────────────────────────────────────────
def apply_plan(meta_path: Path, plan: dict, dry_run: bool = True,
               snap_dir: Path | None = None,
               backup_dir: Path | None = None) -> dict:
    """执行梳理计划。dry_run=True 只校验并打印。

    原子性做法：全程在 meta 库的**临时副本**上操作，全部成功才
    `os.replace` 覆盖真文件 —— 中途异常时真文件逐字节未变。
    """
    meta_path = Path(meta_path)
    have = {t["id"] for t in list_topics(meta_path)}
    errs = validate_plan(plan, have)
    if errs:
        return {"ok": False, "errors": errs, "applied": False}

    if dry_run:
        return {"ok": True, "errors": [], "applied": False,
                "preview": _preview(plan, have)}

    backup_dir = Path(backup_dir) if backup_dir else meta_path.parent
    bak = backup_dir / (meta_path.name + time.strftime(
        "-bak-%Y%m%d-%H%M%S-pre-apply"))
    shutil.copy2(meta_path, bak)
    tmp = meta_path.with_name(meta_path.name + ".tmp-apply")
    shutil.copy2(meta_path, tmp)

    created: dict[str, str] = {}
    try:
        for n in plan.get("new_topics") or []:
            created[n["key"]] = register_topic(tmp, n["name"],
                                               keywords=n.get("keywords") or [])
        for r in plan.get("renames") or []:
            rename_topic(tmp, r["id"], r["name"])
        for g in plan.get("groups") or []:
            tgt = g.get("target") or created[g["new"]]
            srcs = g.get("from") or []
            if srcs:
                merge_topics(tmp, tgt, srcs, delete_sources=True)
        if snap_dir:
            Path(snap_dir).mkdir(parents=True, exist_ok=True)
        for d in plan.get("discard") or []:
            snap = delete_topic(tmp, d["id"])
            if snap_dir:
                snap["discard_reason"] = d.get("reason", "")
                (Path(snap_dir) / f"{d['id']}.json").write_text(
                    json.dumps(snap, ensure_ascii=False, indent=2),
                    encoding="utf-8", newline="\n")
        noise_added = register_noise(tmp, plan.get("noise") or [])
    except Exception as exc:                       # noqa: BLE001 —— 原子回滚
        tmp.unlink(missing_ok=True)
        return {"ok": False, "applied": False,
                "errors": [f"执行失败，已放弃临时副本（真库未改）: "
                           f"{type(exc).__name__}: {exc}"],
                "backup": str(bak)}

    os.replace(tmp, meta_path)                     # 原子换入
    left = list_topics(meta_path)
    return {"ok": True, "applied": True, "backup": str(bak),
            "noise_added": noise_added, "errors": [],
            "topics_after": len(left),
            "topics": [{"id": t["id"], "name": t["name"],
                        "members": t["members"]} for t in left]}


def _preview(plan: dict, have: set[str]) -> dict:
    groups = []
    for g in plan.get("groups") or []:
        tgt = g.get("target") or f"（新建 {g.get('new')}）"
        groups.append({"target": tgt, "from": len(g.get("from") or [])})
    return {"new_topics": len(plan.get("new_topics") or []),
            "renames": len(plan.get("renames") or []),
            "groups": groups,
            "discard": len(plan.get("discard") or []),
            "noise": len(plan.get("noise") or []),
            "keep": len(plan.get("keep") or [])}
