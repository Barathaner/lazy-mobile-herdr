#!/usr/bin/env python3
"""herdr-web: phone-first web UI for a local herdr, stdlib only.

Wraps the herdr CLI (no shell, argv lists only) and reads Claude transcripts
from ~/.claude/projects/*/<session-id>.jsonl for per-pane history.
"""
import argparse
import hashlib
import hmac
import http.server
import ipaddress
import json
import os
import re
import secrets
import socket
import socketserver
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit, parse_qs

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
CONFIG_DIR = os.path.expanduser("~/.config/herdr-web")
TOKEN_FILE = os.path.join(CONFIG_DIR, "token")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

PANE_RE = re.compile(r"^w[0-9]{1,6}:p[0-9]{1,6}$")
TAB_RE = re.compile(r"^w[0-9]{1,6}:t[0-9]{1,6}$")
WS_RE = re.compile(r"^w[0-9]{1,6}$")
SESSION_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# UI key name -> herdr send-keys name. Anything else is rejected.
KEYS = {
    "enter": "enter", "esc": "esc", "ctrl+c": "ctrl+c", "tab": "tab",
    "up": "up", "down": "down", "left": "left", "right": "right",
}
MAX_BODY = 16 * 1024
MAX_TEXT = 4000
COOKIE = "hw_session"
SESSION_TTL = 30 * 24 * 3600
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW = 300
GLOBAL_MAX_FAILS = 40

CFG = {"herdr": "herdr", "allow_input": False, "allow_nets": [], "hosts": set(), "port": 8780}


# ---------------------------------------------------------------- token / auth
def load_or_create_token():
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    try:
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        os.chmod(TOKEN_FILE, 0o600)
        with open(TOKEN_FILE) as f:
            return f.read().strip(), False
    tok = secrets.token_urlsafe(48)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    return tok, True


class Auth:
    def __init__(self, token):
        self.token = token.encode()
        self.sessions = {}
        self.fails = {}
        self.global_fails = []
        self.lock = threading.Lock()

    def check_token(self, candidate):
        return hmac.compare_digest(candidate.encode("utf-8", "replace"), self.token)

    def locked_out(self, ip):
        now = time.time()
        with self.lock:
            self.global_fails = [t for t in self.global_fails if now - t < LOGIN_WINDOW]
            fl = [t for t in self.fails.get(ip, []) if now - t < LOGIN_WINDOW]
            self.fails[ip] = fl
            if len(fl) >= LOGIN_MAX_FAILS:
                return int(LOGIN_WINDOW - (now - fl[0])) + 1
            if len(self.global_fails) >= GLOBAL_MAX_FAILS:
                return int(LOGIN_WINDOW - (now - self.global_fails[0])) + 1
        return 0

    def record_fail(self, ip):
        now = time.time()
        with self.lock:
            self.fails.setdefault(ip, []).append(now)
            self.global_fails.append(now)

    def new_session(self):
        sid = secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            self.sessions = {k: v for k, v in self.sessions.items() if v > now}
            self.sessions[sid] = now + SESSION_TTL
        return sid

    def valid_session(self, sid):
        if not sid:
            return False
        with self.lock:
            exp = None
            for k, v in self.sessions.items():  # constant-time-ish scan
                if hmac.compare_digest(k.encode(), sid.encode("utf-8", "replace")):
                    exp = v
            return exp is not None and exp > time.time()

    def drop(self, sid):
        with self.lock:
            self.sessions.pop(sid, None)


AUTH = None


# ---------------------------------------------------------------- herdr CLI
class HerdrError(Exception):
    def __init__(self, msg, status=502):
        super().__init__(msg)
        self.status = status


