# -*- coding: utf-8 -*-
"""v0.82 链创作包（chain-build）与链发布（chain-publish）测试。

用户 2026-10-24 裁决：「命令出包 + Agent 写 + 命令校验落盘」。
本文件钉住这条链路的**可判定点**：

1. **确定性**：同库同参数渲染两次逐字节一致（不取时钟、不用随机）——
   这是"包能进 A 档动作、能被门复验"的前提；
2. **分桶诚实**：按月的机械分桶（不是分期）+ 桶内时间序 + span 正确；
   包里必须**明说"机械分桶不是分期"**，避免把工具产物当结论；
3. **取样报数**：每桶均匀取样上限 + 被预算砍掉的**报数**（不静默少给）；
4. **members 逐字等于主题成员**：校验器按这段核，少一个都发布不了；
5. **发布之前必须先过两道门**：validate（结构/锚点）+ audit（引文逐字），
   任一不过就不许落盘——这是"不许未校验文本污染知识正本"的执行点；
6. **缺省预演不写盘**：`apply=False` 时目标文件必须**不存在**；
7. **目标已存在必须 --force**：防手滑覆盖已发布的链。

真库只读：全部用临时库，不碰 harvester.db / topics_meta.db。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester import chainbuild as CB
from harvester.topics import add_members, ensure_topics_db, register_topic

try:  # 链校验器不降级（H9）：没有 PyYAML 就没有 frontmatter 块解析
    import yaml  # noqa: F401
    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False

#: `chain-publish` 的判定依赖 chain-validate/chain-audit 真读 frontmatter，
#: 没有 PyYAML 时那条路根本走不到断言（会变成 failure 而不是 error）——
#: 按 H9 的纪律显式 skip，让"环境缺依赖"不长成"实现错了"。
_NEED_YAML = unittest.skipUnless(_HAS_YAML, "需要 PyYAML（链校验器不降级，H9）")

S1 = "src-a:s-001"
S2 = "src-a:s-002"
S3 = "src-b:s-003"


def make_db(path: Path) -> None:
    """临时索引库：3 个会话，跨两个时间桶，含可用于引文核对的原文。"""
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE sessions (sid TEXT PRIMARY KEY, source TEXT,"
                " session_id TEXT, title TEXT, category TEXT,"
                " created_at TEXT, updated_at TEXT, file TEXT, model TEXT)")
    con.execute("CREATE TABLE messages (sid TEXT, role TEXT, ts TEXT,"
                " text TEXT, raw TEXT)")
    con.execute("CREATE TABLE steps (sid TEXT, seq INTEGER, ts TEXT,"
                " tool TEXT, phase TEXT, status TEXT, error TEXT,"
                " detail TEXT)")
    rows = [
        (S1, "2026-08-05 10:00:00", "第一版我想写成爽文",
         "我第一版我想写成爽文，节奏快一点"),
        (S2, "2026-08-20 09:00:00", "后来改成克制叙述",
         "后来改成克制叙述，删掉打脸桥段"),
        (S3, "2026-09-02 20:00:00", "读者反馈说太闷",
         "读者反馈说太闷，于是加回一点冲突"),
    ]
    for sid, ts, text, raw in rows:
        con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                    (sid, sid.split(":")[0], sid.split(":")[1], text, "c",
                     ts, ts, "f", "m"))
        con.execute("INSERT INTO messages VALUES (?,?,?,?,?)",
                    (sid, "user", ts, text, raw))
    con.commit()
    con.close()


def make_meta(path: Path, sids: list[str]) -> str:
    ensure_topics_db(path)
    tid = register_topic(path, "测试主题（链）", ["链", "测试"])
    add_members(path, tid, sids, evidence="单测夹具")
    return tid


CHAIN = """---
topic: "测试主题（链）"
topic_id: {tid}
members:
{members}
dedup:
  line: 单测夹具，无去重
  reps: 3
  duplicates: 0
