# -*- coding: utf-8 -*-
"""Qianwen detail harvest driver (CDP 版，verify/ only)。

Reads corpus/qianwen_raw/list_*.json manifests, fetches each conversation's
msg/list via in-page fetch (CDP Runtime.evaluate), POSTs payload to local
receiver (port 8767, outdir qianwen_raw). Resumable: skips existing files.

msg/list: GET /api/v1/session/msg/list?page_size=100&session_id=...
分页: have_next_page=true 时以 pos=<最后一条的 pos> 续拉（实测参数）。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp_driver import Cdp  # noqa: E402

RAW = Path(__file__).resolve().parent.parent / "corpus" / "qianwen_raw"
BATCH = 10
DELAY_MS = 1000
PAGE_SIZE = 100

UT = "3d0317b3-22a4-fa85-07ec-da4f6376353d"
BASE_Q = (f"?biz_id=ai_qwen&chat_client=h5&device=pc&fr=pc&pr=qwen"
          f"&ut={UT}&la=zh-CN")


def fetch_session_js(sid: str) -> str:
    """JS: 拉一个会话全部消息页并合并，POST 到接收器。返回结果摘要。"""
    return f"""(async () => {{
  const sid = {json.dumps(sid)};
  const pages = [];
  let pos = null, guard = 0;
  do {{
    const q = `https://chat2-api.qianwen.com/api/v1/session/msg/list{BASE_Q}` +
      `&session_id=${{sid}}&page_size={PAGE_SIZE}` + (pos ? `&pos=${{pos}}` : '');
    const r = await fetch(q, {{credentials:'include'}});
    const j = await r.json();
    if (j.code !== 0) return JSON.stringify({{sid, err: j.msg || ('code ' + j.code)}});
    const d = j.data || {{}};
    pages.push(...(d.list || []));
    if (d.have_next_page && (d.list || []).length) {{
      pos = d.list[d.list.length - 1].pos;
    }} else pos = null;
    guard++;
  }} while (pos && guard < 30);
  const merged = JSON.stringify({{session_id: sid, rounds: pages.length, list: pages}});
  await fetch('http://127.0.0.1:8767/detail/' + sid, {{mode:'no-cors',
    method:'POST', headers:{{'Content-Type':'text/plain'}}, body: merged}});
  await new Promise(s => setTimeout(s, {DELAY_MS}));
  return JSON.stringify({{sid, rounds: pages.length}});
}})()"""


def main() -> None:
    ids: list[str] = []
    for p in sorted(RAW.glob("list_*.json"),
                    key=lambda x: int(x.stem.split("_")[1])):
        d = json.loads(p.read_text(encoding="utf-8"))
        for s in d.get("list", []):
            if s.get("session_id"):
                ids.append(s["session_id"])
    ids = list(dict.fromkeys(ids))
    todo = [i for i in ids if not (RAW / f"detail_{i}.json").exists()]
    print(f"total={len(ids)} todo={len(todo)}", flush=True)

    c = Cdp(9333)
    failed: list[str] = []
    t0 = time.time()
    for k, sid in enumerate(todo, 1):
        try:
            out = c.eval_js(fetch_session_js(sid), timeout=120)
            info = json.loads(out)
            if "err" in info:
                failed.append(sid)
                print(f"[{k:>4}/{len(todo)}] ERR {sid} {info['err']}", flush=True)
            else:
                print(f"[{k:>4}/{len(todo)}] ok rounds={info['rounds']} "
                      f"{sid[:12]} ({time.time()-t0:.0f}s)", flush=True)
        except Exception as e:  # noqa: BLE001
            failed.append(sid)
            print(f"[{k:>4}/{len(todo)}] EXC {sid} {e}", flush=True)
            time.sleep(3)
            c = Cdp(9333)   # 重连
        time.sleep(0.3)
    if failed:
        (RAW / "detail_failed.json").write_text(
            json.dumps(failed, ensure_ascii=False), encoding="utf-8", newline="\n")
    print("DONE failed=", len(failed), flush=True)


if __name__ == "__main__":
    main()
