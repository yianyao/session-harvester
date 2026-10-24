# -*- coding: utf-8 -*-
"""P10 评审包验收门：`evidence-pack` 已**并入 `llm-pack`**，这里钉的是"合并后的契约"。

用户 2026-10-24 裁决合并，理由不是"名字像"，而是当时两条命令各有一套取数与渲染：
`evidence-pack` 有 attrib/--since/--meta/md，`llm-pack` 有 tools/--validate/多面。
合并后**只有一个打包器**（`harvester.llmpack`），`evidence-pack` 只剩翻译参数 +
一句弃用提示。所以本文件的门分三类：

1. **合并真的发生了**：`harvester.evidencepack` 不存在了（旧模块删除）；
   alias 的 md 与 `llm-pack --faces` 逐字相同（同一实现，不是"看起来一样"）。
2. **可评审的定义性特征还在**：md 里有判定槽、行数 = 条目数、裁决列全空
   ——"没有判定槽的包不是评审包，是报告"（P6-6 设计 §3.2）。
3. **诚实边界还在**：artifact 不在包内；tools/chains 不按窗口截；窗口外的老坑
   不许被说成"已经没有了"。
"""
import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvester import cli
from harvester import llmpack as L

REPO = Path(__file__).resolve().parents[1]
REAL_DB = REPO / "harvester.db"

try:
    import yaml as _yaml_mod          # noqa: F401
    _HAS_YAML = True
except ImportError:                    # pragma: no cover - 门禁环境
    _HAS_YAML = False

_NEED_REAL = unittest.skipUnless(_HAS_YAML and REAL_DB.is_file(),
                                 "需要 PyYAML + 真实 harvester.db（集成对账）")


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(argv)
        except SystemExit as e:        # argparse 的 exit
            rc = int(e.code or 0)
    return rc, out.getvalue(), err.getvalue()


class TestMergeReallyHappened(unittest.TestCase):
    def test_old_module_is_gone(self):
        """旧模块文件必须删掉——留着就是"两套实现"的入口。"""
        self.assertFalse((REPO / "harvester" / "evidencepack.py").exists(),
                         "evidencepack.py 还在：合并没做完")
        with self.assertRaises(ImportError):
            __import__("harvester.evidencepack")

    def test_only_one_builder_in_cli(self):
        src = (REPO / "harvester" / "cli.py").read_text(encoding="utf-8")
        self.assertIn("from .llmpack import (build_pack as _lp_build", src)
        self.assertNotIn("from .evidencepack", src)


class TestAliasContract(unittest.TestCase):
    def test_alias_warns_deprecated(self):
        rc, _out, err = _run(["evidence-pack", "--db", str(REPO / "nope.db")])
        self.assertEqual(rc, 2)                 # 库不存在 → 响亮失败
        self.assertIn("已并入 llm-pack", err)

    def test_alias_rejects_unknown_kind(self):
        rc, _out, err = _run(["evidence-pack", "--kind", "nope",
                              "--db", str(REPO / "nope.db")])
        self.assertEqual(rc, 2)

    @_NEED_REAL
    def test_alias_md_identical_to_llm_pack(self):
        """逐字对账：alias 不是"另一个渲染器"，是同一个。"""
        rc1, md1, _ = _run(["evidence-pack", "--kind", "attrib",
                            "--top", "3", "--db", str(REAL_DB)])
        rc2, md2, _ = _run(["llm-pack", "--faces", "attrib", "--limit", "3",
                            "--out", "-", "--db", str(REAL_DB)])
        self.assertEqual((rc1, rc2), (0, 0))

        def strip(s: str) -> str:
            return "\n".join(ln for ln in s.splitlines()
                             if not ln.startswith("- 生成时间："))
        self.assertEqual(strip(md1), strip(md2))


