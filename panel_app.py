#!/usr/bin/env python3
"""RunRunDeals Telegram Bot — macOS Dashboard (Tkinter).
İki bağımsız kaynak (Amazon/Strapi + Walmart/savings101) için ayrı tab + ayrı kontrol.
Daemon arka planda hep çalışır; her kaynak ayrı enable/disable flag'i ile yönetilir."""
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

# ---------- daemon / state helpers ----------

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
    if is_running(): return False, "Daemon zaten çalışıyor"
    log_f = open(LOG, "a")
    p = subprocess.Popen(
        ["/usr/bin/caffeinate", "-i", sys.executable, DAEMON_PY],
        stdout=log_f, stderr=subprocess.STDOUT,
        cwd=BASE, start_new_session=True,
    )
    with open(PID_FILE, "w") as f: f.write(str(p.pid))
    return True, f"Daemon başlatıldı (PID {p.pid})"

def stop_daemon():
    pid = is_running()
    if not pid: return False, "Daemon zaten kapalı"
    try: os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        try: os.kill(pid, signal.SIGTERM)
        except: pass
    try: os.remove(PID_FILE)
    except: pass
    return True, f"Daemon durduruldu (PID {pid})"

def force_refresh():
    subprocess.Popen([sys.executable, DAEMON_PY, "--refresh", "--once", "--dry-run"], cwd=BASE)
    return True, "Strapi refresh tetiklendi"

def load_state():
    if not os.path.exists(STATE):
        return {"posted": [], "failed": {}, "hashes": {}, "last_refresh": 0, "sources": {}}
    try:
        s = json.load(open(STATE))
        s.setdefault("sources", {})
        return s
    except: return {"posted": [], "failed": {}, "hashes": {}, "last_refresh": 0, "sources": {}}

def save_state(s):
    try:
        with open(STATE, "w") as f: json.dump(s, f, indent=2)
    except Exception: pass

def source_enabled(source_name, default=True):
    """Read enable/disable from state; default True so old states keep working."""
    s = load_state()
    return s.get("sources", {}).get(source_name, {}).get("enabled", default)

