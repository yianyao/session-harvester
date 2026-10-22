# -*- coding: utf-8 -*-
"""P6 归因层测试：规则表加载 / 两出口分离 / 台账结构对账。

对应 `_eng/TODO-attrib-review-P6.md` P6-1、P6-2、P6-3 的门禁栏。

本模块的三条**会红的门**（每条都能说出"什么情况下红"，见 AGENTS.md §3 11）：
- P6-1：缺四要素任一 → 加载必须抛（故意破坏规则表即红）；
- P6-2：给 `render_brief` 加 `[:60]` 截断 → 必须红（项目栽过的"报告截断
  导致下游静默漏数据"教训，SOP §3.3）；
- P6-3：`attribations` 表结构必须与 `card_review` **同构**（列名/类型对账）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvester.attrib import (  # noqa: E402
    ATTRIB_VERDICTS, DEFAULT_RULES, JSON_SCHEMA, MAX_ANCHORS, RuleError,
    _compile, attribute, collect_attributions, ensure_attrib_db,
    filter_reviewed, list_verdicts, load_rules, load_verdict_map,
    record_verdict, render_brief, render_json)
from harvester.cards import _REVIEW_SCHEMA, ensure_review_db  # noqa: E402
from harvester.errstats import CLASSES, classify_error  # noqa: E402

#: attribations 相对 card_review 的**额外列**（报备制：新增列须登记在此，
#: 否则 test_schema_is_isomorphic_to_card_review 会红）。
#: `class` 的存在理由：台账记**裁决当时**的类别——将来规则改了 class，
#: 台账仍能证明"人当时批的是哪一类"。
_ADDITIVE_COLS = ("class",)

#: 两表的**列角色对齐**（左 = card_review / 右 = attribations）。
#: 角色相同、语义名不同：card_file↔pattern_key（定位对象）、
#: warn_key↔rule_id（定位候选）。结构对账按角色比类型，不按列名比。
_ROLE_PAIRS = (
    ("card_file", "pattern_key"),
    ("warn_key", "rule_id"),
    ("verdict", "verdict"),
    ("note", "note"),
    ("reviewed_at", "reviewed_at"),
    ("reviewed_by", "reviewed_by"),
)


def _errors(*specs) -> list[dict]:
    """构造事实层 errors（errstats.collect_errors_from_db 的产物形态）。"""
    out = []
    for i, (pat, err, cls) in enumerate(specs):
        out.append({"sid": f"s{i % 2}", "seq": i + 1, "ts": "2026-10-12 00:00:00",
                    "tool": "Edit", "error": err, "source": "dsh",
                    "model": "deepseek-flash", "class": cls, "pattern": pat,
                    "bucket": "中途", "resolved": i % 2 == 0, "skill": None})
    return out


class TestRulesLoading(unittest.TestCase):
    """P6-1：规则表四要素齐备才准载入（SOP A3 fail loud）。"""

    def test_default_rules_file_exists_and_loads(self):
        """随包规则表存在且可载入（路径在 harvester/data/，随包分发）。"""
        self.assertTrue(DEFAULT_RULES.is_file(),
                        f"规则表缺失: {DEFAULT_RULES}")
        rules = load_rules()
        self.assertGreaterEqual(len(rules), 3, "起步应有 FS_* 三条规则")
        ids = [r["id"] for r in rules]
        self.assertEqual(len(ids), len(set(ids)), "规则 id 必须唯一")

    def test_starter_rules_cover_fs_codes(self):
        """起步三条 FS_* 必须覆盖设计文档 §1.1 实测的三个码。"""
        rules = load_rules()
        blob = " ".join(m for r in rules for m in r["match"])
        for code in ("FS_NOT_OBSERVED", "FS_EDIT_NOT_FOUND", "FS_STALE_VERSION"):
            self.assertIn(code, blob, f"规则表未覆盖 {code}")

    def test_all_rules_have_four_elements(self):
        """每条规则四要素齐备且 class 在既有四分类内（不另造分类体系）。"""
        for r in load_rules():
            for f in ("match", "class", "why", "fix"):
                self.assertTrue(r.get(f), f"{r['id']} 缺 {f}")
            self.assertIn(r["class"], CLASSES, f"{r['id']} class 越界")

    def test_missing_element_fails_loud(self):
        """**会红的门**：缺四要素任一 → 加载即抛 RuleError。

        这条正是 A3 的全部要点：缺 fix 的归因是"知道有病、没有药方"。
        """
        base = {"id": "AR-X", "class": "env", "match": ["x"],
                "why": "w", "fix": "f"}
        with tempfile.TemporaryDirectory() as td:
            for missing in ("match", "class", "why", "fix"):
                body = {k: v for k, v in base.items() if k != missing}
                p = Path(td) / f"miss-{missing}.yaml"
                p.write_text(_dump_rule(body), encoding="utf-8")
                with self.assertRaises(RuleError, msg=f"缺 {missing} 未报错"):
                    load_rules(p)

    def test_bad_class_fails_loud(self):
        """class 越界（自造分类）→ fail loud。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad-class.yaml"
            p.write_text(_dump_rule({"id": "AR-X", "class": "my_own_class",
                                     "match": ["x"], "why": "w", "fix": "f"}),
                         encoding="utf-8")
            with self.assertRaises(RuleError):
                load_rules(p)

    def test_missing_file_fails_loud(self):
        """规则表不存在 → fail loud（静默返回空会让"归因缺口"凭空消失）。"""
        with self.assertRaises(RuleError):
            load_rules(Path("no-such-rules-file.yaml"))

    def test_duplicate_id_fails_loud(self):
        """规则 id 重复 → fail loud（否则台账键有歧义）。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "dup.yaml"
            one = _dump_rule({"id": "AR-1", "class": "env", "match": ["a"],
                              "why": "w", "fix": "f"}, head=False)
            two = _dump_rule({"id": "AR-1", "class": "env", "match": ["b"],
                              "why": "w", "fix": "f"}, head=False)
            p.write_text("version: 1\nrules:\n" + one + two, encoding="utf-8")
            with self.assertRaises(RuleError):
                load_rules(p)

    def test_bad_regex_fails_loud(self):
        """match 里的非法正则 → fail loud（别静默不匹配）。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad-re.yaml"
            p.write_text(_dump_rule({"id": "AR-1", "class": "env",
                                     "match": ["[unclosed"],
                                     "why": "w", "fix": "f"}),
                         encoding="utf-8")
            with self.assertRaises(RuleError):
                _compile(load_rules(p))


