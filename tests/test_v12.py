# -*- coding: utf-8 -*-
"""v0.12 测试：sync 一键同步（收件箱收割 / 导出 / 索引重建 / 新增判定）。

fixture 为最小 deepseek mapping 形态导出包（与真机核验形态 B 同构），
通过 only={"deepseek-export"} 隔离本机其他数据源。
"""

import json
import tempfile
import unittest
from pathlib import Path

from harvester.sync import harvest_inbox, inbox_plugins, run_sync

_DS_CONVS = [
    {
        "id": "conv-001",
        "title": "同步测试会话",
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
                                              "content": "你好！有什么可以帮你？"}]}},
        },
    }
]


def _write_ds_export(inbox: Path, name: str = "conversations.json",
                     convs=None) -> Path:
    p = inbox / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(convs if convs is not None else _DS_CONVS,
                            ensure_ascii=False), encoding="utf-8",
                 newline="\n")
    return p


def _sync_kwargs(root: Path) -> dict:
    return {
        "root": root,
        "sources_path": root / "sources.json",  # 不存在 => 空配置
        "db_path": root / "harvester.db",
        "inbox_dir": root / "inbox",
        "exports_dir": root / "exports",
        "only": {"deepseek-export"},
    }


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = Path(self.tmp.name) / "inbox"

    def test_harvest_deepseek_json_archives(self):
        _write_ds_export(self.inbox)
        r = harvest_inbox(self.inbox)
        self.assertEqual(len(r["archived"]), 1)
        self.assertEqual(r["archived"][0]["plugin"], "deepseek-export")
        dest = Path(r["archived"][0]["dest"])
        self.assertTrue(dest.exists())
        self.assertEqual(dest.parent.name, "deepseek-export")
        self.assertFalse((self.inbox / "conversations.json").exists())

    def test_harvest_unrecognized_stays(self):
        bad = self.inbox / "random.json"
        self.inbox.mkdir(parents=True)
        bad.write_text('{"hello": 1}', encoding="utf-8")
        r = harvest_inbox(self.inbox)
        self.assertEqual(r["archived"], [])
        self.assertEqual(len(r["unclaimed"]), 1)
        self.assertTrue(bad.exists())  # 绝不静默丢弃

    def test_harvest_duplicate_skips(self):
        _write_ds_export(self.inbox)
        r1 = harvest_inbox(self.inbox)
        self.assertFalse(r1["archived"][0].get("duplicate"))
        # 同内容再次投递 => 判重跳过，归档目录仍只有一份
        _write_ds_export(self.inbox)
        r2 = harvest_inbox(self.inbox)
        self.assertTrue(r2["archived"][0].get("duplicate"))
        self.assertEqual(len(list((self.inbox / "done"
                                   / "deepseek-export").iterdir())), 1)

    def test_inbox_plugins_paths_as_declarations(self):
        _write_ds_export(self.inbox)
        harvest_inbox(self.inbox)
        plugins = inbox_plugins(self.inbox)
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["id"], "deepseek-export")
        self.assertTrue(Path(plugins[0]["paths"][0]).exists())


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def _run(self):
        return run_sync(**_sync_kwargs(self.root))

    def test_end_to_end_and_delta(self):
        _write_ds_export(self.root / "inbox")
        r = self._run()
        self.assertEqual(r["sessions_total"], 1)
        self.assertEqual(len(r["new"]), 1)
        self.assertEqual(r["new"][0], "deepseek-export:conv-001")
        self.assertTrue((self.root / "harvester.db").exists())
        # 导出产物落盘（json 供索引；manifest 不计入）
        exports = [p for p in (self.root / "exports").rglob("*.json")
                   if p.name != "export_manifest.json"]
        self.assertEqual(len(exports), 1)

        # 幂等：重跑无新增
        r2 = self._run()
        self.assertEqual(r2["sessions_total"], 1)
        self.assertEqual(r2["new"], [])

        # 归档源文件追加第二个会话 => 重跑恰好新增 1 条
        done_file = next((self.root / "inbox" / "done"
                          / "deepseek-export").iterdir())
        convs = json.loads(done_file.read_text(encoding="utf-8"))
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
        done_file.write_text(json.dumps(convs, ensure_ascii=False),
                             encoding="utf-8", newline="\n")
        r3 = self._run()
        self.assertEqual(r3["sessions_total"], 2)
        self.assertEqual(r3["new"], ["deepseek-export:conv-002"])

    def test_garbage_never_blocks_sync(self):
        # 收件箱里混入无法识别的文件：不影响主流程，留在原地报告
        self.root.mkdir(parents=True, exist_ok=True)
        bad = self.root / "inbox" / "random.json"
        bad.parent.mkdir(parents=True)
        bad.write_text('{"x": 1}', encoding="utf-8")
        r = self._run()
        self.assertEqual(len(r["inbox"]["unclaimed"]), 1)
        self.assertEqual(r["sessions_total"], 0)
        self.assertTrue(bad.exists())


if __name__ == "__main__":
    unittest.main()
