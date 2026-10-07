# -*- coding: utf-8 -*-
"""Yuanbao detail harvest driver.

Reads corpus/yuanbao_raw/harvest_manifest.json, fetches each conversation
detail via in-page eval (agent-browser), POSTs payload to local receiver
which writes detail_<id>.json. Resumable: skips existing files.
"""
import json
import os
import subprocess
import time
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "corpus" / "yuanbao_raw"
BATCH = 10
DELAY_MS = 1200
NODE = r"C:\Users\yianyao\.workbuddy\binaries\node\versions\24.14.0\node.exe"
AB_JS = (r"C:\Users\yianyao\.workbuddy\binaries\node\versions\24.14.0"
         r"\node_modules\agent-browser\bin\agent-browser.js")

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
      if (t.length > 50) {
        await fetch('http://127.0.0.1:8766/detail/' + id, {
          mode: 'no-cors', method: 'POST',
          headers: { 'Content-Type': 'text/plain' }, body: t,
        });
      }
      if (r.ok) { ok++; } else { err.push(id + ':' + r.status); }
    } catch (e) { err.push(id + ':EXC:' + e.message); }
    await new Promise((s) => setTimeout(s, %d));
  }
  return 'ok=' + ok + ' err=' + JSON.stringify(err);
})()"""


def run_batch(ids: list[str]) -> str:
    js = JS_TMPL % (json.dumps(ids), DELAY_MS)
    env = dict(os.environ, AGENT_BROWSER_SESSION="yuanbao_harvest")
    p = subprocess.run([NODE, AB_JS, "eval", js], capture_output=True,
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