def herdr(*args, raw=False, timeout=6):
    cmd = [CFG["herdr"], *args]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, shell=False, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise HerdrError("herdr binary not found")
    except subprocess.TimeoutExpired:
        raise HerdrError("herdr timed out")
    out = p.stdout[:4_000_000].decode("utf-8", "replace")
    if p.returncode != 0:
        msg = (p.stderr.decode("utf-8", "replace") or out)[:300].strip()
        raise HerdrError(msg or "herdr failed", 404 if "not found" in msg.lower() else 502)
    if raw:
        return out
    try:
        d = json.loads(out)
    except ValueError:
        raise HerdrError("unexpected herdr output")
    if isinstance(d, dict) and d.get("error"):
        e = d["error"]
        msg = e.get("message") if isinstance(e, dict) else str(e)
        status = 404 if isinstance(e, dict) and "not_found" in str(e.get("code")) else 502
        raise HerdrError(str(msg)[:300], status)
    return d.get("result", d) if isinstance(d, dict) else d


def tree():
    ws = herdr("workspace", "list").get("workspaces", [])
    tabs = herdr("tab", "list").get("tabs", [])
    panes = herdr("pane", "list").get("panes", [])
    out = []
    for w in ws:
        wt = []
        for t in tabs:
            if t.get("workspace_id") != w["workspace_id"]:
                continue
            tp = [pane_view(p) for p in panes if p.get("tab_id") == t["tab_id"]]
            wt.append({**t, "panes": tp})
        out.append({**w, "tabs": wt})
    return {"workspaces": out}


def pane_view(p):
    sess = p.get("agent_session") or {}
    sid = sess.get("value") if sess.get("kind") == "id" else None
    return {
        "pane_id": p.get("pane_id"), "tab_id": p.get("tab_id"), "workspace_id": p.get("workspace_id"),
        "agent": p.get("agent"), "agent_status": p.get("agent_status"),
        "title": p.get("terminal_title_stripped") or p.get("terminal_title") or "",
        "cwd": p.get("foreground_cwd") or p.get("cwd") or "", "focused": bool(p.get("focused")),
        "revision": p.get("revision"),
        "session_id": sid if isinstance(sid, str) and SESSION_RE.match(sid) else None,
        "rows": (p.get("scroll") or {}).get("viewport_rows"),
    }


def get_pane(pid):
    return pane_view(herdr("pane", "get", pid).get("pane", {}))


def screen(pid, source, lines):
    args = ["pane", "read", pid, "--source", source, "--ansi"]
    if source != "visible":
        args += ["--lines", str(lines)]
    return herdr(*args, raw=True)


def focus_pane(pid):
    """herdr's CLI only focuses directionally, so walk from the focused pane."""
    p = get_pane(pid)
    if not p["workspace_id"] or not p["tab_id"]:
        raise HerdrError("pane has no tab", 404)
    herdr("workspace", "focus", p["workspace_id"])
    herdr("tab", "focus", p["tab_id"])
    for _ in range(8):
        lay = herdr("pane", "layout", "--pane", pid).get("layout", {})
        cur = lay.get("focused_pane_id")
        if cur == pid:
            return {"focused": pid}
        rects = {x["pane_id"]: x["rect"] for x in lay.get("panes", [])}
        if cur not in rects or pid not in rects:
            break
        c, t = rects[cur], rects[pid]
        dx = (t["x"] + t["width"] / 2) - (c["x"] + c["width"] / 2)
        dy = (t["y"] + t["height"] / 2) - (c["y"] + c["height"] / 2)
        if abs(dx) * c["height"] >= abs(dy) * c["width"]:
            d = "right" if dx > 0 else "left"
        else:
            d = "down" if dy > 0 else "up"
        herdr("pane", "focus", "--pane", cur, "--direction", d)
    lay = herdr("pane", "layout", "--pane", pid).get("layout", {})
    return {"focused": lay.get("focused_pane_id"), "requested": pid}


# ---------------------------------------------------------------- history
_hist = {}
_hist_lock = threading.Lock()


