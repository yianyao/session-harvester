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

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..models import Message, SessionRecord, normalize_text, to_local_ts
from .base import BaseAdapter, DetectReport

DSH_SESSIONS = Path(os.environ.get(
    "USERPROFILE", str(Path.home()))) / ".dsh" / "sessions"

_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# ── schema 守卫（v0.45，SOP C3）────────────────────────────────────────
# 背景：DSH 0.3 落地前，本 adapter 对**私有 transcript 形态**的依赖全靠
# "实测核验"（模块头那段注释）。上游一旦改版，旧代码的失败方式是
# **静默产出半成品**（消息全丢或字段读空），而纲要/统计照跑不报错。
# 这里照 autoclaw 的守卫先例补两道：
# ① **声明版本**（文件名 `session.v4.jsonl.zstd` 自带）——不是已知版本就降级；
# ② **消费字段形态**——逐条核对"我们真正读的字段路径"是否存在且类型对。
# 外加一个**观察指纹**用于溯源（写进 detect 报告），它与版本号一起回答
# "这份数据我当时看到的形态是什么"。
_DECLARED_RE = re.compile(r"session\.v(\d+)\.")

#: 本实现对应的声明版本（文件名里那个 vN）
SCHEMA_VERSION = "4"
KNOWN_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})

#: 我们**真正读**的字段路径（消费契约）。这些路径缺失/类型不对 = 形态已改，
#: 因为下面的解析代码全部按它们取值（见 load_session 各分支）。
#: 注：approval/policy、sandbox/mode、permission/preset 读的是可空标量，
#: 缺失时只是渲染成 None，不算形态破坏，故不进必需集。
REQUIRED_SHAPE: dict[str, tuple[str, ...]] = {
    "session/title": ("data.title",),
    "user/message": ("data.content",),
    "assistant/message": ("data.message.content",),
    "tool/call": ("data.name",),
    "tool/result": ("data.message.toolCallId",),
}


def declared_version(path: Path) -> str | None:
    """从文件名取上游声明的 schema 版本（`session.v4.jsonl.zstd` → `"4"`）。"""
    m = _DECLARED_RE.search(Path(path).name)
    return m.group(1) if m else None


def _dig(obj: dict, dotted: str):
    """按 `a.b.c` 取值；中途不是 dict 就返回 None。"""
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def shape_problems(lines: list[dict]) -> list[str]:
    """核对消费字段形态；返回问题清单（**空 = 与 v4 形态一致**）。

    只看"出现了该类型的行、但其必需字段缺失/类型不对"。数据稀疏（某些类型
    本来就没出现）不算问题——那是会话内容差异，不是 schema 差异。
    """
    problems: list[str] = []
    for o in lines:
        if not isinstance(o, dict):
            continue
        t = o.get("type")
        want = REQUIRED_SHAPE.get(t) if isinstance(t, str) else None
        if not want:
            continue
        for path in want:
            v = _dig(o, path)
            if v is None:
                problems.append(f"{t} 缺字段 {path}（形态可能已改版）")
                break
    # 去重但保序，避免同一问题刷屏
    seen: set[str] = set()
    return [p for p in problems if not (p in seen or seen.add(p))]


