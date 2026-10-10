# -*- coding: utf-8 -*-
"""v0.26 主题梳理流水线测试（consolidate）。

锁住四件事（每件都能说清"什么情况会红"）：
1. **完整性校验**：库里有一个主题没归位 → apply 拒绝执行（防"漏掉一个悄悄留着"）；
2. **文件级原子**：执行中途抛异常 → 真库**逐字节未变**（这是 v0.25 那次
   "T1 合并落库后脚本崩"事故的直接回归测试）；
3. dry-run 不写库；
4. 零散会话只进 meta 的 `sessions_noise`，采集库只读（测试里给采集库加
   authorizer，任何写操作直接炸）。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from unittest import mock
from pathlib import Path


import harvester.consolidate as consolidate
from harvester.consolidate import (apply_plan, build_plan_packet, list_noise,
                                   load_plan, register_noise, validate_plan)
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.topics import (add_members, ensure_topics_db, list_topics,
                              register_topic, show_topic)


def _fixture(tmp: Path) -> tuple[Path, Path]:
    db = tmp / "h.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    index_session(con, SessionRecord(
        source="src", session_id="s1", title="单轮短问答",
        created_at="2026-01-01 10:00:00", updated_at="2026-01-01 10:00:00",
        messages=[Message(role="user", text="差额的英文")]))
    index_session(con, SessionRecord(
        source="src", session_id="s2", title="长会话",
        created_at="2026-01-02 10:00:00", updated_at="2026-01-02 10:00:00",
        messages=[Message(role="user", text="请详细分析" + "x" * 60),
                  Message(role="assistant", text="好的")]))
    con.commit()
    con.close()
    return db, ensure_topics_db(tmp / "topics_meta.db")


def _seed(meta: Path) -> tuple[str, str, str, str]:
    a = register_topic(meta, "甲", keywords=["k"])
    b = register_topic(meta, "乙", keywords=["k"])
    c = register_topic(meta, "丙", keywords=["k"])
    d = register_topic(meta, "丁", keywords=["k"])
    add_members(meta, a, ["src:s1"], evidence="e1")
    add_members(meta, b, ["src:s2"], evidence="e2")
    add_members(meta, c, ["src:s1"], evidence="e3")
    add_members(meta, d, ["src:s2"], evidence="e4")
    return a, b, c, d


class TestPlanValidation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db, self.meta = _fixture(self.dir)
        self.a, self.b, self.c, self.d = _seed(self.meta)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_missing_topic_is_rejected(self):
        plan = {"version": 1,
                "groups": [{"target": self.a, "from": [self.b]}],
                "keep": [{"id": self.c, "reason": "待定"},
                         {"id": self.d, "reason": "待定"}]}
        self.assertEqual(validate_plan(plan, {self.a, self.b, self.c, self.d}), [])
        plan2 = {"version": 1, "groups": [{"target": self.a,
                                           "from": [self.b]}]}   # 丙 未归位
        errs = validate_plan(plan2, {self.a, self.b, self.c, self.d})
        self.assertTrue(any("未归置" in e for e in errs), errs)

    def test_bad_refs_and_overlap_rejected(self):
        errs = validate_plan({"version": 1,
                              "renames": [{"id": "tp-ghost", "name": "x"}],
                              "groups": [{"target": self.a, "from": ["tp-ghost"]}],
                              "discard": [{"id": self.b}, {"id": self.b}]},
                             {self.a, self.b, self.c, self.d})
        self.assertTrue(any("不存在" in e for e in errs))
        self.assertTrue(any("未归置" in e or "重叠" in e for e in errs))
        self.assertIn("plan.version 必须是 1", validate_plan({}, set()))

    def test_apply_refuses_invalid_plan_without_touching_db(self):
        before = self.meta.read_bytes()
        r = apply_plan(self.meta, {"version": 1, "groups": []}, dry_run=False)
        self.assertFalse(r["ok"])
        self.assertFalse(r["applied"])
        self.assertEqual(self.meta.read_bytes(), before)


class TestApplyAtomicity(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db, self.meta = _fixture(self.dir)
        self.a, self.b, self.c, self.d = _seed(self.meta)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_dry_run_changes_nothing(self):
        before = self.meta.read_bytes()
        plan = {"version": 1,
                "groups": [{"target": self.a, "from": [self.b]}],
                "keep": [{"id": self.c}, {"id": self.d}]}
        r = apply_plan(self.meta, plan, dry_run=True)
        self.assertTrue(r["ok"])
        self.assertFalse(r["applied"])
        self.assertEqual(self.meta.read_bytes(), before)

    def test_failure_midway_leaves_db_byte_identical(self):
        """回归 v0.25 事故：第一组成功、第二组抛异常 → 真库逐字节未变。

        校验器会挡掉"自相矛盾的 plan"，所以这里用**故障注入**制造运行期失败
        （第 2 次 merge 抛错）——这正是真实事故的形态：plan 合法，执行中途炸。
        """
        before = self.meta.read_bytes()
        plan = {"version": 1,
                "groups": [{"target": self.a, "from": [self.b]},
                           {"target": self.c, "from": [self.d]}],
                "keep": []}
        real = consolidate.merge_topics
        calls = {"n": 0}

        def flaky(meta, tgt, srcs, delete_sources=True):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("模拟第 2 组执行中途失败")
            return real(meta, tgt, srcs, delete_sources=delete_sources)

        with mock.patch.object(consolidate, "merge_topics", flaky):
            r = apply_plan(self.meta, plan, dry_run=False)
        self.assertEqual(calls["n"], 2)               # 第 1 组确实执行过
        self.assertFalse(r["ok"])
        self.assertTrue(any("真库未改" in e for e in r["errors"]), r["errors"])
        self.assertEqual(self.meta.read_bytes(), before)
        # 临时副本不残留
        self.assertEqual(list(self.dir.glob("*.tmp-apply")), [])
        ids = {t["id"] for t in list_topics(self.meta)}
        self.assertEqual(ids, {self.a, self.b, self.c, self.d})

    def test_successful_apply_swaps_in_and_backs_up(self):
        plan = {"version": 1,
                "new_topics": [{"key": "N1", "name": "新类目",
                                "keywords": ["n"]}],
                "renames": [{"id": self.c, "name": "丙改名"}],
                "groups": [{"new": "N1", "from": [self.b]}],
                "discard": [{"id": self.a, "reason": "单点问答"}],
                "noise": [{"sid": "src:s1", "reason": "单轮短问答"}],
                "keep": [{"id": self.d, "reason": "本轮不动"}]}
        snap = self.dir / "snaps"
        r = apply_plan(self.meta, plan, dry_run=False, snap_dir=snap)
        self.assertTrue(r["ok"], r["errors"])
        self.assertTrue(r["applied"])
        self.assertTrue(Path(r["backup"]).is_file())
        left = {t["id"]: t for t in list_topics(self.meta)}
        self.assertEqual(len(left), 3)                       # 新类目 + 丙改名
        self.assertNotIn(self.a, left)                       # 被舍弃
        self.assertNotIn(self.b, left)                       # 并入新类目
        self.assertEqual(left[self.c]["name"], "丙改名")
        new_id = [k for k in left if k not in (self.a, self.b, self.c)][0]
        self.assertEqual(left[new_id]["members"], 1)         # 乙的成员随行
        # 舍弃快照落盘且含理由
        s = json.loads((snap / f"{self.a}.json").read_text(encoding="utf-8"))
        self.assertEqual(s["discard_reason"], "单点问答")
        # 快照里的成员保留原始形态（含证据），核对 sid 即可
        self.assertEqual([m["sid"] for m in s["members"]], ["src:s1"])
        self.assertEqual(s["members"][0]["evidence"], "e1")
        # 零散登记进 meta
        self.assertEqual([n["sid"] for n in list_noise(self.meta)], ["src:s1"])

    def test_noise_registration_is_idempotent_and_readonly_on_db(self):
        """登记零散会话不得写采集库（authorizer 拦任何写）。"""
        writes: list[str] = []

        def deny(action, *_a):
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE,
                          sqlite3.SQLITE_DELETE, sqlite3.SQLITE_DROP_TABLE):
                writes.append(str(action))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        con.set_authorizer(deny)
        try:
            con.execute("SELECT COUNT(*) FROM sessions").fetchone()
        finally:
            con.close()
        self.assertEqual(writes, [])

        n1 = register_noise(self.meta, [{"sid": "src:s1", "reason": "r1"}])
        n2 = register_noise(self.meta, [{"sid": "src:s1", "reason": "r2"}])
        self.assertEqual((n1, n2), (1, 0))                   # 幂等
        self.assertEqual(list_noise(self.meta)[0]["reason"], "r1")  # 不覆盖

    def test_plan_roundtrip_yaml(self):
        # 缺 PyYAML 时显式报错（与其余 chain 类测试同口径，不做降级解析）
        try:
            import yaml
        except ImportError:
            raise RuntimeError(
                "topic-consolidate 的 plan 读写需要 PyYAML（H9）；"
                "不提供降级解析——语义归组读错比报错危险")
        plan = {"version": 1, "discard": [{"id": self.a, "reason": "r"}]}
        p = self.dir / "plan.yaml"
        p.write_text(yaml.safe_dump(plan, allow_unicode=True), encoding="utf-8")
        self.assertEqual(load_plan(p), plan)


class TestPlanPacket(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db, self.meta = _fixture(self.dir)
        self.a, self.b, self.c, self.d = _seed(self.meta)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_packet_lists_signals_noise_and_template(self):
        # s1 已是主题成员 → 不应出现在零散候选里；再造一个纯零散会话
        con = sqlite3.connect(str(self.db))
        index_session(con, SessionRecord(
            source="src", session_id="s9", title="查词",
            created_at="2026-01-03 10:00:00", updated_at="2026-01-03 10:00:00",
            messages=[Message(role="user", text="悄摸摸是啥")]))
        con.commit()
        con.close()
        txt = build_plan_packet(self.meta, db_path=self.db)
        self.assertIn("主题信号表", txt)
        self.assertIn("零散会话候选", txt)
        self.assertIn("src:s9", txt)          # 单轮短问答 + 无归属 → 候选
        self.assertNotIn("src:s1", txt)       # 有归属 → 不候选
        self.assertIn("version: 1", txt)      # 执行模板
        self.assertIn("完整性要求", txt)

    def test_packet_without_db_still_renders(self):
        txt = build_plan_packet(self.meta)
        self.assertIn("主题信号表", txt)
        self.assertIn("零散会话候选（0 个", txt)


class TestNoiseWiring(unittest.TestCase):
    """V4：零散会话要真的被消费方用上（否则登记了也白登记）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db, self.meta = _fixture(self.dir)
        self.a, self.b, self.c, self.d = _seed(self.meta)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_noise_sids_readonly_and_missing_table(self):
        from harvester.consolidate import noise_sids
        # 未登记（且表还不存在）→ 空集，不建表、不报错
        self.assertEqual(noise_sids(self.meta), set())
        con = sqlite3.connect(f"file:{self.meta}?mode=ro", uri=True)
        try:
            has = con.execute("SELECT 1 FROM sqlite_master WHERE "
                              "name='sessions_noise'").fetchone()
        finally:
            con.close()
        self.assertIsNone(has, "只读查询不该顺势建表")
        register_noise(self.meta, [{"sid": "src:s2", "reason": "r"}])
        self.assertEqual(noise_sids(self.meta), {"src:s2"})
        self.assertEqual(noise_sids(None), set())

    def test_keywords_excludes_noise(self):
        from harvester.consolidate import noise_sids
        from harvester.kwstats import build_stats
        meta_kw = self.dir / "kw.db"
        base = build_stats(self.db, meta_kw, ns=[2], role="user")
        register_noise(self.meta, [{"sid": "src:s2", "reason": "零散"}])
        filt = build_stats(self.db, meta_kw, ns=[2], role="user",
                           exclude_sids=noise_sids(self.meta))
        self.assertEqual(base["total_msgs"], 2)
        self.assertEqual(filt["total_msgs"], 1)          # s2 被排除
        self.assertEqual(filt["params"]["noise_excluded"], 1)
        self.assertEqual(filt["params"]["noise_msgs_excluded"], 1)

    def test_candidates_excludes_noise(self):
        from harvester.candidates import build_candidates
        r2 = build_candidates(self.db, min_sim=0.0, min_size=2,
                              topics_meta=self.meta)
        register_noise(self.meta, [{"sid": "src:s2", "reason": "零散"}])
        r3 = build_candidates(self.db, min_sim=0.0, min_size=2,
                              topics_meta=self.meta)
        sids = {m["sid"] for c in r3["clusters"] for m in c["members"]}
        self.assertNotIn("src:s2", sids)
        self.assertLessEqual(sum(len(c["members"]) for c in r3["clusters"]),
                             sum(len(c["members"]) for c in r2["clusters"]))


