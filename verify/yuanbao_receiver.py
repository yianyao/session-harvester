# -*- coding: utf-8 -*-
"""Yuanbao harvest receiver v2.  ⚠️ 永久保留，勿删（元宝 1223 会话采集管线组件）。

Page-context eval POSTs (mode:'no-cors', Content-Type text/plain):
- POST /list/<n>     -> list_page_<n>.json
- POST /detail/<cid> -> detail_<cid>.json

Only path + body are used (no custom headers, they are dropped in no-cors).

v2.1: 输出目录可指定（千问/豆包复用同一协议）：
    python yuanbao_receiver.py [port] [outdir_name]
  默认 outdir_name=yuanbao_raw（向后兼容）。
"""
import http.server
import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent / "corpus"


def make_out(name: str) -> Path:
    out = _BASE / name
    out.mkdir(parents=True, exist_ok=True)
    return out


class Handler(http.server.BaseHTTPRequestHandler):
    out: Path = make_out("yuanbao_raw")   # main() 会覆盖

    def do_POST(self):  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            parts = [p for p in self.path.split("/") if p]
            if len(parts) == 2 and parts[0] in ("list", "detail"):
                name = "".join(ch for ch in parts[1] if ch.isalnum() or ch in "-_")
                if not name:
                    raise ValueError("empty name")
                (self.out / f"{parts[0]}_{name}.json").write_bytes(body)
            else:
                raise ValueError(f"bad path {self.path}")
            self.send_response(200)
            self.end_headers()
        except Exception as e:  # pragma: no cover
            sys.stderr.write(f"receiver error: {e}\n")
            self.send_response(400)
            self.end_headers()

    def log_message(self, fmt, *args):  # silence default logging
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    out = make_out(sys.argv[2]) if len(sys.argv) > 2 else make_out("yuanbao_raw")
    Handler.out = out
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"receiver v2.1 on {port} -> {out}", flush=True)
    srv.serve_forever()
