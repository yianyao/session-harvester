# -*- coding: utf-8 -*-
"""v0.46 P0-1 测试：索引侧增量（update 只索引本次导出的 json）。

背景（2026-10-12 真机实测，`docs/reports/probe-update-timing.py`）：
`run_update` 手上已有 new/updated，但索引侧仍 `DELETE` 三表 + 从 exports
归档**全量重建**（1965 json / 244 MB）→ 274.7 s，占 update 总耗时 87%。

本文件钉三件事：
1. **口径一致**：增量索引出来的库与全量重建**逐行相等**（防"优化出一个
   不一样的索引"）；
2. **真的没重解析**：第二次 update 的 parsed == 0（首跑必须 > 0，否则这条
   断言恒真）；
3. **gone 清干净**：源侧少了会话 → 三表行消失且 FTS 无残留；同时钉住
   "某源一条都没列出来时保守不清理"这道安全闸。
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.indexing import _cjk_bigram, index_exports, index_incremental
from harvester.sync import run_sync, run_update

_CONV_TMPL = {
    "title": "增量测试会话",
    "inserted_at": "2026-10-01T10:00:00+08:00",
    "updated_at": "2026-10-01T10:02:00+08:00",
    "mapping": {
        "root": {"id": "root", "parent": None, "children": ["n1"],
                 "message": None},
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


def _conv(idx: int, marker: str) -> dict:
    """第 idx 条会话；marker 塞进正文，供 FTS 残留检查用（全局唯一）。"""
    c = json.loads(json.dumps(_CONV_TMPL, ensure_ascii=False))
    c["id"] = f"conv-{idx:03d}"
    c["title"] = f"增量测试会话 {idx}"
    c["mapping"]["n2"]["message"]["fragments"][0]["content"] = marker
    return c


def _write_inbox(root: Path, convs: list[dict]) -> Path:
    p = root / "inbox" / "conversations.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(convs, ensure_ascii=False), encoding="utf-8",
                 newline="\n")
    return p


def _kwargs(root: Path, db_name: str = "harvester.db") -> dict:
    return {
        "root": root,
        "sources_path": root / "sources.json",
        "db_path": root / db_name,
        "inbox_dir": root / "inbox",
        "exports_dir": root / "exports",
        "only": {"deepseek-export"},
    }


def _dump(db: Path) -> dict:
    """三表逐行快照（排序后比较；rowid 顺序无关）。"""
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        return {
            "sessions": sorted(tuple(r) for r in con.execute(
                "SELECT sid,source,session_id,title,category,created_at,"
                "updated_at,file,model FROM sessions")),
            "messages": sorted(tuple(r) for r in con.execute(
                "SELECT sid,role,ts,text,raw FROM messages")),
            "steps": sorted(tuple(r) for r in con.execute(
                "SELECT sid,seq,ts,tool,phase,status,error,detail FROM steps")),
        }
    finally:
        con.close()


def _count(db: Path, table: str, sid: str | None = None) -> int:
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        if sid is None:
            return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return con.execute(f"SELECT COUNT(*) FROM {table} WHERE sid=?",
                           (sid,)).fetchone()[0]
    finally:
        con.close()


def _fts_hits(db: Path, token: str) -> int:
    """FTS 侧残留检查：直接 MATCH messages 虚表。

    查询侧必须做与插入侧相同的 CJK bigram 改写，否则短语永远命不中
    （unicode61 把连续汉字当一个 token，而插入的是空格分隔二元组）；
    这里复用插入侧那个函数，正是要钉"FTS 行真的被删掉了"。
    """
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM messages WHERE messages "
                           "MATCH ?", (f'"{_cjk_bigram(token)}"',)).fetchone()[0]
    finally:
        con.close()


def _archive_file(root: Path) -> Path:
    return next((root / "inbox" / "done" / "deepseek-export").iterdir())


class IndexIncrementalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    # ---- 1. 口径一致 ----

    def test_incremental_matches_full_rebuild_row_by_row(self):
        _write_inbox(self.root, [_conv(i, f"耐克{i}") for i in (1, 2, 3)])
        r1 = run_update(**_kwargs(self.root, "inc.db"))
        # 增量索引必须真的干了活（否则下面的对账是"两个空库相等"）
        self.assertEqual(r1["index"]["parsed"], 3)
        index_exports(self.root / "exports", self.root / "full.db")
        self.assertEqual(_dump(self.root / "inc.db"),
                         _dump(self.root / "full.db"))

        # 第二次 update：无新会话，parsed 必须为 0，且库仍与全量重建逐行相等
        r2 = run_update(**_kwargs(self.root, "inc.db"))
        self.assertEqual(r2["index"]["parsed"], 0)
        self.assertEqual(_dump(self.root / "inc.db"),
                         _dump(self.root / "full.db"))

    def test_second_run_parses_nothing_but_first_does(self):
        """反恒真对照：parsed==0 只对"无变化的第二跑"成立，首跑必须 >0。"""
        _write_inbox(self.root, [_conv(1, "阿尔法")])
        first = run_update(**_kwargs(self.root))
        second = run_update(**_kwargs(self.root))
        self.assertGreater(first["index"]["parsed"], 0)
        self.assertEqual(second["index"]["parsed"], 0)
        self.assertEqual(second["index"]["messages_parsed"], 0)
        self.assertEqual(second["index"]["sessions"], 1)
        self.assertEqual(second["unchanged"], 1)

    def test_updated_session_alone_is_reparsed(self):
        _write_inbox(self.root, [_conv(1, "布拉沃"), _conv(2, "查理")])
        run_update(**_kwargs(self.root))
        arch = _archive_file(self.root)
        convs = json.loads(arch.read_text(encoding="utf-8"))
        convs[0]["updated_at"] = "2026-10-09T09:00:00+08:00"
        convs[0]["mapping"]["n2"]["message"]["fragments"][0]["content"] = "布拉沃改"
        arch.write_text(json.dumps(convs, ensure_ascii=False),
                        encoding="utf-8", newline="\n")
        r = run_update(**_kwargs(self.root))
        self.assertEqual(r["updated"], ["deepseek-export:conv-001"])
        self.assertEqual(r["index"]["parsed"], 1)   # 只重解析变化的那一个
        self.assertEqual(r["index"]["sessions"], 2)  # 另一条原样保留
        self.assertEqual(_fts_hits(self.root / "harvester.db", "布拉沃改"), 1)

    def test_broken_json_in_manifest_is_skipped_not_fatal(self):
        """损坏的导出 json 只跳过该文件（计 skipped），不拖垮增量索引。"""
        _write_inbox(self.root, [_conv(1, "德尔塔")])
        run_update(**_kwargs(self.root))
        db = self.root / "harvester.db"
        broken = self.root / "exports" / "Agent" / "2026-10" / "broken.json"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("{ 这不是 json", encoding="utf-8", newline="\n")
        st = index_incremental(self.root / "exports", db, [broken])
        self.assertEqual(st["skipped"], 1)
        self.assertEqual(st["parsed"], 0)
        self.assertEqual(st["sessions"], 1)          # 原有会话不动
        self.assertEqual(_count(db, "sessions", "deepseek-export:conv-001"), 1)

    def test_missing_file_is_reported_not_silently_ignored(self):
        _write_inbox(self.root, [_conv(1, "回声")])
        run_update(**_kwargs(self.root))
        st = index_incremental(self.root / "exports", self.root / "harvester.db",
                              ["Agent/2026-10/nope.json"])
        self.assertEqual(len(st["missing"]), 1)      # 静默跳过 = 覆盖有洞
        self.assertEqual(st["parsed"], 0)

    # ---- 2. gone ----

    def test_gone_source_session_drops_all_three_tables(self):
        _write_inbox(self.root, [_conv(1, "艾克"), _conv(2, "狐步")])
        run_update(**_kwargs(self.root))
        db = self.root / "harvester.db"
        self.assertEqual(_fts_hits(db, "艾克"), 1)

        arch = _archive_file(self.root)
        convs = json.loads(arch.read_text(encoding="utf-8"))
        arch.write_text(json.dumps([c for c in convs
                                    if c["id"] != "conv-001"],
                                   ensure_ascii=False),
                        encoding="utf-8", newline="\n")
        r = run_update(**_kwargs(self.root))
        sid = "deepseek-export:conv-001"
        self.assertEqual(r["gone"], [sid])
        self.assertEqual(r["index"]["dropped"], 1)
        self.assertEqual(_count(db, "sessions", sid), 0)
        self.assertEqual(_count(db, "steps", sid), 0)
        self.assertEqual(_count(db, "messages", sid), 0)
        self.assertEqual(_fts_hits(db, "艾克"), 0)      # FTS 无残留
        self.assertEqual(_count(db, "sessions"), 1)     # 另一条不受影响
        self.assertEqual(_fts_hits(db, "狐步"), 1)

    def test_gone_kept_when_source_lists_nothing(self):
        """安全闸：源列 0 条 = "读不到"而非"删光了" → 不清理，只回报。"""
        _write_inbox(self.root, [_conv(1, "高尔夫")])
        run_update(**_kwargs(self.root))
        db = self.root / "harvester.db"
        arch = _archive_file(self.root)
        arch.write_text("[]", encoding="utf-8", newline="\n")
        r = run_update(**_kwargs(self.root))
        self.assertEqual(r["gone"], [])
        self.assertEqual(r["gone_protected"], ["deepseek-export"])
        self.assertEqual(_count(db, "sessions"), 1)  # 行仍在，没被误删

    def test_sync_restores_pruned_session_from_archive(self):
        """口径差异的钉子：update 以源为准，sync 以 exports 归档为准。

        归档文件永不删除，所以 update 清掉的行是可恢复的（不是数据丢失）——
        这条测试就是那句话的证据；两条命令的分工也因此写进 README。
        """
        _write_inbox(self.root, [_conv(1, "朱丽叶"), _conv(2, "印度")])
        run_update(**_kwargs(self.root))
        arch = _archive_file(self.root)
        convs = json.loads(arch.read_text(encoding="utf-8"))
        arch.write_text(json.dumps([c for c in convs if c["id"] != "conv-001"],
                                   ensure_ascii=False),
                        encoding="utf-8", newline="\n")
        run_update(**_kwargs(self.root))
        db = self.root / "harvester.db"
        self.assertEqual(_count(db, "sessions", "deepseek-export:conv-001"), 0)

        run_sync(**_kwargs(self.root))  # 全量：从 exports 归档重建
        self.assertEqual(_count(db, "sessions", "deepseek-export:conv-001"), 1)
        self.assertEqual(_fts_hits(db, "朱丽叶"), 1)


if __name__ == "__main__":
    unittest.main()
