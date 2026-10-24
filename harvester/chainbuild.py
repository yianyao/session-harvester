# -*- coding: utf-8 -*-
"""链创作包与链发布（v0.82，用户 2026-10-24 裁决）。

## 为什么需要这两个东西

用户原话：「不仅仅是补一条链那么简单，要补一个生成链的命令给我」。

链长文（`knowledge/topics/chain-*.md`）是**写作产物**，不是聚合产物——
它要求「思想/技法/观念的演变和转折」，聚合器算不出来。历史上三条链
都是 Agent 读完蒸馏包后手写的，于是流程散在人的记忆里：没有包、没有
格式契约、没有"写完了怎么把关"的入口。

本模块把这条流程钉成两条命令（裁决：**命令出包 + Agent 写 + 命令校验落盘**，
不接 LLM——知识正本不许机器代笔未校验的文本）：

- `chain-build`：产出**链创作包**（确定性、零副作用）。包 = 判据头 +
  主题画像 + 按月分桶的锚点候选 + chain 格式契约 + 写作指令 + 校验命令。
  它**不写知识正本**，只写一个工作文件（缺省打印到 stdout）。
- `chain-publish`：把 Agent 写好的草稿过两道既有校验（`validate_chain`
  结构与锚点可回溯、`audit_chain` 引文逐字门），过了才**落进知识正本**。
  缺省 dry-run，`--apply` 才写；目标已存在必须 `--force`。

## 确定性铁律（H 系列）

`chain-build` 的产物**逐字节可复现**：不取当前时钟、不用随机、不依赖
目录遍历顺序（`sorted` 收口）。同库同参数跑两次指纹相等——这是它能
进 A 档动作（页面直接执行）的前提。

## 口径诚实

- 包里的锚点只是**候选**（每个会话的首轮原话），不是链的证据全集；
  被字符预算截掉的桶**报数**，不静默少给。
- 分桶是「按月」的机械分桶，**不是分期**——分期（阶段名/转折点）是
  Agent 的活，包里明写这句，避免把机械分桶当结论。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

PACK_SCHEMA = "chain-pack/1"
PUBLISH_SCHEMA = "chain-publish/1"
#: 提示词/契约版本：格式契约一改就升，链文件里记这个值
PROMPT_VERSION = "t3-chainbuild-20261024"
#: 创作包字符预算（含成员清单与锚点；超出后只砍锚点，且报数）
DEFAULT_BUDGET = 72000
#: 每个时间桶最多给几个候选锚点（均匀取样，不偏向头部）
PER_BUCKET = 6
#: 成员 sid 分批查 messages 的批大小（sqlite 变量上限 999）
_CHUNK = 500


def _chunks(seq, n=_CHUNK):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _turn_counts(db: Path, sids: list[str]) -> dict[str, int]:
    """每个会话的 user 轮数（`turn` 的上界，与 reader.split_turns 同口径：
    turn 从 1 起、按 user 消息计）。"""
    out: dict[str, int] = {}
    con = sqlite3.connect(f"file:{Path(db)}?mode=ro", uri=True)
    try:
        for part in _chunks(sorted(sids)):
            marks = ",".join("?" * len(part))
            for sid, n in con.execute(
                    f"SELECT sid, COUNT(*) FROM messages WHERE role='user' "
                    f"AND sid IN ({marks}) GROUP BY sid", part):
                out[sid] = int(n)
    finally:
        con.close()
    return out


def _even_pick(rows: list[dict], k: int) -> list[dict]:
    """均匀取样：首、尾与中间等距——避免只看到开头那几轮（头部偏差）。"""
    if len(rows) <= k:
        return list(rows)
    if k <= 1:
        return rows[:1]
    step = (len(rows) - 1) / (k - 1)
    idx = sorted({int(round(i * step)) for i in range(k)})
    return [rows[i] for i in idx]


def build(db: Path, meta_path: Path, topic_id: str,
          level: str = "coarse", max_chars: int | None = None) -> dict:
    """产出链创作包（确定性；只读索引库与主题库）。"""
    from .topics import show_topic, timeline
    from .dbmeta import db_fingerprint

    db, meta_path = Path(db), Path(meta_path)
    if not db.is_file():
        raise ValueError(f"索引库不存在: {db}")
    if not meta_path.is_file():
        raise ValueError(f"主题库不存在: {meta_path}")

    topic = show_topic(meta_path, topic_id)
    members = [m["sid"] for m in topic["members"]]
    tl = timeline(meta_path, topic_id, db=db, level="coarse")
    rows = tl.get("rows") or []
    turns = _turn_counts(db, members)

    # 按月分桶（机械分桶，不是分期）
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        key = str(r.get("created_at") or "")[:7] or "（无日期）"
        buckets.setdefault(key, []).append(r)

    budget = int(max_chars if max_chars is not None else DEFAULT_BUDGET)
    # 成员清单与固定骨架的占用先扣掉，剩下的预算全给锚点
    fixed = len(members) * 60 + 6000
    left = max(0, budget - fixed)
    out_buckets = []
    total_anchors = shown = budget_skipped = 0
    for key in sorted(buckets):
        rs = sorted(buckets[key], key=lambda x: (str(x.get("created_at") or ""),
                                                 str(x.get("sid") or "")))
        picked = _even_pick(rs, PER_BUCKET)
        nodes = []
        for r in picked:
            cap = len(r.get("first_user") or "") + 200
            if left <= 0:
                budget_skipped += 1
                continue
            nodes.append({
                "sid": r["sid"],
                "turn": 1,
                "turn_max": turns.get(r["sid"], 0),
                "title": r.get("title") or "",
                "quote": r.get("first_user") or "",
            })
            left -= cap
        total_anchors += len(rs)
        shown += len(nodes)
        out_buckets.append({
            "month": key,
            "sessions": len(rs),
            "span": [str(rs[0].get("created_at") or ""),
                     str(rs[-1].get("created_at") or "")],
            "candidates": len(rs),
            "nodes": nodes,
        })

    fp = db_fingerprint(db)
    return {
        "schema": PACK_SCHEMA,
        "prompt_version": PROMPT_VERSION,
        "topic": {"id": topic["id"], "name": topic["name"],
                  "keywords": topic.get("keywords") or [],
                  "created": topic.get("created")},
        "level": "coarse",
        "members": members,
        "missing": tl.get("missing") or [],
        "db_fingerprint": fp,
        "budget": {"max_chars": budget, "left_after": left,
                   "budget_skipped": budget_skipped,
                   "truncated": budget_skipped > 0,
                   "per_bucket_cap": PER_BUCKET},
        "buckets": out_buckets,
        "counts": {"sessions": len(rows), "members": len(members),
                   "buckets": len(out_buckets), "anchor_candidates": total_anchors,
                   "anchor_shown": shown},
    }


def render(d: dict) -> str:
    """把包渲染成一份自包含的 md（给 Agent 读，不是给人读的摘要）。"""
    t = d["topic"]
    fp = d.get("db_fingerprint") or {}
    c = d["counts"]
    b = d["budget"]
    L: list[str] = []
    A = L.append
    A(f"# 链创作包：{t['name']}")
    A("")
    A("> 这是**素材 + 格式契约**，不是链本身。链长文必须由 Agent 按 §5 写，"
      "再经 §6 两条校验命令把关——机器不代笔（2026-10-24 用户裁决）。")
    A("")
    A("## 0 判据头")
    A("")
    A(f"- 主题：`{t['id']}` {t['name']}")
    A(f"- 关键词：{'、'.join(t['keywords']) or '（无）'}")
    A(f"- 成员：{c['members']} 个会话（索引库命中 {c['sessions']} 个"
      f"{'，缺失 ' + str(len(d.get('missing') or [])) + ' 个' if d.get('missing') else ''}）")
    A("- 分层：coarse（每会话**首轮原话**，超过 400 字截断）")
    A(f"- 时间桶：{c['buckets']} 个（**按月的机械分桶，不是分期**）")
    A(f"- 锚点候选：{c['anchor_candidates']} 个会话；本包给出 {c['anchor_shown']} 个"
      f"（每桶均匀取样最多 {b['per_bucket_cap']} 个"
      + ("；**另有 %d 个因字符预算被砍**，预算 %d 字——调大 --max-chars 或按桶重跑"
         % (b["budget_skipped"], b["max_chars"]) if b.get("truncated") else "")
      + "）")
    A(f"- 库快照：会话 {fp.get('sessions', '?')}｜步骤 {fp.get('steps', '?')}"
      f"｜错误 {fp.get('errors', '?')}｜库 mtime {fp.get('db_mtime', '?')}")
    A(f"- 生成器：`chain-build` {d['prompt_version']}（确定性：不取时钟、"
      f"不用随机；同库同参数两次输出逐字节一致）")
    A("")
    A("## 1 主题画像")
    A("")
    A("| 月 | 会话数 | 区间 |")
    A("|---|---|---|")
    for bk in d["buckets"]:
        A(f"| {bk['month']} | {bk['sessions']} | {bk['span'][0][:10]} ~ "
          f"{bk['span'][1][:10]} |")
    A("")
    A("## 2 锚点候选（按时间桶）")
    A("")
    A("每个会话只给**首轮原话**——这是你判断「这一轮干了什么」的入口，"
      "不是证据全集。要更深的话用 `turn_max` 上限自己取：")
    A("")
    A("```")
    A("python -m harvester topic turns --id %s --sid <sid> --full" % t["id"])
    A("```")
    A("")
    for bk in d["buckets"]:
        if not bk["nodes"]:
            A(f"### {bk['month']}（{bk['sessions']} 会话｜**本桶因预算被跳过**）")
            A("")
            continue
        A(f"### {bk['month']}（{bk['sessions']} 会话｜"
          f"{bk['span'][0][:10]} ~ {bk['span'][1][:10]}）")
        A("")
        for n in bk["nodes"]:
            q = " ".join((n["quote"] or "").split())
            A(f"- `{n['sid']}` #1（该会话共 {n['turn_max']} 轮）"
              f"｜{n['title'][:60]}")
            A(f"  > {q}")
        A("")
    A("## 3 chain 长文格式契约")
    A("")
    A("文件名 `chain-<主题名>.md`，放 `knowledge/topics/`。结构如下"
      "（`generated_from` 的指纹**必须原样抄**，校验器会核）：")
    A("")
    A("```yaml")
    A("---")
    A(f"topic: \"{t['name']}\"")
    A(f"topic_id: {t['id']}")
    A("members:")
    for sid in d["members"]:
        A(f"  - {sid}")
    A("dedup:")
    A("  line: <去重口径，例如 bigram-Jaccard>=0.90 去重后剩几条>")
    A("  reps: <代表会话数>")
    A("  duplicates: <被去掉的重复数>")
    A("generated_from:")
    A(f"  db_mtime: \"{fp.get('db_mtime', '')}\"")
    A(f"  sessions: {fp.get('sessions', 0)}")
    A(f"  steps: {fp.get('steps', 0)}")
    A(f"  errors: {fp.get('errors', 0)}")
    A(f"prompt_version: {d['prompt_version']}")
    A("anchors:")
    A("  - stage: <阶段名——写「变化」，不写「内容」>")
    A("    span: <起 ~ 止，YYYY-MM-DD ~ YYYY-MM-DD>")
    A("    nodes:")
    A("      - sid: <必须是上面 members 里的某个 sid>")
    A("        turn: 1")
    A("        note: <这一轮让事情往哪变了；不许写「讨论了X」这种摘要>")
    A("---")
    A("```")
    A("")
    A("## 4 硬性规则（下面的校验器会逐条拦）")
    A("")
    A("1. `members` **逐字等于 §7 清单**（少一个、多一个都报错）；")
    A("2. 每个 `node.sid` 必须在 members 内，`turn` ∈ [1, 该会话 user 轮数"
      "（见 §2 的「该会话共 N 轮」）]；")
    A("3. 每个 `stage` 至少 1 个 `nodes`——空阶段直接不合格；")
    A("4. 正文（frontmatter 之后）不允许为空；")
    A("5. 正文里的「」引文**必须能在原文逐字找到**（省略号多段算部分命中，"
      "未命中即不合格）——写不准就少引，别改写原话；")
    A("6. `topic / topic_id / members / anchors / generated_from / "
      "prompt_version` 六项必填。")
    A("")
    A("## 5 写作指令（决定这份链有没有用）")
    A("")
    A("- **写「变化」不写「摘要」**：每一阶段要能回答「之前怎么做、后来改成什么、"
      "为什么改」。把三份会话摘要拼起来不是链。")
    A("- **阶段 3–8 个**：少于 3 个说明没看出转折，多于 8 个说明在记流水账。"
      "阶段名自带时间感（如「阶段一·市场对标与诊断范式定型」）。")
    A("- **锚点服务于论点**：每个 note 一句话点出这一轮的**判断变化**；"
      "不要罗列话题。")
    A("- **引文逐字**：正文引用用户原话必须一字不改（可截断，加省略号）。"
      "拿不到的原文就转述，别造引文。")
    A("- **不编造 sid/turn**：只用手上真有的；`turn_max` 是上界，超了就是错的。")
    A("- **结论层写「现在还在争什么」**：链的末尾要留未决问题，"
      "而不是假装已经想通了。")
    A("")
    A("## 6 写完之后（两条校验 + 一条发布）")
    A("")
    A("```")
    A("python -m harvester chain-validate knowledge/topics/chain-<名>.md --meta topics_meta.db")
    A("python -m harvester chain-audit   knowledge/topics/chain-<名>.md --db harvester.db")
    A("python -m harvester chain-publish knowledge/topics/chain-<名>.md --apply")
    A("```")
    A("")
    A("`chain-publish` 缺省是**预演**（不写盘，只报会落到哪、过没过）；"
      "`--apply` 才真写。目标文件已存在时必须加 `--force`。")
    A("")
    A("## 7 权威成员清单（members 必须逐字等于这段）")
    A("")
    A("```")
    for sid in d["members"]:
        A(sid)
    A("```")
    A("")
    return "\n".join(L)


def _slug(name: str) -> str:
    """文件名安全化：保留中日韩与字母数字，其余压成 `-`。"""
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "-", str(name or "")).strip(" -")
    s = re.sub(r"-{2,}", "-", s)
    return s or "untitled"


def publish(draft: Path, chain_root: Path, meta_path: Path, db_path: Path,
             name: str | None = None, apply: bool = False,
             force: bool = False, allow_audit: bool = False) -> dict:
    """校验 + 落盘（缺省 dry-run）。返回机读结果，CLI 负责打印人话。"""
    from .topicchain import load_chain, validate_chain
    from .chainaudit import audit_chain

    draft, chain_root = Path(draft), Path(chain_root)
    meta_path, db_path = Path(meta_path), Path(db_path)
    if not draft.is_file():
        raise ValueError(f"草稿不存在: {draft}")
    v = validate_chain(draft, meta_path, db_path)
    a = audit_chain(draft, db_path)
    try:
        fm = (load_chain(draft)["fm"] or {})
    except (ValueError, RuntimeError) as exc:
        return {"schema": PUBLISH_SCHEMA, "ok": False, "applied": False,
                "draft": str(draft), "errors": [str(exc)], "warnings": [],
                "target": None}

    title = name or fm.get("topic") or fm.get("topic_id") or draft.stem
    target = chain_root / f"chain-{_slug(title)}.md"
    blockers: list[str] = []
    warnings: list[str] = []
    if not v.get("ok"):
        blockers.extend(v.get("errors") or ["chain-validate 未通过"])
    if not a.get("ok"):
        msg = ("chain-audit 未通过（引文逐字门/锚点语义门）："
               f"引文未命中 {len((a.get('quotes') or {}).get('misses') or [])} 条；"
               f"疑似错配 {(a.get('anchors') or {}).get('suspicious') and '有' or '无'}")
        (warnings if allow_audit else blockers).append(msg)
    if target.exists() and not force:
        blockers.append(f"目标已存在: {target.name}（要覆盖加 --force）")
    if not target.parent.is_dir():
        blockers.append(f"chain 目录不存在: {target.parent}（检查 --chain-root）")

    applied = False
    if apply and not blockers:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(draft.read_bytes())
        applied = True
    return {
        "schema": PUBLISH_SCHEMA,
        "ok": not blockers,
        "applied": applied,
        "draft": str(draft),
        "target": str(target),
        "topic_id": fm.get("topic_id"),
        "validate": {"ok": v.get("ok"), "stats": v.get("stats") or {},
                     "errors": v.get("errors") or [],
                     "warnings": v.get("warnings") or []},
        "audit": {"ok": a.get("ok"),
                  "quotes_unmatched": len((a.get("quotes") or {}).get("misses")
                                          or []),
                  "anchors_suspicious": len((a.get("anchors") or {}).get(
                      "suspicious") or [])},
        "blockers": blockers,
        "warnings": warnings,
    }
