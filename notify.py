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

def critical(token, chat_id, message, throttle_per_hour=5):
    """Send a CRITICAL alert. Throttled per hour."""
    if not chat_id:
        _log("no admin_chat_id — alarm dropped")
        return False
    # Throttle: count alerts in last hour
    state = _load_state()
    alerts = state.setdefault("alerts", [])
    cutoff = (datetime.now() - timedelta(hours=1)).isoformat()
    alerts = [a for a in alerts if a > cutoff]
    if len(alerts) >= throttle_per_hour:
        _log(f"throttled — {len(alerts)} alerts already this hour")
        return False
    text = f"🚨 RunRunDeals Bot\n{message}"
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
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
    except Exception as e:
        _log(f"send err: {e}")
    return False
