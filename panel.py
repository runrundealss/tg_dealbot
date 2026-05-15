"""RunRunDeals Bot — Dashboard Panel"""
import json, os, subprocess, signal, time, re
from datetime import datetime, timedelta
from collections import Counter
import streamlit as st
import pandas as pd

BASE      = "/Users/kaan/tg_dealbot"
STATE     = f"{BASE}/state.json"
PRODUCTS  = f"{BASE}/products.json"
LOG       = f"{BASE}/dealbot.log"
PID_FILE  = f"{BASE}/dealbot.pid"
DAEMON_PY = f"{BASE}/dealbot.py"

st.set_page_config(page_title="RunRunDeals Bot", page_icon="🛒", layout="wide")

# ---------- helpers ----------

def is_running():
    if not os.path.exists(PID_FILE): return None
    try:
        pid = int(open(PID_FILE).read().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        try: os.remove(PID_FILE)
        except: pass
        return None

def start_daemon():
    if is_running(): return "Daemon zaten çalışıyor"
    p = subprocess.Popen(
        ["/usr/bin/caffeinate", "-i", "python3", DAEMON_PY],
        stdout=open(LOG, "a"), stderr=subprocess.STDOUT,
        cwd=BASE, start_new_session=True,
    )
    with open(PID_FILE, "w") as f: f.write(str(p.pid))
    return f"Başlatıldı PID={p.pid}"

def stop_daemon():
    pid = is_running()
    if not pid: return "Zaten kapalı"
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        try: os.kill(pid, signal.SIGTERM)
        except: pass
    try: os.remove(PID_FILE)
    except: pass
    return f"Durduruldu PID={pid}"

def force_refresh():
    subprocess.Popen(["python3", DAEMON_PY, "--refresh", "--once", "--dry-run"], cwd=BASE)
    return "Strapi refresh tetiklendi"

def load_state():
    if not os.path.exists(STATE): return {"posted": [], "failed": {}, "hashes": {}, "last_refresh": 0}
    return json.load(open(STATE))

def load_queue():
    if not os.path.exists(PRODUCTS): return []
    return json.load(open(PRODUCTS))

def read_log_tail(n=80):
    if not os.path.exists(LOG): return ""
    with open(LOG) as f:
        lines = f.readlines()
    return "".join(lines[-n:])

# ---------- top header ----------

state = load_state()
queue = load_queue()
pid   = is_running()

c1, c2, c3, c4, c5 = st.columns([2,1,1,1,1])
c1.title("🛒 RunRunDeals Bot")

# Status pill
if pid:
    c2.success(f"🟢 ÇALIŞIYOR\nPID {pid}")
else:
    c2.error("🔴 DURMUŞ")

# Buttons
if c3.button("▶️ Başlat", use_container_width=True, type="primary", disabled=bool(pid)):
    st.toast(start_daemon()); time.sleep(0.5); st.rerun()
if c4.button("⏹️ Durdur", use_container_width=True, disabled=not bool(pid)):
    st.toast(stop_daemon()); time.sleep(0.5); st.rerun()
if c5.button("🔄 Strapi Refresh", use_container_width=True):
    st.toast(force_refresh()); time.sleep(0.5); st.rerun()

st.divider()

# ---------- stats row ----------

posted_all = state.get("posted", [])
def parse_dt(x):
    try: return datetime.fromisoformat(x)
    except: return None
now = datetime.now()
today_count   = sum(1 for e in posted_all if (d := parse_dt(e.get("posted_at"))) and d.date() == now.date())
week_count    = sum(1 for e in posted_all if (d := parse_dt(e.get("posted_at"))) and (now - d) < timedelta(days=7))
total_count   = len(posted_all)
failed_count  = sum(1 for f in state.get("failed", {}).values() if f.get("attempts", 0) >= 3)
queue_count   = len(queue)
last_post     = max([d for e in posted_all if (d := parse_dt(e.get("posted_at")))] or [None])
last_refresh  = state.get("last_refresh", 0)
last_refresh_dt = datetime.fromtimestamp(last_refresh) if last_refresh else None

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Bugün", f"{today_count} post")
m2.metric("Son 7 gün", f"{week_count} post")
m3.metric("Toplam", f"{total_count}")
m4.metric("Kuyrukta", f"{queue_count}")
m5.metric("Başarısız (3+)", f"{failed_count}", delta_color="inverse")
next_post_in = "-"
if pid and last_post:
    next_at = last_post + timedelta(minutes=10)
    secs = (next_at - now).total_seconds()
    if secs > 0:
        next_post_in = f"{int(secs//60)}m {int(secs%60)}s"
    else:
        next_post_in = "şimdi"
m6.metric("Sonraki post", next_post_in)

# Hourly chart (last 24h)
if posted_all:
    df = pd.DataFrame([{"hour": parse_dt(e.get("posted_at")).replace(minute=0, second=0, microsecond=0)}
                       for e in posted_all if parse_dt(e.get("posted_at"))])
    if not df.empty:
        cutoff = now - timedelta(hours=24)
        df = df[df["hour"] >= cutoff]
        if not df.empty:
            counts = df.groupby("hour").size().reset_index(name="posts")
            st.bar_chart(counts.set_index("hour"), height=160)

st.divider()

# ---------- queue + log ----------

col_q, col_l = st.columns([1, 1])

with col_q:
    st.subheader("📋 Sonraki Kuyruk")
    if queue:
        next_5 = []
        for p in queue[:30]:
            # skip if already posted (use state)
            pid_ = p.get("id")
            asin = p.get("asin")
            posted_ids = {e.get("id") for e in posted_all}
            if pid_ and pid_ in posted_ids: continue
            next_5.append({
                "Ürün": (p.get("title") or "")[:60],
                "İndirim": f"%{p.get('disc')}",
                "Was→Now": f"{p.get('reg'):.2f} → {p.get('sale'):.2f}",
                "Kod": p.get("code") or "—",
            })
            if len(next_5) >= 10: break
        if next_5:
            st.dataframe(pd.DataFrame(next_5), height=380, use_container_width=True, hide_index=True)
        else:
            st.info("Kuyrukta postlanmaya uygun ürün kalmamış")
    else:
        st.info("Kuyruk boş — Strapi'den çekilmeyi bekliyor")

    st.caption(f"Son Strapi refresh: {last_refresh_dt.strftime('%H:%M:%S') if last_refresh_dt else 'henüz yok'}")

with col_l:
    st.subheader("📜 Canlı Log (son 80 satır)")
    tail = read_log_tail(80)
    # color-code
    pretty = []
    for line in tail.splitlines():
        if "SENT" in line:        pretty.append(f"🟢 {line}")
        elif "FAIL" in line:      pretty.append(f"🔴 {line}")
        elif "SKIP" in line:      pretty.append(f"⚪ {line}")
        elif "refresh" in line:   pretty.append(f"🔄 {line}")
        elif "DAEMON" in line:    pretty.append(f"⚡ {line}")
        else:                     pretty.append(f"   {line}")
    st.code("\n".join(pretty[-80:]), language=None, height=380)

st.divider()

# ---------- recent posts ----------

st.subheader("📰 Son Postlar")
if posted_all:
    recent = sorted(posted_all, key=lambda e: e.get("posted_at",""), reverse=True)[:15]
    df = pd.DataFrame([{
        "Saat": (parse_dt(e.get("posted_at")).strftime("%d.%m %H:%M") if parse_dt(e.get("posted_at")) else "?"),
        "ID": (e.get("id") or "")[:24],
        "ASIN": e.get("asin") or "—",
        "Başlık": (e.get("title_key") or "")[:55],
        "msg_id": e.get("msg_id") or "",
    } for e in recent])
    st.dataframe(df, hide_index=True, use_container_width=True, height=420)
else:
    st.info("Henüz post yok")

# ---------- failed posts ----------

if state.get("failed"):
    with st.expander(f"⚠️ Başarısız ürünler ({len(state['failed'])})"):
        fdata = []
        for pid_, info in state["failed"].items():
            fdata.append({
                "ID": pid_[:24],
                "Denemeler": info.get("attempts"),
                "Aşama": info.get("stage"),
                "Son deneme": info.get("last_try"),
                "Hata": (info.get("error") or "")[:80],
            })
        st.dataframe(pd.DataFrame(fdata), hide_index=True, use_container_width=True)

# Auto-refresh every 10s
st.caption(f"⏱️ Dashboard 10 saniyede bir tazelenir. Şu an: {datetime.now().strftime('%H:%M:%S')}")
time.sleep(10)
st.rerun()
