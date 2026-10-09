#!/usr/bin/env python3
"""Headless-Chrome mobile screenshots of herdr-web (stdlib only, minimal CDP client).

usage: cdp_shots.py BASE_URL TOKEN_FILE OUT_DIR PANE_ID
"""
import base64, json, os, socket, struct, subprocess, sys, time, urllib.request

base, token_file, out, pane = sys.argv[1:5]
os.makedirs(out, exist_ok=True)
token = open(token_file).read().strip()
req = urllib.request.Request(base + "/api/login", data=json.dumps({"token": token}).encode(),
                             headers={"Origin": base, "Content-Type": "application/json"})
cookie = urllib.request.urlopen(req).headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
port = 9333
proc = subprocess.Popen(["google-chrome", "--headless=new", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                         "--no-sandbox", "--remote-debugging-port=%d" % port, "--user-data-dir=" + os.path.join(out, "profile"),
                         "--window-size=390,844", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(50):
        try:
            tabs = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % port))
            ws_url = [t for t in tabs if t["type"] == "page"][0]["webSocketDebuggerUrl"]
            break
        except Exception:
            time.sleep(0.2)
    host, path = ws_url.split("://")[1].split("/", 1)
    s = socket.create_connection(host.split(":") and (host.split(":")[0], int(host.split(":")[1])))
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, host, key)).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    buf = buf.split(b"\r\n\r\n", 1)[1]

    def recv_exact(n):
        global buf
        while len(buf) < n:
            buf += s.recv(65536)
        r, buf = buf[:n], buf[n:]
        return r

    def read_msg():
        data = b""
        while True:
            b1, b2 = recv_exact(2)
            ln = b2 & 0x7F
            if ln == 126: ln = struct.unpack(">H", recv_exact(2))[0]
            elif ln == 127: ln = struct.unpack(">Q", recv_exact(8))[0]
            data += recv_exact(ln)
            if b1 & 0x80: return data

    def send(obj):
        p = json.dumps(obj).encode(); m = os.urandom(4)
        h = bytes([0x81])
        if len(p) < 126: h += bytes([0x80 | len(p)])
        elif len(p) < 65536: h += bytes([0x80 | 126]) + struct.pack(">H", len(p))
        else: h += bytes([0x80 | 127]) + struct.pack(">Q", len(p))
        s.sendall(h + m + bytes(b ^ m[i % 4] for i, b in enumerate(p)))

    mid = [0]
    def call(method, **params):
        mid[0] += 1; send({"id": mid[0], "method": method, "params": params})
        while True:
            r = json.loads(read_msg())
            if r.get("id") == mid[0]:
                return r.get("result", r)

    def js(expr):
        return call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True).get("result", {}).get("value")

    def shot(name):
        r = call("Page.captureScreenshot", format="png")
        open(os.path.join(out, name + ".png"), "wb").write(base64.b64decode(r["data"]))
        print("shot", name)

    host_only = base.split("://")[1].split(":")[0]
    call("Network.enable")
    call("Network.setCookie", name="hw_session", value=cookie, domain=host_only, path="/", httpOnly=True, sameSite="Strict")
    call("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
    call("Page.enable")
    call("Page.navigate", url=base + "/"); time.sleep(2.5); shot("1-home")
    print("title:", js("document.getElementById('title').textContent"),
          "| panes:", js("document.querySelectorAll('.pane').length"),
          "| hscroll:", js("document.documentElement.scrollWidth > innerWidth"))
    call("Page.navigate", url=base + "/#/pane/" + pane); time.sleep(2.5); shot("2-screen")
    js("document.querySelectorAll('.seg button')[1].click()"); time.sleep(1.5); shot("3-history")
    print("history msgs:", js("document.querySelectorAll('.msg').length"))
    js("var i=document.querySelector('input[type=search]'); i.value='the'; i.dispatchEvent(new Event('input'))"); time.sleep(1.5); shot("4-search")
    print("search marks:", js("document.querySelectorAll('.msg mark').length"))
    js("document.getElementById('theme').click()"); time.sleep(0.5); shot("5-theme-toggled")
    print("input toggle text:", js("document.querySelector('.inputbar button').textContent"),
          "| input field present:", js("!!document.querySelector('.inputbar input')"))
    if len(sys.argv) > 5 and sys.argv[5] == "input":
        js("document.querySelector('.inputbar button').click()"); time.sleep(0.5); shot("6-input-on")
        print("keys:", js("document.querySelectorAll('.keys button').length"))
finally:
    proc.terminate()
