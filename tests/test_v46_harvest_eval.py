# -*- coding: utf-8 -*-
"""v0.46 P1-4 测试：自进化评估闭环（harvest-eval）。

判据（每条都说得出"什么情况下会红"）：
- 四种判定各钉一例（改善/无变化/变差/不下结论）：夹具数字或判定阈值一改就红；
- **样本门槛优先于幅度**：GATE 例未解决率 16.7% → 100%（Δ +500%）但合计只有
  12 条 → 必须仍判「不下结论（样本不足）」；门槛一去掉，这条立刻变「变差」；
- 分界缺失 → 「不下结论（无分界日期）」，而且**不许**算成「无变化」；
- 复发率定义：同会话出现 2 次算复发（前窗口 5/15 = 33.3%），口径一改就红；
- 窗口左闭右开：恰在 T 的错误算**后**窗口（IMP 后窗口是 21 而不是 20）；
- 台账侧：`adopted_at` 的迁移/只增不减/回填/清空，`note` 的保留与清空；
- 桥：key 是建议句原文时，`note` 里的 `pattern: <错误 pattern 原文>` 必须真的
  接上错误数据（不写则退回 key 匹配，写了但命中 0 条要如实点明）；
- `topic:<id>` 登记留痕行整体跳过（计入 `skipped_topic`，不进评估表）。

夹具手法沿用 `tests/test_v19.py::_fixture_db`：真建 steps/sessions 表并插数据，
让 `errstats.collect_errors_from_db` 真的去算 `resolved`（不 mock 口径）。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.errstats import normalize_error
from harvester.harvesteval import (evaluate_all, evaluate_entry, judge,
                                   load_errors, matches_key, parse_note_pattern,
                                   parse_note_patterns, relative_change,
                                   render_report, run_eval, window_stats)
from harvester.indexing import SCHEMA
from harvester.suggestmeta import (TOPIC_PREFIX, load_records, set_status,
                                   validate_stamp)

# ---- 夹具：5 个 key（各自独立 pattern），共用同一个分界 T ------------------

_T = "2026-10-05"                       # 分界时刻（date 形态，窗口按 00:00:00 切）
_T_TS = "2026-10-05 00:00:00"
_TS_BEFORE = "2026-09-30 10:00:00"      # 前窗口 [2026-09-28, 2026-10-05)
_TS_AFTER = "2026-10-07 10:00:00"       # 后窗口 [2026-10-05, 2026-10-12)
_TS_FAR = "2026-11-20 10:00:00"         # 两窗口都不含（钉"窗口真的在切"）
_TOOL = "Edit"

# 归一后必须等于左侧 key（见 test_fixture_patterns_match_their_keys）
_KEY_IMP = "old_string was not found in <q>"
_ERR_IMP = 'old_string was not found in "a.txt"'
_KEY_UNCH = "tool_permission_revoked"
_ERR_UNCH = "tool_permission_revoked"
_KEY_WORSE = "web fetch failed: TypeError: fetch failed"
_ERR_WORSE = "Error: web fetch failed: TypeError: fetch failed"
_KEY_GATE = "subagent depth N exceeds maxDepth N"
_ERR_GATE = "subagent depth 3 exceeds maxDepth 2"
_KEY_UNDEC = "File not found: <q>"
_ERR_UNDEC = 'File not found: "b.txt"'
_KEY_REJECTED = "sandbox-center cmd decisionRecord missing actual resource subject"
# 桥（note 里的 `pattern:` 标记）用的建议句原文 key——与任何错误 pattern 都不吻合
_BRIDGE_KEY = "Edit/Write 前必须先 Read 目标文件最新内容。"
_BRIDGE_NOTE = "已并入 AGENTS.md；pattern: old_string was not found in <q>"
# 一条规则覆盖同根因的多个 pattern（真机形态）——换行分隔，命中取并集
_BRIDGE_MULTI_NOTE = ("pattern: old_string was not found in <q>\n"
                      "pattern: File not found: <q>")
_BRIDGE_MISS_NOTE = "pattern: 这个模式在库里根本不存在 xyz"


def _session(con: sqlite3.Connection, sid: str, err: str, ts: str,
             occurrences: int, resolved: bool) -> None:
    """一个会话：`occurrences` 次该错误。

    `resolved=True` 时每次错误后跟一次同工具 call——正是 errstats 的判定口径
    （错误后同会话同工具是否再次调用）；False 则不插 call（= 放弃/未解决）。
    注意：末尾那次 call 会把**同会话早先**的错误也带成 resolved，所以本函数
    只用于"全 resolved"或"全 unresolved"的会话，不混用。
    """
    rows = []
    seq = 0
    for _ in range(occurrences):
        rows.append((sid, seq, ts, _TOOL, "result", "error", err, ""))
        seq += 1
        if resolved:
            rows.append((sid, seq, ts, _TOOL, "call", "", "", ""))
            seq += 1
    con.executemany(
        "INSERT INTO steps (sid, seq, ts, tool, phase, status, error, detail) "
        "VALUES (?,?,?,?,?,?,?,?)", rows)
    con.execute(
        "INSERT INTO sessions (sid, source, session_id, title, created_at, "
        "updated_at, model) VALUES (?,?,?,?,?,?,?)",
        (sid, "src", sid, sid, ts, ts, "m1"))


def _fill(con: sqlite3.Connection, prefix: str, err: str, ts: str,
          plan: list[tuple[int, bool]]) -> None:
    """plan: [(次数, 是否 resolved), ...]，每项一个会话（sid = prefix + 序号）。"""
    for i, (occ, resolved) in enumerate(plan):
        _session(con, f"{prefix}{i:02d}", err, ts, occ, resolved)


def _fixture_db(tmp: Path) -> Path:
    """合成库（一段一段互相独立，见每行的注释——数字改了对应断言就红）。

    合计错误 141 条；`IMP` 另有 1 条远在 T+46 天（两窗口都不含）。
    """
    db = tmp / "v46_eval.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    # IMP：前 20 条（10 未解决 = 50%）→ 后 20 条（4 未解决 = 20%）→ 改善
    _fill(con, "imp-b", _ERR_IMP, _TS_BEFORE,
          [(1, True)] * 10 + [(1, False)] * 10)
    _fill(con, "imp-a", _ERR_IMP, _TS_AFTER,
          [(1, True)] * 16 + [(1, False)] * 4)
    _session(con, "imp-t0", _ERR_IMP, _T_TS, 1, True)     # 恰在 T → 算后窗口
    _session(con, "imp-far", _ERR_IMP, _TS_FAR, 1, False)  # 两窗口都不含
    # UNCH：前 20 条 / 15 会话（5 个会话各 2 次 → 复发率 5/15），未解决率 50% 不变
    for tag, ts in (("unch-b", _TS_BEFORE), ("unch-a", _TS_AFTER)):
        _fill(con, tag, _ERR_UNCH, ts, [(2, True)] * 5 + [(1, False)] * 10)
    # WORSE：50% → 80% → 变差
    _fill(con, "worse-b", _ERR_WORSE, _TS_BEFORE,
          [(1, True)] * 10 + [(1, False)] * 10)
    _fill(con, "worse-a", _ERR_WORSE, _TS_AFTER,
          [(1, True)] * 4 + [(1, False)] * 16)
    # GATE：6 条（1 未解决 = 16.7%）/ 6 条（全未解决 = 100%）→ 各窗口都 >= 5，
    # 只有"合计 12 < 20"这道门槛能拦住它
    _fill(con, "gate-b", _ERR_GATE, _TS_BEFORE, [(1, True)] * 5 + [(1, False)])
    _fill(con, "gate-a", _ERR_GATE, _TS_AFTER, [(1, False)] * 6)
    # UNDEC：前 4 / 后 3（两窗口都 < 5）→ 不下结论（样本不足）
    _fill(con, "undec-b", _ERR_UNDEC, _TS_BEFORE,
          [(1, True)] * 2 + [(1, False)] * 2)
    _fill(con, "undec-a", _ERR_UNDEC, _TS_AFTER, [(1, False)] * 3)
    con.commit()
    con.close()
    return db


def _records(undecided_at: str | None = None) -> dict:
    """台账记录：4 条有分界 T 的 adopted + 1 条分界由参数控制 + 1 条 rejected。"""
    recs = {}
    for k in (_KEY_IMP, _KEY_UNCH, _KEY_WORSE, _KEY_GATE):
        recs[k] = {"status": "adopted", "adopted_at": _T, "note": None,
                   "updated_at": _T_TS}
    recs[_KEY_UNDEC] = {"status": "adopted", "adopted_at": undecided_at,
                        "note": None, "updated_at": _T_TS}
    recs[_KEY_REJECTED] = {"status": "rejected", "adopted_at": _T, "note": None,
                           "updated_at": _T_TS}
    return recs


# ---- 纯函数层 --------------------------------------------------------------

class TestWindowStats(unittest.TestCase):
    """窗口口径：左闭右开、没数据 = None、复发 = 同会话 >= 2 次。"""

    def test_empty_window_rates_are_none_not_zero(self):
        """没数据必须是 None（不是 0.0）：写成 0 会让"没数据"伪装成"没有未解决"。"""
        st = window_stats([], "2026-10-01 00:00:00", "2026-10-08 00:00:00")
        self.assertEqual(st["errors"], 0)
        self.assertIsNone(st["unresolved_rate"])
        self.assertIsNone(st["recurrence_rate"])

    def test_half_open_interval_and_recurrence(self):
        hits = [
            {"sid": "s1", "ts": "2026-10-01 00:00:00", "resolved": False},
            {"sid": "s1", "ts": "2026-10-07 23:59:59", "resolved": True},
            {"sid": "s2", "ts": "2026-10-08 00:00:00", "resolved": False},
        ]
        st = window_stats(hits, "2026-10-01 00:00:00", "2026-10-08 00:00:00")
        self.assertEqual(st["errors"], 2)      # 右端那条不计（左闭右开）
        self.assertEqual(st["sessions"], 1)
        self.assertEqual(st["unresolved"], 1)
        self.assertAlmostEqual(st["unresolved_rate"], 0.5)
        self.assertAlmostEqual(st["recurrence_rate"], 1.0)  # 同会话 2 次 = 复发

    def test_missing_ts_is_not_counted(self):
        """ts 为 NULL 的脏行不进任何窗口（不然它会同时落进两个窗口）。"""
        st = window_stats([{"sid": "s", "ts": None, "resolved": False}],
                          "2026-10-01 00:00:00", "2026-10-08 00:00:00")
        self.assertEqual(st["errors"], 0)


class TestJudge(unittest.TestCase):
    """判定层（元测试）：恒真实现与"没有样本门槛"的实现在这里必红。"""

    @staticmethod
    def _stat(errors: int, unresolved: int) -> dict:
        return {"errors": errors, "sessions": errors, "unresolved": unresolved,
                "unresolved_rate": unresolved / errors,
                "recurrence_rate": 0.0}

    def test_three_directions_and_threshold_boundaries(self):
        """永远返回"无变化"（恒真）在这里红：三个方向必须给出三种判定。"""
        b = self._stat(100, 50)
        self.assertEqual(judge(b, self._stat(100, 45))[0], "改善")    # -10%（含）
        self.assertEqual(judge(b, self._stat(100, 46))[0], "无变化")  # -8%
        self.assertEqual(judge(b, self._stat(100, 50))[0], "无变化")
        self.assertEqual(judge(b, self._stat(100, 54))[0], "无变化")  # +8%
        self.assertEqual(judge(b, self._stat(100, 55))[0], "变差")    # +10%（含）

    def test_sample_gate_cannot_be_removed(self):
        """去掉样本门槛后，"6 vs 6、Δ=+500%" 会变成「变差」→ 这条当场红。"""
        v, reason = judge(self._stat(6, 1), self._stat(6, 6))
        self.assertEqual(v, "不下结论")          # 各 6 条（>= 5），被"合计 12 < 20"拦住
        self.assertIn("样本不足", reason)
        # 任一侧 < 5 同样拦（哪怕合计够）
        self.assertEqual(judge(self._stat(4, 0), self._stat(30, 30))[0], "不下结论")

    def test_zero_base_convention(self):
        """未解决率从 0 变成 >0 记 +100%（不然除零后容易悄悄退回"无变化"）。"""
        self.assertEqual(relative_change(0.0, 0.3), 1.0)
        self.assertEqual(relative_change(0.0, 0.0), 0.0)
        self.assertEqual(judge(self._stat(20, 0), self._stat(20, 20))[0], "变差")


class TestTwoMetricJudge(unittest.TestCase):
    """v0.47 A1 裁决的**回归钉子**：复发率也进判据（未解决率不再独家说了算）。

    真机触发这条的情形（2026-10-12）：`Edit/Write 前必须先 Read` 命中
    64→41、复发率 80.0%→57.1%、未解决率恒 0.0%——旧口径判「无变化 → 可退休」。
    用户裁决：**未解决率与复发率都要不升才算改善**，任一显著变差即变差。

    **故意破坏指引**：把 `judge` 里的复发率那一支去掉（改回只看
    `unresolved_rate`）→ 下面 `test_recurrence_improvement_alone_is_improved`
    必须变红（它会退回「无变化」）；这是这组测试存在的全部理由。
    """

    @staticmethod
    def _stat(errors: int, unresolved: int, recurrence: float,
              sessions: int | None = None) -> dict:
        return {"errors": errors, "sessions": sessions or errors,
                "unresolved": unresolved,
                "unresolved_rate": unresolved / errors,
                "recurrence_rate": recurrence}

    def test_recurrence_improvement_alone_is_improved(self):
        """未解决率不变 + 复发率显著下降 → **改善**（现口径的判「无变化」是钉子）。"""
        b = self._stat(50, 0, 0.80)
        a = self._stat(50, 0, 0.57)         # -28.75%
        v, reason = judge(b, a)
        self.assertEqual(v, "改善", reason)
        self.assertIn("复发率", reason)

    def test_recurrence_worsening_alone_is_worsened(self):
        """未解决率不变 + 复发率显著上升 → **变差**（不许因为另一项没动就放过）。"""
        b = self._stat(50, 0, 0.30)
        a = self._stat(50, 0, 0.60)         # +100%
        self.assertEqual(judge(b, a)[0], "变差")

    def test_both_improve_is_improved(self):
        b = self._stat(50, 25, 0.80)
        a = self._stat(50, 10, 0.40)
        self.assertEqual(judge(b, a)[0], "改善")

    def test_conflict_resolves_to_worsened(self):
        """一好一坏 → **变差**（保守优先：不能拿"另一项在变好"抵消）。"""
        b = self._stat(50, 25, 0.50)
        a = self._stat(50, 10, 0.90)        # 未解决率 -60%，复发率 +80%
        self.assertEqual(judge(b, a)[0], "变差")

    def test_both_flat_is_unchanged(self):
        b = self._stat(50, 25, 0.50)
        a = self._stat(50, 26, 0.52)        # 都在 ±10% 内
        self.assertEqual(judge(b, a)[0], "无变化")

    def test_missing_recurrence_is_not_judged(self):
        """某窗口没有错误 → 该比率 None：**不参与判定**，而不是当成 0 或当成改善。

        （"没复发"与"没数据"必须分得开——§五 16。）
        """
        b = self._stat(20, 10, 0.50)
        a = {"errors": 20, "sessions": 0, "unresolved": 10,
             "unresolved_rate": 0.5, "recurrence_rate": None}
        v, reason = judge(b, a)
        self.assertEqual(v, "无变化", reason)
        self.assertIn("—", reason)          # 无数据展示为「—」，不是 0.0%


class TestKeyMatching(unittest.TestCase):
    """key ↔ pattern 的匹配口径。"""

    def test_normalized_equality(self):
        self.assertTrue(matches_key("tool_permission_revoked",
                                   "tool_permission_revoked"))

    def test_long_containment(self):
        """归一模式常是更长真实错误的前缀，只判相等会漏样本。"""
        self.assertTrue(matches_key("old_string was not found in <q>",
                                    "old_string was not found in <q> (Edit)"))

    def test_short_pattern_does_not_reverse_match(self):
        """短 pattern（< 8 字符）不做反向包含，防 'sandbox' 误命中外文建议句。"""
        self.assertFalse(matches_key("沙箱拦截不等于命令有错（sandbox）", "sandbox"))

    def test_rule_sentence_key_matches_nothing(self):
        """建议句原文 key 与真实 pattern 不吻合 → 0 样本（如实暴露，不硬凑）。"""
        self.assertFalse(matches_key(
            "Edit/Write 前必须先 Read 目标文件最新内容。",
            "cannot edit <q>: file changed since it was read"))


class TestNotePatternBridge(unittest.TestCase):
    """H31 口径的桥：key 是建议句原文时，靠 note 的 `pattern:` 接上错误数据。

    三条判据：
    ① 写了 pattern → 用它匹配并跑出判定（桥一断就退回 key → 样本 0 → 红）；
    ② 没写 → 退回 key 匹配（现行为，不许回归）；
    ③ 写了但命中 0 条 → 样本 0 + 理由点明（不许含糊成"无变化"）。
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _fixture_db(Path(cls.tmp.name))
        cls.errors, _meta = load_errors(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _row(self, note: str | None) -> dict:
        recs = {_BRIDGE_KEY: {"status": "adopted", "adopted_at": _T,
                              "note": note, "updated_at": _T_TS}}
        return evaluate_all(recs, self.errors)["entries"][0]

    def test_note_pattern_drives_matching(self):
        """桥通了：借 note 的 pattern 拿到 IMP 那批样本并判「改善」。"""
        r = self._row(_BRIDGE_NOTE)
        self.assertEqual(r["match_source"], "note.pattern")
        self.assertEqual(r["targets"], [_KEY_IMP])
        self.assertEqual(r["target"], _KEY_IMP)
        self.assertEqual((r["before"]["errors"], r["after"]["errors"]), (20, 21))
        self.assertEqual(r["verdict"], "改善")

    def test_multiple_patterns_are_unioned(self):
        """一条规则覆盖同根因多个 pattern → 取并集（不重复计），样本才够判定。

        会红的情形：只认第一条 pattern（后窗口会变成 21 而不是 24、判定变红）、
        或把各 pattern 的命中列表拼接（重复计 → 样本数翻倍）。
        """
        r = self._row(_BRIDGE_MULTI_NOTE)
        self.assertEqual(r["targets"], [_KEY_IMP, _KEY_UNDEC])
        self.assertEqual((r["before"]["errors"], r["after"]["errors"]), (24, 24))
        self.assertEqual(r["pattern_hits"], 49)     # 42（IMP）+ 7（UNDEC）
        self.assertAlmostEqual(r["before"]["unresolved_rate"], 12 / 24)
        self.assertEqual(r["verdict"], "改善")

    def test_without_note_falls_back_to_key(self):
        """没写 pattern: → 用 key 匹配（建议句原文 → 样本 0）——旧行为不回归。"""
        r = self._row("已并入 AGENTS.md")           # 没有可解析的 pattern 片段
        self.assertEqual(r["match_source"], "key")
        self.assertEqual(r["target"], _BRIDGE_KEY)
        self.assertEqual(r["before"]["errors"], 0)
        self.assertEqual(r["after"]["errors"], 0)
        self.assertEqual(r["verdict"], "不下结论")
        self.assertEqual(r["verdict_label"], "不下结论（样本不足）")

    def test_note_pattern_with_no_hits_says_so(self):
        r = self._row(_BRIDGE_MISS_NOTE)
        self.assertEqual(r["match_source"], "note.pattern")
        self.assertTrue(r["pattern_miss"])
        self.assertEqual((r["before"]["errors"], r["after"]["errors"]), (0, 0))
        self.assertEqual(r["verdict"], "不下结论")
        self.assertIn("未命中任何错误", r["reason"])

    def test_parse_tolerance(self):
        """解析容错：分号混写、大小写、多行、空值、非字符串都各有说法。"""
        self.assertEqual(parse_note_pattern("pattern: abc"), "abc")
        self.assertEqual(parse_note_pattern("已退休；PATTERN: abc"), "abc")
        self.assertEqual(parse_note_pattern("x\npattern: abc"), "abc")
        self.assertIsNone(parse_note_pattern("已退休"))
        self.assertIsNone(parse_note_pattern("pattern:"))
        self.assertIsNone(parse_note_pattern(None))
        self.assertEqual(parse_note_patterns("pattern: a\npattern: b"), ["a", "b"])
        self.assertEqual(parse_note_patterns("pattern: a; pattern: a"), ["a"])
        self.assertEqual(parse_note_patterns("没有标记"), [])
        self.assertEqual(parse_note_patterns(None), [])


# ---- 判定端到端（合成库 + 真 resolved 计算） -------------------------------

class TestHarvestEvalVerdicts(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _fixture_db(Path(cls.tmp.name))
        cls.errors, _meta = load_errors(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _eval(self, since: str | None = None, **kw) -> tuple[dict, dict]:
        result = evaluate_all(_records(**kw), self.errors, since=since)
        return result, {r["key"]: r for r in result["entries"]}

    def test_fixture_patterns_match_their_keys(self):
        """夹具自检：每条错误原文归一后必须恰好等于它声明的 key。

        会红的情形：夹具写错一个字符（如把 `"a.txt"` 的引号去掉）→ 该 key 的
        样本变成 0，四种判定的断言会跟着一起红。先钉死它，免得红了还要怀疑工具。
        """
        for raw, key in ((_ERR_IMP, _KEY_IMP), (_ERR_UNCH, _KEY_UNCH),
                         (_ERR_WORSE, _KEY_WORSE), (_ERR_GATE, _KEY_GATE),
                         (_ERR_UNDEC, _KEY_UNDEC)):
            self.assertEqual(normalize_error(raw), key)

    def test_four_verdicts(self):
        """四种判定各一例（同一条断言会红于：任一判定算错/阈值失效）。"""
        _res, rows = self._eval()
        self.assertEqual(rows[_KEY_IMP]["verdict"], "改善")
        self.assertEqual(rows[_KEY_UNCH]["verdict"], "无变化")
        self.assertEqual(rows[_KEY_WORSE]["verdict"], "变差")
        self.assertEqual(rows[_KEY_GATE]["verdict"], "不下结论")
        self.assertEqual(rows[_KEY_GATE]["verdict_label"], "不下结论（样本不足）")

    def test_improved_samples_rates_and_boundary(self):
        _res, rows = self._eval()
        r = rows[_KEY_IMP]
        self.assertEqual(r["boundary"], _T_TS)
        self.assertEqual(r["boundary_source"], "adopted_at")
        self.assertEqual(r["before"]["errors"], 20)
        self.assertEqual(r["after"]["errors"], 21)   # 含恰在 T 的那条（左闭右开）
        self.assertAlmostEqual(r["before"]["unresolved_rate"], 0.5)
        self.assertAlmostEqual(r["after"]["unresolved_rate"], 4 / 21)
        self.assertEqual(r["pattern_hits"], 42)      # 41 条在窗口内 + 1 条 T+46 天
        self.assertEqual(r["action"], "保持")

    def test_unchanged_and_recurrence_definition(self):
        """复发率 = 同会话出现 >= 2 次的会话占比：5 个会话 / 共 15 个 = 33.3%。

        会红的情形：口径改成"跨会话复发"、或把 >= 2 写成 >= 3（数字变 0）。
        """
        _res, rows = self._eval()
        r = rows[_KEY_UNCH]
        self.assertEqual(r["before"]["errors"], 20)
        self.assertEqual(r["before"]["sessions"], 15)
        self.assertEqual(r["before"]["unresolved"], 10)
        self.assertAlmostEqual(r["before"]["recurrence_rate"], 5 / 15)
        self.assertAlmostEqual(r["after"]["recurrence_rate"], 5 / 15)
        self.assertEqual(r["verdict"], "无变化")
        self.assertEqual(r["action"], "该规则可退休（防 AGENTS.md 膨胀成噪声墙）")

    def test_worse_gives_rollback_advice(self):
        _res, rows = self._eval()
        r = rows[_KEY_WORSE]
        self.assertAlmostEqual(r["before"]["unresolved_rate"], 0.5)
        self.assertAlmostEqual(r["after"]["unresolved_rate"], 0.8)
        self.assertGreaterEqual(r["delta"], 0.1)
        self.assertEqual(r["action"], "建议回滚该规则")

    def test_sample_gate_beats_huge_change(self):
        """GATE：16.7% → 100%（Δ +500%），各窗口都 >= 5，但合计仅 12 < 20 → 不下结论。

        会红的情形：判定里先看幅度后看样本，或干脆去掉合计门槛 → 变成「变差」。
        """
        _res, rows = self._eval()
        r = rows[_KEY_GATE]
        self.assertEqual(r["before"]["errors"], 6)
        self.assertEqual(r["after"]["errors"], 6)
        self.assertGreaterEqual(r["delta"], 1.0)
        self.assertEqual(r["verdict"], "不下结论")
        self.assertIn("样本不足", r["reason"])
        self.assertEqual(r["action"], "样本不足，暂不处置")

    def test_missing_boundary_is_inconclusive_not_unchanged(self):
        """无 adopted_at 且无 since → 「不下结论（无分界日期）」，不许说成「无变化」。"""
        _res, rows = self._eval()
        r = rows[_KEY_UNDEC]
        self.assertIsNone(r["boundary"])
        self.assertIsNone(r["before"])          # 没有分界就不该有窗口数字
        self.assertIsNone(r["after"])
        self.assertEqual(r["pattern_hits"], 7)  # 数据在，只是没有分界
        self.assertEqual(r["verdict"], "不下结论")
        self.assertEqual(r["verdict_label"], "不下结论（无分界日期）")
        self.assertNotEqual(r["verdict"], "无变化")

    def test_since_fallback_when_adopted_at_missing(self):
        """没有 adopted_at 但有 --since → 走 since 路径（分界来源要如实标）。"""
        _res, rows = self._eval(since=_T)
        r = rows[_KEY_UNDEC]
        self.assertEqual(r["boundary"], _T_TS)
        self.assertEqual(r["boundary_source"], "since")
        self.assertEqual((r["before"]["errors"], r["after"]["errors"]), (4, 3))
        self.assertEqual(r["verdict_label"], "不下结论（样本不足）")

    def test_adopted_at_wins_over_since(self):
        _res, rows = self._eval(since="2026-01-01")
        self.assertEqual(rows[_KEY_IMP]["boundary_source"], "adopted_at")
        self.assertEqual(rows[_KEY_IMP]["boundary"], _T_TS)

    def test_only_adopted_is_evaluated(self):
        """pending/rejected 不评估，但必须能被数出来（"没评估"≠"评估了没变化"）。"""
        result, rows = self._eval()
        self.assertEqual(result["adopted_total"], 5)
        self.assertEqual(result["skipped"], 1)
        self.assertNotIn(_KEY_REJECTED, rows)
        self.assertEqual(result["counts"],
                         {"变差": 1, "无变化": 1, "改善": 1, "不下结论": 2})

    def test_bad_window_raises(self):
        with self.assertRaises(ValueError):
            evaluate_all(_records(), self.errors, window_days=0)

    def test_topic_rows_are_skipped_not_evaluated(self):
        """`topic:<id>`（P2-5 登记留痕）不进评估表：主题不是规则、没有错误 pattern，
        逐条跑只会产出"不下结论（样本不足 0/0）"把报告淹成噪音。

        会红的情形：跳过逻辑被删 → topic 行会出现在 entries 里、
        `skipped_topic` 变 0、`adopted_total` 变 6。
        """
        recs = _records()
        recs[f"{TOPIC_PREFIX}010"] = {"status": "adopted", "adopted_at": _T,
                                      "note": "主题注册表：小说",
                                      "updated_at": _T_TS}
        result = evaluate_all(recs, self.errors)
        self.assertEqual(result["skipped_topic"], 1)
        self.assertEqual(result["adopted_total"], 5)     # 口径 = 可评估的条数
        self.assertEqual(result["skipped"], 1)           # 只有那条 rejected
        self.assertNotIn(f"{TOPIC_PREFIX}010",
                         {r["key"] for r in result["entries"]})
        # 账目闭合：可评估 + 非 adopted + 跳过的主题行 = 台账总行数
        self.assertEqual(len(result["entries"]) + result["skipped"]
                         + result["skipped_topic"], len(recs))

    def test_no_topic_rows_means_zero(self):
        """没有主题行 → `skipped_topic == 0`（不回归、不虚报跳过）。"""
        result, _rows = self._eval()
        self.assertEqual(result["skipped_topic"], 0)


# ---- I/O 入口 + 渲染 -------------------------------------------------------

class TestRunEvalIo(unittest.TestCase):
    """run_eval：只读采集库 + 读台账 → 报告结构与渲染。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.db = _fixture_db(cls.root)
        cls.meta = cls.root / "suggestions_meta.db"
        for key in (_KEY_IMP, _KEY_UNCH, _KEY_WORSE, _KEY_GATE, _KEY_UNDEC):
            set_status(cls.meta, key, "adopted", adopted_at=_T)
        set_status(cls.meta, _KEY_REJECTED, "rejected")
        # P2-5 人账：主题登记留痕行（真台账里 14 条），必须被跳过
        set_status(cls.meta, f"{TOPIC_PREFIX}010", "adopted", adopted_at=_T,
                   note="主题注册表：小说")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_run_eval_end_to_end_and_render(self):
        result = run_eval(self.db, self.meta)
        self.assertEqual(result["adopted_total"], 5)
        self.assertEqual(result["skipped"], 1)          # 那条 rejected
        self.assertEqual(result["skipped_topic"], 1)    # 那条 topic:010
        self.assertEqual(result["errors_total"], 141)   # 夹具错误总数
        txt = render_report(result)
        self.assertIn("| key | 分界 |", txt)
        self.assertIn("该规则可退休", txt)
        self.assertIn("建议回滚该规则", txt)
        self.assertIn("主题级台账 1 条已跳过", txt)
        # 纪律必须写在报告里（"自"= 评估自动化，不是改动自动化）
        self.assertIn("只评估，绝不自动改", txt)

    def test_run_eval_rejects_bad_since(self):
        with self.assertRaises(ValueError):
            run_eval(self.db, self.meta, since="2026/10/05")

    def test_render_escapes_pipe_in_key(self):
        """key 里带 `|`（错误原文里常见）不能把 Markdown 表格切碎。"""
        result = {"window_days": 7, "threshold": 0.1, "min_per_window": 5,
                  "min_total": 20, "since": None, "errors_total": 0,
                  "adopted_total": 1, "skipped": 0,
                  "counts": {"变差": 0, "无变化": 0, "改善": 0, "不下结论": 1},
                  "entries": [evaluate_entry("a|b", [])]}
        self.assertIn("a\\|b", render_report(result))


# ---- 台账侧：adopted_at / note 的迁移与语义 --------------------------------

class TestSuggestMetaAdoptedAt(unittest.TestCase):
    """只增不减语义 + 迁移；旧调用方（三个位置参数）必须一个都不坏。"""

    def test_migration_adds_columns_to_old_db(self):
        """旧库（只有 key/status/updated_at）→ 读不炸、写后列齐。"""
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "old.db"
            con = sqlite3.connect(str(meta))
            con.execute("CREATE TABLE suggestion_status ("
                        "key TEXT PRIMARY KEY, status TEXT NOT NULL, "
                        "updated_at TEXT NOT NULL)")
            con.execute("INSERT INTO suggestion_status VALUES"
                        "('旧条目', 'adopted', '2026-09-01 00:00:00')")
            con.commit()
            con.close()
            # 读路径：缺列按 None，且**不改库**
            recs = load_records(meta)
            self.assertIsNone(recs["旧条目"]["adopted_at"])
            self.assertIsNone(recs["旧条目"]["note"])
            # 写路径：两条列都补上，旧行的历史状态照读
            set_status(meta, "旧条目", "adopted", adopted_at="2026-10-05")
            set_status(meta, "新条目", "adopted", note="首次采纳")
            con = sqlite3.connect(str(meta))
            cols = {r[1] for r in con.execute(
                "PRAGMA table_info(suggestion_status)")}
            con.close()
            self.assertLessEqual({"adopted_at", "note"}, cols)
            recs = load_records(meta)
            self.assertEqual(recs["旧条目"]["adopted_at"], "2026-10-05")
            self.assertEqual(recs["新条目"]["note"], "首次采纳")

    def test_adopted_at_is_only_increased(self):
        """已采纳 + 未显式给日期 → 保留历史分界（不许刷成"今天"）。

        会红的情形：把 `prev_adopted or now` 改成恒 `now` —— 第二行断言当场红。
        """
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "m.db"
            set_status(meta, "k", "adopted", adopted_at="2026-01-02")
            set_status(meta, "k", "adopted")             # 未给 → 保留
            self.assertEqual(load_records(meta)["k"]["adopted_at"], "2026-01-02")
            set_status(meta, "k", "adopted",
                       adopted_at="2026-01-02 03:04:05")  # 显式 → 回填覆盖
            self.assertEqual(load_records(meta)["k"]["adopted_at"],
                             "2026-01-02 03:04:05")

    def test_first_adoption_stamps_now(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "m.db"
            set_status(meta, "k", "adopted")
            self.assertRegex(load_records(meta)["k"]["adopted_at"],
                             r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_non_adopted_clears_adopted_at(self):
        """pending/rejected 不再是采纳态 → 分界清空（否则旧分界还会继续当 A/B 轴）。"""
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "m.db"
            set_status(meta, "k", "adopted", adopted_at="2026-01-02")
            set_status(meta, "k", "rejected")
            rec = load_records(meta)["k"]
            self.assertEqual(rec["status"], "rejected")
            self.assertIsNone(rec["adopted_at"])

    def test_stamp_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "m.db"
            for bad in ("2026/01/02", "2026-1-2", "01-02-2026", "昨天", ""):
                with self.assertRaises(ValueError, msg=bad):
                    set_status(meta, "k", "adopted", adopted_at=bad)
            self.assertEqual(validate_stamp("2026-01-02"), "2026-01-02")
            self.assertEqual(validate_stamp("2026-01-02 03:04:05"),
                             "2026-01-02 03:04:05")

    def test_note_kept_when_none_and_cleared_when_empty(self):
        """note=None 保留既有说明（只增不减）；note='' 才清空。"""
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "m.db"
            set_status(meta, "k", "adopted", note="建议句已退休")
            set_status(meta, "k", "adopted")             # 未给 → 保留
            self.assertEqual(load_records(meta)["k"]["note"], "建议句已退休")
            set_status(meta, "k", "adopted", note="")    # 显式清空
            self.assertEqual(load_records(meta)["k"]["note"], "")

    def test_old_call_signature_still_works(self):
        """additive 保证：老的三个位置参数调用一个都不许坏。"""
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "m.db"
            set_status(meta, "k", "adopted")
            rec = load_records(meta)["k"]
            self.assertEqual(rec["status"], "adopted")
            self.assertIsNotNone(rec["adopted_at"])
            self.assertIsNone(rec["note"])


if __name__ == "__main__":
    unittest.main()
