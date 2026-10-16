# -*- coding: utf-8 -*-
"""Yuanbao detail harvest driver.

Reads corpus/yuanbao_raw/harvest_manifest.json, fetches each conversation
detail via in-page eval (agent-browser), POSTs payload to local receiver
which writes detail_<id>.json. Resumable: skips existing files.
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "corpus" / "yuanbao_raw"
BATCH = 10
DELAY_MS = 1200


def _ver_key(d: Path):
    return [int(x) for x in d.name.split(".") if x.isdigit()]


def _find_node() -> str:
    """node.exe 查找顺序（v0.75 可迁移化，不再硬编码用户名/版本）：
    env HARVESTER_NODE_EXE → PATH（shutil.which）→ WorkBuddy 自带
    ~/.workbuddy/binaries/node/versions/<最高版本>/node.exe。"""
    exe = os.environ.get("HARVESTER_NODE_EXE")
    if exe and Path(exe).is_file():
        return exe
    found = shutil.which("node")
    if found:
        return found
    root = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    if root.is_dir():
        for d in sorted((x for x in root.iterdir() if x.is_dir()),
                        key=_ver_key, reverse=True):
            cand = d / "node.exe"
            if cand.is_file():
                return str(cand)
    raise FileNotFoundError(
        "找不到 node.exe：设 HARVESTER_NODE_EXE、把 node 加入 PATH，"
        "或确认 WorkBuddy 自带 node 在位")


def _find_agent_browser_js(node_exe: str) -> str:
    """agent-browser.js：优先 node 同目录，其次 WorkBuddy versions 倒序。"""
    cand = (Path(node_exe).resolve().parent
            / "node_modules" / "agent-browser" / "bin" / "agent-browser.js")
    if cand.is_file():
        return str(cand)
    root = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    if root.is_dir():
        for d in sorted((x for x in root.iterdir() if x.is_dir()),
                        key=_ver_key, reverse=True):
            cand = d / "node_modules" / "agent-browser" / "bin" / "agent-browser.js"
            if cand.is_file():
                return str(cand)
    raise FileNotFoundError(
        "找不到 agent-browser.js（node 同目录或 ~/.workbuddy/.../versions 下）")

JS_TMPL = """(async () => {
  const ids = %s;
  let ok = 0; const err = [];
  for (const id of ids) {
    try {
      const r = await fetch('/api/user/agent/conversation/v1/detail', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ conversationId: id }),
      });
      const t = await r.text();
      // v0.75：先验 r.ok 再验 body 形态——错误页/HTML 也可能 >50 字符，
      // 原判定会把登录页固化成 detail_<id>.json（静默数据损坏）。
      if (r.ok && t.length > 50 && t.trimStart().startsWith('{')) {
        await fetch('http://127.0.0.1:8766/detail/' + id, {
          mode: 'no-cors', method: 'POST',
          headers: { 'Content-Type': 'text/plain' }, body: t,
        });
        ok++;
      } else {
        err.push(id + ':' + (r.ok ? 'BADBODY:' + t.slice(0, 40) : r.status));
      }
    } catch (e) { err.push(id + ':EXC:' + e.message); }
    await new Promise((s) => setTimeout(s, %d));
  }
  return 'ok=' + ok + ' err=' + JSON.stringify(err);
})()"""


def run_batch(ids: list[str]) -> str:
    js = JS_TMPL % (json.dumps(ids), DELAY_MS)
    env = dict(os.environ, AGENT_BROWSER_SESSION="yuanbao_harvest")
    node = _find_node()
    ab = _find_agent_browser_js(node)
    p = subprocess.run([node, ab, "eval", js], capture_output=True,
                       text=True, timeout=300, env=env)
    return (p.stdout or "").strip() + (p.stderr or "").strip()


def main() -> None:
    ids = json.loads((RAW / "harvest_manifest.json").read_text(encoding="utf-8"))
    todo = [i for i in ids if not (RAW / f"detail_{i}.json").exists()]
    print(f"total={len(ids)} todo={len(todo)}", flush=True)
    failed: list[str] = []
    t0 = time.time()
    for k in range(0, len(todo), BATCH):
        chunk = todo[k:k + BATCH]
        try:
            out = run_batch(chunk)
        except subprocess.TimeoutExpired:
            out = "TIMEOUT"
            failed.extend(chunk)
        print(f"[{k + len(chunk):>4}/{len(todo)}] {out} "
              f"({time.time() - t0:.0f}s)", flush=True)
        time.sleep(1)
    if failed:
        (RAW / "detail_failed.json").write_text(
            json.dumps(failed, ensure_ascii=False), encoding="utf-8", newline="\n")
    print("DONE failed=", len(failed), flush=True)


if __name__ == "__main__":
    main()