def _dump_rule(body: dict, head: bool = True) -> str:
    """把规则 dict 写成 YAML 片段（测试夹具，避免手写缩进出错）。"""
    lines = []
    if head:
        lines.append("version: 1")
        lines.append("rules:")
    lines.append(f"  - id: {body['id']}")
    for k in ("class", "why", "fix"):
        if k in body:
            lines.append(f"    {k}: {body[k]}")
    if "match" in body:
        items = ", ".join(f'"{m}"' for m in body["match"])
        lines.append(f"    match: [{items}]")
    for k in ("confidence", "verified"):
        if k in body:
            lines.append(f'    {k}: "{body[k]}"')
    return "\n".join(lines) + "\n"


class TestAttribution(unittest.TestCase):
    """P6-2：归因命中的机制与"候选"语义。"""

    def setUp(self):
        self.compiled = _compile(load_rules())

    def test_fs_code_attributed_where_english_is_not_unclassified(self):
        """机制验证（本工程立论）：同一件事两种写法，DSH 的 FS_* 原本落
        unclassified，归因层把它归到与英文写法**同一类**。"""
        en = "file has not been read"
        fs = "FS_NOT_OBSERVED"
        self.assertEqual(classify_error(en), "tool_interface")  # 事实层：英文命中
        self.assertEqual(classify_error(fs), "unclassified")    # 事实层：FS_* 漏掉
        hit_en = attribute(en, self.compiled)
        hit_fs = attribute(fs, self.compiled)
        self.assertIsNotNone(hit_fs, "FS_* 未被归因——本工程的核心收益为 0")
        self.assertIsNotNone(hit_en)
        # 归因把两种写法归到同一 class（这正是"归一"的定义）
        self.assertEqual(hit_fs["class"], hit_en["class"])
        self.assertEqual(hit_fs["class"], "tool_interface")

    def test_hit_is_marked_candidate(self):
        """每条命中必须显式标 candidate=True（A1：候选会错，不是裁决）。"""
        hit = attribute("FS_EDIT_NOT_FOUND", self.compiled)
        self.assertTrue(hit["candidate"])
        for f in ("rule_id", "class", "why", "fix", "confidence", "matched"):
            self.assertIn(f, hit)

    def test_no_match_returns_none(self):
        """未命中返回 None（渲染层须显式标"未归因"，不许静默吞掉）。"""
        self.assertIsNone(attribute("完全不相干的报错 zzz", self.compiled))

    def test_no_over_attribution_missing_file_is_not_edit_miss(self):
        """**归因会错**的实测实例（A1 的意义所在）：这条必须归 context 而不是
        tool_interface。

        `File not found: <path>. Use an empty old_string to create a new file.`
        的真实根因是**文件不存在**；早期规则表用裸 `old_string` 当匹配词，
        把它劫持成了「编辑未命中」（实测发生过）。

        判据：该串的归因类必须与"文件不存在"一致，而不是与"编辑未命中"一致。
        """
        missing_file = ("File not found: C:\\x\\y.py. "
                        "Use an empty old_string to create a new file.")
        hit = attribute(missing_file, self.compiled)
        self.assertIsNotNone(hit)
        self.assertNotEqual(
            hit["rule_id"], "AR-002",
            "缺文件被误归为「编辑未命中」——裸 old_string 匹配词又回来了")
        self.assertEqual(hit["class"], "context",
                         "文件不存在的根因应归 context")

    def test_real_edit_miss_still_attributed(self):
        """收紧 AR-002 后，**真正的**编辑未命中仍须被归因（别收过了头）。"""
        for text in ("FS_EDIT_NOT_FOUND",
                     "workspace_edit_match_not_found",
                     "String to replace not found in file.",
                     "Error: No changes to make: old_string and new_string "
                     "are exactly the same."):
            hit = attribute(text, self.compiled)
            self.assertIsNotNone(hit, f"漏归因: {text}")
            self.assertEqual(hit["rule_id"], "AR-002", f"归错规则: {text}")

    def test_collect_aggregates_by_pattern(self):
        """按归一模式聚合；未命中模式的 rule 必须为 None（不许假装归因了）。"""
        errs = _errors(
            ("FS_NOT_OBSERVED", "FS_NOT_OBSERVED", "unclassified"),
            ("FS_NOT_OBSERVED", "FS_NOT_OBSERVED", "unclassified"),
            ("mystery", "完全不相干的报错 zzz", "unclassified"),
        )
        res = collect_attributions(errs, load_rules())
        self.assertEqual(res["summary"]["patterns_total"], 2)
        self.assertEqual(res["summary"]["patterns_attributed"], 1)
        self.assertEqual(res["summary"]["errors_attributed"], 2)
        self.assertEqual(res["summary"]["errors_unattributed"], 1)
        self.assertIsNotNone(res["by_pattern"]["FS_NOT_OBSERVED"]["rule"])
        self.assertIsNone(res["by_pattern"]["mystery"]["rule"])

    def test_anchors_capped_but_count_is_full(self):
        """锚点有上限（展示用），但 **count 是全量**——展示上限不许污染计数。"""
        errs = _errors(*[("FS_NOT_OBSERVED", "FS_NOT_OBSERVED", "unclassified")]
                       * (MAX_ANCHORS + 7))
        res = collect_attributions(errs, load_rules())
        row = res["by_pattern"]["FS_NOT_OBSERVED"]
        self.assertEqual(len(row["anchors"]), MAX_ANCHORS)
        self.assertEqual(row["count"], MAX_ANCHORS + 7)


