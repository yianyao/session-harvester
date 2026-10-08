# -*- coding: utf-8 -*-
"""topic-chain 长文独立校验器（T3，v0.22）。

裁决依据 PLAN §0.2-A：思维链是长文不是卡片，**不复用 §8 卡片校验**。
本模块校验 chain-<topic>.md 的结构与锚点可回溯性：

- frontmatter 必填项：topic / topic_id / members（非空 list）/
  anchors（非空 list，stage 含非空 nodes）/ generated_from（dict，
  至少含 db_mtime）/ prompt_version；
- 锚点逐个可回溯：node.sid 必须在 members 内、在索引库 sessions 表
  存在、turn ∈ [1, 该会话 user 消息数]（turn=reader.split_turns
  权威口径：1-based，user 消息起始，H24：messages 无 seq 列，
  时序 = rowid）；
- stages 必须有成员证据：每 stage 至少 1 个 node 且 sid ∈ members。

frontmatter 解析需要 PyYAML（块结构超出手写降级解析器能力，H8/H9；
无 PyYAML 时显式报错，不静默降级）。索引库一律只读打开。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

try:  # H9：PyYAML 环境差异，链校验不降级——缺库直接错误
    import yaml
    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False

REQUIRED_FIELDS = ("topic", "topic_id", "members", "anchors",
                   "generated_from", "prompt_version")
REQUIRED_FINGERPRINT_KEYS = ("db_mtime",)


def load_chain(path: Path) -> dict:
    """读 chain 长文，拆 frontmatter / 正文。缺 PyYAML 抛 RuntimeError。"""
    if not _HAS_YAML:
        raise RuntimeError(
            "topic-chain 校验需要 PyYAML（venv 解释器，H9）；"
            "不提供降级解析——块结构静默误读比报错更危险")
    text = Path(path).read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: 缺 frontmatter（须以 '---\\n' 开头）")
    parts = text.split("\n---\n", 1)
    fm = yaml.safe_load(parts[0][4:])
    body = parts[1] if len(parts) == 2 else ""
    return {"fm": fm, "body": body}


def _turn_count(db: sqlite3.Connection, sid: str) -> int:
    """turn 总数 = 该会话 user 消息数（split_turns 口径，rowid 序）。"""
    row = db.execute(
        "SELECT COUNT(*) FROM messages WHERE sid=? AND role='user'",
        (sid,)).fetchone()
    return int(row[0]) if row else 0


def validate_chain(path: Path, meta_path: Path, db_path: Path) -> dict:
    """校验 chain 长文。返回 {ok, errors, warnings, stats}。

    meta_path：topics_meta.db（查 members 成员资格）；
    db_path：harvester.db（只读，查 sid 存在性与 turn 上界）。
    """
    errors: list[str] = []
    warnings: list[str] = []
    p = Path(path)
    try:
        d = load_chain(p)
    except (ValueError, RuntimeError) as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": warnings,
                "stats": {}}
    fm: dict = d["fm"] if isinstance(d["fm"], dict) else {}
    body: str = d["body"]

    # 1) frontmatter 必填项
    for field in REQUIRED_FIELDS:
        if field not in fm or fm[field] is None:
            errors.append(f"frontmatter 缺必填字段: {field}")
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings,
                "stats": {}}
    members: list = fm["members"]
    anchors: list = fm["anchors"]
    generated: dict = fm["generated_from"]

    if not isinstance(members, list) or not members:
        errors.append("members 必须是非空列表")
        members = []
    if not isinstance(anchors, list) or not anchors:
        errors.append("anchors 必须是非空列表（至少一个 stage）")
        anchors = []
    if not isinstance(generated, dict):
        errors.append("generated_from 必须是 dict（db_fingerprint）")
        generated = {}
    for key in REQUIRED_FINGERPRINT_KEYS:
        if key not in generated:
            errors.append(f"generated_from 缺 fingerprint 键: {key}")
    if not body.strip():
        errors.append("正文(body)为空——思维链长文不允许空正文")

    # 2) 成员资格 + 锚点可回溯
    member_set = set(members)
    stages = 0
    nodes = 0
    con = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        for si, stage in enumerate(anchors):
            label = stage.get("stage") if isinstance(stage, dict) else None
            s_label = label or f"anchors[{si}]"
            if not isinstance(stage, dict):
                errors.append(f"{s_label}: stage 必须是 dict")
                continue
            stage_nodes = stage.get("nodes")
            if not isinstance(stage_nodes, list) or not stage_nodes:
                errors.append(f"阶段「{s_label}」无成员证据（nodes 为空）")
                continue
            stages += 1
            for ni, node in enumerate(stage_nodes):
                if not isinstance(node, dict):
                    errors.append(f"{s_label} nodes[{ni}]: 必须是 dict")
                    continue
                nodes += 1
                sid = node.get("sid")
                turn = node.get("turn")
                if not sid:
                    errors.append(f"{s_label} nodes[{ni}]: 缺 sid")
                    continue
                if turn is None:
                    errors.append(f"{s_label} nodes[{ni}] ({sid}): 缺 turn")
                    continue
                if sid not in member_set:
                    errors.append(
                        f"{s_label} nodes[{ni}]: sid {sid} 不在主题成员列表"
                        f"（锚点必须挂成员会话）")
                    continue
                row = con.execute(
                    "SELECT 1 FROM sessions WHERE sid=?", (sid,)).fetchone()
                if row is None:
                    errors.append(f"{s_label} nodes[{ni}]: sid {sid} "
                                  f"不在索引库中，锚点不可回溯")
                    continue
                try:
                    turn_i = int(turn)
                except (TypeError, ValueError):
                    errors.append(f"{s_label} nodes[{ni}] ({sid}): "
                                  f"turn 不是整数: {turn!r}")
                    continue
                n_turns = _turn_count(con, sid)
                if not 1 <= turn_i <= n_turns:
                    errors.append(
                        f"{s_label} nodes[{ni}]: sid {sid} turn {turn_i} "
                        f"越界（该会话共 {n_turns} 个 user 回合）")
    finally:
        con.close()

    # 3) topic_id 在注册表的存在性（宽松：warning 级，注册可后补）
    try:
        mcon = sqlite3.connect(f"file:{Path(meta_path)}?mode=ro", uri=True)
        try:
            hit = mcon.execute("SELECT 1 FROM topics WHERE id=?",
                               (fm["topic_id"],)).fetchone()
            if hit is None:
                warnings.append(f"topic_id {fm['topic_id']} 未在 "
                                f"topics_meta.db 注册")
        finally:
            mcon.close()
    except sqlite3.Error as exc:
        warnings.append(f"topics_meta.db 不可读，跳过注册表核对: {exc}")

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "stats": {"members": len(members), "stages": stages,
                      "nodes": nodes}}
