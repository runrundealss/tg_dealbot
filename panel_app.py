#!/usr/bin/env python3
"""RunRunDeals Bot — Native macOS dashboard (Tkinter)."""
import json, os, subprocess, signal, time, sys, threading, webbrowser
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

BASE      = os.path.expanduser("~/tg_dealbot")
STATE     = f"{BASE}/state.json"
PRODUCTS  = f"{BASE}/products.json"
LOG       = f"{BASE}/dealbot.log"
PID_FILE  = f"{BASE}/dealbot.pid"
DAEMON_PY = f"{BASE}/dealbot.py"

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
    if is_running(): return False, "Zaten çalışıyor"
    log_f = open(LOG, "a")
    p = subprocess.Popen(
        ["/usr/bin/caffeinate", "-i", sys.executable, DAEMON_PY],
        stdout=log_f, stderr=subprocess.STDOUT,
        cwd=BASE, start_new_session=True,
    )
    with open(PID_FILE, "w") as f: f.write(str(p.pid))
    return True, f"Başlatıldı (PID {p.pid})"

def stop_daemon():
    pid = is_running()
    if not pid: return False, "Zaten kapalı"
    try: os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        try: os.kill(pid, signal.SIGTERM)
        except: pass
    try: os.remove(PID_FILE)
    except: pass
    return True, f"Durduruldu (PID {pid})"

def force_refresh():
    subprocess.Popen([sys.executable, DAEMON_PY, "--refresh", "--once", "--dry-run"], cwd=BASE)
    return True, "Strapi refresh tetiklendi"

def load_state():
    if not os.path.exists(STATE):
        return {"posted": [], "failed": {}, "hashes": {}, "last_refresh": 0}
    try:
        return json.load(open(STATE))
    except: return {"posted": [], "failed": {}, "hashes": {}, "last_refresh": 0}

def load_queue():
    if not os.path.exists(PRODUCTS): return []
    try: return json.load(open(PRODUCTS))
    except: return []

def parse_dt(s):
    try: return datetime.fromisoformat(s)
    except: return None

# ---------- main app ----------

