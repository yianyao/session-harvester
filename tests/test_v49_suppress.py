# -*- coding: utf-8 -*-
"""v0.49 测试：会话抑制（「删除没营养的会话」）——后端四个挂点 + CLI。

依据：`docs/PROPOSAL-session-suppress.md`（用户已逐项批准 §5 ①-⑦，
⑧ 裁决**不设时限**）。本文件钉的就是提案 §4 的四条验收：

1. `suppress add --apply` 后：`harvester.db` 三表该 sid 行数 0、`exports/`
   下该会话的 `.json`/`.md` 不存在、`suppress list` 有这一行；
2. **`sync` 之后它仍然不在**（本需求的核心验收）：三表行数 0 且不在
   `export_manifest.json`；
3. `suppress remove` + `sync` 后它回来（证明「语料还在就可恢复」）；
4. 既有 785 例保持全绿（本文件不依赖真库；全部用 `tempfile` 临时库）。

**为什么每条断言都值得写**（说清它在什么情况下会红）：
- 「抑制后 sync 不再入库」：去掉 `export(skip_sids=...)` 或去掉
  `index_exports(skip_sids=...)` 或去掉 `prune_suppressed` 任一处 → 红；
- 「旧副本文件真被删掉」：`prune_suppressed` 只报不删（或只删库不删文件）→ 红；
- 「FTS 无残留」：删三表行时漏掉 `messages`（或误以为 FTS 是 external-content
  而不敢删）→ 红；
- 「`remove` 不恢复数据」：把 `unsuppress` 写成「顺手把语料重导一遍」→ 红；
- 「读路径不建表」：`suppressed_sids` 用写连接（顺手 `CREATE TABLE`）→ 红。

夹具一律临时库（`indexing.SCHEMA` + `index_session`，或真的 `run_sync` 全流程）
——**真 `harvester.db` / `topics_meta.db` 一字节不动**。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.cli import build_parser, cmd_suppress, main
from harvester.exporter import file_sid, prune_suppressed
from harvester.indexing import SCHEMA, _cjk_bigram, index_exports, index_session
from harvester.models import Message, SessionRecord
from harvester.suppress import (db_row_counts, ensure_suppress_table,
                                list_suppressed, suppress, suppressed_sids,
                                unsuppress)
from harvester.sync import run_sync, run_update

# ---- 通用夹具 ----

_CONV_TMPL = {
    "title": "抑制测试会话",
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
    """第 idx 条会话；marker 进正文，供 FTS 残留检查用（全局唯一）。"""
    c = json.loads(json.dumps(_CONV_TMPL, ensure_ascii=False))
    c["id"] = f"conv-{idx:03d}"
    c["title"] = f"抑制测试会话 {idx}"
    c["mapping"]["n2"]["message"]["fragments"][0]["content"] = marker
    return c


def _kwargs(root: Path, db_name: str = "harvester.db") -> dict:
    return {
        "root": root,
        "sources_path": root / "sources.json",
        "db_path": root / db_name,
        "inbox_dir": root / "inbox",
        "exports_dir": root / "exports",
        "only": {"deepseek-export"},
    }


def _write_inbox(root: Path, convs: list[dict]) -> Path:
    p = root / "inbox" / "conversations.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(convs, ensure_ascii=False), encoding="utf-8",
                 newline="\n")
    return p


def _count(db: Path, table: str, sid: str) -> int:
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table} WHERE sid=?",
                           (sid,)).fetchone()[0]
    finally:
        con.close()


def _fts_hits(db: Path, token: str) -> int:
    """FTS 残留检查：查询侧必须与插入侧同样做 CJK bigram 改写。"""
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM messages WHERE messages "
                           "MATCH ?", (f'"{_cjk_bigram(token)}"',)).fetchone()[0]
    finally:
        con.close()


def _rec(i: int, marker: str) -> SessionRecord:
    return SessionRecord(
        source="dsh", session_id=f"s{i}", title=f"会话{i}",
        created_at="2026-10-01 10:00:00", updated_at="2026-10-01 10:02:00",
        messages=[Message(role="user", text=marker),
                  Message(role="assistant", text="好的")])


def _mk_index_db(path: Path, recs: list[SessionRecord]) -> None:
    """临时索引库：`indexing.SCHEMA` + `index_session`（与既有测试同款造法）。"""
    con = sqlite3.connect(str(path))
    try:
        con.executescript(SCHEMA)
        for rec in recs:
            index_session(con, rec, category="test")
        con.commit()
    finally:
        con.close()


def _export_jsons_for(root: Path, sid: str) -> list[Path]:
    """exports 目录里属于该 sid 的 json（靠**读内容**，不靠文件名——文件名只有
    6 位短哈希，见提案 §1）。"""
    return [p for p in (root / "exports").rglob("*.json")
            if p.name != "export_manifest.json" and file_sid(p) == sid]


def _manifest_sids(root: Path) -> set[str]:
    """`export_manifest.json` 里列出的会话 sid（清单条目无 sid 字段，按文件内容取）。"""
    man = json.loads((root / "exports" / "export_manifest.json")
                     .read_text(encoding="utf-8"))
    out: set[str] = set()
    for e in man.get("exported", []):
        p = (root / "exports" / e["file"]).with_suffix(".json")
        out.add(file_sid(p) if p.is_file() else f"<missing:{e['file']}>")
    return out


@contextlib.contextmanager
def _cli(argv: list[str]):
    """跑一次 CLI 并捕获输出；**异常时先把捕获到的输出打出来再抛**
    （否则现象是"套件跑一半安静地没了"，本项目在 CLI helper 上踩过两次）。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except BaseException:
            print("---- 捕获的 stdout ----", out.getvalue())
            print("---- 捕获的 stderr ----", err.getvalue())
            raise
    yield code, out.getvalue() + err.getvalue()


