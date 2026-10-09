#!/usr/bin/env python3
"""Stop hook: forward a Claude session's last reply to Telegram (outbound only).

Inbound stays with the single --channels session, so messages never run twice.
Skips the session that already owns the Telegram channel (it uses the reply tool).
"""
import json, os, sys, urllib.request, urllib.parse

STATE = os.path.expanduser("~/.claude/channels/telegram")


def is_leader():
    # walk up parents; leader = claude started with --channels plugin:telegram
    pid = os.getppid()
    for _ in range(6):
        try:
            cmd = open(f"/proc/{pid}/cmdline").read()
            if "--channels" in cmd and "telegram" in cmd:
                return True
            pid = int(open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[1])
        except Exception:
            return False
    return False


def main():
    if is_leader():
        return
    hook = json.load(sys.stdin)
    text = hook.get("last_assistant_message")
    if not text:
        text = ""
        for line in open(hook["transcript_path"]):
            try:
                m = json.loads(line).get("message", {})
            except Exception:
                continue
            if m.get("role") == "assistant" and isinstance(m.get("content"), list):
                t = "\n".join(b.get("text", "") for b in m["content"] if b.get("type") == "text").strip()
                if t:
                    text = t
    if not text.strip():
        return
    env = dict(l.strip().split("=", 1) for l in open(f"{STATE}/.env") if "=" in l)
    token = env["TELEGRAM_BOT_TOKEN"]
    chat = json.load(open(f"{STATE}/access.json"))["allowFrom"][0]
    pane = os.environ.get("HERDR_PANE_ID", "?")
    label = f"[{pane} {os.path.basename(hook.get('cwd', '') or '') or '~'}]"
    body = f"{label}\n{text}"[:4000]
    data = urllib.parse.urlencode({"chat_id": chat, "text": body}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=15)


try:
    main()
except Exception as e:
    print(f"tg-forward: {e}", file=sys.stderr)