class TestTwoExits(unittest.TestCase):
    """P6-2 门禁：人读 / 机读两个出口**不共用实现**，且都不截断。"""

    def _result(self, n_extra: int = 0):
        specs = [("FS_NOT_OBSERVED", "FS_NOT_OBSERVED", "unclassified")]
        for i in range(n_extra):
            specs.append((f"mystery-{i}", f"完全不相干的报错 zzz {i}",
                          "unclassified"))
        return collect_attributions(_errors(*specs), load_rules())

    def test_brief_lists_every_pattern_no_truncation(self):
        """**会红的门**：brief 必须全量列出——给 render_brief 加 `[:60]`
        截断（无论截条目还是截行）这条必红。

        对应项目栽过的教训：报告为篇幅只印 60 条 + 尾部"另有 N 条"，
        下游照抄会**静默漏掉尾部且不报错**（SOP §3.3）。
        """
        res = self._result(n_extra=25)
        brief = render_brief(res)
        for pat in res["by_pattern"]:
            self.assertIn(pat, brief, f"brief 漏掉模式 {pat}（被截断了？）")
        # 每个模式都要有一个 ### 段（不允许"其余 N 个从略"这类写法）
        self.assertEqual(brief.count("### x"), res["summary"]["patterns_total"])
        self.assertNotIn("从略", brief)
        self.assertNotIn("另有", brief)

    def test_brief_shows_why_and_fix_for_attributed(self):
        """已归因的行必须同时给 why 与 fix（A3：知道有病还得有药方）。"""
        brief = render_brief(self._result())
        self.assertIn("根因候选（why）：", brief)
        self.assertIn("应对候选（fix）：", brief)

    def test_brief_marks_unattributed_explicitly(self):
        """未归因的行必须**显式标"未归因"**，不许空着让人误以为没问题。"""
        brief = render_brief(self._result(n_extra=1))
        self.assertIn("[未归因]", brief)
        self.assertIn("未归因——无规则命中", brief)

    def test_brief_does_not_truncate_error_text(self):
        """错误原文**不许截断**——长原文必须完整出现在人读出口里。

        这条同时钉住"给 `_fmt_anchors(...)` 加 `[:60]` 会红"：变异自测
        （docs/reports/mutation-attrib.py）确认过本条是**真门**。
        """
        long_err = "FS_NOT_OBSERVED " + "很长的上下文" * 40
        res = collect_attributions(
            _errors(("FS_NOT_OBSERVED", long_err, "unclassified")), load_rules())
        brief = render_brief(res)
        self.assertIn(long_err, brief, "错误原文被截断了")
        # 长原文必须**逐字全文**在，且长度确实超过 60（否则本条钉不住 [:60]）
        self.assertGreater(len(long_err), 60)
        self.assertIn(long_err[:60], brief)
        self.assertNotIn(long_err[:60] + "\n", brief.replace(long_err, ""),
                         "疑似在 60 字符处断行")

    def test_brief_anchor_and_pattern_not_truncated_at_60(self):
        """**会红的门**：`[:60]` 出现在 brief 任何一处都必须红。

        项目栽过的形态就是"看上去只截了一点"，而下游照抄会静默漏数据。
        判据：所有超过 60 字符的模式串与错误原文，都必须**整串**出现。
        """
        long_pat = "模式前缀" * 20          # 100 字符
        long_err = "FS_NOT_OBSERVED " + "x" * 90
        res = collect_attributions(
            _errors((long_pat, long_err, "unclassified")), load_rules())
        brief = render_brief(res)
        self.assertGreater(len(long_pat), 60)
        self.assertIn(long_pat, brief, "模式串被截断")
        self.assertIn(long_err, brief, "错误原文被截断")

    def test_anchors_strip_tool_echo(self):
        """证据原文必须走共享口径 clean_error_sample：截掉 Edit 类失败尾部
        附带的工具回显（用户文档内容）。

        实测发现的真缺陷：不加这一步时，brief 的锚点行会把被编辑文件的
        大段源码/正文打印出来（真库 x64 那条 Edit 未命中就是如此）——
        证据被污染，且可能把用户正文带进产物。口径与建议池/G1 报告同源。
        """
        raw = ("String to replace not found in file. 请先 Read 再 Edit.\n"
               'String: "function secret() { return 42; }"')
        res = collect_attributions(
            _errors(("edit-miss", raw, "tool_interface")), load_rules())
        anchor_err = res["by_pattern"]["edit-miss"]["anchors"][0]["error"]
        self.assertNotIn("function secret", anchor_err,
                         "工具回显（用户文档内容）没被截掉")
        self.assertNotIn("String:", anchor_err)
        self.assertIn("String to replace not found", anchor_err)

    def test_anchor_stripping_is_not_truncation(self):
        """截回显 ≠ 截长度：长错误本体必须**完整**保留（两者别混）。

        这条防"用 clean_error_sample(max_len=...) 顺手把长度也截了"。
        """
        long_body = "错误本体" * 60          # 240 字符，无回显标记
        res = collect_attributions(
            _errors(("long", long_body, "tool_interface")), load_rules())
        got = res["by_pattern"]["long"]["anchors"][0]["error"]
        self.assertEqual(got, long_body, "长错误本体被截短了")

    def test_json_and_brief_are_separate_implementations(self):
        """两出口不共用实现：JSON 是结构化 payload、brief 是 Markdown 正文。

        判据：JSON 里不含 brief 的 Markdown 标题，brief 里不含 JSON 的
        schema 字段——若有人把两者合并成一个函数，这条会红。
        """
        res = self._result()
        js = render_json(res)
        br = render_brief(res)
        self.assertNotIn("# 归因候选清单", js)
        self.assertNotIn(JSON_SCHEMA, br)
        json.loads(js)  # 必须是合法 JSON

    def test_json_has_schema_and_full_patterns(self):
        """机读出口：带 schema、全量 patterns（一个不少）。"""
        res = self._result(n_extra=10)
        doc = json.loads(render_json(res))
        self.assertEqual(doc["schema"], JSON_SCHEMA)
        self.assertEqual(len(doc["patterns"]), res["summary"]["patterns_total"])
        self.assertIn("candidate_semantics", doc["notes"])

    def test_json_carries_snapshot_and_meta(self):
        """口径随行：快照与事实层 meta 必须进机读出口。"""
        res = self._result()
        doc = json.loads(render_json(res, {"error_count": 362, "total_steps": 1},
                                     db_path="x.db", snapshot={"sessions": 1720}))
        self.assertEqual(doc["errors_meta"]["error_count"], 362)
        self.assertEqual(doc["snapshot"]["sessions"], 1720)
        self.assertEqual(doc["db"], "x.db")