def find_session_file(sid):
    if not SESSION_RE.match(sid):
        return None
    root = os.path.realpath(PROJECTS_DIR)
    try:
        dirs = os.listdir(root)
    except OSError:
        return None
    for d in dirs:
        cand = os.path.realpath(os.path.join(root, d, sid + ".jsonl"))
        if os.path.dirname(os.path.dirname(cand)) == root and os.path.isfile(cand):
            return cand
    return None


def _trunc(s, n):
    return s if len(s) <= n else s[:n] + "\n… [%d more chars]" % (len(s) - n)


def entries_from(d):
    t = d.get("type")
    if t not in ("user", "assistant"):
        return []
    ts = d.get("timestamp")
    msg = d.get("message") or {}
    c = msg.get("content")
    side = bool(d.get("isSidechain"))
    out = []

    def add(kind, text, **kw):
        out.append({"kind": kind, "ts": ts, "text": text, "side": side, **kw})

    if isinstance(c, str):
        if t == "user":
            k = "system" if c.lstrip().startswith("<") else "prompt"
            add(k, _trunc(c, 8000))
        else:
            add("reply", _trunc(c, 20000))
        return out
    for b in c or []:
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt == "text":
            txt = b.get("text") or ""
            if t == "user":
                add("system" if txt.lstrip().startswith("<") else "prompt", _trunc(txt, 8000))
            else:
                add("reply", _trunc(txt, 20000))
        elif bt == "tool_use":
            inp = json.dumps(b.get("input"), ensure_ascii=False)
            add("tool_use", _trunc(inp, 1500), tool=b.get("name"))
        elif bt == "tool_result":
            rc = b.get("content")
            if isinstance(rc, list):
                rc = "\n".join(x.get("text", "") for x in rc if isinstance(x, dict))
            add("tool_result", _trunc(str(rc or ""), 3000), error=bool(b.get("is_error")))
    return out


def history_entries(sid):
    path = find_session_file(sid)
    if not path:
        return None
    with _hist_lock:
        st = os.stat(path)
        ent = _hist.get(sid)
        if ent and (ent["path"] != path or st.st_size < ent["off"]):
            ent = None
        if not ent:
            ent = {"path": path, "off": 0, "entries": [], "used": 0}
            if len(_hist) >= 8:
                _hist.pop(min(_hist, key=lambda k: _hist[k]["used"]))
            _hist[sid] = ent
        ent["used"] = time.time()
        if st.st_size > ent["off"]:
            with open(path, "rb") as f:
                f.seek(ent["off"])
                data = f.read()
            nl = data.rfind(b"\n")
            if nl >= 0:
                for line in data[: nl + 1].splitlines():
                    try:
                        ent["entries"].extend(entries_from(json.loads(line)))
                    except ValueError:
                        pass
                ent["off"] += nl + 1
                for i, e in enumerate(ent["entries"]):
                    e["i"] = i
        return ent["entries"]


