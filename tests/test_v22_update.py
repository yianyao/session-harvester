# -*- coding: utf-8 -*-
"""v0.22 测试：update 增量同步（库即水位，只获取未获取的）。

用户裁决（2026-10-09）：日常数据更新走增量——不全量重取历史轨迹，
只导出新 sid 与 updated_at 变化的会话；sync 保持全量作对账基线。
fixture 复用 v0.12 的 deepseek-export mapping 形态。
"""

import json
import tempfile
import unittest
from pathlib import Path

from harvester.sync import run_sync, run_update

_DS_CONVS = [
    {
        "id": "conv-001",
        "title": "增量测试会话",
        "inserted_at": "2026-10-01T10:00:00+08:00",
        "updated_at": "2026-10-01T10:02:00+08:00",
        "mapping": {
            "root": {"id": "root", "parent": None,
                     "children": ["n1"], "message": None},
            "n1": {"id": "n1", "parent": "root", "children": ["n2"],
                   "message": {"model": "deepseek-chat",
                               "inserted_at": "2026-10-01T10:00:00+08:00",
                               "fragments": [{"type": "REQUEST",
                                              "content": "你好"}]}},
            "n2": {"id": "n2", "parent": "n1", "children": [],
                   "message": {"model": "deepseek-chat",
                               "inserted_at": "2026-10-01T10:01:00+08:00",
                               "fragments": [{"type": "RESPONSE",
                                              "content": "你好！"}]}},
        },
    }
]


def _write_ds_export(inbox: Path, convs=None) -> Path:
    p = inbox / "conversations.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(convs if convs is not None else _DS_CONVS,
                            ensure_ascii=False), encoding="utf-8",
                 newline="\n")
    return p


def _update_kwargs(root: Path) -> dict:
    return {
        "root": root,
        "sources_path": root / "sources.json",
        "db_path": root / "harvester.db",
        "inbox_dir": root / "inbox",
        "exports_dir": root / "exports",
        "only": {"deepseek-export"},
    }


def _export_files(root: Path) -> list[Path]:
    return [p for p in (root / "exports").rglob("*.json")
            if p.name != "export_manifest.json"]


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def _update(self):
        return run_update(**_update_kwargs(self.root))

    def test_first_run_is_full(self):
        _write_ds_export(self.root / "inbox")
        r = self._update()
        self.assertEqual(r["mode"], "incremental")
        self.assertEqual(r["export"]["count"], 1)
        self.assertEqual(r["sessions_total"], 1)
        self.assertEqual(r["new"], ["deepseek-export:conv-001"])

    def test_second_run_exports_nothing(self):
        # 增量核心断言：无变化时一个会话都不重导
        _write_ds_export(self.root / "inbox")
        self._update()
        r = self._update()
        self.assertEqual(r["export"]["count"], 0)
        self.assertEqual(r["updated"], [])
        self.assertEqual(len(_export_files(self.root)), 1)

    def test_new_session_exported_only(self):
        _write_ds_export(self.root / "inbox")
        self._update()
        done = next((self.root / "inbox" / "done"
                     / "deepseek-export").iterdir())
        convs = json.loads(done.read_text(encoding="utf-8"))
        extra = json.loads(json.dumps(convs[0], ensure_ascii=False))
        extra["id"] = "conv-002"
        extra["mapping"] = {
            "root": {"id": "root", "parent": None,
                     "children": ["m1"], "message": None},
            "m1": {"id": "m1", "parent": "root", "children": [],
                   "message": {"model": "deepseek-chat",
                               "inserted_at": "2026-10-02T10:00:00+08:00",
                               "fragments": [{"type": "REQUEST",
                                              "content": "第二条"}]}},
        }
        convs.append(extra)
        done.write_text(json.dumps(convs, ensure_ascii=False),
                        encoding="utf-8", newline="\n")
        r = self._update()
        self.assertEqual(r["export"]["count"], 1)
        self.assertEqual(r["new"], ["deepseek-export:conv-002"])
        self.assertEqual(r["sessions_total"], 2)
        self.assertEqual(len(_export_files(self.root)), 2)

    def test_updated_session_reexported(self):
        # updated_at 变化 => 该会话重导（续聊场景）
        _write_ds_export(self.root / "inbox")
        self._update()
        done = next((self.root / "inbox" / "done"
                     / "deepseek-export").iterdir())
        convs = json.loads(done.read_text(encoding="utf-8"))
        convs[0]["updated_at"] = "2026-10-03T12:00:00+08:00"
        convs[0]["mapping"]["n2"]["children"] = ["n3"]
        convs[0]["mapping"]["n3"] = {
            "id": "n3", "parent": "n2", "children": [],
            "message": {"model": "deepseek-chat",
                        "inserted_at": "2026-10-03T12:00:00+08:00",
                        "fragments": [{"type": "REQUEST",
                                       "content": "续聊一轮"}]}}
        done.write_text(json.dumps(convs, ensure_ascii=False),
                        encoding="utf-8", newline="\n")
        r = self._update()
        self.assertEqual(r["export"]["count"], 1)
        self.assertEqual(r["updated"], ["deepseek-export:conv-001"])
        self.assertEqual(r["new"], [])
        self.assertEqual(r["sessions_total"], 1)

    def test_sync_stays_full(self):
        # sync 全量语义不变：重跑仍全量导出（对账基线）
        _write_ds_export(self.root / "inbox")
        self._update()
        r = run_sync(**_update_kwargs(self.root))
        self.assertEqual(r["export"]["count"], 1)  # 全量：仍导 1 条


if __name__ == "__main__":
    unittest.main()
