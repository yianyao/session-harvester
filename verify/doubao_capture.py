# -*- coding: utf-8 -*-
"""Doubao network capture via CDP Network domain (verify/ only).

Captures all /im/|/alice/ requests (including Web Worker traffic) while
a human/agent drives the UI. Dumps URL + method + POST data + response
content-type for later replay.
"""
import base64
import json
import sys
import threading
import time

import requests
import websocket

PORT = 9334
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0


def main() -> None:
    tabs = [t for t in requests.get(f"http://127.0.0.1:{PORT}/json", timeout=5,
                                    proxies={"http": None}).json()
            if t.get("type") == "page"]
    tab = tabs[0]
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)
    ws.send(json.dumps({"id": 1, "method": "Network.enable",
                        "params": {"maxPostDataSize": 1 << 20}}))
    events: list[dict] = []
    req_ids: dict[str, dict] = {}
    mid = [1]

    def listen() -> None:
        while True:
            try:
                msg = json.loads(ws.recv())
            except Exception:
                return
            m = msg.get("method", "")
            p = msg.get("params", {})
            if m == "Network.requestWillBeSent":
                r = p.get("request", {})
                url = r.get("url", "")
                if "/im/" in url or "/alice/" in url:
                    req_ids[p["requestId"]] = {"url": url, "method": r.get("method"),
                                               "postData": r.get("postData", ""),
                                               "headers": r.get("headers") or {}}
                    events.append(req_ids[p["requestId"]])
            elif m == "Network.responseReceived":
                rid = p.get("requestId")
                if rid in req_ids:
                    resp = p.get("response", {})
                    req_ids[rid]["status"] = resp.get("status")
                    req_ids[rid]["resp_ct"] = (resp.get("mimeType") or "")

    th = threading.Thread(target=listen, daemon=True)
    th.start()
    print(f"capturing {DURATION}s... interact with the doubao window now")
    time.sleep(DURATION)
    # 收响应体与缺失的 postData
    for rid, e in req_ids.items():
        mid[0] += 1
        try:
            ws.send(json.dumps({"id": mid[0], "method": "Network.getResponseBody",
                                "params": {"requestId": rid}}))
        except Exception:
            continue
        # 同步收一个匹配响应（简化：循环读到对应 id）
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                ws.settimeout(max(0.2, deadline - time.time()))
                msg = json.loads(ws.recv())
            except Exception:
                break
            if msg.get("id") == mid[0]:
                r = msg.get("result", {})
                body = (r.get("body") or "")
                if r.get("base64Encoded"):
                    body = base64.b64decode(body).decode("utf-8", "replace")
                e["resp_body"] = body[:300000]
                break
        mid[0] += 1
        try:
            ws.send(json.dumps({"id": mid[0], "method": "Network.getRequestPostData",
                                "params": {"requestId": rid}}))
            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    ws.settimeout(max(0.2, deadline - time.time()))
                    msg = json.loads(ws.recv())
                except Exception:
                    break
                if msg.get("id") == mid[0]:
                    e["postData"] = (msg.get("result", {}).get("postData")
                                     or e.get("postData") or "")
                    break
        except Exception:
            pass
    print(json.dumps(events[:20], ensure_ascii=False)[:2000])
    with open("doubao_capture.json", "w", encoding="utf-8") as f:
        json.dump(list(req_ids.values()), f, ensure_ascii=False, indent=1)
    print(f"saved {len(req_ids)} events -> doubao_capture.json")


if __name__ == "__main__":
    main()
