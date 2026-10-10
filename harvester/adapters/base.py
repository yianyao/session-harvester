# -*- coding: utf-8 -*-
"""Adapter 基类协议。

实现契约（新增 Adapter 必须遵守）：
1. detect() 只做路径/环境探测，绝不抛异常——探测失败返回 status=MISSING。
2. list_sessions() 返回纲要级信息（不加载消息正文），供 scan 阶段快速列举。
3. load_session(session_id) 返回完整 SessionRecord。
4. 数据不存在或格式无法验证时：detect 返回 STUB/MISSING，绝不允许凭猜测解析。
   ——本项目铁律：实现不了就是实现不了，留接口不留臆测代码。
5. 所有文件读取必须 UTF-8；解析失败逐条跳过并记录 warning，不中断整体扫描。
"""

from __future__ import annotations

import abc
import atexit
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..models import SessionRecord, normalize_text

#: open_ro_sqlite 临时副本目录（进程退出时统一清理）
_TMP_DIRS: list[Path] = []


def _cleanup_tmp_dirs() -> None:
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup_tmp_dirs)


def open_ro_sqlite(db: Path) -> sqlite3.Connection:
    """只读打开 SQLite 库。

    WAL 模式下宿主进程（如正在运行的 VS Code / AutoClaw）持库时，
    只读直连会在首次查询报 disk I/O error（无法做 shm 恢复）；
    此时复制主库连同 -wal 到临时目录再打开，读到复制时刻的最新已提交数据。
    """
    def _connect(p: Path) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    con = _connect(db)
    try:
        # connect 是惰性的，触发一次真实读才能暴露 WAL 锁冲突
        con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
    except sqlite3.Error:
        con.close()
        td = Path(tempfile.mkdtemp(prefix="harvester-ro-"))
        _TMP_DIRS.append(td)
        dst = td / db.name
        shutil.copy2(db, dst)
        wal = db.with_name(db.name + "-wal")
        if wal.is_file():
            shutil.copy2(wal, dst.with_name(dst.name + "-wal"))
        con = _connect(dst)
    return con


@dataclass
class DetectReport:
    adapter_id: str
    name: str
    # OK: 可完整提取；STUB: 有接口但暂不可实现；MISSING: 本机未安装
    status: str
    detail: str = ""              # 人类可读说明（如数据落点、不可实现原因）
    session_count: int | None = None
    hints: list[str] = field(default_factory=list)  # 后续实现时的接入线索
    # v0.45 additive：私有 schema 的版本/指纹/守卫结论（可选；只有做守卫的
    # 源会填）。用途是**溯源与对账**：DSH 0.3 之类上游改版时，
    # 报告里能直接看到"声明版本 vs 本实现支持版本"，而不是等到解析出半成品。
    schema_version: str | None = None
    schema_fingerprint: str | None = None
    schema_ok: bool | None = None


class BaseAdapter(abc.ABC):
    id: str = "base"
    name: str = "Base"
    category: str = "其他"       # 导出时的"大类"目录名
    #: detect 用的候选路径（存在性检查用）；元组不可变，避免类级可变默认值共享
    candidate_paths: tuple[str, ...] = ()
    #: 源形态声明（契约字段，v0.12）：能否认领"单个导出文件"。
    #: file  = 单文件源（官方导出 zip/json），可参与收件箱竞标；
    #: dir  = 目录型源（采集产物目录 detail_*.json 等），禁止认领单文件——
    #:        其 detect() 可能经默认候选回退在别处返回 OK，导致错误认领。
    claims_files: bool = True

    @abc.abstractmethod
    def detect(self) -> DetectReport: ...

    @abc.abstractmethod
    def list_sessions(self) -> list[dict]:
        """纲要条目。字段约定：
        session_id / title / created_at / updated_at / message_count / preview
        message_count 与 preview 可为 None（纲要阶段不读正文时）。
        """

    @abc.abstractmethod
    def load_session(self, session_id: str) -> SessionRecord:
        """加载完整会话。源数据无法完整还原时应在 SessionRecord.extra
        中注明 lossy=True 及原因，而不是抛异常。"""

    # ---- 通用工具 ----
    @staticmethod
    def _read_text(path: str | Path, encoding: str = "utf-8") -> str:
        with open(path, "r", encoding=encoding, errors="replace") as f:
            return normalize_text(f.read())  # 源文件可能为 CRLF，统一归一化

    def slugify(self, title: str, max_len: int = 40) -> str:
        """导出文件名安全化：去路径分隔/非法字符，空白转下划线。"""
        bad = '<>:"/\\|?*\n\r\t'
        s = "".join(c if c not in bad else "_" for c in (title or "untitled"))
        s = "_".join(s.split()) or "untitled"
        return s[:max_len]