generated_from:
  db_mtime: "{db_mtime}"
  sessions: 3
  steps: 0
  errors: 0
prompt_version: {pv}
anchors:
  - stage: 阶段一·从爽文到克制叙述
    span: 2026-08-05 ~ 2026-08-20
    nodes:
      - sid: {s1}
        turn: 1
        note: 第一版写成爽文，后来改成克制叙述
      - sid: {s2}
        turn: 1
        note: 改成克制叙述后删掉打脸桥段
  - stage: 阶段二·外部反馈回摆
    span: 2026-09-02 ~ 2026-09-02
    nodes:
      - sid: {s3}
        turn: 1
        note: 读者反馈说太闷于是加回冲突
---

# 思维链：测试主题

阶段一里「第一版我想写成爽文」是起点，阶段二「读者反馈说太闷」把方向拉回来。
"""


class ChainBuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "harvester.db"
        self.meta = self.dir / "topics_meta.db"
        make_db(self.db)
        self.tid = make_meta(self.meta, [S1, S2, S3])

    def tearDown(self):
        self.tmp.cleanup()

    def _pack(self, **kw):
        return CB.build(self.db, self.meta, self.tid, **kw)

    def _chain_text(self, tid=None) -> str:
        from harvester.dbmeta import db_fingerprint
        fp = db_fingerprint(self.db)
        members = "\n".join(f"  - {s}" for s in (S1, S2, S3))
        return CHAIN.format(tid=tid or self.tid, members=members,
                            db_mtime=fp["db_mtime"], pv=CB.PROMPT_VERSION,
                            s1=S1, s2=S2, s3=S3)

    # ---- 1 确定性 ----
    def test_render_is_byte_identical(self):
        t1 = CB.render(CB.build(self.db, self.meta, self.tid))
        t2 = CB.render(CB.build(self.db, self.meta, self.tid))
        self.assertEqual(t1, t2, "两次渲染不一致——包里混进了时钟/随机/顺序")

    # ---- 2 分桶 ----
    def test_buckets_are_monthly_and_sorted(self):
        d = self._pack()
        months = [b["month"] for b in d["buckets"]]
        self.assertEqual(months, ["2026-08", "2026-09"], months)
        self.assertEqual(d["buckets"][0]["sessions"], 2)
        self.assertEqual(d["buckets"][0]["span"],
                         ["2026-08-05 10:00:00", "2026-08-20 09:00:00"])
        self.assertEqual(d["counts"]["sessions"], 3)

    def test_pack_says_bucketing_is_not_staging(self):
        text = CB.render(self._pack())
        self.assertIn("按月的机械分桶，不是分期", text,
                      "机械分桶必须自陈不是分期——否则会被当成结论")
        self.assertIn("素材 + 格式契约", text)
        self.assertIn("机器不代笔", text)

    # ---- 3 取样与预算报数 ----
    def test_per_bucket_sampling_cap(self):
        d = self._pack()
        for b in d["buckets"]:
            self.assertLessEqual(len(b["nodes"]), CB.PER_BUCKET, b["month"])

    def test_budget_truncation_is_reported(self):
        # 预算压到极小 → 全部锚点被砍，且包与计数都要报出来
        d = self._pack(max_chars=6000)
        self.assertTrue(d["budget"]["truncated"], d["budget"])
        self.assertGreater(d["budget"]["budget_skipped"], 0)
        self.assertEqual(d["counts"]["anchor_shown"], 0)
        text = CB.render(d)
        self.assertIn("因字符预算被砍", text,
                      "被砍的锚点必须报数（不许静默少给）")

    # ---- 4 members 逐字等于主题成员 ----
    def test_pack_members_match_topic_verbatim(self):
        from harvester.topics import show_topic
        d = self._pack()
        want = [m["sid"] for m in show_topic(self.meta, self.tid)["members"]]
        self.assertEqual(d["members"], want)
        text = CB.render(d)
        for sid in want:
            self.assertIn(sid, text, "权威成员清单里缺 %s" % sid)

    # ---- 5/6 预演不写盘 ----
    @_NEED_YAML
    def test_publish_dry_run_does_not_write(self):
        draft = self.dir / "draft.md"
        draft.write_text(self._chain_text(), encoding="utf-8", newline="\n")
        root = self.dir / "topics"
        root.mkdir()
        r = CB.publish(draft, root, self.meta, self.db)
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["applied"])
        self.assertIn("chain-测试主题（链）.md", r["target"])
        self.assertFalse((root / "chain-测试主题（链）.md").exists(),
                         "预演不许写盘")

    @_NEED_YAML
    def test_publish_apply_writes_identical_bytes(self):
        draft = self.dir / "draft.md"
        body = self._chain_text()
        draft.write_text(body, encoding="utf-8", newline="\n")
        root = self.dir / "topics"
        root.mkdir()
        r = CB.publish(draft, root, self.meta, self.db, apply=True)
        self.assertTrue(r["applied"], r)
        got = Path(r["target"])
        self.assertTrue(got.is_file())
        self.assertEqual(got.read_bytes(), draft.read_bytes(),
                         "落盘必须逐字节等于草稿（不许二次加工）")

    @_NEED_YAML
    def test_publish_requires_force_to_overwrite(self):
        draft = self.dir / "draft.md"
        draft.write_text(self._chain_text(), encoding="utf-8", newline="\n")
        root = self.dir / "topics"
        root.mkdir()
        (root / "chain-测试主题（链）.md").write_text("旧内容\n",
                                                   encoding="utf-8")
        r = CB.publish(draft, root, self.meta, self.db, apply=True)
        self.assertFalse(r["ok"])
        self.assertFalse(r["applied"])
        self.assertTrue(any("--force" in b for b in r["blockers"]), r)
        self.assertEqual((root / "chain-测试主题（链）.md").read_text(
            encoding="utf-8"), "旧内容\n", "没 --force 不许覆盖")

    # ---- 5 结构门 ----
    @_NEED_YAML
    def test_publish_blocks_on_validate_errors(self):
        draft = self.dir / "draft.md"
        bad = self._chain_text().replace(
            "prompt_version: %s\n" % CB.PROMPT_VERSION, "")
        draft.write_text(bad, encoding="utf-8", newline="\n")
        root = self.dir / "topics"
        root.mkdir()
        r = CB.publish(draft, root, self.meta, self.db, apply=True)
        self.assertFalse(r["ok"])
        self.assertFalse(r["applied"])
        joined = " ".join(r["blockers"])
        self.assertIn("prompt_version", joined,
                      "缺必填字段必须拦下并指名道姓")

    @_NEED_YAML
    def test_publish_blocks_on_audit_failure(self):
        """引文「」在原文找不到 → 引文逐字门不合格 → 不许发布。"""
        draft = self.dir / "draft.md"
        txt = self._chain_text().replace("「第一版我想写成爽文」",
                                         "「这句话原文里根本没有」")
        draft.write_text(txt, encoding="utf-8", newline="\n")
        root = self.dir / "topics"
        root.mkdir()
        r = CB.publish(draft, root, self.meta, self.db, apply=True)
        self.assertFalse(r["ok"])
        self.assertFalse(r["applied"])
        self.assertGreater(r["audit"]["quotes_unmatched"], 0)
        r2 = CB.publish(draft, root, self.meta, self.db, apply=True,
                        allow_audit=True)
        self.assertTrue(r2["ok"], r2)
        self.assertTrue(any("chain-audit" in w for w in r2["warnings"]))

    def test_publish_missing_draft(self):
        with self.assertRaises(ValueError):
            CB.publish(self.dir / "nope.md", self.dir, self.meta, self.db)


if __name__ == "__main__":
    unittest.main()
