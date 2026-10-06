# -*- coding: utf-8 -*-
"""WorkBuddy 本地工作区日志 Adapter。

数据源（2026-10-05 实测验证）：
- C:/Users/yianyao/WorkBuddy/<workspace>/.workbuddy/memory/YYYY-MM-DD.md
  —— 每日工作日志，按工作区×日期为一个"会话"单元
- 跨项目备忘 ~/.workbuddy/MEMORY.md 与项目级 MEMORY.md 属于提炼产物，
  不作为会话条目（避免与原始轨迹重复计数）。

已知局限：日志是"主动记录"而非逐字归档，不含完整对话原文。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..models import Message, SessionRecord
from .base import BaseAdapter, DetectReport

WORKSPACE_ROOT = str(Path.home() / "WorkBuddy")
LOG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


class WorkBuddyAdapter(BaseAdapter):
    id = "workbuddy"
    name = "WorkBuddy 工作区日志"
    # 独立 category（v0.6）：本源是 agent 自述的每日工作总结，不是对话
    # 轨迹；真实逐回合轨迹见 workbuddy-transcript。导出目录因此分开，
    # 避免蒸馏时把"总结"当"过程"提炼。
    category = "Agent工作总结"

    def __init__(self, workspace_root: str = WORKSPACE_ROOT):
        self.root = Path(workspace_root)
        self._index: list[tuple[Path, str, str]] = []  # (path, workspace, date)

    def detect(self) -> DetectReport:
        if not self.root.is_dir():
            return DetectReport(self.id, self.name, "MISSING", f"目录不存在: {self.root}")
        logs = self._find_logs()
        if not logs:
            return DetectReport(self.id, self.name, "MISSING", "未找到任何每日日志")
        workspaces = len({ws for _, ws, _ in logs})
        return DetectReport(
            self.id, self.name, "OK",
            f"{len(logs)} 份日志 / {workspaces} 个工作区",
            session_count=len(logs),
        )

    def _find_logs(self) -> list[tuple[Path, str, str]]:
        if self._index:
            return self._index
        out: list[tuple[Path, str, str]] = []
        if not self.root.is_dir():
            return out
        for ws in sorted(self.root.iterdir()):
            mem = ws / ".workbuddy" / "memory"
            if not mem.is_dir():
                continue
            for f in sorted(mem.iterdir()):
                if f.is_file() and LOG_RE.match(f.name):
                    out.append((f, ws.name, f.stem))
        self._index = out
        return out

    def list_sessions(self) -> list[dict]:
        items = []
        for path, ws, date in self._find_logs():
            try:
                head = self._read_text(path)[:200].replace("\n", " ")
            except OSError:
                head = ""
            items.append({
                "session_id": f"{ws}::{date}",
                "title": f"[{ws}] {date} 工作日志",
                "created_at": date,
                "updated_at": date,
                "message_count": None,
                "preview": head[:80],
            })
        return items

    def load_session(self, session_id: str) -> SessionRecord:
        ws, date = session_id.split("::", 1)
        path = self.root / ws / ".workbuddy" / "memory" / f"{date}.md"
        text = self._read_text(path)
        msgs = [Message(role="assistant", text=text, timestamp=date)]
        return SessionRecord(
            source=self.id, session_id=session_id,
            title=f"[{ws}] {date} 工作日志",
            created_at=date, updated_at=date,
            messages=msgs, extra={"workspace": ws, "kind": "daily-log"},
        )