class TestLedger(unittest.TestCase):
    """P6-3：attribations 台账照抄 card_review 结构；人裁过的不再进候选。"""

    def test_schema_is_isomorphic_to_card_review(self):
        """**会红的门**：attribations 必须与 card_review **结构同构**。

        判据来自设计文档 §2.3 与 TODO P6-3「照抄 card_review 结构」
        + 同一句里给出的列清单（含 `class`）。

        ⚠ 这两条要求在字面上不可能同时满足：`card_review` 只有 6 列
        （cards.py:219-227，**无 class**），而规格的 attribations 列清单是
        7 列。故本门把"同构"定义为**可检验的两条**：

        ① **角色对齐列逐一类型相同**：两张表的**列角色**一一对应，
           只是语义名不同（`card_file`↔`pattern_key`、`warn_key`↔`rule_id`）
           ——按角色比类型，任何一处漂移即红；
        ② **额外列必须显式声明**：attribations 多出的列必须在
           `_ADDITIVE_COLS` 里列名报备，否则红（防"悄悄加列"变成新事实源）。

        `class` 是②里唯一的报备项，理由：台账要记**裁决当时**的类别——
        将来规则改了 class，台账仍能证明"人当时批的是哪一类"。
        """
        with tempfile.TemporaryDirectory() as td:
            a = ensure_attrib_db(Path(td) / "attrib_meta.db")
            c = ensure_review_db(Path(td) / "cards_meta.db")
            cols_a = self._cols(a, "attribations")
            cols_c = self._cols(c, "card_review")
            names_a = [n for n, _t, _pk in cols_a]
            names_c = [n for n, _t, _pk in cols_c]
            type_a = {n: t for n, t, _pk in cols_a}
            type_c = {n: t for n, t, _pk in cols_c}

            # ① 角色对齐（左侧 card_review / 右侧 attribations），逐对比类型
            for role_c, role_a in _ROLE_PAIRS:
                self.assertIn(role_c, type_c, f"card_review 缺 {role_c}")
                self.assertIn(role_a, type_a, f"attribations 缺 {role_a}")
                self.assertEqual(
                    type_a[role_a], type_c[role_c],
                    f"角色 {role_c}↔{role_a} 类型不同: "
                    f"{type_c[role_c]} vs {type_a[role_a]}")

            # ② 额外列必须显式报备（防静默加列）。
            # "额外"= 既不在 card_review 里同名出现、也不属于角色对齐对的列。
            aligned = {a for _c, a in _ROLE_PAIRS}
            extra = sorted(set(names_a) - set(names_c) - aligned)
            self.assertEqual(extra, sorted(_ADDITIVE_COLS),
                             f"attribations 相对 card_review 的额外列变了: "
                             f"{extra}（新增列须在 _ADDITIVE_COLS 报备）")

            # 主键：都是复合主键两列（两侧一致）
            self.assertEqual(sum(1 for _n, _t, pk in cols_a if pk), 2)
            self.assertEqual(sum(1 for _n, _t, pk in cols_c if pk), 2)
            pk_a = [n for n, _t, pk in cols_a if pk]
            pk_c = [n for n, _t, pk in cols_c if pk]
            self.assertEqual(pk_a, ["pattern_key", "rule_id"])
            self.assertEqual(pk_c, ["card_file", "warn_key"])
            # 规格列清单齐备
            for expect in ("pattern_key", "rule_id", "class", "verdict", "note",
                           "reviewed_at", "reviewed_by"):
                self.assertIn(expect, names_a, f"attribations 缺列 {expect}")

    def test_all_columns_are_text(self):
        """两表全列均为 TEXT（SQLite 弱类型，但声明类型要一致地对齐）。"""
        with tempfile.TemporaryDirectory() as td:
            a = ensure_attrib_db(Path(td) / "attrib_meta.db")
            c = ensure_review_db(Path(td) / "cards_meta.db")
            for db, tbl in ((a, "attribations"), (c, "card_review")):
                for n, t, _pk in self._cols(db, tbl):
                    self.assertEqual(t, "TEXT", f"{tbl}.{n} 类型非 TEXT: {t}")

    def test_notnull_matches_card_review_pattern(self):
        """NOT NULL 形态对齐：两键 + verdict + reviewed_at 非空，note/
        reviewed_by 可空（同 card_review；缺省不猜身份，见 CLI --by 口径）。"""
        with tempfile.TemporaryDirectory() as td:
            a = ensure_attrib_db(Path(td) / "attrib_meta.db")
            con = sqlite3.connect(str(a))
            try:
                info = {r[1]: r[3] for r in
                        con.execute("PRAGMA table_info(attribations)")}
            finally:
                con.close()
            for n in ("pattern_key", "rule_id", "class", "verdict",
                      "reviewed_at"):
                self.assertEqual(info[n], 1, f"{n} 应为 NOT NULL")
            for n in ("note", "reviewed_by"):
                self.assertEqual(info[n], 0, f"{n} 应可空")

    @staticmethod
    def _cols(db: Path, table: str) -> list[tuple[str, str, int]]:
        con = sqlite3.connect(str(db))
        try:
            return [(r[1], r[2], r[5])
                    for r in con.execute(f"PRAGMA table_info({table})")]
        finally:
            con.close()

    def test_verdict_values_are_three_state(self):
        """verdict 三态（accepted/rejected/revise）——归因会错，故必须有"
        驳回"档，不能只有"接受"。"""
        self.assertEqual(set(ATTRIB_VERDICTS),
                         {"accepted", "rejected", "revise"})

    def test_record_and_load_roundtrip(self):
        """落账 → 读回一致（UPSERT 改判=覆盖）。"""
        with tempfile.TemporaryDirectory() as td:
            meta = Path(td) / "attrib_meta.db"
            n = record_verdict(meta, "FS_NOT_OBSERVED", "AR-001",
                               cls="tool_interface", verdict="accepted",
                               note="核过了", reviewed_by="tester")
            self.assertEqual(n, 1)
            vm = load_verdict_map(meta)
            self.assertIn(("FS_NOT_OBSERVED", "AR-001"), vm)
            self.assertEqual(vm[("FS_NOT_OBSERVED", "AR-001")]["verdict"],
                             "accepted")
            # 改判 = 覆盖，不新增行
            record_verdict(meta, "FS_NOT_OBSERVED", "AR-001",
                           cls="tool_interface", verdict="rejected")
            self.assertEqual(len(list_verdicts(meta)), 1)
            self.assertEqual(
                load_verdict_map(meta)[("FS_NOT_OBSERVED", "AR-001")]["verdict"],
                "rejected")

    def test_bad_verdict_raises(self):
        """verdict 越界 → 抛（不许写脏行进台账）。"""
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                record_verdict(Path(td) / "m.db", "p", "AR-1", cls="env",
                               verdict="whatever")

    def test_empty_keys_no_write(self):
        """空 pattern/rule 幂等返回 0，不静默写脏行。"""
        with tempfile.TemporaryDirectory() as td:
            meta = Path(td) / "m.db"
            self.assertEqual(record_verdict(meta, "", "AR-1", cls="env"), 0)
            self.assertEqual(record_verdict(meta, "p", "", cls="env"), 0)

    def test_missing_meta_is_zero_awareness(self):
        """库不存在 → 空映射（不加台账的用户零感知，行为与旧版一致）。"""
        self.assertEqual(load_verdict_map(None), {})
        self.assertEqual(load_verdict_map(Path("no-such.db")), {})

    def test_reviewed_candidate_leaves_candidate_list(self):
        """**核心语义**：人裁过的归因下次不再进候选（同 card_review）。"""
        with tempfile.TemporaryDirectory() as td:
            meta = Path(td) / "attrib_meta.db"
            errs = _errors(
                ("FS_NOT_OBSERVED", "FS_NOT_OBSERVED", "unclassified"),
                ("mystery", "完全不相干的报错 zzz", "unclassified"))
            res = collect_attributions(errs, load_rules())
            self.assertEqual(len(res["by_pattern"]), 2)
            record_verdict(meta, "FS_NOT_OBSERVED", "AR-001",
                           cls="tool_interface", verdict="accepted")
            out = filter_reviewed(res, meta)
            self.assertNotIn("FS_NOT_OBSERVED", out["by_pattern"])
            self.assertIn("mystery", out["by_pattern"])
            self.assertEqual(out["summary"]["patterns_reviewed"], 1)
            # **事实计数不变**——被移出的落 reviewed 段，不静默消失
            self.assertEqual(out["summary"]["errors_total"],
                             res["summary"]["errors_total"])
            self.assertEqual(out["summary"]["errors_reviewed"], 1)

    def test_reviewed_not_filtered_when_rule_changed(self):
        """换规则 = 换了候选内容 → 应重新问人，不被旧裁决静默压掉。

        键是 (pattern_key, rule_id)：同一模式换了规则 id，旧裁决不生效。
        """
        with tempfile.TemporaryDirectory() as td:
            meta = Path(td) / "m.db"
            record_verdict(meta, "FS_NOT_OBSERVED", "AR-OLD",
                           cls="tool_interface", verdict="accepted")
            errs = _errors(("FS_NOT_OBSERVED", "FS_NOT_OBSERVED",
                            "unclassified"))
            res = collect_attributions(errs, load_rules())
            out = filter_reviewed(res, meta)
            self.assertIn("FS_NOT_OBSERVED", out["by_pattern"],
                          "换了规则 id 后旧裁决不该继续压制候选")

    def test_reviewed_shown_in_brief_not_hidden(self):
        """被移出的候选要在 brief 的「已裁决」段出现——否则"处理过"看起来
        像"数据变少了"（静默失效型错误）。"""
        with tempfile.TemporaryDirectory() as td:
            meta = Path(td) / "m.db"
            errs = _errors(("FS_NOT_OBSERVED", "FS_NOT_OBSERVED",
                            "unclassified"))
            res = collect_attributions(errs, load_rules())
            record_verdict(meta, "FS_NOT_OBSERVED", "AR-001",
                           cls="tool_interface", verdict="accepted",
                           note="已核")
            brief = render_brief(filter_reviewed(res, meta))
            self.assertIn("已裁决", brief)
            self.assertIn("已核", brief)


class TestCardReviewSchemaConstantUsed(unittest.TestCase):
    """护栏：确认对账用的 card_review schema 常量真的被 import 到了
    （若有人把 _REVIEW_SCHEMA 改名/移走，本模块导入即红，而不是静默跳过对账）。"""

    def test_schema_constant_is_real(self):
        self.assertIn("card_review", _REVIEW_SCHEMA)
        self.assertIn("warn_key", _REVIEW_SCHEMA)


if __name__ == "__main__":
    unittest.main(verbosity=2)