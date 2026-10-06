# -*- coding: utf-8 -*-
"""Doubao harvest via app-driven capture (verify/ only).

不重放请求（被 msToken/a_bogus 签名拦截），而是让页面自己发请求、
CDP Network 域拦截响应体：
- recent_conv (cmd 3200) → 会话列表 → corpus/doubao_raw/list_0.json
- 对每个会话导航 /chat/<id>，截获 chain/single (cmd 3100) 响应，
  多次向上滚动触发更早消息，按 message_id 去重合并 → detail_<id>.json

并发模型：监听线程是唯一的 ws 读线程（独占 recv），负责
1) 分发 CDP 事件（responseReceived / loadingFinished）；
2) 对完成的目标请求自动补发 Network.getResponseBody 并解析响应体；
3) 回填主线程 rpc() 的应答。
主线程只通过 rpc() 发命令 + 等事件，绝不直接 recv。
send 方向有锁（主线程与监听线程都会发）。
"""
import base64
import json
import threading
import time
from pathlib import Path

import requests
import websocket

PORT = 9334
RAW = Path(__file__).resolve().parent.parent / "corpus" / "doubao_raw"
RAW.mkdir(parents=True, exist_ok=True)


class DoubaoHarvester:
    def __init__(self):
        tabs = [t for t in requests.get(
                    f"http://127.0.0.1:{PORT}/json", timeout=5,
                    proxies={"http": None, "https": None}).json()
                if t.get("type") == "page"]
        self.tab = tabs[0]
        self.ws = websocket.create_connection(self.tab["webSocketDebuggerUrl"],
                                              timeout=30)
        self.mid = 0
        self._send_lock = threading.Lock()
        self._rpc = {}           # gid -> {"event": Event, "msg": dict|None}
        self._pending = {}       # requestId -> kind（responseReceived 记账）
        self._body_waiters = {}  # gid -> kind（getResponseBody 应答记账）
        self.conv_cells = []     # recent_conv 解析出的会话对象
        self.conv_event = threading.Event()
        self.chain_messages = {}  # conv_id -> {message_id: msg}
        self._listen_started = False

    # ---- 底层 ----
    def _send(self, method: str, params: dict | None = None) -> int:
        with self._send_lock:
            self.mid += 1
            self.ws.send(json.dumps({"id": self.mid, "method": method,
                                     "params": params or {}}))
            return self.mid

    def rpc(self, method: str, params: dict | None = None,
            timeout: float = 15.0) -> dict:
        """发命令并等应答（应答由监听线程回填）。注册必须先于发送。"""
        with self._send_lock:
            self.mid += 1
            gid = self.mid
            entry = {"event": threading.Event(), "msg": None}
            self._rpc[gid] = entry
            self.ws.send(json.dumps({"id": gid, "method": method,
                                     "params": params or {}}))
        if not entry["event"].wait(timeout):
            self._rpc.pop(gid, None)
            raise TimeoutError(method)
        self._rpc.pop(gid, None)
        msg = entry["msg"]
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result") or {}

    def eval_js(self, js: str, timeout: float = 15.0):
        return self.rpc("Runtime.evaluate",
                        {"expression": js, "returnByValue": True,
                         "awaitPromise": True}, timeout)

    def nav(self, url: str, wait_s: float = 10.0) -> None:
        try:
            self.rpc("Page.navigate", {"url": url}, timeout=15)
        except Exception:
            pass
        time.sleep(wait_s)

    # ---- 监听线程 ----
    def _start_listener(self) -> None:
        if self._listen_started:
            return
        self._listen_started = True

        def listen():
            while True:
                try:
                    msg = json.loads(self.ws.recv())
                except websocket.WebSocketTimeoutException:
                    continue  # 空闲超时不退出，连接还在
                except Exception:
                    return
                gid = msg.get("id")
                # 1) rpc 应答
                if gid and gid in self._rpc:
                    self._rpc[gid]["msg"] = msg
                    self._rpc[gid]["event"].set()
                    continue
                # 2) getResponseBody 应答
                if gid and gid in self._body_waiters:
                    kind = self._body_waiters.pop(gid)
                    r = msg.get("result") or {}
                    body = r.get("body") or ""
                    if r.get("base64Encoded"):
                        body = base64.b64decode(body).decode("utf-8", "replace")
                    self._handle_body(kind, body)
                    continue
                # 3) 事件分发
                m = msg.get("method", "")
                p = msg.get("params") or {}
                if m == "Network.responseReceived":
                    url = p.get("response", {}).get("url", "")
                    if "recent_conv" in url:
                        self._pending[p.get("requestId")] = "recent_conv"
                    elif "chain/single" in url:
                        self._pending[p.get("requestId")] = "chain_single"
                elif m == "Network.loadingFinished":
                    rid = p.get("requestId")
                    kind = self._pending.pop(rid, None)
                    if kind:
                        g = self._send("Network.getResponseBody",
                                       {"requestId": rid})
                        self._body_waiters[g] = kind

        threading.Thread(target=listen, daemon=True).start()
        # 监听线程就位后再启用域，否则应答无人读取
        self.rpc("Network.enable", {"maxPostDataSize": 1 << 20})
        self.rpc("Page.enable")

    # ---- 业务解析 ----
    def _handle_body(self, kind: str, body: str) -> None:
        try:
            j = json.loads(body)
        except Exception:
            return
        if kind == "recent_conv":
            dl = (j.get("downlink_body") or {}).get(
                "pull_recent_conv_chain_downlink_body") or {}
            for c in dl.get("cells") or []:
                conv = c.get("conversation") or {}
                if conv.get("conversation_id"):
                    self.conv_cells.append(conv)
            self.conv_event.set()
        elif kind == "chain_single":
            dl = (j.get("downlink_body") or {}).get(
                "pull_singe_chain_downlink_body") or {}
            for m in dl.get("messages") or []:
                cid = m.get("conversation_id")
                if cid:
                    bucket = self.chain_messages.setdefault(cid, {})
                    bucket[m.get("message_id")] = m