# ---------------------------------------------------------------- HTTP
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
PUBLIC_STATIC = {"login.js", "app.css"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "herdr-web"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (self.client_address[0], fmt % a))

    # --- helpers
    def send(self, status, body=b"", ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def err(self, status, msg, extra=None):
        self.send(status, {"error": msg}, extra=extra)

    def cookie_sid(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return None

    def authed(self):
        return AUTH.valid_session(self.cookie_sid())

    def client_ok(self):
        try:
            ip = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return any(ip in n for n in CFG["allow_nets"])

    def host_ok(self):
        host = self.headers.get("Host", "")
        h = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        if h in CFG["hosts"]:
            return True
        try:
            ipaddress.ip_address(h.strip("[]"))
            return True  # IP literal: not a DNS-rebinding vector
        except ValueError:
            return False

    def origin_ok(self, required):
        o = self.headers.get("Origin")
        if o is None:
            return not required
        try:
            return urlsplit(o).netloc == self.headers.get("Host")
        except ValueError:
            return False

    def body_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("bad length")
        if n > MAX_BODY:
            raise ValueError("body too large")
        raw = self.rfile.read(n) if n else b"{}"
        d = json.loads(raw or b"{}")
        if not isinstance(d, dict):
            raise ValueError("json object required")
        return d

    # --- dispatch
    def gate(self, post):
        if not self.client_ok():
            self.err(403, "client address not allowed")
            return False
        if not self.host_ok():
            self.err(403, "bad host")
            return False
        if not self.origin_ok(required=post):
            self.err(403, "bad origin")
            return False
        return True

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.gate(False):
            return
        u = urlsplit(self.path)
        path, q = u.path, parse_qs(u.query)
        try:
            if path == "/login":
                return self.static("login.html")
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                if name not in PUBLIC_STATIC and not self.authed():
                    return self.err(401, "auth required")
                return self.static(name)
            if not self.authed():
                if path == "/":
                    return self.send(302, b"", extra={"Location": "/login"})
                return self.err(401, "auth required")
            if path == "/":
                return self.static("index.html")
            return self.api_get(path, q)
        except HerdrError as e:
            self.err(e.status, str(e))
        except Exception as e:  # noqa: BLE001
            sys.stderr.write("error: %r\n" % (e,))
            self.err(500, "internal error")

    def do_POST(self):
        if not self.gate(True):
            return
        path = urlsplit(self.path).path
        try:
            if path == "/api/login":
                return self.login()
            if not self.authed():
                return self.err(401, "auth required")
            if path == "/api/logout":
                AUTH.drop(self.cookie_sid())
                return self.send(200, {"ok": True}, extra={"Set-Cookie": self.cookie("", 0)})
            m = re.match(r"^/api/pane/([^/]+)/(focus|text|keys)$", path)
            if not m:
                return self.err(404, "not found")
            pid, act = m.groups()
            if not PANE_RE.match(pid):
                return self.err(400, "bad pane id")
            try:
                body = self.body_json()
            except ValueError as e:
                return self.err(400, str(e))
            if act == "focus":
                return self.send(200, focus_pane(pid))
            if not CFG["allow_input"]:
                return self.err(403, "input disabled (start with --allow-input)")
            get_pane(pid)  # must exist
            if act == "text":
                text = body.get("text")
                if not isinstance(text, str) or not text or len(text) > MAX_TEXT or "\x00" in text:
                    return self.err(400, "bad text")
                herdr("pane", "send-text", pid, "--", text)
                if body.get("enter"):
                    herdr("pane", "send-keys", pid, "enter")
                return self.send(200, {"ok": True})
            keys = body.get("keys")
            if (not isinstance(keys, list) or not 1 <= len(keys) <= 8
                    or any(not isinstance(k, str) or k not in KEYS for k in keys)):
                return self.err(400, "bad keys")
            herdr("pane", "send-keys", pid, *[KEYS[k] for k in keys])
            return self.send(200, {"ok": True})
        except HerdrError as e:
            self.err(e.status, str(e))
        except Exception as e:  # noqa: BLE001
            sys.stderr.write("error: %r\n" % (e,))
            self.err(500, "internal error")

    def cookie(self, val, age):
        return "%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d" % (COOKIE, val, age)

    def login(self):
        ip = self.client_address[0]
        wait = AUTH.locked_out(ip)
        if wait:
            return self.err(429, "too many attempts", extra={"Retry-After": str(wait)})
        try:
            tok = self.body_json().get("token")
        except ValueError as e:
            return self.err(400, str(e))
        if not isinstance(tok, str) or len(tok) > 512 or not AUTH.check_token(tok.strip()):
            AUTH.record_fail(ip)
            time.sleep(0.4)
            return self.err(401, "invalid token")
        sid = AUTH.new_session()
        self.send(200, {"ok": True}, extra={"Set-Cookie": self.cookie(sid, SESSION_TTL)})

    def static(self, name):
        if not re.match(r"^[A-Za-z0-9_.-]+$", name) or name.startswith("."):
            return self.err(404, "not found")
        fp = os.path.realpath(os.path.join(STATIC_DIR, name))
        if os.path.dirname(fp) != os.path.realpath(STATIC_DIR) or not os.path.isfile(fp):
            return self.err(404, "not found")
        with open(fp, "rb") as f:
            self.send(200, f.read(), MIME.get(os.path.splitext(fp)[1], "application/octet-stream"))

    def api_get(self, path, q):
        def qs(k, default=""):
            return (q.get(k) or [default])[0]

        if path == "/api/config":
            return self.send(200, {"allow_input": CFG["allow_input"]})
        if path == "/api/tree":
            return self.send(200, tree())
        m = re.match(r"^/api/pane/([^/]+)(?:/(screen|history|layout))?$", path)
        if not m:
            return self.err(404, "not found")
        pid, what = m.groups()
        if not PANE_RE.match(pid):
            return self.err(400, "bad pane id")
        if what is None:
            return self.send(200, get_pane(pid))
        if what == "layout":
            return self.send(200, herdr("pane", "layout", "--pane", pid).get("layout", {}))
        if what == "screen":
            source = qs("source", "visible")
            if source not in ("visible", "recent"):
                return self.err(400, "bad source")
            try:
                lines = max(1, min(int(qs("lines", "300")), 2000))
            except ValueError:
                return self.err(400, "bad lines")
            txt = screen(pid, source, lines)
            h = hashlib.sha1(txt.encode()).hexdigest()[:16]
            if qs("etag") == h:
                return self.send(200, {"unchanged": True, "etag": h})
            return self.send(200, {"ansi": txt, "etag": h})
        # history
        sid = get_pane(pid)["session_id"]
        if not sid:
            return self.send(200, {"entries": [], "total": 0, "has_more": False, "session_id": None})
        ents = history_entries(sid)
        if ents is None:
            return self.err(404, "transcript not found")
        needle = qs("q").strip().lower()
        try:
            limit = max(1, min(int(qs("limit", "40")), 200))
            before = int(qs("before", str(len(ents))))
            after = int(qs("after", "-1"))
        except ValueError:
            return self.err(400, "bad paging")
        pool = ents
        if needle:
            pool = [e for e in ents if needle in (e["text"] + " " + (e.get("tool") or "")).lower()]
        if after >= 0:
            out = [e for e in pool if e["i"] > after][:200]
            return self.send(200, {"entries": out, "total": len(ents), "session_id": sid})
        cand = [e for e in pool if e["i"] < before]
        page = cand[-limit:]
        self.send(200, {"entries": page, "total": len(ents), "session_id": sid,
                        "has_more": len(cand) > len(page)})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    global AUTH
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--allow-input", action="store_true", help="enable send-text/send-keys endpoints")
    ap.add_argument("--herdr-bin", default="herdr")
    ap.add_argument("--allow-cidr", action="append", default=[],
                    help="extra client network allowed (default: loopback + private ranges)")
    a = ap.parse_args()
    CFG.update(herdr=a.herdr_bin, allow_input=a.allow_input, port=a.port)
    nets = ["127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fe80::/10", "fc00::/7"]
    CFG["allow_nets"] = [ipaddress.ip_network(n) for n in nets + a.allow_cidr]
    CFG["hosts"] = {"localhost", socket.gethostname(), socket.gethostname() + ".local"}
    tok, created = load_or_create_token()
    AUTH = Auth(tok)
    if created:
        print("herdr-web access token (shown once, stored 0600 at %s):\n\n  %s\n" % (TOKEN_FILE, tok))
    else:
        print("Using existing token from %s" % TOKEN_FILE)
    print("input: %s" % ("ENABLED" if a.allow_input else "disabled (use --allow-input)"))
    print("listening on http://%s:%d/" % (a.host, a.port), flush=True)
    try:
        Server((a.host, a.port), Handler).serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
