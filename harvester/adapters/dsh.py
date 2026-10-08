# -*- coding: utf-8 -*-
"""DSH（DeepSeek Harness）会话 Adapter（~/.dsh/sessions/）。

数据源（2026-10-06 实测核验，本机 3 工作区 20+ 会话 / 21.5MB）：
  ~/.dsh/sessions/<workspace-slug>/<session-id>/session.v4.jsonl.zstd

压缩形态：**多 zstd frame 串联**（magic 0x28B52FFD 分帧；实测单文件 247
frame）。解压按优先级分层，全部不可用时 STUB（不硬猜）：
1. Python 3.14+ 标准库 compression.zstd；
2. 可选第三方包 zstandard（pip install zstandard，非本套件强制依赖）；
3. PATH 上的 zstd.exe；
4. PATH 上的 node（zlib.zstdDecompressSync，按 magic 分帧逐帧解压；
   Node ≥ 23.8 / 24 原生支持）。

jsonl 行 type（v4 实测全集）→ 映射：
- session          会话元信息（id/createdAt/cwd/origin/delegationDepth）
- session/title    标题
- user/message     data.content[] text 块 → user
- assistant/message data.message.content[]：text→assistant、reasoning→note
- tool/call        data.name/callId/arguments → note [tool_call]
- tool/result      data.message.toolCallId/content → note [tool_result]
                   （无结构化错误码；错误以 "Error:" 文本前缀识别，同
                   workbuddy-transcript 口径）
- approval/policy、sandbox/mode、permission/preset
                   → note [policy]（Harness 配置信号，直接服务目标 2.3）
- 其余（system/message、step/*、turn/*、request/*、web/*、
   session-log-*、agent/inbox/*）跳过
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..models import Message, SessionRecord, normalize_text, to_local_ts
from .base import BaseAdapter, DetectReport

DSH_SESSIONS = Path(os.environ.get(
    "USERPROFILE", str(Path.home()))) / ".dsh" / "sessions"

_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

_SKIP_TYPES = {
    "system/message", "session-log-deepseek/delivery-accepted",
    "request/header", "request/context",
    "web/deepseek-search-llm-request",
    "agent/inbox/spliced",
}
# 前缀匹配的跳过类型（step/start、step/end、turn/start、turn/end 等）
_SKIP_PREFIXES = ("step/", "turn/", "session-log-")


class ZstdUnavailable(RuntimeError):
    """四种解压路径全部不可用。"""


def _split_frames(buf: bytes) -> list[int]:
    starts: list[int] = []
    i = buf.find(_ZSTD_MAGIC)
    while i != -1:
        starts.append(i)
        i = buf.find(_ZSTD_MAGIC, i + 4)
    return starts


def zstd_decompress(path: Path) -> str:
    """解压多 frame zstd 文件为文本。分层策略见模块注释。"""
    buf = path.read_bytes()
    # 1) Python 3.14+ 标准库
    try:
        from compression import zstd as _zstd  # noqa: PLC0415
        return _zstd.decompress(buf).decode("utf-8", "replace")
    except ImportError:
        pass
    # 2) zstandard 可选包
    try:
        import zstandard  # noqa: PLC0415
        dctx = zstandard.ZstdDecompressor()
        starts = _split_frames(buf)
        if len(starts) <= 1:
            with dctx.stream_reader(io.BytesIO(buf)) as r:
                return r.read().decode("utf-8", "replace")
        out = []
        for k, s in enumerate(starts):
            e = starts[k + 1] if k + 1 < len(starts) else len(buf)
            with dctx.stream_reader(io.BytesIO(buf[s:e])) as r:
                out.append(r.read())
        return b"".join(out).decode("utf-8", "replace")
    except ImportError:
        pass
    # 3) 外部 zstd.exe（天然支持多 frame 串联）
    exe = shutil.which("zstd")
    if exe:
        r = subprocess.run([exe, "-d", "-c", str(path)],
                           capture_output=True, timeout=120)
        if r.returncode == 0:
            return r.stdout.decode("utf-8", "replace")
    # 4) node（按 magic 分帧逐帧解压）
    node = shutil.which("node")
    if node:
        return _decompress_via_node(node, path)
    raise ZstdUnavailable(
        "无法解压 zstd：需要 Python 3.14+ / pip install zstandard / "
        "PATH 上的 zstd.exe 或 node（≥23.8）任一可用")


def _decompress_via_node(node: str, path: Path) -> str:
    script = (
        "const fs=require('fs'),z=require('zlib'),os=require('os'),p=require('path');"
        "const [src,dst]=process.argv.slice(2);"
        "const buf=fs.readFileSync(src);"
        "const MAGIC=Buffer.from([0x28,0xB5,0x2F,0xFD]);"
        "const starts=[];let i=0;"
        "while((i=buf.indexOf(MAGIC,i))!==-1){starts.push(i);i+=4;}"
        "if(!starts.length){console.error('no zstd frame');process.exit(3);}"
        "let out=[];"
        "for(let k=0;k<starts.length;k++){"
        "  const end=k+1<starts.length?starts[k+1]:buf.length;"
        "  out.push(z.zstdDecompressSync(buf.subarray(starts[k],end)));}"
        "fs.writeFileSync(dst,Buffer.concat(out));"
    )
    with tempfile.TemporaryDirectory(prefix="harvester-zstd-") as td:
        js = Path(td) / "zd.js"
        dst = Path(td) / "out.jsonl"
        js.write_text(script, encoding="utf-8", newline="\n")
        r = subprocess.run([node, str(js), str(path), str(dst)],
                           capture_output=True, timeout=120)
        if r.returncode != 0 or not dst.is_file():
            raise ZstdUnavailable(
                f"node 解压失败: {r.stderr.decode('utf-8', 'replace')[:200]}")
        return dst.read_text(encoding="utf-8", errors="replace")


def _policy_line(t: str, data: dict) -> str | None:
    if t == "approval/policy":
        return f"approval={data.get('policy')} (source={data.get('source')})"
    if t == "sandbox/mode":
        return f"sandbox={data.get('mode')} (source={data.get('source')})"
    if t == "permission/preset":
        return f"permission={data.get('preset')}"
    return None


def _model_from_lines(lines: list[dict]) -> tuple[str | None, dict]:
    """会话主模型抽取（v0.22 P0-5，H13）：request/header 每请求一条
    header.config.model 计次（真实日志实测路径）；subagent 会话由
    subagent/descriptor.agentModel 兜底。返回 (众数, 全分布)。
    纯函数（行列表已解析），与 workbuddy_transcript 的 providerData.model
    众数口径对齐。"""
    from collections import Counter
    seen: Counter[str] = Counter()
    for o in lines:
        t = o.get("type")
        data = o.get("data") or {}
        if t == "request/header":
            cfg = ((data.get("header") or {}).get("config") or {})
            if cfg.get("model"):
                seen[str(cfg["model"])] += 1
        elif t == "subagent/descriptor" and data.get("agentModel"):
            seen[str(data["agentModel"])] += 1
    if not seen:
        return None, {}
    return seen.most_common(1)[0][0], dict(seen)


class DshAdapter(BaseAdapter):
    """DSH session.v4.jsonl.zstd。id: dsh。"""

    id = "dsh"
    name = "DSH (DeepSeek Harness)"
    category = "Agent"

    def __init__(self, sessions_root: Path | None = None):
        self.root = Path(sessions_root) if sessions_root else DSH_SESSIONS
        self._zstd_ok: bool | None = None

    # ---- helpers -------------------------------------------------

    def _files(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted(self.root.glob("*/*/session.v4.jsonl.zstd"))

    def _decompress_ok(self) -> bool:
        if self._zstd_ok is None:
            try:
                self._probe_zstd()
                self._zstd_ok = True
            except ZstdUnavailable:
                self._zstd_ok = False
        return self._zstd_ok

    @staticmethod
    def _probe_zstd() -> None:
        """用 1 字节合法空输入探测解压能力（不落盘真实数据）。"""
        try:
            from compression import zstd as _z  # noqa: F401,PLC0415
            return
        except ImportError:
            pass
        try:
            import zstandard  # noqa: F401,PLC0415
            return
        except ImportError:
            pass
        if shutil.which("zstd") or shutil.which("node"):
            return
        raise ZstdUnavailable("no decompressor")

    def _lines(self, f: Path) -> list[dict]:
        try:
            text = zstd_decompress(f)
        except ZstdUnavailable as e:
            # 活会话写入中的瞬态（实测：文件在两次读取间被截断/重写）。
            # 仅当报"无 frame"时按可跳过处理；其他解压失败照抛。
            if "no zstd frame" in str(e) or not f.stat().st_size:
                return []
            raise
        items = []
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                items.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return items

    # ---- protocol ------------------------------------------------

    def detect(self) -> DetectReport:
        files = self._files()
        if not files:
            return DetectReport(self.id, self.name, "MISSING",
                                f"未找到 DSH 会话（{self.root}）")
        if not self._decompress_ok():
            return DetectReport(
                self.id, self.name, "STUB",
                f"找到 {len(files)} 个会话，但本机无法解压 zstd——"
                "需 Python 3.14+ / pip install zstandard / zstd.exe / "
                "node（≥23.8）任一可用",
                session_count=len(files))
        return DetectReport(self.id, self.name, "OK",
                            f"{len(files)} 个会话（多 frame zstd，已具备解压能力）",
                            session_count=len(files))

    def list_sessions(self) -> list[dict]:
        items = []
        for f in self._files():
            lines = self._lines(f)
            if not lines:
                continue  # 活会话写入中（空/无 frame），纲要阶段跳过
            created = updated = None
            title = None
            label = None
            n_msgs = 0
            origin = None
            depth = 0
            for o in lines:
                t = o.get("type")
                tm = o.get("time")
                if isinstance(tm, (int, float)) and tm > 0:
                    s = tm / 1000.0
                    created = created or s
                    updated = s if updated is None else max(updated, s)
                if t == "session/title" and (o.get("data") or {}).get("title"):
                    title = o["data"]["title"]
                elif t == "subagent/descriptor":
                    label = (o.get("data") or {}).get("label") or label
                elif t == "session":
                    d = o.get("data") if isinstance(o.get("data"), dict) else o
                    origin = d.get("origin")
                    depth = int(d.get("delegationDepth") or 0)
                elif t == "user/message":
                    n_msgs += 1
                elif t == "assistant/message":
                    n_msgs += 1
            rel = f.relative_to(self.root).as_posix()
            is_sub = origin == "subagent" or depth > 0
            items.append({
                "session_id": rel,
                "title": (("[subagent] " if is_sub else "")
                          + (title or label or f"DSH 会话 {f.parent.name[:8]}")),
                "created_at": to_local_ts(created),
                "updated_at": to_local_ts(updated),
                "message_count": n_msgs,
                "preview": (title or label or "")[:100],
                "workspace": rel.split("/")[0],
            })
        items.sort(key=lambda x: x["updated_at"] or "", reverse=True)
        return items

    def load_session(self, session_id: str) -> SessionRecord:
        rel = session_id
        f = self.root / Path(*rel.split("/"))
        if not f.is_file():
            raise KeyError(f"DSH 会话不存在: {f}")
        lines = self._lines(f)
        msgs: list[Message] = []
        title = None
        meta: dict = {}
        pending_calls: dict[str, str] = {}  # callId → 工具名
        first_t = last_t = None

        def _iso(o) -> str | None:
            tm = o.get("time")
            if not isinstance(tm, (int, float)) or tm <= 0:
                return None
            return to_local_ts(tm / 1000.0)
        for o in lines:
            t = o.get("type")
            tm = o.get("time")
            if isinstance(tm, (int, float)) and tm > 0:
                s = tm / 1000.0
                first_t = first_t or s
                last_t = s if last_t is None else max(last_t, s)
            if t in _SKIP_TYPES or (isinstance(t, str)
                                    and t.startswith(_SKIP_PREFIXES)):
                continue
            data = o.get("data") or {}
            if t == "session":
                meta = {"cwd": o.get("cwd") or data.get("cwd"),
                        "origin": o.get("origin") or data.get("origin"),
                        "delegationDepth": o.get("delegationDepth")
                        or data.get("delegationDepth"),
                        "parentSession": o.get("parentSession")
                        or data.get("parentSession")}
                continue
            if t == "session/title":
                title = data.get("title") or title
                continue
            if t == "subagent/descriptor":
                if not title and data.get("label"):
                    title = data["label"]
                continue
            if t in ("approval/policy", "sandbox/mode", "permission/preset"):
                msgs.append(Message(
                    role="note", text=f"[policy] {_policy_line(t, data)}",
                    timestamp=_iso(o)))
                continue
            if t == "user/message":
                text = "".join(
                    b.get("text", "") for b in data.get("content") or []
                    if isinstance(b, dict))
                text = normalize_text(text).strip()
                if text:
                    msgs.append(Message(role="user", text=text,
                                        timestamp=_iso(o)))
                continue
            if t == "assistant/message":
                for b in (data.get("message") or {}).get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    txt = normalize_text(b.get("text") or "").strip()
                    if not txt:
                        continue
                    if bt == "reasoning":
                        msgs.append(Message(
                            role="note", text=f"[reasoning] {txt}",
                            timestamp=_iso(o)))
                    elif bt == "text":
                        msgs.append(Message(
                            role="assistant", text=txt, timestamp=_iso(o)))
                continue
            if t == "tool/call":
                name = data.get("name") or "?"
                args = data.get("arguments") or ""
                call_id = data.get("callId") or ""
                if call_id:
                    pending_calls[call_id] = name
                msgs.append(Message(
                    role="note", text=f"[tool_call] {name}: {args[:200]}",
                    timestamp=_iso(o),
                    raw={"kind": "tool_call", "tool": name,
                         "detail": str(args)[:400]}))
                continue
            if t == "tool/result":
                msg = data.get("message") or {}
                call_id = msg.get("toolCallId") or ""
                text = "".join(
                    b.get("text", "") for b in msg.get("content") or []
                    if isinstance(b, dict))
                text = normalize_text(text).strip()
                is_err = text.lower().startswith("error")
                status = "error" if is_err else "completed"
                preview = text[:400] + ("…" if len(text) > 400 else "")
                # v4 的 tool/result 无 toolName，仅 toolCallId；
                # 工具名从对应 tool/call 行回填（行序保证 call 在前）。
                name = pending_calls.get(call_id, "unknown")
                msgs.append(Message(
                    role="note",
                    text=f"[tool_result] {name}: {status}"
                         f"{'; ' + preview if is_err else ''}",
                    timestamp=_iso(o),
                    raw={"kind": "tool_result", "tool": name,
                         "status": status,
                         "error": preview if is_err else None,
                         "callId": call_id}))
                continue
        warns: list[str] = []
        if not lines:
            warns.append("文件为空或尚无完整 zstd frame（会话可能正在写入）")
        if not msgs:
            warns.append("未解析出任何消息（格式可能已改版）")
        model, models = _model_from_lines(lines)  # v0.22 P0-5（H13）
        return SessionRecord(
            source=self.id, session_id=rel,
            title=title or f"DSH 会话 {f.parent.name[:8]}",
            created_at=to_local_ts(first_t), updated_at=to_local_ts(last_t),
            messages=msgs,
            extra={"file": str(f), "workspace": rel.split("/")[0],
                   "is_subagent": (meta.get("origin") == "subagent"
                                   or int(meta.get("delegationDepth") or 0) > 0),
                   "origin": "dsh-session-v4", "dsh_meta": meta,
                   "model": model, "models": models or None,
                   "lossy": bool(warns), "warnings": warns},
        )