class _TmpRoot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.meta = self.root / "topics_meta.db"

    def tearDown(self):
        self.tmp.cleanup()


# ---- 1. 抑制表与库内行（suppress.py） ----

class SuppressModuleTest(_TmpRoot):

    def test_suppress_writes_row_and_list_roundtrips(self):
        db = self.root / "harvester.db"
        _mk_index_db(db, [_rec(1, "阿尔法")])
        r = suppress(self.meta, db, ["dsh:s1"], reason="单轮查词",
                     created_by="tester")
        self.assertEqual(r["added"], 1)
        rows = list_suppressed(self.meta)
        self.assertEqual([x["sid"] for x in rows], ["dsh:s1"])
        self.assertEqual(rows[0]["reason"], "单轮查词")
        self.assertEqual(rows[0]["created_by"], "tester")
        self.assertTrue(rows[0]["created"], "created 必须有值（谁/何时/为什么）")

    def test_suppress_purges_three_tables_and_fts(self):
        """立刻生效（提案 §2.3）：三表行 + FTS 索引都不留残留。"""
        db = self.root / "harvester.db"
        _mk_index_db(db, [_rec(1, "阿尔法"), _rec(2, "布拉沃")])
        self.assertEqual(_fts_hits(db, "阿尔法"), 1)      # 反恒真：先确实在
        r = suppress(self.meta, db, ["dsh:s1"], reason="无营养")
        self.assertEqual(r["purged"]["dsh:s1"]["sessions"], 1)
        for tbl in ("sessions", "messages", "steps"):
            self.assertEqual(_count(db, tbl, "dsh:s1"), 0, tbl)
        self.assertEqual(_fts_hits(db, "阿尔法"), 0, "FTS 残留未清")
        # 另一条会话不受影响
        self.assertEqual(_count(db, "sessions", "dsh:s2"), 1)
        self.assertEqual(_fts_hits(db, "布拉沃"), 1)

    def test_suppress_is_idempotent_and_keeps_first_created(self):
        """重复抑制：只更新理由，不重复计数，`created` 保留首次（何时第一次被抑制）。"""
        db = self.root / "harvester.db"
        _mk_index_db(db, [_rec(1, "阿尔法")])
        suppress(self.meta, db, ["dsh:s1"], reason="第一次")
        first = list_suppressed(self.meta)[0]["created"]
        again = suppress(self.meta, db, ["dsh:s1"], reason="第二次")
        self.assertEqual(again["added"], 0, "重复抑制不该算新增")
        rows = list_suppressed(self.meta)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reason"], "第二次")
        self.assertEqual(rows[0]["created"], first)
        self.assertEqual(again["purged"]["dsh:s1"]["sessions"], 0)

    def test_unsuppress_removes_row_but_does_not_restore_data(self):
        """口径诚实（提案 §5 ⑧）：`unsuppress` 只删抑制行，**不恢复数据**；
        恢复靠下次 sync 重导出（那是另一条命令的职责，本函数不许顺手做）。"""
        db = self.root / "harvester.db"
        _mk_index_db(db, [_rec(1, "阿尔法")])
        suppress(self.meta, db, ["dsh:s1"], reason="无营养")
        self.assertEqual(unsuppress(self.meta, ["dsh:s1"]), 1)
        self.assertEqual(list_suppressed(self.meta), [])
        self.assertEqual(_count(db, "sessions", "dsh:s1"), 0,
                         "取消抑制不该恢复数据（恢复要靠 sync 重导出）")
        self.assertEqual(unsuppress(self.meta, ["dsh:s1"]), 0, "再删一次为 0")

    def test_list_suppressed_is_readonly_when_meta_missing(self):
        """读路径不建库、不建表（否则消费方（sync）读一下就产生副作用）。"""
        self.assertFalse(self.meta.exists())
        self.assertEqual(list_suppressed(self.meta), [])
        self.assertEqual(suppressed_sids(self.meta), set())
        self.assertFalse(self.meta.exists(), "读路径不该创建 meta 库")
        # meta 存在但没有抑制表 → 也只返回空，不建表
        con = sqlite3.connect(str(self.meta))
        con.execute("CREATE TABLE topics (id TEXT PRIMARY KEY, name TEXT)")
        con.commit()
        con.close()
        before = self.meta.read_bytes()
        self.assertEqual(list_suppressed(self.meta), [])
        self.assertEqual(suppressed_sids(self.meta), set())
        self.assertEqual(self.meta.read_bytes(), before, "读路径不该改 meta 库")

    def test_db_row_counts_is_readonly(self):
        """dry-run 用的计数走只读连接：不建表、不改库。"""
        db = self.root / "harvester.db"
        _mk_index_db(db, [_rec(1, "阿尔法")])
        before = db.read_bytes()
        counts = db_row_counts(db, ["dsh:s1", "dsh:nope"])
        self.assertEqual(counts["dsh:s1"]["sessions"], 1)
        self.assertEqual(counts["dsh:s1"]["messages"], 2)
        self.assertEqual(counts["dsh:s1"]["title"], "会话1")
        self.assertTrue(counts["dsh:s1"]["found"])
        self.assertFalse(counts["dsh:nope"]["found"])
        self.assertEqual(db.read_bytes(), before, "预演不该写库")

    def test_suppressed_sids_on_missing_db_is_empty(self):
        self.assertEqual(suppressed_sids(None), set())
        self.assertEqual(suppressed_sids(self.root / "nope.db"), set())

    def test_messages_is_self_contained_fts5_without_triggers(self):
        """把「直接 DELETE messages 行 = FTS 自动维护」这条**假设**钉死。

        提案 §2.3 要求实施前先确认 FTS 形态（是不是 external-content + trigger）:
        2026-10-13 用探针在真库与 `indexing.SCHEMA` 两处核对，结论是——
        **self-contained FTS5 虚表**（`content=` 未设置，影子表齐全）、
        **全库没有任何 trigger**，所以 `DELETE FROM messages WHERE sid=?`
        由 FTS5 自己维护索引，不需要额外同步语句。

        会红的情形：有人把 `messages` 改成 external-content（`content=`）或
        引入 trigger 来同步 → 直接 DELETE 的语义变了，docstring 里那条
        「已核对」的结论必须重做，而不是悄悄失效。
        """
        from harvester.indexing import SCHEMA as IDX_SCHEMA
        self.assertIn("USING fts5", IDX_SCHEMA)
        self.assertNotIn("content=", IDX_SCHEMA,
                         "external-content 时直接 DELETE 不再维护 FTS 索引")
        self.assertNotIn("TRIGGER", IDX_SCHEMA.upper())
        db = self.root / "harvester.db"
        _mk_index_db(db, [_rec(1, "阿尔法")])
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            rows = dict(con.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' "
                "AND name LIKE 'messages%'"))
            self.assertIn("messages_content", rows, "影子表在 = self-contained")
            self.assertNotIn("content=", rows.get("messages", ""))
            self.assertEqual(
                con.execute("SELECT name FROM sqlite_master WHERE "
                            "type='trigger'").fetchall(), [],
                "出现 trigger → 同步口径变了，需重做 FTS 形态核对")
        finally:
            con.close()