class BotDashboard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("🛒 RunRunDeals Bot")
        self.geometry("1080x780")
        self.minsize(900, 600)
        self.configure(bg="#f5f5f7")

        self.style = ttk.Style(self)
        try: self.style.theme_use("aqua")    # native macOS
        except: pass

        self._build_header()
        self._build_metrics()
        self._build_tabs()
        self._build_statusbar()

        self.refresh()                       # initial
        self.after(5000, self._auto_refresh) # then every 5s

    # ---- HEADER ----
    def _build_header(self):
        bar = tk.Frame(self, bg="#f5f5f7", padx=16, pady=12)
        bar.pack(fill="x", side="top")

        tk.Label(bar, text="🛒 RunRunDeals Bot",
                 font=("SF Pro Display", 22, "bold"),
                 bg="#f5f5f7").pack(side="left")

        self.status_label = tk.Label(bar, text="🔴 Durmuş",
                                     font=("SF Pro Display", 12, "bold"),
                                     bg="#f5f5f7", fg="#d32f2f", padx=18)
        self.status_label.pack(side="left", padx=12)

        btn_frame = tk.Frame(bar, bg="#f5f5f7")
        btn_frame.pack(side="right")

        self.btn_start = ttk.Button(btn_frame, text="▶ Başlat", command=self.on_start)
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop  = ttk.Button(btn_frame, text="■ Durdur", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4)
        self.btn_refresh = ttk.Button(btn_frame, text="🔄 Strapi Refresh", command=self.on_refresh)
        self.btn_refresh.pack(side="left", padx=4)
        self.btn_chan = ttk.Button(btn_frame, text="🔗 Kanalı Aç",
                                   command=lambda: webbrowser.open("https://t.me/RunRunDeals"))
        self.btn_chan.pack(side="left", padx=4)

    # ---- METRICS ----
    def _build_metrics(self):
        wrap = tk.Frame(self, bg="#f5f5f7", padx=16)
        wrap.pack(fill="x")
        self.metric_widgets = {}
        cards = [("Bugün","today"), ("Son 7 gün","week"), ("Toplam","total"),
                 ("Kuyruk","queue"), ("Hatalı","failed"), ("Sonraki post","next")]
        for i, (label, key) in enumerate(cards):
            f = tk.Frame(wrap, bg="white", bd=0, padx=14, pady=10,
                         highlightbackground="#dddddd", highlightthickness=1)
            f.grid(row=0, column=i, padx=4, sticky="ew")
            wrap.grid_columnconfigure(i, weight=1)
            tk.Label(f, text=label, font=("SF Pro Display", 11),
                     bg="white", fg="#666").pack(anchor="w")
            v = tk.Label(f, text="—", font=("SF Pro Display", 22, "bold"),
                         bg="white", fg="#111")
            v.pack(anchor="w")
            self.metric_widgets[key] = v

    # ---- TABS ----
    def _build_tabs(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=16, pady=12)

        # Queue tab
        f_q = tk.Frame(nb, bg="white")
        nb.add(f_q, text="📋 Kuyruk")
        cols = ("disc","title","prices","code")
        self.tree_q = ttk.Treeview(f_q, columns=cols, show="headings", height=18)
        for cid, txt, w in [("disc","%",60),("title","Ürün",460),
                            ("prices","Was → Now",160),("code","Kod",140)]:
            self.tree_q.heading(cid, text=txt)
            self.tree_q.column(cid, width=w, anchor="w")
        self.tree_q.pack(fill="both", expand=True, padx=10, pady=10)

        # Log tab
        f_l = tk.Frame(nb, bg="white")
        nb.add(f_l, text="📜 Log")
        self.log_text = scrolledtext.ScrolledText(
            f_l, font=("Menlo", 11), bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white", wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=10, pady=10)
        for tag, color in [("SENT","#7ec07e"),("FAIL","#f48771"),
                            ("SKIP","#999"),("DAEMON","#dcdcaa"),
                            ("refresh","#4fc1ff")]:
            self.log_text.tag_configure(tag, foreground=color)

        # Recent posts
        f_r = tk.Frame(nb, bg="white")
        nb.add(f_r, text="📰 Son Postlar")
        rcols = ("time","id","asin","title","msg")
        self.tree_r = ttk.Treeview(f_r, columns=rcols, show="headings", height=18)
        for cid, txt, w in [("time","Saat",110),("id","Strapi ID",220),
                             ("asin","ASIN",100),("title","Başlık",420),("msg","msg_id",80)]:
            self.tree_r.heading(cid, text=txt)
            self.tree_r.column(cid, width=w, anchor="w")
        self.tree_r.pack(fill="both", expand=True, padx=10, pady=10)

        # Failed
        f_f = tk.Frame(nb, bg="white")
        nb.add(f_f, text="⚠️ Hatalı")
        fcols = ("id","attempts","stage","last","err")
        self.tree_f = ttk.Treeview(f_f, columns=fcols, show="headings", height=18)
        for cid, txt, w in [("id","ID",220),("attempts","Deneme",70),
                             ("stage","Aşama",140),("last","Son Deneme",150),("err","Hata",400)]:
            self.tree_f.heading(cid, text=txt)
            self.tree_f.column(cid, width=w, anchor="w")
        self.tree_f.pack(fill="both", expand=True, padx=10, pady=10)

    # ---- STATUS BAR ----
    def _build_statusbar(self):
        self.sb = tk.Label(self, text="", anchor="w", bg="#ecedf0",
                           padx=10, pady=4, font=("SF Pro Display", 11))
        self.sb.pack(fill="x", side="bottom")

    # ---- ACTIONS ----
    def on_start(self):
        ok, msg = start_daemon()
        self._toast(msg, success=ok)
        self.refresh()
    def on_stop(self):
        ok, msg = stop_daemon()
        self._toast(msg, success=ok)
        self.refresh()
    def on_refresh(self):
        ok, msg = force_refresh()
        self._toast(msg, success=ok)

    def _toast(self, msg, success=True):
        self.sb.config(text=("✅ " if success else "⚠️ ") + msg)

    # ---- DATA REFRESH ----
    def _auto_refresh(self):
        try: self.refresh()
        except Exception as e:
            self.sb.config(text=f"refresh err: {e}")
        self.after(5000, self._auto_refresh)

    def refresh(self):
        state = load_state()
        queue = load_queue()
        pid   = is_running()
        posted_all = state.get("posted", [])
        now = datetime.now()

        # Header status
        if pid:
            self.status_label.config(text=f"🟢 ÇALIŞIYOR  PID {pid}", fg="#2e7d32")
            self.btn_start.config(state="disabled"); self.btn_stop.config(state="normal")
        else:
            self.status_label.config(text="🔴 DURMUŞ", fg="#d32f2f")
            self.btn_start.config(state="normal"); self.btn_stop.config(state="disabled")

        # Metrics
        today = sum(1 for e in posted_all if (d := parse_dt(e.get("posted_at"))) and d.date() == now.date())
        week  = sum(1 for e in posted_all if (d := parse_dt(e.get("posted_at"))) and (now - d) < timedelta(days=7))
        total = len(posted_all)
        failed_n = sum(1 for f in state.get("failed", {}).values() if f.get("attempts", 0) >= 3)
        last_post = max([d for e in posted_all if (d := parse_dt(e.get("posted_at")))] or [None])

        # filter queue: not in posted ids
        posted_ids = {e.get("id") for e in posted_all}
        queue_visible = [p for p in queue if p.get("id") not in posted_ids]
        qcount = len(queue_visible)

        next_post = "—"
        if pid and last_post:
            next_at = last_post + timedelta(minutes=10)
            secs = (next_at - now).total_seconds()
            next_post = f"{int(secs//60)}m {int(secs%60)}s" if secs > 0 else "şimdi"

        for k, v in [("today",today),("week",week),("total",total),
                     ("queue",qcount),("failed",failed_n),("next",next_post)]:
            self.metric_widgets[k].config(text=str(v))

        # Queue tree
        self.tree_q.delete(*self.tree_q.get_children())
        for p in queue_visible[:50]:
            try:
                self.tree_q.insert("", "end", values=(
                    f"%{p.get('disc')}",
                    (p.get("title") or "")[:65],
                    f"{p.get('reg'):.2f} → {p.get('sale'):.2f}",
                    p.get("code") or "—",
                ))
            except Exception: pass

        # Log
        try:
            tail = open(LOG).read().splitlines()[-150:]
        except Exception:
            tail = []
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        for line in tail:
            tag = None
            for key in ("SENT","FAIL","SKIP","DAEMON","refresh"):
                if key in line: tag = key; break
            self.log_text.insert("end", line + "\n", tag)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

        # Recent posts
        self.tree_r.delete(*self.tree_r.get_children())
        for e in sorted(posted_all, key=lambda x: x.get("posted_at",""), reverse=True)[:50]:
            d = parse_dt(e.get("posted_at"))
            self.tree_r.insert("", "end", values=(
                d.strftime("%d.%m %H:%M") if d else "?",
                (e.get("id") or "")[:24],
                e.get("asin") or "—",
                (e.get("title_key") or "")[:60],
                e.get("msg_id") or "",
            ))

        # Failed
        self.tree_f.delete(*self.tree_f.get_children())
        for pid_, info in state.get("failed", {}).items():
            self.tree_f.insert("", "end", values=(
                pid_[:24], info.get("attempts"), info.get("stage"),
                info.get("last_try",""), (info.get("error") or "")[:90],
            ))

        # status bar
        lr = state.get("last_refresh", 0)
        lr_str = datetime.fromtimestamp(lr).strftime('%H:%M:%S') if lr else "yok"
        self.sb.config(text=f"Strapi son refresh: {lr_str}  •  Şu an: {now.strftime('%H:%M:%S')}")

if __name__ == "__main__":
    app = BotDashboard()
    app.mainloop()
