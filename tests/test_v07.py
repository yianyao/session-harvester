# -*- coding: utf-8 -*-
"""v0.7 测试：DeepSeek 官方导出 mapping/fragments 形态（真机核验 2026-10-06）。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvester.adapters.deepseek_export import (  # noqa: E402
    DeepSeekExportAdapter, _ds_walk, _norm_time)

_TMP = Path(__file__).parent / "_tmp"


def _try_unlink(f: Path) -> None:
    try:
        f.unlink(missing_ok=True)
    except PermissionError:
        pass


def _msg(model: str, inserted_at: str, frags: list[dict]) -> dict:
    return {"model": model, "inserted_at": inserted_at, "fragments": frags}


def _mapping(nodes: dict) -> dict:
    return nodes


# ---------- _ds_walk 单元 ----------

class TestDsWalk(unittest.TestCase):
    def test_linear_with_think(self):
        conv = {
            "id": "c1", "title": "线性会话",
            "mapping": _mapping({
                "root": {"id": "root", "parent": None, "children": ["1"],
                         "message": None},
                "1": {"id": "1", "parent": "root", "children": ["2"],
                      "message": _msg("deepseek-chat", "2026-10-01T10:00:00+08:00",
                                      [{"type": "REQUEST", "content": "你好"}])},
                "2": {"id": "2", "parent": "1", "children": [],
                      "message": _msg("deepseek-reasoner",
                                      "2026-10-01T10:00:05+08:00",
                                      [{"type": "THINK", "content": "先想一想"},
                                       {"type": "RESPONSE", "content": "你好！"}])},
            }),
        }
        msgs, warns, lossy, meta = _ds_walk(conv)
        self.assertFalse(lossy)
        self.assertEqual([(m.role, m.text) for m in msgs],
                         [("user", "你好"), ("note", "[think]\n先想一想"),
                          ("assistant", "你好！")])
        self.assertEqual(meta["models"], ["deepseek-chat", "deepseek-reasoner"])
        self.assertEqual(meta["branch_points"], 0)
        # 时间戳落在 user/assistant 上，think note 不带时间
        self.assertEqual(msgs[0].timestamp, "2026-10-01T10:00:00+08:00")
        self.assertIsNone(msgs[1].timestamp)

    def test_regen_branch_picks_latest(self):
        # 重新生成：节点 1 下挂 3 个并列 RESPONSE（2/4/6），应选时间最新者
        conv = {
            "id": "c2", "title": "重新生成",
            "mapping": _mapping({
                "root": {"id": "root", "parent": None, "children": ["1"],
                         "message": None},
                "1": {"id": "1", "parent": "root",
                      "children": ["2", "4", "6"],
                      "message": _msg("deepseek-chat", "2026-10-01T10:00:00+08:00",
                                      [{"type": "REQUEST", "content": "问"}])},
                "2": {"id": "2", "parent": "1", "children": [],
                      "message": _msg("deepseek-chat", "2026-10-01T10:00:01+08:00",
                                      [{"type": "RESPONSE", "content": "旧答案"}])},
                "4": {"id": "4", "parent": "1", "children": [],
                      "message": _msg("deepseek-chat", "2026-10-01T10:00:02+08:00",
                                      [{"type": "RESPONSE", "content": "中间答案"}])},
                "6": {"id": "6", "parent": "1", "children": [],
                      "message": _msg("deepseek-chat", "2026-10-01T10:00:03+08:00",
                                      [{"type": "RESPONSE", "content": "最新答案"}])},
            }),
        }
        msgs, warns, lossy, meta = _ds_walk(conv)
        self.assertFalse(lossy)
        texts = [m.text for m in msgs if m.role == "assistant"]
        self.assertEqual(texts, ["最新答案"])          # 只取用户当前看到的分支
        self.assertEqual(meta["branch_points"], 1)
        # 非选中分支不算消息也不报 lossy（是正常分支语义，非解析失败）
        self.assertEqual(warns, [])

    def test_edit_branch_picks_deeper_latest_subtree(self):
        # 改写重问：u1 下并列 [a2(旧回复), u3(改写后新问题→a4)]，
        # u3 子树最新时间更大 → 走 u3 链
        conv = {
            "id": "c3", "title": "改写重问",
            "mapping": _mapping({
                "root": {"id": "root", "parent": None, "children": ["1"],
                         "message": None},
                "1": {"id": "1", "parent": "root", "children": ["2", "3"],
                      "message": _msg("deepseek-chat", "2026-10-01T10:00:00+08:00",
                                      [{"type": "REQUEST", "content": "问题v1"}])},
                "2": {"id": "2", "parent": "1", "children": [],
                      "message": _msg("deepseek-chat", "2026-10-01T10:00:01+08:00",
                                      [{"type": "RESPONSE", "content": "v1答复"}])},
                "3": {"id": "3", "parent": "1", "children": ["4"],
                      "message": _msg("deepseek-chat", "2026-10-01T10:01:00+08:00",
                                      [{"type": "REQUEST", "content": "问题v2"}])},
                "4": {"id": "4", "parent": "3", "children": [],
                      "message": _msg("deepseek-chat", "2026-10-01T10:01:05+08:00",
                                      [{"type": "RESPONSE", "content": "v2答复"}])},
            }),
        }
        msgs, _w, lossy, meta = _ds_walk(conv)
        self.assertFalse(lossy)
        self.assertEqual([(m.role, m.text) for m in msgs],
                         [("user", "问题v1"), ("user", "问题v2"),
                          ("assistant", "v2答复")])
        self.assertEqual(meta["branch_points"], 1)

    def test_tool_fragment_notes(self):
        conv = {
            "id": "c4", "title": "联网与文件",
            "mapping": _mapping({
                "root": {"id": "root", "parent": None, "children": ["1"],
                         "message": None},
                "1": {"id": "1", "parent": "root", "children": ["2"],
                      "message": _msg("deepseek-chat", "2026-10-01T10:00:00+08:00",
                                      [{"type": "REQUEST", "content": "查一下"},
                                       {"type": "FILE",
                                        "files": [{"file_id": "f1",
                                                   "file_name": "a.pdf",
                                                   "file_size": 123}]}])},
                "2": {"id": "2", "parent": "1", "children": [],
                      "message": _msg("deepseek-chat", "2026-10-01T10:00:09+08:00",
                                      [{"type": "SEARCH",
                                        "results": [{"url": "u1", "title": "t1"}]},
                                       {"type": "TOOL_SEARCH",
                                        "results": [{"url": "u2", "title": "t2"}]},
                                       {"type": "TOOL_OPEN"},
                                       {"type": "RESPONSE", "content": "结果…"}])},
            }),
        }
        msgs, warns, lossy, _meta = _ds_walk(conv)
        self.assertFalse(lossy)
        notes = [m for m in msgs if m.role == "note"]
        kinds = [n.text.split("\n")[0].split(" ")[0] for n in notes]
        self.assertEqual(kinds, ["[FILE]", "[SEARCH]", "[TOOL_SEARCH]",
                                 "[TOOL_OPEN]"])
        self.assertIn("a.pdf (123 bytes)", notes[0].text)
        self.assertIn("t1 — u1", notes[1].text)

    def test_unknown_fragment_skipped_with_warning(self):
        conv = {
            "id": "c5", "title": "未知块",
            "mapping": _mapping({
                "root": {"id": "root", "parent": None, "children": ["1"],
                         "message": None},
                "1": {"id": "1", "parent": "root", "children": ["2"],
                      "message": _msg("deepseek-chat", "2026-10-01T10:00:00+08:00",
                                      [{"type": "REQUEST", "content": "问"}])},
                "2": {"id": "2", "parent": "1", "children": [],
                      "message": _msg("deepseek-chat", "2026-10-01T10:00:01+08:00",
                                      [{"type": "FUTURE_TYPE", "x": 1},
                                       {"type": "RESPONSE", "content": "答"}])},
            }),
        }
        msgs, warns, lossy, _meta = _ds_walk(conv)
        self.assertFalse(lossy)          # 结构可解析，lossy 只标记结构性缺失
        self.assertEqual([m.role for m in msgs], ["user", "assistant"])
        self.assertTrue(any("FUTURE_TYPE" in w for w in warns))

    def test_cycle_guard(self):
        conv = {
            "id": "c6", "title": "脏数据环",
            "mapping": _mapping({
                "root": {"id": "root", "parent": None, "children": ["1"],
                         "message": None},
                "1": {"id": "1", "parent": "root", "children": ["2"],
                      "message": _msg("deepseek-chat", "2026-10-01T10:00:00+08:00",
                                      [{"type": "REQUEST", "content": "问"}])},
                "2": {"id": "2", "parent": "1", "children": ["1"],
                      "message": _msg("deepseek-chat", "2026-10-01T10:00:01+08:00",
                                      [{"type": "RESPONSE", "content": "答"}])},
            }),
        }
        msgs, warns, lossy, _meta = _ds_walk(conv)  # 不得死循环
        self.assertFalse(lossy)
        self.assertTrue(any("环" in w for w in warns))
        self.assertEqual(len(msgs), 2)

    def test_missing_root_lossy(self):
        msgs, warns, lossy, _meta = _ds_walk({"id": "c7", "mapping": {}})
        self.assertTrue(lossy)
        self.assertEqual(msgs, [])


# ---------- Adapter 端到端 ----------

class TestAdapterMappingForm(unittest.TestCase):
    def _adapter_with(self, data) -> DeepSeekExportAdapter:
        _TMP.mkdir(parents=True, exist_ok=True)
        f = _TMP / f"dsmap_{id(data):x}.json"
        f.write_text(json.dumps(data, ensure_ascii=False),
                     encoding="utf-8", newline="\n")
        self.addCleanup(_try_unlink, f)
        return DeepSeekExportAdapter(paths=[f])

    def test_detect_and_load(self):
        conv = {
            "id": "conv-real-1", "title": "真机形态",
            "inserted_at": "2025-02-07T13:30:52.443000+08:00",
            "updated_at": "2025-02-07T14:36:28.215000+08:00",
            "mapping": _mapping({
                "root": {"id": "root", "parent": None, "children": ["1"],
                         "message": None},
                "1": {"id": "1", "parent": "root", "children": ["2"],
                      "message": _msg("deepseek-chat",
                                      "2025-02-07T13:30:52.727000+08:00",
                                      [{"type": "REQUEST", "content": "问"}])},
                "2": {"id": "2", "parent": "1", "children": [],
                      "message": _msg("deepseek-chat",
                                      "2025-02-07T13:31:00.000000+08:00",
                                      [{"type": "RESPONSE", "content": "答"}])},
            }),
        }
        ad = self._adapter_with([conv])
        rep = ad.detect()
        self.assertEqual(rep.status, "OK")
        self.assertEqual(rep.session_count, 1)
        items = ad.list_sessions()
        self.assertEqual(items[0]["message_count"], 2)
        self.assertEqual(items[0]["created_at"],
                         "2025-02-07T13:30:52.443000+08:00")
        rec = ad.load_session("conv-real-1")
        self.assertEqual(rec.extra["models"], ["deepseek-chat"])
        self.assertEqual(rec.extra["branch_points"], 0)
        self.assertFalse(rec.extra["lossy"])
        self.assertEqual(rec.created_at, "2025-02-07T13:30:52.443000+08:00")

    def test_legacy_messages_form_still_works(self):
        # 形态 A（messages 数组）回归：mapping 缺席时仍走旧逻辑
        conv = {"id": "legacy-1", "title": "旧形态",
                "messages": [{"role": "user", "content": "旧"},
                             {"role": "assistant", "content": "也旧"}]}
        ad = self._adapter_with([conv])
        self.assertEqual(ad.detect().status, "OK")
        rec = ad.load_session("legacy-1")
        self.assertEqual([m.role for m in rec.messages], ["user", "assistant"])
        self.assertNotIn("branch_points", rec.extra)  # meta 为空时不产生键
        self.assertNotIn("models", rec.extra)


class TestNormTimeISO(unittest.TestCase):
    def test_iso_passthrough(self):
        self.assertEqual(_norm_time("2025-02-07T13:30:52+08:00"),
                         "2025-02-07T13:30:52+08:00")
        self.assertIsNone(_norm_time("   "))


if __name__ == "__main__":
    unittest.main()