# ---- 2. 导出侧两个挂点（exporter） ----

class ExportSkipTest(_TmpRoot):

    def _export_two(self, **kw) -> dict:
        from harvester.exporter import export
        _write_inbox(self.root, [_conv(1, "阿尔法保留"), _conv(2, "布拉沃抑制")])
        src = {"plugins": [{"id": "deepseek-export",
                            "paths": [str(self.root / "inbox"
                                          / "conversations.json")]}]}
        return export(self.root / "exports", sources=src,
                      only={"deepseek-export"}, include_notes=True, **kw)

    def test_export_skips_suppressed_sid_and_reports_it(self):
        man = self._export_two(skip_sids={"deepseek-export:conv-002"})
        self.assertEqual(man["count"], 1)
        self.assertEqual(man["skipped_suppressed"], ["deepseek-export:conv-002"])
        self.assertEqual([file_sid(p) for p in _export_jsons_for(self.root, "deepseek-export:conv-002")], [])
        self.assertEqual(len(_export_jsons_for(self.root,
                                               "deepseek-export:conv-001")), 1)

    def test_export_without_skip_sids_exports_both(self):
        """反恒真：不传 skip_sids 时两条都要导出（否则上面那条可能是"本来就没导出"）。"""
        man = self._export_two()
        self.assertEqual(man["count"], 2)
        self.assertEqual(man["skipped_suppressed"], [])

    def test_prune_suppressed_deletes_json_md_and_manifest_entry(self):
        """**文件真被删掉**的断言（提案 §6 第 2 步明确要求）。"""
        self._export_two()
        sid = "deepseek-export:conv-002"
        jsonp = _export_jsons_for(self.root, sid)[0]
        mdp = jsonp.with_suffix(".md")
        self.assertTrue(mdp.is_file(), "夹具前提：同名 .md 也要存在")
        self.assertIn(sid, _manifest_sids(self.root))

        r = prune_suppressed(self.root / "exports", {sid})
        self.assertFalse(jsonp.exists(), "命中的 .json 必须被真删掉")
        self.assertFalse(mdp.exists(), "同名 .md 必须一并删掉")
        self.assertEqual([d["sid"] for d in r["deleted"]], [sid])
        self.assertEqual(r["manifest_removed"], 1)
        self.assertNotIn(sid, _manifest_sids(self.root))
        # 另一条会话的副本与清单条目原样保留
        self.assertEqual(len(_export_jsons_for(self.root,
                                               "deepseek-export:conv-001")), 1)
        self.assertIn("deepseek-export:conv-001", _manifest_sids(self.root))
        man = json.loads((self.root / "exports" / "export_manifest.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(man["count"], len(man["exported"]))

    def test_prune_dry_run_deletes_nothing(self):
        self._export_two()
        sid = "deepseek-export:conv-002"
        jsonp = _export_jsons_for(self.root, sid)[0]
        before = jsonp.read_bytes()
        r = prune_suppressed(self.root / "exports", {sid}, dry_run=True)
        self.assertEqual(len(r["deleted"]), 1)
        self.assertTrue(jsonp.is_file(), "dry-run 不许删文件")
        self.assertEqual(jsonp.read_bytes(), before)
        self.assertIn(sid, _manifest_sids(self.root))
        self.assertEqual(r["manifest_removed"], 0)

    def test_prune_reports_unreadable_json_instead_of_silently_skipping(self):
        self._export_two()
        broken = self.root / "exports" / "Agent" / "2026-10" / "broken.json"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("{ 这不是 json", encoding="utf-8", newline="\n")
        notsess = broken.with_name("not-a-session.json")
        notsess.write_text('{"foo": 1}', encoding="utf-8", newline="\n")
        r = prune_suppressed(self.root / "exports", {"deepseek-export:conv-002"})
        self.assertIn("Agent/2026-10/broken.json", r["unreadable"])
        self.assertIn("Agent/2026-10/not-a-session.json", r["unreadable"],
                      "合法 json 但不是会话结构 → 也算读不出（不静默跳过）")
        self.assertTrue(broken.is_file(), "读不出的文件不许删（列出来让人看）")
        self.assertTrue(notsess.is_file())

    def test_file_sid_agrees_with_index_sid_formula(self):
        """清理侧读出的 sid 必须与**入库侧**算出的 sid 逐字相同。

        会红的情形：`file_sid` 的兜底公式（`data.get('session_id', p.stem)`）
        或 `source` 默认值被改动 → 清理与索引对同文件认定不同 sid，
        会出现「索引以为是 A、清理以为是 B」的静默错配。
        """
        from harvester.indexing import _index_json_file
        self._export_two()
        p = _export_jsons_for(self.root, "deepseek-export:conv-001")[0]
        con = sqlite3.connect(str(self.root / "harvester.db"))
        try:
            con.executescript(SCHEMA)
            sid, _n, _bad, sup = _index_json_file(con, p, self.root / "exports")
        finally:
            con.close()
        self.assertFalse(sup)
        self.assertEqual(sid, file_sid(p))
        self.assertEqual(sid, "deepseek-export:conv-001")

    def test_prune_falls_back_to_full_parse_when_sid_not_in_head(self):
        """头部 8 KB 嗅不到 sid 时的兜底路径必须真的能认出会话。

        会红的情形：删掉 `file_sid` 的整文件解析兜底（只留头部嗅探）——
        这个夹具的 sid 字段被 9 KB 填充推到后面，兜底一没就认不出来，
        文件不会被删。
        """
        import harvester.exporter as E
        d = self.root / "exports" / "Agent" / "2026-10"
        d.mkdir(parents=True, exist_ok=True)
        pad = "填" * 5000          # 每字 3 字节 → 远超头部开窗
        payload = {"pad": pad, "source": "adapter-x", "session_id": "big-1",
                   "messages": []}
        p = d / "big.json"
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8",
                     newline="\n")
        self.assertGreater(p.stat().st_size, E._SID_SNIFF_BYTES)
        self.assertEqual(file_sid(p), "adapter-x:big-1")
        r = prune_suppressed(self.root / "exports", {"adapter-x:big-1"})
        self.assertEqual([x["sid"] for x in r["deleted"]], ["adapter-x:big-1"])
        self.assertFalse(p.exists())

    def test_prune_without_suppressed_is_noop(self):
        self._export_two()
        r = prune_suppressed(self.root / "exports", set())
        self.assertEqual(r["scanned"], 0, "没有抑制项就不该扫（省掉 2000 文件的开销）")
        self.assertEqual(r["deleted"], [])
        r2 = prune_suppressed(self.root / "exports", {"deepseek-export:nope"})
        self.assertEqual(r2["scanned"], 2, "有抑制项时扫全部 json，但一个都不删")
        self.assertEqual(r2["deleted"], [])


# ---- 3. 索引侧兜底（indexing） ----

class IndexSkipTest(_TmpRoot):

    def _build_exports(self) -> None:
        from harvester.exporter import export
        _write_inbox(self.root, [_conv(1, "阿尔法保留"), _conv(2, "布拉沃抑制")])
        src = {"plugins": [{"id": "deepseek-export",
                            "paths": [str(self.root / "inbox"
                                          / "conversations.json")]}]}
        export(self.root / "exports", sources=src, only={"deepseek-export"},
               include_notes=True)

    def test_index_exports_skip_sids_leaves_no_rows(self):
        """兜底闸：文件没被清掉时，索引也不收（提案 §2.3 挂点 C）。"""
        self._build_exports()
        db = self.root / "harvester.db"
        stats = index_exports(self.root / "exports", db,
                              skip_sids={"deepseek-export:conv-002"})
        self.assertEqual(stats["sessions"], 1)
        self.assertEqual(stats["skipped_suppressed"], 1)
        self.assertEqual(_count(db, "sessions", "deepseek-export:conv-002"), 0)
        self.assertEqual(_count(db, "messages", "deepseek-export:conv-002"), 0)
        self.assertEqual(_count(db, "steps", "deepseek-export:conv-002"), 0)
        self.assertEqual(_fts_hits(db, "布拉沃抑制"), 0)
        self.assertEqual(_count(db, "sessions", "deepseek-export:conv-001"), 1)

    def test_index_exports_without_skip_indexes_everything(self):
        """反恒真：不传 skip_sids 时两条都要进库（否则上一条可能是"本来就没进"）。"""
        self._build_exports()
        db = self.root / "harvester.db"
        stats = index_exports(self.root / "exports", db)
        self.assertEqual(stats["sessions"], 2)
        self.assertEqual(stats["skipped_suppressed"], 0)
        self.assertEqual(_fts_hits(db, "布拉沃抑制"), 1)


# ---- 4. 端到端：抑制 → sync/update → 不再入库（核心验收） ----

class EndToEndSuppressTest(_TmpRoot):
    SID = "deepseek-export:conv-001"
    KEEP = "deepseek-export:conv-002"

    def _two_sessions(self, use_sync: bool = False) -> Path:
        _write_inbox(self.root, [_conv(1, "阿尔法抑制"), _conv(2, "布拉沃保留")])
        (run_sync if use_sync else run_update)(**_kwargs(self.root))
        db = self.root / "harvester.db"
        self.assertEqual(_count(db, "sessions", self.SID), 1)
        self.assertEqual(_count(db, "sessions", self.KEEP), 1)
        return db

    def test_suppressed_session_stays_out_after_full_sync(self):
        """§4 第 2 条（本需求的核心验收）：抑制 → sync → 三表行数 0 且不在清单。"""
        db = self._two_sessions()
        self.assertEqual(_fts_hits(db, "阿尔法抑制"), 1)      # 反恒真
        suppress(self.meta, db, [self.SID], reason="单轮查词",
                 created_by="tester", exports_dir=self.root / "exports")
        r = run_sync(**_kwargs(self.root))
        self.assertEqual(r["suppress"]["count"], 1)
        self.assertEqual(r["suppress"]["sids"], [self.SID])
        self.assertEqual(r["suppress"]["export_skipped"], 1,
                         "导出步必须真的拦住它（否则只能靠后面清副本兜底，"
                         "每一步都白写一个 244 MB 的副本）")
        self.assertEqual(r["suppress"]["index_skipped"], 0,
                         "导出已拦住 → 索引侧不该再有兜底命中（有=旧副本没清干净）")
        for tbl in ("sessions", "messages", "steps"):
            self.assertEqual(_count(db, tbl, self.SID), 0, f"sync 后 {tbl} 还有行")
        self.assertEqual(_fts_hits(db, "阿尔法抑制"), 0)
        self.assertEqual(_export_jsons_for(self.root, self.SID), [],
                         "suppressed sid 的 export json 仍在 exports 里")
        self.assertNotIn(self.SID, _manifest_sids(self.root),
                         "suppressed sid 出现在 export_manifest.json 里")
        # 保留的那条照常
        self.assertEqual(_count(db, "sessions", self.KEEP), 1)
        self.assertEqual(_fts_hits(db, "布拉沃保留"), 1)
        self.assertIn(self.KEEP, _manifest_sids(self.root))

    def test_sync_prunes_leftover_export_file_of_suppressed_sid(self):
        """旧副本还在 = 「抑制了又回来」的真正成因（提案 §1）：sync 必须清掉它。

        夹具刻意**不**清文件地写抑制行（`exports_dir=None`），模拟
        「抑制表里有、上一轮的 export 文件还躺在 exports 里」。
        会红的情形：去掉 `run_sync` 里的 `prune_suppressed` 调用
        —— 文件仍在 → 本条的「文件被删」断言红；若同时去掉索引兜底，
        库内行数断言也红。
        """
        db = self._two_sessions()
        suppress(self.meta, db, [self.SID], reason="只写抑制行，不清文件")
        leftover = _export_jsons_for(self.root, self.SID)
        self.assertEqual(len(leftover), 1, "夹具前提：旧副本确实还在")
        r = run_sync(**_kwargs(self.root))
        pruned = r["suppress"]["pruned"]
        self.assertEqual([d["sid"] for d in pruned["deleted"]], [self.SID])
        self.assertFalse(leftover[0].exists(), "旧副本必须被 sync 清掉")
        self.assertEqual(_count(db, "sessions", self.SID), 0)

    def test_update_drops_suppressed_sid_even_if_the_row_came_back(self):
        """update 侧：抑制 sid 并进 `drop_sids`，**不混进「源已消失」口径**。

        夹具刻意制造「抑制之后行又回到库里」（再跑一次整库重建）——
        这正是 update 必须自己兜住的情形：抑制表里的 sid 只要被索引路径
        建回来，下一次 update 就得清掉它。会红的情形：`run_update` 里
        去掉 `sorted(suppressed)` 的并入 → 三表行数断言红。
        """
        db = self._two_sessions()
        suppress(self.meta, db, [self.SID], reason="只写抑制行，不清文件")
        index_exports(self.root / "exports", db)         # 行又被建回来
        self.assertEqual(_count(db, "sessions", self.SID), 1)
        r = run_update(**_kwargs(self.root))
        self.assertEqual(r["suppress"]["count"], 1)
        self.assertEqual(r["suppress"]["drop_sids"], 1)
        self.assertEqual(r["gone"], [], "抑制的清理不许记成「源已消失」")
        self.assertEqual(r["index"]["dropped"], 1)
        for tbl in ("sessions", "messages", "steps"):
            self.assertEqual(_count(db, tbl, self.SID), 0, tbl)
        self.assertEqual(_fts_hits(db, "阿尔法抑制"), 0)
        self.assertEqual(_count(db, "sessions", self.KEEP), 1)
        self.assertEqual(_export_jsons_for(self.root, self.SID), [])

    def test_unsuppress_then_sync_restores_from_corpus(self):
        """§4 第 3 条 + 口径诚实：语料还在 → 取消抑制后 sync 就能回来。

        注意本测试**只**证明"语料还在"这一半；语料不在了会怎样由
        `test_unsuppress_removes_row_but_does_not_restore_data` 与 CLI 文案如实陈述。
        """
        db = self._two_sessions()
        suppress(self.meta, db, [self.SID], reason="无营养",
                 exports_dir=self.root / "exports")
        run_sync(**_kwargs(self.root))
        self.assertEqual(_count(db, "sessions", self.SID), 0)
        self.assertEqual(unsuppress(self.meta, [self.SID]), 1)
        self.assertEqual(_count(db, "sessions", self.SID), 0,
                         "取消抑制本身不恢复数据（恢复靠下面的 sync）")
        r = run_sync(**_kwargs(self.root))
        self.assertEqual(r["suppress"]["count"], 0)
        self.assertEqual(_count(db, "sessions", self.SID), 1)
        self.assertEqual(_fts_hits(db, "阿尔法抑制"), 1)
        self.assertEqual(len(_export_jsons_for(self.root, self.SID)), 1)

    def test_sync_without_suppress_meta_is_unchanged_behaviour(self):
        """没有抑制表（绝大多数机器）：行为与 v0.48 逐字一致，且**不创建** meta 库。"""
        db = self._two_sessions()
        self.assertFalse(self.meta.exists())
        r = run_sync(**_kwargs(self.root))
        self.assertEqual(r["suppress"]["count"], 0)
        self.assertEqual(r["suppress"]["export_skipped"], 0)
        self.assertFalse(self.meta.exists(), "sync 不该创建 topics_meta.db")
        self.assertEqual(_count(db, "sessions", self.SID), 1)

    def test_suppress_meta_false_disables_interception(self):
        """`suppress_meta=False` = 显式关闭（--no-suppress 的底层语义）：
        被抑制的会话会**回来**——这正是"关掉拦截"该有的样子。"""
        db = self._two_sessions()
        suppress(self.meta, db, [self.SID], reason="无营养",
                 exports_dir=self.root / "exports")
        r = run_sync(**_kwargs(self.root), suppress_meta=False)
        self.assertEqual(r["suppress"]["count"], 0)
        self.assertEqual(r["suppress"]["meta"], None)
        self.assertEqual(_count(db, "sessions", self.SID), 1)

    def test_custom_suppress_meta_path_is_honoured(self):
        """`suppress_meta=<别的路径>` 时按**那一个**库拦截（默认位置不被读）。"""
        db = self._two_sessions()
        other = self.root / "sub" / "s.meta.db"
        ensure_suppress_table(other)
        con = sqlite3.connect(str(other))
        con.execute("INSERT INTO suppressed_sessions (sid, reason, created, "
                    "created_by) VALUES (?,?,?,?)",
                    (self.SID, "别处抑制的", "2026-10-13 10:00:00", "t"))
        con.commit()
        con.close()
        r = run_sync(**_kwargs(self.root), suppress_meta=other)
        self.assertEqual(r["suppress"]["count"], 1)
        self.assertEqual(r["suppress"]["meta"], str(other))
        self.assertEqual(_count(db, "sessions", self.SID), 0)


# ---- 5. CLI（suppress 四个动作 + 默认 dry-run） ----

class SuppressCliTest(_TmpRoot):

    def _two_sessions(self) -> Path:
        _write_inbox(self.root, [_conv(1, "阿尔法抑制"), _conv(2, "布拉沃保留")])
        run_update(**_kwargs(self.root))
        return self.root / "harvester.db"

    def _base(self) -> list[str]:
        return ["--meta", str(self.meta), "--db",
                str(self.root / "harvester.db"),
                "--exports", str(self.root / "exports")]

    def test_parser_defaults_and_flags(self):
        a = build_parser().parse_args(["suppress", "add", "--sid", "x"])
        self.assertEqual(a.action, "add")
        self.assertEqual(a.meta, "topics_meta.db")
        self.assertEqual(a.db, "harvester.db")
        self.assertEqual(a.exports, "exports")
        self.assertFalse(a.apply, "默认必须 dry-run（提案 §5 ⑤）")
        self.assertEqual(a.sids, ["x"])
        b = build_parser().parse_args(["suppress", "add", "--sid", "x",
                                       "--sid", "y", "--apply"])
        self.assertEqual(b.sids, ["x", "y"])
        self.assertTrue(b.apply)
        c = build_parser().parse_args(["suppress", "list"])
        self.assertIs(c.func, cmd_suppress)
        d = build_parser().parse_args(["suppress", "prune-exports"])
        self.assertFalse(d.apply)
        # sync / update 的抑制开关
        s = build_parser().parse_args(["sync"])
        self.assertEqual(s.suppress_meta, "topics_meta.db")
        self.assertFalse(s.no_suppress)
        s2 = build_parser().parse_args(["sync", "--no-suppress",
                                        "--suppress-meta", "m.db"])
        self.assertTrue(s2.no_suppress)
        self.assertEqual(s2.suppress_meta, "m.db")

    def test_add_is_dry_run_by_default_then_apply_writes(self):
        db = self._two_sessions()
        sid = "deepseek-export:conv-001"
        with _cli(["suppress", "add", "--sid", sid, "--reason", "单轮查词"]
                  + self._base()) as (code, out):
            self.assertEqual(code, 0)
            self.assertIn(sid, out)
            self.assertIn("dry-run", out)
        self.assertFalse(self.meta.exists(), "dry-run 不该创建抑制表")
        self.assertEqual(_count(db, "sessions", sid), 1, "dry-run 不该删库内行")
        self.assertEqual(len(_export_jsons_for(self.root, sid)), 1,
                         "dry-run 不该删 exports 副本")

        with _cli(["suppress", "add", "--sid", sid, "--reason", "单轮查词",
                   "--by", "tester", "--apply"] + self._base()) as (code, out):
            self.assertEqual(code, 0)
            self.assertIn("已抑制", out)
            self.assertIn("不删采集源", out)
        self.assertEqual(_count(db, "sessions", sid), 0)
        self.assertEqual(_export_jsons_for(self.root, sid), [])
        rows = list_suppressed(self.meta)
        self.assertEqual([r["sid"] for r in rows], [sid])
        self.assertEqual(rows[0]["created_by"], "tester")

    def test_list_prints_rows_and_states_recovery_scope_honestly(self):
        db = self._two_sessions()
        sid = "deepseek-export:conv-001"
        suppress(self.meta, db, [sid], reason="单轮查词", created_by="tester")
        with _cli(["suppress", "list", "--meta", str(self.meta)]) as (code, out):
            self.assertEqual(code, 0)
            self.assertIn(sid, out)
            self.assertIn("单轮查词", out)
            self.assertIn("tester", out)
            self.assertIn("只要语料还在", out)
            self.assertNotIn("30 天", out)
        with _cli(["suppress", "list", "--meta",
                   str(self.root / "none.db")]) as (code, out):
            self.assertEqual(code, 0)
            self.assertIn("抑制表为空", out)

    def test_remove_dry_run_then_apply_and_no_time_limit_wording(self):
        db = self._two_sessions()
        sid = "deepseek-export:conv-001"
        suppress(self.meta, db, [sid], reason="无营养",
                 exports_dir=self.root / "exports")
        with _cli(["suppress", "remove", "--sid", sid,
                   "--meta", str(self.meta)]) as (code, out):
            self.assertEqual(code, 0)
            self.assertIn("dry-run", out)
            self.assertIn("在抑制表里", out)
        self.assertEqual(len(list_suppressed(self.meta)), 1, "dry-run 不该删行")
        with _cli(["suppress", "remove", "--sid", sid, "--meta",
                   str(self.meta), "--apply"]) as (code, out):
            self.assertEqual(code, 0)
            self.assertIn("已取消抑制 1 行", out)
            self.assertIn("不会", out)
            self.assertIn("只取决于语料是否还在", out)
            self.assertIn("不设恢复时限", out)
            self.assertNotIn("30 天", out)
        self.assertEqual(list_suppressed(self.meta), [])
        self.assertEqual(_count(db, "sessions", sid), 0,
                         "remove 不许顺手恢复数据")

    def test_remove_reports_sid_not_in_table(self):
        with _cli(["suppress", "remove", "--sid", "dsh:nope", "--meta",
                   str(self.meta), "--apply"]) as (code, out):
            self.assertEqual(code, 0)
            self.assertIn("已取消抑制 0 行", out)

    def test_prune_exports_dry_run_then_apply(self):
        db = self._two_sessions()
        sid = "deepseek-export:conv-001"
        suppress(self.meta, db, [sid], reason="只写抑制行，不清文件")
        left = _export_jsons_for(self.root, sid)
        self.assertEqual(len(left), 1)
        with _cli(["suppress", "prune-exports", "--meta", str(self.meta),
                   "--exports", str(self.root / "exports")]) as (code, out):
            self.assertEqual(code, 0)
            self.assertIn("dry-run", out)
            self.assertIn("命中 1 个", out)
            self.assertIn(".json", out)
        self.assertTrue(left[0].is_file(), "dry-run 不该删文件")
        with _cli(["suppress", "prune-exports", "--meta", str(self.meta),
                   "--exports", str(self.root / "exports"), "--apply"]) as (code, out):
            self.assertEqual(code, 0)
            self.assertIn("已删文件", out)
        self.assertFalse(left[0].exists())
        self.assertEqual(_manifest_sids(self.root).intersection({sid}), set())

    def test_unknown_action_fails_loud(self):
        """手工构造的 namespace（绕过 argparse）也必须 fail loud，而非静默什么都不做。"""
        ns = argparse.Namespace(action="bogus", meta=str(self.meta),
                                sids=[], db=str(self.root / "harvester.db"),
                                exports=str(self.root / "exports"))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_suppress(ns)
        self.assertEqual(code, 2)
        self.assertIn("未知的 suppress 动作", err.getvalue())


if __name__ == "__main__":
    unittest.main()