def set_source_enabled(source_name, enabled):
    s = load_state()
    s.setdefault("sources", {}).setdefault(source_name, {})["enabled"] = bool(enabled)
    save_state(s)

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
        self.title("🛒 Run Run Deals Telegram Bot")
        self.geometry("1180x820")
        self.minsize(960, 640)
        self.configure(bg="#f5f5f7")
        self.style = ttk.Style(self)
        try: self.style.theme_use("aqua")
        except: pass

        self._build_header()
        self._build_tabs()
        self._build_statusbar()
        self.refresh()
        self.after(5000, self._auto_refresh)

    # ---- HEADER (sadece global daemon + global butonlar) ----
    def _build_header(self):
        bar = tk.Frame(self, bg="#f5f5f7", padx=16, pady=12)
        bar.pack(fill="x", side="top")
        tk.Label(bar, text="🛒 Run Run Deals Telegram Bot",
                 font=("SF Pro Display", 22, "bold"),
                 bg="#f5f5f7").pack(side="left")
        # global daemon status
        self.daemon_label = tk.Label(bar, text="⏳", font=("SF Pro Display", 12, "bold"),
                                      bg="#f5f5f7", fg="#666", padx=18)
        self.daemon_label.pack(side="left", padx=12)
        # right side: global controls
        btns = tk.Frame(bar, bg="#f5f5f7")
        btns.pack(side="right")
        self.btn_update = ttk.Button(btns, text="⬇️ Güncelle", command=self.on_update)
        self.btn_update.pack(side="left", padx=4)
        ttk.Button(btns, text="🔗 Kanalı Aç",
                   command=lambda: webbrowser.open("https://t.me/RunRunDeals")).pack(side="left", padx=4)

    # ---- TABS ----
    def _build_tabs(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=16, pady=12)
        self._build_source_tab("strapi", "📦 Amazon")
        self._build_source_tab("walmart", "🛒 Walmart")
        self._build_log_tab()
        self._build_failed_tab()

    def _build_source_tab(self, src, title):
        """Build identical layout for either source."""
        frame = tk.Frame(self.nb, bg="white")
        self.nb.add(frame, text=title)

        # Top control row
        ctrl = tk.Frame(frame, bg="white", padx=10, pady=10)
        ctrl.pack(fill="x")
        status_lbl = tk.Label(ctrl, text="⏳", font=("SF Pro Display", 13, "bold"),
                              bg="white", padx=10)
        status_lbl.pack(side="left")
        btn_start = ttk.Button(ctrl, text="▶ Başlat",
                               command=lambda s=src: self.on_source_start(s))
        btn_start.pack(side="left", padx=4)
        btn_stop  = ttk.Button(ctrl, text="■ Durdur",
                               command=lambda s=src: self.on_source_stop(s))
        btn_stop.pack(side="left", padx=4)
        if src == "strapi":
            ttk.Button(ctrl, text="🔄 Strapi Refresh",
                       command=self.on_strapi_refresh).pack(side="left", padx=4)
        else:
            ttk.Button(ctrl, text="🛒 Slot Şimdi Çalıştır",
                       command=self.on_walmart_now).pack(side="left", padx=4)

        # Metrics row
        metrics = tk.Frame(frame, bg="white")
        metrics.pack(fill="x", padx=10, pady=(0, 10))
        widgets = {}
        for i, (lbl, key) in enumerate([("Bugün","today"),("Son 7 gün","week"),
                                         ("Toplam","total"),("Sonraki post","next")]):
            f = tk.Frame(metrics, bg="#f5f5f7", padx=12, pady=8,
                         highlightbackground="#dddddd", highlightthickness=1)
            f.grid(row=0, column=i, padx=4, sticky="ew")
            metrics.grid_columnconfigure(i, weight=1)
            tk.Label(f, text=lbl, font=("SF Pro Display", 10),
                     bg="#f5f5f7", fg="#666").pack(anchor="w")
            v = tk.Label(f, text="—", font=("SF Pro Display", 18, "bold"),
                         bg="#f5f5f7", fg="#111")
            v.pack(anchor="w")
            widgets[key] = v

        # Recent posts table
        rcols = ("time","id","title","msg")
        col_titles = [("time","Saat",110),("id","ID",200),
                      ("title","Başlık",520),("msg","msg_id",80)]
        recent_tree = ttk.Treeview(frame, columns=rcols, show="headings", height=20)
        for cid, txt, w in col_titles:
            recent_tree.heading(cid, text=txt)
            recent_tree.column(cid, width=w, anchor="w")
        recent_tree.pack(fill="both", expand=True, padx=10, pady=10)

        # Save handles per-source
        setattr(self, f"{src}_status_lbl", status_lbl)
        setattr(self, f"{src}_btn_start", btn_start)
        setattr(self, f"{src}_btn_stop",  btn_stop)
        setattr(self, f"{src}_metrics",   widgets)
        setattr(self, f"{src}_tree",      recent_tree)

    def _build_log_tab(self):
        f = tk.Frame(self.nb, bg="white"); self.nb.add(f, text="📜 Genel Log")
        bar = tk.Frame(f, bg="white"); bar.pack(fill="x", padx=10, pady=(10,0))
        tk.Button(bar, text="🗑  Log Temizle", command=self._clear_log,
                  bg="#dc2626", fg="white", relief="flat", padx=14, pady=6,
                  font=("SF Pro Text", 12, "bold")).pack(side="right")
        tk.Button(bar, text="📤  Telegram'a Gönder", command=self._send_log_telegram,
                  bg="#2563eb", fg="white", relief="flat", padx=14, pady=6,
                  font=("SF Pro Text", 12, "bold")).pack(side="right", padx=(0,8))
        self.log_text = scrolledtext.ScrolledText(
            f, font=("Menlo", 11), bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white", wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=10, pady=10)
        for tag, color in [("SENT","#7ec07e"),("FAIL","#f48771"),
                            ("SKIP","#999"),("DAEMON","#dcdcaa"),
                            ("WM","#4fc1ff"),("refresh","#4fc1ff")]:
            self.log_text.tag_configure(tag, foreground=color)

    def _build_failed_tab(self):
        f = tk.Frame(self.nb, bg="white"); self.nb.add(f, text="⚠️ Hatalı")
        cols = ("id","attempts","stage","last","err")
        self.tree_f = ttk.Treeview(f, columns=cols, show="headings", height=20)
        for cid, txt, w in [("id","ID",220),("attempts","Deneme",70),
                             ("stage","Aşama",140),("last","Son Deneme",150),
                             ("err","Hata",400)]:
            self.tree_f.heading(cid, text=txt)
            self.tree_f.column(cid, width=w, anchor="w")
        self.tree_f.pack(fill="both", expand=True, padx=10, pady=10)

    def _build_statusbar(self):
        self.sb = tk.Label(self, text="", anchor="w", bg="#ecedf0",
                           padx=10, pady=4, font=("SF Pro Display", 11))
        self.sb.pack(fill="x", side="bottom")

    # ---- ACTIONS ----
    def _toast(self, msg, success=True):
        self.sb.config(text=("✅ " if success else "⚠️ ") + msg)

    def on_source_start(self, src):
        set_source_enabled(src, True)
        # Daemon yoksa başlat (otomatik)
        if not is_running():
            ok, msg = start_daemon()
            if not ok:
                self._toast(msg, success=False); return
        name = "Amazon" if src=="strapi" else "Walmart"
        self._toast(f"{name} kaynağı aktif edildi", success=True)
        self.refresh()

    def on_source_stop(self, src):
        set_source_enabled(src, False)
        name = "Amazon" if src=="strapi" else "Walmart"
        # If BOTH disabled, kill daemon to save resources
        state = load_state()
        any_enabled = any(state.get("sources",{}).get(k,{}).get("enabled", True)
                          for k in ("strapi","walmart"))
        if not any_enabled:
            stop_daemon()
            self._toast(f"{name} durduruldu. Tüm kaynaklar kapalı — daemon da kapatıldı.", success=True)
        else:
            self._toast(f"{name} kaynağı durduruldu", success=True)
        self.refresh()

    def on_strapi_refresh(self):
        ok, msg = force_refresh(); self._toast(msg, success=ok)

    def on_walmart_now(self):
        """Trigger one Walmart slot run immediately. Rate-limit: 15 min between manual tests."""
        # Rate-limit check: last manual test must be ≥15 min ago (Walmart IP block guard)
        state = load_state()
        last_manual = state.get('walmart',{}).get('last_manual_test')
        if last_manual:
            try:
                dt = datetime.fromisoformat(last_manual)
                elapsed = (datetime.now() - dt).total_seconds()
                if elapsed < 900:  # 15 min
                    remaining = int((900 - elapsed) / 60)
                    if not messagebox.askyesno(
                        "Hızlı tekrar tehlikeli",
                        f"Son manuel test {int(elapsed/60)} dk önceydi. Walmart 15 dk içinde tekrar etmen IP banı tetikleyebilir.\n\n"
                        f"{remaining} dk daha beklemeni öneririm.\n\nYine de denemek istiyor musun?",
                        parent=self,
                    ):
                        return
            except Exception: pass
        self._toast("Walmart slot manuel tetiklendi (log'a bak)", success=True)
        def _run():
            try:
                # Clear cooldown so manual test runs immediately
                state = load_state()
                state.setdefault('walmart',{})['last_manual_test'] = datetime.now().isoformat()
                if state.get('walmart',{}).get('cooldown_until'):
                    del state['walmart']['cooldown_until']
                    state['walmart']['consecutive_fail'] = 0
                save_state(state)
                # Run in subprocess with DEALBOT_NO_LOCK=1 so daemon's lock doesn't block us
                env = os.environ.copy()
                env["DEALBOT_NO_LOCK"] = "1"
                proc = subprocess.run(
                    [sys.executable, "-c",
                     f"import sys; sys.path.insert(0,'{BASE}'); import dealbot; "
                     f"dealbot.run_walmart_slot(dealbot.load_state(), dry=False)"],
                    cwd=BASE, env=env, timeout=300,
                    capture_output=True, text=True,
                )
                # Append output to log so user sees it in panel
                with open(LOG, "a") as f:
                    f.write(f"\n--- MANUAL WALMART TEST @ {datetime.now().isoformat(timespec='seconds')} ---\n")
                    if proc.stdout: f.write(proc.stdout)
                    if proc.stderr: f.write(f"\n[stderr]\n{proc.stderr}")
                    f.write("\n--- END MANUAL TEST ---\n")
                if proc.returncode == 0:
                    self.after(0, lambda: self._toast("Walmart slot bitti — log'a bak", success=True))
                else:
                    self.after(0, lambda: self._toast(f"Walmart slot kod={proc.returncode}", success=False))
            except Exception as e:
                self.after(0, lambda: self._toast(f"WM slot err: {e}", success=False))
        threading.Thread(target=_run, daemon=True).start()

    def on_update(self):
        if not messagebox.askyesno(
            "Güncelle",
            "GitHub'dan son sürümü çekiyorum, yeni bağımlılıkları kuruyorum (gerekirse), botu yeniden başlatıyorum.\n\nBu işlem 1-3 dk sürebilir (ilk kurulumda 5+ dk).\n\nDevam?",
            parent=self,
        ): return
        self.btn_update.config(state="disabled")
        self._toast("Güncelleniyor...", success=True)
        def _run():
            try:
                self.after(0, lambda: self._toast("1/3 Kod indiriliyor...", success=True))
                proc = subprocess.run(["git", "-C", BASE, "pull", "--ff-only"],
                                       capture_output=True, text=True, timeout=60)
                out = (proc.stdout + proc.stderr).strip()
                if proc.returncode != 0:
                    self.after(0, lambda: self._toast(f"Pull hatası: {out[:120]}", success=False))
                    self.after(0, lambda: self.btn_update.config(state="normal")); return
                self.after(0, lambda: self._toast("2/3 Bağımlılıklar kontrol ediliyor...", success=True))
                installer = f"{BASE}/install_on_new_mac.sh"
                if os.path.exists(installer):
                    subprocess.run(["bash", installer], capture_output=True, text=True, timeout=600)
                self.after(0, lambda: self._toast("3/3 Bot ve panel yeniden başlatılıyor...", success=True))
                stop_daemon(); time.sleep(1); start_daemon()
                up_to_date = "up to date" in out.lower()
                if up_to_date:
                    self.after(0, lambda: self._toast("Up to date — değişiklik yok", success=True))
                    self.after(0, lambda: self.btn_update.config(state="normal"))
                    return
                # Self-relaunch: panel'in kendisini yeniden aç (yeni kod yüklensin)
                self.after(1500, self._relaunch_panel)
            except Exception as e:
                self.after(0, lambda: self._toast(f"Güncelleme hatası: {e}", success=False))
                self.after(0, lambda: self.btn_update.config(state="normal"))
        threading.Thread(target=_run, daemon=True).start()

    def _relaunch_panel(self):
        """Panel'i tamamen yeniden başlat (yeni Python kodunu yüklemek için)."""
        try:
            # /Applications altında .app varsa onu aç (Mac native)
            app_path = "/Applications/RunRunDealsBot.app"
            if os.path.exists(app_path):
                subprocess.Popen(["open", "-n", app_path])
            else:
                # Fallback: doğrudan Python script'i tekrar başlat
                subprocess.Popen([sys.executable, __file__], cwd=BASE)
        except Exception as e:
            self._toast(f"Relaunch hatası: {e} — manuel aç-kapa yap", success=False)
            return
        # Mevcut instance'ı kapat
        self.after(500, lambda: (self.quit(), os._exit(0)))

    def _send_log_telegram(self):
        """Logun son 80 satırını admin'in Telegram DM'ine yolla."""
        def _run():
            try:
                import json as _json
                cfg = _json.load(open(f"{BASE}/config.json"))
                chat_id = cfg.get("admin_chat_id")
                token_path = os.path.expanduser(cfg.get("token_path",""))
                if not chat_id or not token_path or not os.path.exists(token_path):
                    self.after(0, lambda: self._toast("admin_chat_id veya token yok", success=False))
                    return
                token = open(token_path).read().strip()
                sys.path.insert(0, BASE)
                import notify
                ok = notify.send_full_log(token, chat_id, lines=80)
                if ok:
                    self.after(0, lambda: self._toast("✅ Log Telegram'a gönderildi", success=True))
                else:
                    self.after(0, lambda: self._toast("Log gönderilemedi", success=False))
            except Exception as e:
                self.after(0, lambda: self._toast(f"Hata: {e}", success=False))
        threading.Thread(target=_run, daemon=True).start()

    def _clear_log(self):
        if not messagebox.askyesno("Log Temizle","Tüm log geçmişi silinecek. Emin misin?",
                                     parent=self): return
        try:
            with open(LOG, "w") as f:
                f.write(f"[{datetime.now().isoformat(timespec='seconds')}] [log cleared by panel]\n")
            self.log_text.config(state="normal"); self.log_text.delete("1.0","end")
            self.log_text.config(state="disabled")
            self._toast("Log temizlendi", success=True)
        except Exception as e:
            self._toast(f"Log temizleme hatası: {e}", success=False)

    # ---- DATA REFRESH ----
    def _auto_refresh(self):
        try: self.refresh()
        except Exception as e: self.sb.config(text=f"refresh err: {e}")
        self.after(5000, self._auto_refresh)

    def _source_of(self, entry):
        if entry.get("source") == "walmart": return "walmart"
        if (entry.get("id") or "").startswith("wp:"): return "walmart"
        return "strapi"

    def refresh(self):
        state = load_state()
        pid   = is_running()
        posted_all = state.get("posted", [])
        now = datetime.now()

        # ---- Global daemon status ----
        if pid:
            self.daemon_label.config(text=f"🟢 Daemon: PID {pid}", fg="#2e7d32")
        else:
            self.daemon_label.config(text="🔴 Daemon: durmuş", fg="#d32f2f")

        # ---- Per-source status + metrics ----
        for src, src_name in [("strapi","Amazon"), ("walmart","Walmart")]:
            enabled = state.get("sources",{}).get(src,{}).get("enabled", True)
            lbl    = getattr(self, f"{src}_status_lbl")
            bstart = getattr(self, f"{src}_btn_start")
            bstop  = getattr(self, f"{src}_btn_stop")
            cooldown_msg = ""
            if src == "walmart":
                cd = state.get("walmart",{}).get("cooldown_until")
                if cd:
                    try:
                        dt = datetime.fromisoformat(cd)
                        if dt > now:
                            mins = int((dt - now).total_seconds()/60)
                            cooldown_msg = f"  ⏸️ Cooldown {mins}m"
                    except Exception: pass
            if enabled and pid:
                lbl.config(text=f"🟢 {src_name} aktif{cooldown_msg}", fg="#2e7d32")
                bstart.config(state="disabled"); bstop.config(state="normal")
            elif enabled and not pid:
                lbl.config(text=f"🟡 {src_name} aktif ama daemon kapalı{cooldown_msg}", fg="#a16207")
                bstart.config(state="normal"); bstop.config(state="disabled")
            else:
                lbl.config(text=f"🔴 {src_name} durduruldu{cooldown_msg}", fg="#d32f2f")
                bstart.config(state="normal"); bstop.config(state="disabled")

            posted_src = [e for e in posted_all if self._source_of(e) == src]
            today = sum(1 for e in posted_src if (d := parse_dt(e.get("posted_at"))) and d.date() == now.date())
            week  = sum(1 for e in posted_src if (d := parse_dt(e.get("posted_at"))) and (now - d) < timedelta(days=7))
            total = len(posted_src)
            last_post = max([d for e in posted_src if (d := parse_dt(e.get("posted_at")))] or [None])
            interval_min = 10 if src == "strapi" else 60
            next_post = "—"
            if pid and enabled and last_post:
                next_at = last_post + timedelta(minutes=interval_min)
                secs = (next_at - now).total_seconds()
                next_post = f"{int(secs//60)}m {int(secs%60)}s" if secs > 0 else "şimdi"
            mw = getattr(self, f"{src}_metrics")
            for k, v in [("today",today),("week",week),("total",total),("next",next_post)]:
                mw[k].config(text=str(v))

            # Recent posts for this source
            tree = getattr(self, f"{src}_tree")
            tree.delete(*tree.get_children())
            for e in sorted(posted_src, key=lambda x: x.get("posted_at",""), reverse=True)[:60]:
                d = parse_dt(e.get("posted_at"))
                tree.insert("", "end", values=(
                    d.strftime("%d.%m %H:%M") if d else "?",
                    (e.get("id") or "")[:30],
                    (e.get("title_key") or "")[:80],
                    e.get("msg_id") or "",
                ))

        # ---- Log ----
        try: tail = open(LOG).read().splitlines()[-200:]
        except Exception: tail = []
        self.log_text.config(state="normal"); self.log_text.delete("1.0","end")
        for line in tail:
            tag = None
            for key in ("SENT","FAIL","SKIP","DAEMON","WM","refresh"):
                if key in line: tag = key; break
            self.log_text.insert("end", line + "\n", tag)
        self.log_text.see("end"); self.log_text.config(state="disabled")

        # ---- Failed ----
        self.tree_f.delete(*self.tree_f.get_children())
        for pid_, info in state.get("failed", {}).items():
            self.tree_f.insert("", "end", values=(
                pid_[:24], info.get("attempts"), info.get("stage"),
                info.get("last_try",""), (info.get("error") or "")[:90],
            ))

        lr = state.get("last_refresh", 0)
        lr_str = datetime.fromtimestamp(lr).strftime('%H:%M:%S') if lr else "yok"
        self.sb.config(text=f"Strapi son refresh: {lr_str}  •  Şu an: {now.strftime('%H:%M:%S')}")


if __name__ == "__main__":
    app = BotDashboard()
    app.mainloop()
