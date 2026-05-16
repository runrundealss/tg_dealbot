"""
notify.py — Telegram admin alert with hourly throttle.
Sends CRITICAL events to admin_chat_id from config.
"""
import json, os, time, urllib.request, urllib.parse
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE_DIR, "state.json")
LOG_PATH   = os.path.join(BASE_DIR, "dealbot.log")

def _log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] [notify] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a") as f: f.write(line + "\n")
    except Exception: pass

def _load_state():
    try: return json.load(open(STATE_PATH))
    except Exception: return {}

def _save_state(s):
    try:
        with open(STATE_PATH, "w") as f: json.dump(s, f, indent=2)
    except Exception as e: _log(f"save state err: {e}")

def _log_tail(n=12):
    """Return last N log lines for inclusion in alerts."""
    try:
        with open(LOG_PATH) as f:
            return "".join(f.readlines()[-n:]).rstrip()
    except Exception: return ""

def critical(token, chat_id, message, throttle_per_hour=5, include_log=True):
    """Send a CRITICAL alert. Throttled per hour. Includes log tail by default."""
    if not chat_id:
        _log("no admin_chat_id — alarm dropped")
        return False
    state = _load_state()
    alerts = state.setdefault("alerts", [])
    cutoff = (datetime.now() - timedelta(hours=1)).isoformat()
    alerts = [a for a in alerts if a > cutoff]
    if len(alerts) >= throttle_per_hour:
        _log(f"throttled — {len(alerts)} alerts already this hour")
        return False
    text = f"🚨 RunRunDeals Bot\n{message}"
    if include_log:
        tail = _log_tail(12)
        if tail:
            text += f"\n\n📜 Son log:\n```\n{tail[-1500:]}\n```"
    try:
        data = urllib.parse.urlencode({
            "chat_id": chat_id, "text": text, "parse_mode": "Markdown"
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
        if resp.get("ok"):
            alerts.append(datetime.now().isoformat())
            state["alerts"] = alerts
            _save_state(state)
            _log(f"SENT alarm: {message[:80]}")
            return True
        else:
            _log(f"telegram returned ok=False: {resp}")
    except Exception as e:
        _log(f"send err: {e}")
    return False


def send_full_log(token, chat_id, lines=80):
    """User-triggered: send last N log lines as Telegram message (no throttle)."""
    if not chat_id: return False
    tail = _log_tail(lines)
    if not tail:
        tail = "(log boş)"
    text = f"📜 RunRunDeals Log (son {lines} satır):\n```\n{tail[-3500:]}\n```"
    try:
        data = urllib.parse.urlencode({
            "chat_id": chat_id, "text": text, "parse_mode": "Markdown"
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:
        _log(f"send_full_log err: {e}")
        return False