def schema_fingerprint(lines: list[dict]) -> str:
    """观察指纹：`{类型: 顶层键排序}` 的 sha256 前 12 位。

    **它是溯源记录，不是判断依据**——同一 schema 的稀疏会话与饱满会话
    指纹不同（键集随数据变）。判断交给 `shape_problems` 与声明版本；
    指纹的用处是对账："上一轮探测看到的形态"与"这一轮"能不能对上。
    """
    shape: dict[str, list[str]] = {}
    for o in lines:
        if not isinstance(o, dict):
            continue
        t = o.get("type")
        if not isinstance(t, str):
            continue
        keys = sorted(o.keys())
        if t in shape:
            shape[t] = sorted(set(shape[t]) | set(keys))
        else:
            shape[t] = keys
    canon = json.dumps(shape, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]

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
        # v0.45：**不再把 `v4` 写死在 glob 里**。原实现 `session.v4.jsonl.zstd`
        # 会在上游改名（v5 / 去掉版本号）时让整个源变成 MISSING（"未找到会话"），
        # 把人引向"是不是没装"；而真实结论应该是 **schema 不匹配**。
        # 宽 glob + 守卫判定，才能给出正确的诊断（版本号仍由 detected 报出）。
        return sorted(self.root.glob("*/*/session*.jsonl.zstd"))

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

    def _schema_check(self, f: Path, lines: list[dict]
                      ) -> tuple[bool, str, str | None, str]:
        """守卫：返回 (ok, why, 声明版本, 观察指纹)。

        两道判据：① 文件名声明版本 ∈ KNOWN_SCHEMA_VERSIONS；
        ② `shape_problems` 为空。任一不过 → ok=False 并给出可读原因。
        """
        ver = declared_version(f)
        fp = schema_fingerprint(lines) if lines else ""
        if ver is None:
            return False, f"文件名未声明 schema 版本（{f.name}）", ver, fp
        if ver not in KNOWN_SCHEMA_VERSIONS:
            return (False, f"上游声明 schema v{ver}，本实现只支持 "
                           f"v{'/'.join(sorted(KNOWN_SCHEMA_VERSIONS))}", ver, fp)
        problems = shape_problems(lines)
        if problems:
            return False, "；".join(problems[:3]), ver, fp
        return True, f"schema v{ver} 形态匹配（指纹 {fp}）", ver, fp

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
        # schema 守卫：拿**一个非空样本**判定（首个能解出行的文件）
        sample: list[dict] = []
        for f in files:
            sample = self._lines(f)
            if sample:
                break
        ok, why, ver, fp = self._schema_check(files[0], sample)
        if not ok:
            return DetectReport(
                self.id, self.name, "STUB",
                f"{len(files)} 个会话，但 **schema 守卫未通过**：{why}",
                session_count=len(files),
                hints=["上游 transcript 形态可能已改版：先核对 "
                       "harvester/adapters/dsh.py 的 REQUIRED_SHAPE 与模块头"
                       "的数据源说明，再改解析代码（不许凭猜测解析）"],
                schema_version=ver, schema_fingerprint=fp or None,
                schema_ok=False)
        return DetectReport(
            self.id, self.name, "OK",
            f"{len(files)} 个会话（多 frame zstd，已具备解压能力）；{why}",
            session_count=len(files),
            schema_version=ver, schema_fingerprint=fp or None, schema_ok=True)

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
            # v0.45：纲要条目带 schema 守卫结论（additive）——清单照列（时间/标题
            # 仍然可信），但下游能看到"这条的 schema 没过守卫"，而不是到写库时才发现。
            ok, _why, ver, _fp = self._schema_check(f, lines)
            items.append({
                "session_id": rel,
                "title": (("[subagent] " if is_sub else "")
                          + (title or label or f"DSH 会话 {f.parent.name[:8]}")),
                "created_at": to_local_ts(created),
                "updated_at": to_local_ts(updated),
                "message_count": n_msgs,
                "preview": (title or label or "")[:100],
                "workspace": rel.split("/")[0],
                "schema_ok": ok,
                "schema_version": ver,
            })
        items.sort(key=lambda x: x["updated_at"] or "", reverse=True)
        return items

    def load_session(self, session_id: str) -> SessionRecord:
        rel = session_id
        f = self.root / Path(*rel.split("/"))
        if not f.is_file():
            raise KeyError(f"DSH 会话不存在: {f}")
        lines = self._lines(f)
        # v0.45 schema 守卫（照 autoclaw 先例）：**不匹配就不解析**——宁可交付
        # 一条明确标 lossy、messages 为空的记录（调用方与报告都能看到原因），
        # 也不产出一条"看起来正常"的半成品（旧行为的失败方式正是它）。
        ok, why, ver, fp = self._schema_check(f, lines)
        if not ok:
            return SessionRecord(
                source=self.id, session_id=rel,
                title=f"DSH 会话 {f.parent.name[:8]}",
                created_at=None, updated_at=None,
                messages=[],
                extra={"file": str(f), "workspace": rel.split("/")[0],
                       "origin": "dsh-session-v4",
                       "dsh_meta": {}, "model": None, "models": None,
                       "lossy": True,
                       "warnings": [f"schema 守卫未通过，未解析消息（{why}）"],
                       "dsh_schema": {"ok": False, "declared": ver,
                                      "fingerprint": fp or None,
                                      "expected": sorted(KNOWN_SCHEMA_VERSIONS)},
                       })
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
                   "lossy": bool(warns), "warnings": warns,
                   "dsh_schema": {"ok": True, "declared": ver,
                                  "fingerprint": fp or None}},
        )