def main() -> None:
    h = DoubaoHarvester()
    h._start_listener()
    # 1. 首页截会话列表
    h.nav("https://www.doubao.com/chat/", wait_s=12)
    if not h.conv_event.wait(10):
        print("WARN: 未截到 recent_conv 响应")
    convs = {c["conversation_id"]: c for c in h.conv_cells}
    print(f"列表会话数: {len(convs)}")
    (RAW / "list_0.json").write_text(
        json.dumps(list(convs.values()), ensure_ascii=False, indent=1),
        encoding="utf-8", newline="\n")
    # 2. 逐会话采集
    for i, (cid, conv) in enumerate(convs.items(), 1):
        h.chain_messages.setdefault(cid, {})
        h.nav(f"https://www.doubao.com/chat/{cid}", wait_s=10)
        # 向上滚动触发更早消息（3 轮）
        for _ in range(3):
            try:
                h.eval_js(
                    "(document.scrollingElement||document.documentElement)"
                    ".scrollTop=0")
            except Exception:
                pass
            time.sleep(2)
        time.sleep(2)  # 给最后一个 body 回包留时间
        msgs = h.chain_messages.get(cid, {})
        out = {"conversation_id": cid, "name": conv.get("name"),
               "create_time": conv.get("create_time"),
               "update_time": conv.get("update_time"),
               "n_messages": len(msgs), "messages": list(msgs.values())}
        (RAW / f"detail_{cid}.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1),
            encoding="utf-8", newline="\n")
        print(f"[{i}/{len(convs)}] {cid[:14]} «{conv.get('name', '')}» "
              f"消息 {len(msgs)}")
    print("DONE")


if __name__ == "__main__":
    main()