class TestPackIsReviewable(unittest.TestCase):
    @_NEED_REAL
    def test_md_has_verdict_slot(self):
        """**最重要的一条**：判定槽在 md 里、行数 = 条目数、裁决列全空。"""
        rc, md, _ = _run(["llm-pack", "--faces", "attrib", "--limit", "4",
                          "--out", "-", "--db", str(REAL_DB)])
        self.assertEqual(rc, 0)
        self.assertIn("## 判定槽", md)
        head = md.split("## 判定槽", 1)[1]
        rows = [ln for ln in head.splitlines()
                if ln.startswith("| ") and "---" not in ln]
        # 第一行是表头（item_id/subject/verdict/...），其余是条目
        self.assertGreaterEqual(len(rows), 2, "判定槽表没有行")
        body = rows[1:]
        self.assertEqual(len(body), 4, "判定槽行数必须等于条目数")
        for ln in body:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            # item_id | subject | verdict | rule_id | class | note
            self.assertEqual(cells[2], "", "裁决列必须是空的（机器不许预设判定）")
            self.assertEqual(cells[5], "")

    @_NEED_REAL
    def test_attrib_items_carry_anchors_and_candidate(self):
        pack = L.build_pack(REAL_DB, faces=("attrib",), limit=5)
        self.assertTrue(pack["items"])
        for it in pack["items"]:
            self.assertEqual(it["face"], "attrib")
            self.assertTrue(it["facts"]["anchors"], "没有锚点 = 评不了，不许入包")
            if it["attrib"]:
                self.assertTrue(it["attrib"]["candidate"])

    @_NEED_REAL
    def test_reviewed_flag_from_meta(self):
        """给了台账就要标出"已裁决"——否则人会重复裁同一件事。"""
        with tempfile.TemporaryDirectory() as td:
            meta = Path(td) / "attrib_meta.db"
            con = sqlite3.connect(str(meta))
            con.execute("CREATE TABLE attribations (pattern_key TEXT, "
                        "rule_id TEXT, class TEXT, verdict TEXT, note TEXT, "
                        "reviewed_at TEXT, reviewed_by TEXT)")
            pack0 = L.build_pack(REAL_DB, faces=("attrib",), limit=1)
            it0 = pack0["items"][0]
            con.execute("INSERT INTO attribations VALUES (?,?,?,?,?,?,?)",
                        (it0["subject"], (it0["attrib"] or {}).get("rule_id", ""),
                         "x", "accepted", "看过", "2026-10-24", "me"))
            con.commit()
            con.close()
            pack1 = L.build_pack(REAL_DB, faces=("attrib",), limit=1,
                                 meta=meta)
            self.assertEqual(pack1["items"][0]["facts"]["reviewed"], "accepted")


class TestHonestBoundaries(unittest.TestCase):
    def test_boundaries_state_what_is_missing(self):
        b = L._boundaries(("errors", "tools", "chains", "attrib"),
                          since_days=7, meta=Path("m.db"))
        text = "\n".join(b)
        self.assertIn("artifact", text)          # 产物面不在包里
        self.assertIn("不按", text)               # tools/chains 不截窗
        self.assertIn("候选", text)               # 候选会错
        self.assertIn("窗口", text)               # 窗口外不算"没有了"
        self.assertIn("reviewed", text)           # 已裁决标注

    def test_coverage_only_for_error_faces(self):
        self.assertIsNone(L._coverage([], ("tools",), 10),
                          "tools 面没有会话分母，不许造一个百分比出来")
        cov = L._coverage([{"facts": {"anchors": [{"sid": "s1", "seq": 1}]}}],
                          ("errors",), 10)
        self.assertEqual(cov["covered"], 1)
        self.assertEqual(cov["total"], 10)
        self.assertEqual(cov["pct"], 10.0)
        self.assertIn("本包只覆盖上述会话", cov["note"])


class TestValidateStillBites(unittest.TestCase):
    def test_slot_must_match_items(self):
        bad = {"schema": L.SCHEMA,
               "items": [{"item_id": "E-001", "subject": "x", "facts": {},
                          "attrib": None}],
               "slot": {"columns": list(L.SLOT_COLUMNS), "entries": []}}
        self.assertTrue(L.validate_pack(bad), "槽行数 ≠ 条目数必须报错")

        good = json.loads(json.dumps(bad))
        good["slot"]["entries"] = [{"item_id": "E-001", "subject": "x",
                                    "verdict": "", "rule_id": "",
                                    "class": "", "note": ""}]
        self.assertEqual(L.validate_pack(good), [])


if __name__ == "__main__":
    unittest.main()