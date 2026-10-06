# -*- coding: utf-8 -*-
"""Minimal CDP driver (verify/ only, not part of harvester package).

Drives a chrome started with --remote-debugging-port via websocket-client:
- nav(url)      navigate current tab
- eval_js(js)   Runtime.evaluate, returns JS value (async IIFE supported)
"""
import json
import time

import requests
import websocket


class Cdp:
    def __init__(self, port: int = 9333):
        self.port = port
        self._ws = None
        self._mid = 0

    def _tabs(self) -> list[dict]:
        # 显式绕过系统代理：curl/requests 可能被 HTTP_PROXY 拦截 127.0.0.1
        return requests.get(f"http://127.0.0.1:{self.port}/json", timeout=5,
                            proxies={"http": None, "https": None}).json()

    def _connect(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        pages = [t for t in self._tabs() if t.get("type") == "page"]
        if not pages:
            raise RuntimeError("no page tab")
        self.tab = pages[0]
        self._ws = websocket.create_connection(self.tab["webSocketDebuggerUrl"],
                                               timeout=30)

    def _cmd(self, method: str, params: dict | None = None, timeout: int = 60):
        if self._ws is None:
            self._connect()
        self._mid += 1
        mid = self._mid
        self._ws.send(json.dumps({"id": mid, "method": method,
                                  "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self._ws.settimeout(max(1, deadline - time.time()))
                msg = json.loads(self._ws.recv())
            except websocket.WebSocketTimeoutException:
                break
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(f"{method} timed out")

    def nav(self, url: str, wait_s: float = 8.0) -> None:
        self._cmd("Page.enable")
        try:
            self._cmd("Page.navigate", {"url": url}, timeout=20)
        except RuntimeError:
            # navigation may abort the execution context; that's fine
            pass
        time.sleep(wait_s)
        # reconnect after context swap
        self._connect()

    def eval_js(self, js: str, timeout: int = 90, await_promise: bool = True):
        res = self._cmd("Runtime.enable")
        res = self._cmd("Runtime.evaluate", {
            "expression": js,
            "returnByValue": True,
            "awaitPromise": await_promise,
            "timeout": timeout * 1000,
        }, timeout=timeout + 10)
        r = res.get("result", {})
        if r.get("subtype") == "error" or res.get("exceptionDetails"):
            raise RuntimeError(json.dumps(res.get("exceptionDetails",
                                                   r), ensure_ascii=False)[:500])
        return r.get("value")


if __name__ == "__main__":
    import sys
    c = Cdp(int(sys.argv[1]) if len(sys.argv) > 1 else 9333)
    print("tabs:", [t.get("url") for t in c._tabs()][:3])