class TestPlanAssign(unittest.TestCase):
    """v0.30：plan 级「把散会话并入已有主题」——新数据进来时的主路径。

    可用性缺口：此前只能 merge/rename/delete（主题级），不能把一批**新会话**
    吸进某个已有主题；没有它，收藏越多主题越粗，新会话却进不去。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db, self.meta = _fixture(self.dir)
        self.a, self.b, self.c, self.d = _seed(self.meta)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _plan(self, sids, **kw):
        p = {"version": 1, "assign": [{"target": self.a, "sids": sids,
                                       "evidence": "新会话并入（口径：关键词命中）",
                                       **kw}]}
        p["keep"] = [{"id": i} for i in (self.b, self.c, self.d)]
        return p

    def test_assign_adds_sessions_with_evidence(self):
        r = apply_plan(self.meta, self._plan(["src:new1", "src:new2"]),
                       dry_run=False)
        self.assertTrue(r["ok"], r["errors"])
        d = show_topic(self.meta, self.a)
        by = {m["sid"]: m["evidence"] for m in d["members"]}
        self.assertIn("src:new1", by)
        self.assertIn("关键词命中", by["src:new1"])      # 口径留痕

    def test_assign_dry_run_and_preview(self):
        before = self.meta.read_bytes()
        r = apply_plan(self.meta, self._plan(["src:new1"]), dry_run=True)
        self.assertTrue(r["ok"])
        self.assertFalse(r["applied"])
        self.assertEqual(r["preview"]["assign"],
                         [{"target": self.a, "sids": 1}])
        self.assertEqual(self.meta.read_bytes(), before)

    def test_assign_rejects_session_owned_by_other_topic(self):
        # 候选会话已是 self.b 的成员 → 不能靠 assign 挪动（应显式 remove）
        r = apply_plan(self.meta, self._plan(["src:s2"]), dry_run=False)
        self.assertFalse(r["ok"])
        self.assertTrue(any("已是主题" in e for e in r["errors"]), r["errors"])

    def test_assign_rejects_noise_registered_session(self):
        register_noise(self.meta, [{"sid": "src:new9", "reason": "零散"}])
        r = apply_plan(self.meta, self._plan(["src:new9"]), dry_run=False)
        self.assertFalse(r["ok"])
        self.assertTrue(any("已被登记为零散" in e for e in r["errors"]),
                        r["errors"])

    def test_assign_rejects_duplicate_and_empty(self):
        r = apply_plan(self.meta, self._plan(["src:x", "src:x"]), dry_run=False)
        self.assertFalse(r["ok"])
        self.assertTrue(any("同一个 sid 出现两次" in e for e in r["errors"]))
        r2 = apply_plan(self.meta, self._plan([]), dry_run=False)
        self.assertFalse(r2["ok"])
        self.assertTrue(any("缺 sids" in e for e in r2["errors"]))

    def test_assign_into_new_topic_key(self):
        plan = {"version": 1,
                "new_topics": [{"key": "N1", "name": "新类目"}],
                "assign": [{"new": "N1", "sids": ["src:new1"]}],
                "keep": [{"id": i} for i in (self.a, self.b, self.c, self.d)]}
        r = apply_plan(self.meta, plan, dry_run=False)
        self.assertTrue(r["ok"], r["errors"])
        new_id = [t["id"] for t in list_topics(self.meta)
                  if t["name"] == "新类目"][0]
        self.assertEqual([m["sid"] for m in show_topic(self.meta, new_id)["members"]],
                         ["src:new1"])


if __name__ == "__main__":
    unittest.main()
