# -*- coding: utf-8 -*-
"""v0.80 门：错误类别**只有一个来源**（归因层），且首见时间进得了事实层。

背景（用户 2026-10-23 批准）：老分类器 `errstats._RULES` 只有三条英文短语
正则，DSH 的 `FS_*` 码与它们字面不相交 → 一律 unclassified。蒸馏队列 A 区
因此整列 unclassified（84 次的 FS_STALE_VERSION 也在其中）。修法不是继续
补正则，而是让**类别与根因/应对同源**——都取 `attrib-rules.yaml`。

本门钉四件事：
1. 事实层（classify_error）与归因层给同一个 class；
2. 真库上 unclassified 占比 < 2%（回归到老口径会立刻红）；
3. 蒸馏队列载荷带 why/fix/rule_id（"为什么/怎么修"有人可读的答案）；
4. 归因层坏掉时降级不崩（缺 PyYAML 的机器仍能出报告，只是类别粗）。
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harvester import attrib, errstats, triage  # noqa: E402

DB = ROOT / "harvester.db"


def _attrib_available() -> bool:
    """归因层可用性（缺 PyYAML 的机器上整类跳过）。

    为什么 skip 而不是让断言红：本仓 H9 纪律——缺依赖必须长成 error/skip，
    不许伪装成"断言失败"（那会看起来像代码坏了）。
    """
    try:
        attrib.load_rules()
        return True
    except Exception:  # noqa: BLE001 - 缺 YAML/规则表都是"环境不支持"
        return False


HAS_ATTRIB = _attrib_available()


@unittest.skipUnless(HAS_ATTRIB, "归因层不可用（缺 PyYAML），跳过")
class TestSingleSource(unittest.TestCase):

    def setUp(self) -> None:
        self.rules = attrib.load_rules()

    def test_fact_layer_agrees_with_attrib_layer(self):
        """同一串，`classify_error` 与 `attribute_face` 必须同答案。"""
        for s in ("FS_STALE_VERSION", "FS_NOT_OBSERVED", "WEB_PROVIDER_ERROR",
                  "FS_EDIT_NOT_FOUND", "SEARCH_FAILED", "tool_permission_revoked"):
            hit = attrib.attribute_face(s, self.rules, face="errors")
            self.assertIsNotNone(hit, "归因层应命中 %s" % s)
            self.assertEqual(errstats.classify_error(s), hit["class"],
                             "%s 的类别在两层不一致" % s)

    def test_fs_codes_are_no_longer_unclassified(self):
        self.assertEqual(errstats.classify_error("FS_STALE_VERSION"),
                         "tool_interface")
        self.assertEqual(errstats.classify_error("WEB_PROVIDER_ERROR"), "env")

    def test_really_unattributable_stays_unclassified(self):
        """报错本身没给根因的，宁可 unclassified——不许为了好看硬编。"""
        self.assertEqual(errstats.classify_error("ABORTED"), "unclassified")

    def test_attrib_hit_carries_why_and_fix(self):
        hit = errstats.attrib_hit("FS_STALE_VERSION")
        self.assertIsNotNone(hit)
        self.assertTrue(hit["why"].strip(), "why 不许空——它是「为什么」的答案")
        self.assertTrue(hit["fix"].strip(), "fix 不许空——它是「怎么修」的答案")
        self.assertTrue(hit["rule_id"].startswith("AR-"))

    def test_degrades_without_attrib_layer(self):
        """归因层不可用（缺 PyYAML / 规则表坏）→ 退回老正则，不抛不崩。"""
        errstats._ATTRIB_TRIED = False
        errstats._ATTRIB_RULES = None
        with mock.patch.object(attrib, "load_rules",
                               side_effect=RuntimeError("no yaml")):
            self.assertEqual(errstats.classify_error("file has not been read"),
                             "tool_interface")
            self.assertEqual(errstats.classify_error("FS_STALE_VERSION"),
                             "unclassified")
        # 复位缓存，免得污染同进程的其它测试
        errstats._ATTRIB_TRIED = False
        errstats._ATTRIB_RULES = None


class TestPatternFirstSeen(unittest.TestCase):

    def test_first_seen_is_min_ts(self):
        # 顺序故意把**晚的放前面**：若实现取"最后一条"，这里就会拿到 10-02
        # （第一版夹具把早的放前面，变异 M2 因此假存活——本仓 §三 17 同类坑）。
        errors = [
            {"pattern": "P", "class": "env", "count": 0, "tools": set(),
             "samples": [], "given_up": 0, "sid": "s", "seq": 1, "tool": "t",
             "ts": "2026-10-02 00:00:00", "error": "E"},
            {"pattern": "P", "class": "env", "count": 0, "tools": set(),
             "samples": [], "given_up": 0, "sid": "s", "seq": 2, "tool": "t",
             "ts": "2026-09-01 00:00:00", "error": "E"},
            {"pattern": "P", "class": "env", "count": 0, "tools": set(),
             "samples": [], "given_up": 0, "sid": "s", "seq": 3, "tool": "t",
             "ts": "2026-12-31 00:00:00", "error": "E"},
        ]
        out = errstats.pattern_stats([dict(e, count=1) for e in errors])
        self.assertEqual(out["P"]["first_seen"], "2026-09-01 00:00:00",
                         "first_seen 必须取最早 ts（不是首条/最后一条）")


@unittest.skipUnless(HAS_ATTRIB and DB.is_file(),
                     "归因层不可用或缺真库，跳过")
class TestRealDB(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        try:
            cls.rows = con.execute(
                "SELECT error, COUNT(*) FROM steps WHERE status='error' "
                "AND error<>'' GROUP BY error").fetchall()
        finally:
            con.close()

    def test_unclassified_share_below_two_percent(self):
        """回归到老口径（三条正则）时这条必红：那时 unclassified ≈ 52%。"""
        total = sum(c for _, c in self.rows)
        uncls = sum(c for e, c in self.rows
                    if errstats.classify_error(e) == "unclassified")
        self.assertLess(uncls / total, 0.02,
                        "unclassified 占 %d/%d，类别又退回老口径了" % (uncls, total))

    def test_every_pattern_class_is_known(self):
        for err, _c in self.rows:
            self.assertIn(errstats.classify_error(err), errstats.CLASSES)

    def test_api_payload_carries_first_seen(self):
        """`/api/reports/errors` 的 pattern 行必须带 first_seen——
        不带的话页面那句「其中 N 条是本窗口首见」永远是 0 条（静默失效，
        实测踩过：行是 apiserve 自己拼的，不经过 pattern_stats 的字段）。"""
        from harvester import apiserve
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row  # 服务端连接是 Row 工厂（否则按列名取值报错）
        try:
            d = apiserve.api_reports_errors(con, DB, {"days": "30"})
        finally:
            con.close()
        self.assertTrue(d["patterns"], "真库应有模式行")
        for p in d["patterns"]:
            self.assertIn("first_seen", p, "pattern 行缺 first_seen")
            self.assertRegex(p["first_seen"], r"^\d{4}-\d{2}-\d{2} ")
        # 与库对账：窗口态下 first_seen 的语义是"**本窗口内**最早"，
        # 所以要和 `ts >= since` 的最早错误比（不是全库最早——那是全库口径）。
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        try:
            since = d["meta"]["since"]
            earliest = con.execute(
                "SELECT MIN(ts) FROM steps WHERE status='error' AND error<>'' "
                "AND ts>=?", (since,)).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(min(p["first_seen"] for p in d["patterns"]), earliest,
                         "first_seen 与窗口内最早错误对不上（聚合口径漂了）")


@unittest.skipUnless(HAS_ATTRIB and DB.is_file(),
                     "归因层不可用或缺真库，跳过")
class TestTriagePayload(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.d = triage.collect_triage(DB, since_days=None, min_count=2, top_n=5)

    def test_marks_carry_class_and_why(self):
        self.assertTrue(self.d["new_patterns"], "真库应至少有一个新 pattern")
        for x in self.d["new_patterns"]:
            for k in ("class", "why", "fix", "rule_id"):
                self.assertIn(k, x)
            self.assertTrue(x["class"], "class 不许空")

    def test_top_pattern_class_is_not_unclassified(self):
        top = self.d["new_patterns"][0]
        self.assertNotEqual(top["class"], "unclassified",
                            "A 区头名还显示 unclassified → 口径没接上")
        self.assertTrue(top["why"].strip())

    def test_old_patterns_also_marked(self):
        for x in self.d["old_patterns"]:
            self.assertIn("class", x)
            self.assertIn("why", x)

    def test_skill_zone_has_anchors_and_chains(self):
        """C 区必须带得出锚点与行为链。

        什么时候会红：把 `OK_STATUS` 改回 `== "ok"`。真库 status 只有
        success/completed/no_result/error，写死 "ok" 会让 n_ok 恒 0、
        `sample` 恒 None、`chain` 恒空——页面上是整列空白，**不报任何错**
        （2026-10-23 实测 37/37 全空，用户看到的是「有数据却点不开」）。
        """
        sk = self.d["skills"]
        self.assertTrue(sk, "真库应有 skill 调用")
        with_sample = [s for s in sk if s["sample"]]
        with_chain = [s for s in sk if s["chain"]]
        self.assertTrue(with_sample, "C 区一条锚点都没有 → 状态口径又写死了")
        self.assertTrue(with_chain, "C 区一条行为链都没有 → 状态口径又写死了")
        for s in with_sample:
            self.assertEqual(len(s["sample"]), 2, "锚点必须是 (sid, seq) 两元组")
            self.assertTrue(s["sample"][0], "锚点 sid 不许空")
        for s in sk:
            self.assertLessEqual(s["n_ok"], s["n_calls"],
                                 "成功数不许超过调用数（口径串了）")


if __name__ == "__main__":
    unittest.main()