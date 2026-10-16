# -*- coding: utf-8 -*-
"""v0.51 S4 测试：adapters 整改包（审计 H2/M3/M5/M13/M18/M20）。

四束收敛各有钉子：
- _norm_time 四份 + _fmt_ts 收敛 models.to_local_ts（别名同一性），
  且 to_local_ts 对越界时间戳回 None 不抛（H2：Windows OSError 崩源）；
- safe_join 守卫 sid 拼路径（M18 族 B）：越界 KeyError，不逃数据根；
- raw 三姐妹 load_session 对坏 JSON/顶层非对象转 warns+lossy（M20）；
- zip 成员按路径段精确匹配（M5：user_conversations.json 不再误命中）。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harvester import models  # noqa: E402
from harvester.adapters import deepseek_export, doubao_raw, official_export  # noqa: E402
from harvester.adapters import qianwen_raw, vscode_copilot, yuanbao_raw  # noqa: E402
from harvester.adapters.base import safe_join  # noqa: E402


def _detail_dir(tmp: str, files: dict[str, str]) -> Path:
    d = Path(tmp) / "doubao_raw"
    d.mkdir(parents=True)
    for name, body in files.items():
        (d / f"detail_{name}.json").write_text(body, encoding="utf-8")
    return d


GOOD_DETAIL = json.dumps({
    "name": "会话甲", "create_time": "1760000000", "update_time": "1760000100",
    "n_messages": 1,
    "messages": [{"user_type": 1, "index_in_conv": "0",
                  "create_time": "1760000000",
                  "content_block": [{"block_type": 10000,
                                     "content": {"text_block": {"text": "hi"}}}]}],
}, ensure_ascii=False)


class ToLocalTsGuardTest(unittest.TestCase):
    """H2 根修：越界时间戳 → None（原来 Windows 上抛 OSError 未捕获）。"""

    def test_out_of_range_numeric_returns_none(self):
        """数值入参越界 → None（原来 Windows 上抛 OSError 未捕获，H2）。"""
        for bad in (1e20, -1e20):
            with self.subTest(v=bad):
                self.assertIsNone(models.to_local_ts(bad))

    def test_unparseable_string_passthrough(self):
        """契约：无法解析的字符串原样透传（19/20 位数字串不合 10/13 位
        epoch 正则，按非时间戳字符串走透传口，不硬猜）。"""
        for s in ("9" * 19, "9" * 20):
            with self.subTest(v=s):
                self.assertEqual(models.to_local_ts(s), s)

    def test_valid_still_works(self):
        self.assertEqual(models.to_local_ts(0), "1970-01-01 08:00:00")
        self.assertEqual(models.to_local_ts(1760000000)[:4], "2025")
        self.assertEqual(models.to_local_ts("2026-01-02T03:04:05+08:00"),
                         "2026-01-02 03:04:05")
        self.assertIsNone(models.to_local_ts(None))
        self.assertIsNone(models.to_local_ts("  "))

    def test_norm_time_aliases_converged(self):
        """四份 _norm_time + _fmt_ts 必须就是 to_local_ts（收敛即别名）。"""
        for mod, name in ((deepseek_export, "_norm_time"),
                          (yuanbao_raw, "_norm_time"),
                          (qianwen_raw, "_norm_time"),
                          (doubao_raw, "_norm_time"),
                          (vscode_copilot, "_fmt_ts")):
            with self.subTest(module=mod.__name__):
                self.assertIs(getattr(mod, name), models.to_local_ts,
                              f"{mod.__name__}.{name} 未收敛")


class SafeJoinTest(unittest.TestCase):
    """M18 族 B：resolve 后必须仍在数据根内。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "data"
        self.root.mkdir()

    def test_inside_ok(self):
        p = safe_join(self.root, "detail_x.json")
        self.assertEqual(p.parent, self.root.resolve())
        p2 = safe_join(self.root, "a", "b/c.txt")
        self.assertTrue(p2.is_relative_to(self.root.resolve()))

    def test_escape_rejected(self):
        for parts in (("..", "x"), ("a", "..", "..", "x"), ("a/../../x",)):
            with self.subTest(parts=parts):
                with self.assertRaises(KeyError):
                    safe_join(self.root, *parts)

    def test_absolute_part_rejected(self):
        outside = self.root.parent  # root 之外的绝对路径段
        with self.assertRaises(KeyError):
            safe_join(self.root, str(outside), "x.json")

    def test_doubao_traversal_blocked(self):
        """端到端：detail_ 前缀挡不住嵌套逃逸（审计原例 a/../../x）。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = _detail_dir(tmp.name, {"ok": GOOD_DETAIL})
        ad = doubao_raw.DoubaoRawAdapter([d])
        with self.assertRaises(KeyError) as cm:
            ad.load_session("a/../../evil")
        self.assertIn("越出数据根", str(cm.exception))


class RawLoadSessionGuardTest(unittest.TestCase):
    """M20：坏 JSON / 顶层非对象 → warns + lossy 空会话，不崩、不臆测。"""

    def test_doubao_corrupt_json_lossy(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = _detail_dir(tmp.name, {"bad": "not json{"})
        ad = doubao_raw.DoubaoRawAdapter([d])
        rec = ad.load_session("bad")
        self.assertEqual(rec.messages, [])
        self.assertTrue(rec.extra["lossy"])
        self.assertIn("解析失败", " ".join(rec.extra["warnings"]))

    def test_doubao_top_level_array_lossy(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = _detail_dir(tmp.name, {"bad": "[1,2]"})
        ad = doubao_raw.DoubaoRawAdapter([d])
        rec = ad.load_session("bad")
        self.assertEqual(rec.messages, [])
        self.assertTrue(rec.extra["lossy"])
        self.assertIn("顶层非对象", " ".join(rec.extra["warnings"]))

    def test_doubao_good_session_intact(self):
        """正路不回归：好 detail 照常出正文。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = _detail_dir(tmp.name, {"ok": GOOD_DETAIL})
        ad = doubao_raw.DoubaoRawAdapter([d])
        rec = ad.load_session("ok")
        self.assertEqual(len(rec.messages), 1)
        self.assertEqual(rec.messages[0].text, "hi")
        self.assertFalse(rec.extra["lossy"])

    def test_yuanbao_corrupt_json_lossy(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name) / "yuanbao_raw"
        d.mkdir()
        (d / "list_0.json").write_text(
            json.dumps({"conversations": []}), encoding="utf-8")
        (d / "detail_bad.json").write_text("{{nope", encoding="utf-8")
        ad = yuanbao_raw.YuanbaoRawAdapter([d])
        rec = ad.load_session("bad")
        self.assertEqual(rec.messages, [])
        self.assertTrue(rec.extra["lossy"])

    def test_qianwen_corrupt_json_lossy(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name) / "qianwen_raw"
        d.mkdir()
        (d / "list_0.json").write_text(
            json.dumps({"data": {"data": []}}), encoding="utf-8")
        (d / "detail_bad.json").write_text("<html>", encoding="utf-8")
        ad = qianwen_raw.QianwenRawAdapter([d])
        rec = ad.load_session("bad")
        self.assertEqual(rec.messages, [])
        self.assertTrue(rec.extra["lossy"])


class ZipMemberMatchTest(unittest.TestCase):
    """M5：zip 成员按路径段精确匹配，user_conversations.json 不误命中。"""

    def test_suffix_lookalike_not_matched(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        zp = Path(tmp.name) / "exp.zip"
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("user_conversations.json", "[]")
            z.writestr("chatgpt/conversations.json", "[]")
        f, member = official_export.locate_export_file([zp])
        self.assertIsNotNone(f)
        self.assertEqual(member, "chatgpt/conversations.json")

    def test_exact_and_nested_still_matched(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        zp = Path(tmp.name) / "exp.zip"
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("conversations.json", "[]")
        f, member = official_export.locate_export_file([zp])
        self.assertEqual(member, "conversations.json")

        zp2 = Path(tmp.name) / "exp2.zip"
        with zipfile.ZipFile(zp2, "w") as z:
            z.writestr("a/b/conversations.json", "[]")
        _, member2 = official_export.locate_export_file([zp2])
        self.assertEqual(member2, "a/b/conversations.json")


if __name__ == "__main__":
    unittest.main()
