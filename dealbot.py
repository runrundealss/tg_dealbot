#!/usr/bin/env python3
"""
Telegram Deal Bot — Strapi-driven affiliate deal poster
- Config is read from local config.json (gitignored)
- Each post gets its own UNIQUE image (md5 spot-check)
- 10-min posting cadence, hourly source refresh
- Inline buttons: Go to Product, Copy Code
- Self-updates via git pull every hour (launchd KeepAlive restarts on exit)
"""
# ── SSL bootstrap — macOS system Python 3.9'da urllib CA bulamıyor sorunu.
# Hem certifi varsa onu kullan, yoksa unverified context'e düş (sadece
# public image/api request'i var → kimlik bilgisi sızmaz).
import ssl
try:
    import certifi
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
except ImportError:
    ssl._create_default_https_context = ssl._create_unverified_context

import json, os, sys, time, hashlib, urllib.request, urllib.parse, subprocess, re, atexit
from datetime import datetime, timezone, timedelta
from PIL import Image, ImageDraw, ImageFont, ImageOps

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
_cfg_path     = os.path.join(BASE_DIR, "config.json")
if not os.path.exists(_cfg_path):
    raise SystemExit(f"config.json bulunamadı — config.example.json'u kopyalayıp doldur: {_cfg_path}")
_cfg          = json.load(open(_cfg_path))
TOKEN_PATH    = os.path.expanduser(_cfg["token_path"])
TOKEN         = open(TOKEN_PATH).read().strip()
CHANNEL       = _cfg["channel"]
AFF_TAG       = _cfg["aff_tag"]
FONT_PATH     = "/tmp/fonts/Bangers-Regular.ttf"

STRAPI_URL    = _cfg["strapi_url"]
MIN_DISCOUNT  = 20             # only products with ≥20% off
STRAPI_LIMIT  = 100            # fetch this many on each refresh
REFRESH_SECS  = 3600           # Strapi refresh interval (1 hour)
POST_SECS     = 600            # post cadence (10 minutes)
ASIN_COOLDOWN_DAYS = 10        # don't repost same ASIN/title within this window
STATE_TTL_DAYS = 30            # drop posted entries older than this
MAX_FAIL_ATTEMPTS = 3          # how many fails before giving up on a product
FAIL_RETRY_HOURS = 24          # after this long, reset attempts and try again
UPDATE_CHECK_SECS = 3600       # check git origin for new version every hour

# Path'ler artık script'in kendi klasöründen türer — herhangi bir kullanıcıda
# (kaan, begum, vs.) doğru yere işaret eder.
IMG_DIR       = os.path.join(BASE_DIR, "images")
LOG_PATH      = os.path.join(BASE_DIR, "dealbot.log")
STATE_PATH    = os.path.join(BASE_DIR, "state.json")
PRODUCTS_PATH = os.path.join(BASE_DIR, "products.json")
os.makedirs(IMG_DIR, exist_ok=True)

# Font yoksa otomatik indir — install script'i atlandıysa veya /tmp restartlandıysa
if not os.path.exists(FONT_PATH):
    try:
        os.makedirs(os.path.dirname(FONT_PATH), exist_ok=True)
        urllib.request.urlretrieve(
            "https://github.com/google/fonts/raw/main/ofl/bangers/Bangers-Regular.ttf",
            FONT_PATH,
        )
    except Exception as _e:
        print(f"[bootstrap] font download failed: {_e}", file=sys.stderr)

# ── Lock file — aynı anda birden fazla instance çalışmasını engelle.
# launchd / shell birden çok kez başlatırsa eski PID hâlâ canlıysa exit.
# DEALBOT_NO_LOCK=1 ile bypass (panel'in test post tetiklemesi için).
_LOCK_PATH = os.path.join(BASE_DIR, ".dealbot.lock")
if os.path.exists(_LOCK_PATH) and not os.environ.get('DEALBOT_NO_LOCK'):
    try:
        _old_pid = int(open(_LOCK_PATH).read().strip())
        os.kill(_old_pid, 0)  # PID hâlâ canlı mı?
        print(f"[lock] already running pid={_old_pid}, exiting", file=sys.stderr)
        sys.exit(0)
    except (OSError, ValueError):
        pass  # PID ölü/geçersiz, lock stale → devam, üzerine yaz
if not os.environ.get('DEALBOT_NO_LOCK'):
    with open(_LOCK_PATH, "w") as _f:
        _f.write(str(os.getpid()))
    atexit.register(lambda: os.path.exists(_LOCK_PATH) and os.remove(_LOCK_PATH))

# ---------- helpers ----------

# Log rotation — log dosyası belli boyutu aşınca son N satırı tut, gerisini at.
# Bot 7/24 çalışır, panele 150 satır gösterir; eski log diske ihtiyaç olmaz.
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB üstüne çıkarsa kırp
LOG_KEEP_LINES = 2000            # kırpınca son bu kadar satırı tut

def _rotate_log_if_big():
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            with open(LOG_PATH, "r") as f:
                tail = f.readlines()[-LOG_KEEP_LINES:]
            with open(LOG_PATH, "w") as f:
                f.write(f"[{datetime.now().isoformat(timespec='seconds')}] [log rotated, kept last {LOG_KEEP_LINES} lines]\n")
                f.writelines(tail)
    except Exception:
        pass

_rotate_log_if_big()  # bot başlarken kontrol et

def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f: f.write(line + "\n")

def load_state():
    if os.path.exists(STATE_PATH):
        s = json.load(open(STATE_PATH))
    else:
        s = {"posted": [], "hashes": {}, "last_refresh": 0, "failed": {}}
    # Migrate old format: posted may be a list of strings → convert to objects
    new_posted = []
    for entry in s.get("posted", []):
        if isinstance(entry, str):
            new_posted.append({"id": entry, "asin": "", "title_key": "", "posted_at": "1970-01-01T00:00:00"})
        else:
            new_posted.append(entry)
    s["posted"] = new_posted
    s.setdefault("failed", {})
    return s

def save_state(s):
    with open(STATE_PATH, "w") as f: json.dump(s, f, indent=2)

def title_key(t):
    """Aggressive normalization for variant dedup.
       Strips sizes (S/M/L/XL/...), colors, pack counts, standalone numbers, and
       returns the first 5 significant words.  Same item with different size or
       color produces the SAME key → caught as variant.
    """
    if not t: return ""
    s = t.lower()
    # Strip parenthetical / bracket content (often size/color/SKU)
    s = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", s)
    # Strip size & numeric size+pack tokens
    s = re.sub(r"\b(xs|sm|md|lg|xl|xxl|xxxl|small|medium|large|x[\-]?large|xx[\-]?large)\b", " ", s)
    s = re.sub(r"\b(\d+\s*-?\s*(pack|piece|pieces|pcs|count|ct|set|sets|pair|pairs))\b", " ", s)
    s = re.sub(r"\b\d+\s*(oz|ml|lb|kg|gram|g|cm|in|inch|ft)\b", " ", s)
    # Strip common color words
    s = re.sub(r"\b(red|blue|green|black|white|pink|yellow|gray|grey|brown|purple|orange|navy|beige|ivory|olive|teal|cyan|magenta|gold|silver|rose|khaki)\b", " ", s)
    # Strip standalone numbers (year, edition, sku digits) — keep only words
    s = re.sub(r"[^a-z\s]", " ", s)
    s = re.sub(r"\b\d+\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    words = s.split()[:5]
    return " ".join(words)

def prune_state(state):
    """Drop posted entries older than STATE_TTL_DAYS."""
    cutoff = datetime.now() - __import__("datetime").timedelta(days=STATE_TTL_DAYS)
    before = len(state["posted"])
    kept = []
    for e in state["posted"]:
        try:
            t = datetime.fromisoformat(e["posted_at"])
            if t >= cutoff:
                kept.append(e)
        except Exception:
            kept.append(e)  # keep unparseable
    state["posted"] = kept
    if len(kept) != before:
        log(f"state pruned: {before} → {len(kept)} (TTL {STATE_TTL_DAYS}d)")

def should_skip(product, state):
    """Return (skip:bool, reason:str)."""
    pid   = product.get("id")
    asin  = product.get("asin") or ""
    tkey  = title_key(product.get("title"))
    from datetime import timedelta
    cutoff_cool = datetime.now() - timedelta(days=ASIN_COOLDOWN_DAYS)
    # Permanent-fail check
    f = state.get("failed", {}).get(pid)
    if f and f.get("attempts", 0) >= MAX_FAIL_ATTEMPTS:
        try:
            last = datetime.fromisoformat(f["last_try"])
            if datetime.now() - last < timedelta(hours=FAIL_RETRY_HOURS):
                return True, f"failed {f['attempts']}x — retry in {FAIL_RETRY_HOURS}h"
        except Exception: pass
    for e in state["posted"]:
        if pid and e.get("id") == pid:
            return True, f"same _id ({pid})"
        if asin and e.get("asin") == asin:
            try:
                if datetime.fromisoformat(e["posted_at"]) >= cutoff_cool:
                    return True, f"ASIN {asin} cooldown ({ASIN_COOLDOWN_DAYS}d)"
            except Exception: pass
        # Variant dedup: same normalized title_key counts as already-posted, regardless of ASIN
        if tkey and e.get("title_key") == tkey:
            try:
                if datetime.fromisoformat(e["posted_at"]) >= cutoff_cool:
                    return True, f"variant of recently-posted '{tkey[:30]}' ({ASIN_COOLDOWN_DAYS}d)"
            except Exception: pass
    return False, ""

def record_fail(state, pid, stage, err):
    """Increment failure counter; bot will give up after MAX_FAIL_ATTEMPTS."""
    from datetime import timedelta
    f = state.setdefault("failed", {}).setdefault(pid, {"attempts": 0})
    # Reset counter if last attempt was long ago
    try:
        last = datetime.fromisoformat(f.get("last_try", "1970-01-01"))
        if datetime.now() - last >= timedelta(hours=FAIL_RETRY_HOURS):
            f["attempts"] = 0
    except Exception: pass
    f["attempts"] = int(f.get("attempts", 0)) + 1
    f["last_try"] = datetime.now().isoformat(timespec="seconds")
    f["stage"]    = stage
    f["error"]    = str(err)[:200]
    save_state(state)
    log(f"FAIL ({f['attempts']}/{MAX_FAIL_ATTEMPTS}) {pid} stage={stage}: {err}")

def clear_fail(state, pid):
    if pid and pid in state.get("failed", {}):
        del state["failed"][pid]
        save_state(state)

# ---------- auto-update via git pull ----------

_last_update_check = 0

def check_for_update():
    """git pull origin; if HEAD changed, exit so launchd restarts us fresh."""
    global _last_update_check
    now = time.time()
    if now - _last_update_check < UPDATE_CHECK_SECS:
        return False
    _last_update_check = now
    if not os.path.isdir(os.path.join(BASE_DIR, ".git")):
        return False
    try:
        before = subprocess.check_output(["git","rev-parse","HEAD"], cwd=BASE_DIR, text=True).strip()
        subprocess.run(["git","fetch","--quiet"], cwd=BASE_DIR, timeout=30, check=False)
        # Try fast-forward only — never overwrite local
        r = subprocess.run(["git","merge","--ff-only","--quiet","@{u}"],
                           cwd=BASE_DIR, timeout=30, capture_output=True, text=True)
        after = subprocess.check_output(["git","rev-parse","HEAD"], cwd=BASE_DIR, text=True).strip()
        if before != after:
            log(f"🔄 UPDATE: {before[:7]} → {after[:7]}  — restarting via launchd")
            sys.exit(0)   # KeepAlive=Crashed:true + SuccessfulExit:false → launchd restarts
        return False
    except Exception as e:
        log(f"update check err: {e}")
        return False

def with_aff_tag(url, tag=AFF_TAG):
    if not url: return ""
    if "tag=" in url:
        return re.sub(r"tag=[\w-]+", f"tag={tag}", url)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}tag={tag}"

def parse_asin(p):
    bd = p.get("bottom_description") or ""
    m = re.search(r"ASIN[:\s]+([A-Z0-9]{10})", bd)
    if m: return m.group(1)
    u = p.get("url") or ""
    m = re.search(r"/dp/([A-Z0-9]{10})", u)
    if m: return m.group(1)
    return ""

def fetch_strapi():
    url = f"{STRAPI_URL}?_sort=createdAt:DESC&_limit={STRAPI_LIMIT}&store=Amazon"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read())
    except Exception as e:
        log(f"Strapi fetch FAILED: {e}")
        return []
    out = []
    for p in data:
        if not p.get("imgurl"): continue
        if (p.get("discount") or 0) < MIN_DISCOUNT: continue
        out.append({
            "id": p["_id"],
            "asin": parse_asin(p),
            "title": p["title"],
            "disc": int(p["discount"]),
            "sale": float(p["price"]),
            "reg":  float(p["old_price"]),
            "code": p.get("code") or "",
            "image_url": p["imgurl"],
            "product_url": with_aff_tag(p.get("url") or ""),
            "expires": p.get("expirationDate",""),
            "createdAt": p.get("createdAt",""),
        })
    return out

def refresh_products(state, force=False):
    """Saat başı: eski queue'yi sil, Strapi'den taze ürünleri çek.
       - Daha önce paylaşılmış ID/ASIN/title-variant'ları düşürür.
       - Aynı batch içinde variant tekrarları varsa sadece 1'ini tutar.
    """
    now = time.time()
    if not force and now - state.get("last_refresh", 0) < REFRESH_SECS:
        return None
    items = fetch_strapi()
    if not items:
        log("refresh: no items returned, keeping existing products.json")
        return None

    posted = state.get("posted", []) or []
    posted_ids   = {e.get("id")   for e in posted if e.get("id")}
    posted_asins = {e.get("asin") for e in posted if e.get("asin")}
    posted_tkeys = {e.get("title_key") for e in posted if e.get("title_key")}

    fresh = []
    seen_tkeys = set()    # intra-batch variant dedup
    seen_asins = set()
    dropped = {"posted_id": 0, "posted_asin": 0, "posted_variant": 0, "batch_variant": 0}
    for p in items:
        pid  = p.get("id")
        asin = p.get("asin") or ""
        tkey = title_key(p.get("title"))
        if pid in posted_ids:
            dropped["posted_id"] += 1; continue
        if asin and asin in posted_asins:
            dropped["posted_asin"] += 1; continue
        if tkey and tkey in posted_tkeys:
            dropped["posted_variant"] += 1; continue
        if tkey and tkey in seen_tkeys:
            dropped["batch_variant"] += 1; continue
        if asin and asin in seen_asins:
            dropped["batch_variant"] += 1; continue
        seen_tkeys.add(tkey)
        if asin: seen_asins.add(asin)
        fresh.append(p)

    with open(PRODUCTS_PATH, "w") as f: json.dump(fresh, f, indent=2)
    state["last_refresh"] = now
    save_state(state)
    log(f"refresh: Strapi={len(items)} → queue REPLACED with {len(fresh)} fresh "
        f"(dropped: posted_id={dropped['posted_id']}, posted_asin={dropped['posted_asin']}, "
        f"posted_variant={dropped['posted_variant']}, batch_variant={dropped['batch_variant']})")
    return fresh

# ---------- image generation ----------

def _hires_amazon_url(url):
    """Strapi 400x400 thumbnail → Amazon 2000x2000 hires.
    Pattern: 41xxx._SR400,400_.jpg → 41xxx._UF2000,2000_.jpg
    """
    if not url or ("media-amazon.com" not in url and "ssl-images-amazon" not in url):
        return url
    if re.search(r"\._[A-Z0-9_,]+_\.(jpe?g|png)", url, flags=re.I):
        return re.sub(r"\._[A-Z0-9_,]+_\.(jpe?g|png)",
                      r"._UF2000,2000_.\1", url, flags=re.I)
    return re.sub(r"\.(jpe?g|png)$", r"._UF2000,2000_.\1", url, flags=re.I)

def _trim_whitespace(img, threshold=240):
    """Crop the near-white border around the product."""
    try:
        import numpy as np
        arr = np.array(img.convert("RGB"))
        mask = ~(arr > threshold).all(axis=2)
        rows = mask.any(axis=1)
        cols = mask.any(axis=0)
        if not rows.any() or not cols.any():
            return img
        rmin, rmax = int(np.where(rows)[0][0]), int(np.where(rows)[0][-1])
        cmin, cmax = int(np.where(cols)[0][0]), int(np.where(cols)[0][-1])
        return img.crop((cmin, rmin, cmax+1, rmax+1))
    except Exception:
        # numpy missing — fall back to PIL bbox-based trim
        bg = Image.new("RGB", img.size, (255, 255, 255))
        diff = ImageOps.invert(ImageOps.grayscale(img))
        bbox = diff.getbbox()
        return img.crop(bbox) if bbox else img

def make_image(prod_img_path, disc_pct, sale, reg, out_path):
    """Build a 1500×1500 Telegram post image.
    Note: prod_img_path is treated as a hint — caller may have downloaded the
    Strapi thumbnail. We re-fetch the hires version from Amazon CDN when
    possible (overrides whatever was on disk)."""
    SIZE = 1500
    canvas = Image.new("RGB", (SIZE, SIZE), "white")
    prod = Image.open(prod_img_path).convert("RGB")
    prod = _trim_whitespace(prod, threshold=240)

    TOP_AREA, BOTTOM_AREA, SIDE_PAD = 300, 245, 85
    target_w = SIZE - 2*SIDE_PAD
    target_h = SIZE - TOP_AREA - BOTTOM_AREA
    prod.thumbnail((target_w, target_h), Image.LANCZOS)
    px = (SIZE - prod.width) // 2
    py = TOP_AREA + (target_h - prod.height) // 2
    canvas.paste(prod, (px, py))

    draw = ImageDraw.Draw(canvas)
    F_TOP = ImageFont.truetype(FONT_PATH, 250)
    F_BOT = ImageFont.truetype(FONT_PATH, 155)
    def outlined(d, txt, font, y, outline):
        bb = d.textbbox((0,0), txt, font=font); w = bb[2]-bb[0]
        x = (SIZE - w)//2 - bb[0]
        for dx in range(-outline, outline+1):
            for dy in range(-outline, outline+1):
                if dx*dx + dy*dy <= outline*outline:
                    d.text((x+dx, y+dy), txt, font=font, fill="black")
        d.text((x, y), txt, font=font, fill="white")
    outlined(draw, f"%{disc_pct} DEALS", F_TOP, 35, outline=8)
    outlined(draw, f"ONLY {sale:.2f} - REG ({reg:.2f})", F_BOT, SIZE-210, outline=6)
    canvas.save(out_path, "PNG", optimize=True)
    with open(out_path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()

def upload_catbox(path):
    r = subprocess.run([
        "curl", "-sS", "-F", "reqtype=fileupload",
        "-F", f"fileToUpload=@{path}",
        "https://catbox.moe/user/api.php"
    ], capture_output=True, text=True, timeout=60)
    return r.stdout.strip()

# ---------- Telegram send ----------

def send_to_channel(product, image_url):
    asin  = product.get("asin") or product.get("id")
    title = product["title"]
    disc  = product["disc"]
    sale  = product["sale"]
    reg   = product["reg"]
    code  = product.get("code") or ""
    short_title = re.split(r"[,|\-–—:]", title, maxsplit=1)[0].strip()[:60]
    cap_lines = [
        f"🔥 {short_title} – {disc}% OFF",
        "",
        f"💰 Was {reg:.2f} → Now {sale:.2f}",
    ]
    if code:
        cap_lines.append(f"🏷️ Code: {code}")
    cap_lines += ["", "#ad"]
    caption = "\n".join(cap_lines)

    url = product.get("product_url") or f"https://www.amazon.com/dp/{asin}?tag={AFF_TAG}"
    keyboard = [[{"text": "🛒 Go to Product", "url": url}]]
    if code:
        keyboard.append([{"text": f"📋 Copy Code: {code}", "copy_text": {"text": code}}])

    data = {
        "chat_id": CHANNEL,
        "photo": image_url,
        "caption": caption,
        "reply_markup": json.dumps({"inline_keyboard": keyboard}),
    }
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
        data=urllib.parse.urlencode(data).encode(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---------- post one ----------

def process_one(product, state, dry_run=False):
    key = product.get("id") or product.get("asin")
    skip, reason = should_skip(product, state)
    if skip:
        log(f"SKIP {key} — {reason}")
        return "SKIPPED"
    raw_path = f"/tmp/dealbot_raw_{key}.jpg"
    # Strapi sends 400×400 thumbnail; ask Amazon CDN for the 2000×2000 hires.
    # If the hires URL fails (rare), fall back to the original thumbnail.
    hires_url = _hires_amazon_url(product["image_url"])
    try:
        urllib.request.urlretrieve(hires_url, raw_path)
    except Exception as e_hi:
        try:
            urllib.request.urlretrieve(product["image_url"], raw_path)
        except Exception as e:
            record_fail(state, key, "image_download", e)
            return None
    img_path = f"{IMG_DIR}/dealpost_{key}.png"
    try:
        md5 = make_image(raw_path, product["disc"], product["sale"], product["reg"], img_path)
    except Exception as e:
        record_fail(state, key, "image_gen", e)
        return None
    for prev_key, prev_md5 in state["hashes"].items():
        if prev_md5 == md5 and prev_key != key:
            log(f"⚠️ DUPLICATE IMAGE: {key} == {prev_key} (md5 {md5}) — ABORT")
            return "DUPLICATE_ABORT"
    state["hashes"][key] = md5
    if dry_run:
        log(f"DRY {key} → hash={md5}")
        return {"dry": True}
    upload_url = upload_catbox(img_path)
    if not upload_url.startswith("http"):
        record_fail(state, key, "catbox_upload", upload_url)
        return None
    res = send_to_channel(product, upload_url)
    if res.get("ok"):
        msg_id = res["result"]["message_id"]
        log(f"SENT {key} msg_id={msg_id}")
        state["posted"].append({
            "id": product.get("id"),
            "asin": product.get("asin") or "",
            "title_key": title_key(product.get("title")),
            "posted_at": datetime.now().isoformat(timespec="seconds"),
            "msg_id": msg_id,
        })
        clear_fail(state, key)
        save_state(state)
        return res
    else:
        record_fail(state, key, "telegram_send", res)
        return None

# ---------- Walmart pipeline integration ----------

def run_walmart_slot(state, dry=False, force=False):
    """One Walmart slot. HARD rate-limit guard: cadence içinde 2. fire'ı reddeder.
       force=True manuel/test için bypass (ama yine cadence/3 saatlik koruma)."""
    try:
        from sources import walmart as wm
        import notify
    except Exception as e:
        log(f"walmart module import err: {e}")
        return
    if not _cfg.get('walmart',{}).get('enabled'):
        return
    # HARD rate-limit guard — nereden tetiklenirse tetiklensin (daemon, manual, subprocess)
    cadence = int(_cfg.get('schedule',{}).get('walmart_cadence_min', 120))
    min_gap = cadence - 10 if not force else max(30, cadence // 4)  # force = min 30dk veya cadence/4
    last_fire = state.get('walmart',{}).get('last_fire_at')
    if last_fire:
        try:
            from datetime import datetime as _dt
            elapsed_min = (_dt.now() - _dt.fromisoformat(last_fire)).total_seconds() / 60
            if elapsed_min < min_gap:
                log(f"WM REFUSED — son fire'dan sadece {int(elapsed_min)}dk geçti (min {min_gap}dk). force={force}")
                return
        except Exception: pass
    posted_hashes = set(state.get('hashes',{}).values())
    status, ready, _ = wm.run_one(_cfg, TOKEN, CHANNEL, state, posted_hashes, log)
    if status == 'BLOCKED':
        log("WM: Walmart blocked — entering 60-min cooldown")
        notify.critical(TOKEN, _cfg.get('admin_chat_id'),
            "⚠️ Walmart geçici olarak bizi engelliyor (rate-limit).\n\n"
            "Walmart kaynağı 60 dk pas geçilecek, Amazon kaynağı normal çalışıyor.\n"
            "Bot durmuyor, sadece Walmart slot'u atlanıyor.",
            _cfg.get('alert_throttle_per_hour', 5))
        state.setdefault('walmart',{})['cooldown_until'] = (datetime.now() + timedelta(minutes=60)).isoformat()
        save_state(state)
        return
    if status == 'DONE_TODAY':
        # Günlük hedef doldu — sessizce pas geç, alert YOK, walmart.com'a hiç uğranmadı.
        # Cooldown US/Eastern gün dönümüne (00:01 ET) kadar — savings101 buna göre çalışıyor.
        log("WM: günlük hedef (12 post) tamam, yarın ET 00:01'e kadar uyumakta")
        wm_st = state.setdefault('walmart',{})
        wm_st['last_fire_at'] = datetime.now().isoformat(timespec='seconds')
        wm_st['consecutive_fail'] = 0
        from datetime import datetime as _dt, timedelta as _td
        try:
            from zoneinfo import ZoneInfo
            ET = ZoneInfo("America/New_York")
        except Exception:
            from datetime import timezone as _tz
            ET = _tz(_td(hours=-5))
        tomorrow_et = (_dt.now(ET) + _td(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
        # Local'e geri çevir ki dealbot'un mevcut karşılaştırma mantığı (datetime.fromisoformat)
        # doğru çalışsın.
        wm_st['cooldown_until'] = tomorrow_et.astimezone().replace(tzinfo=None).isoformat()
        save_state(state)
        return
    if status != 'READY' or not ready:
        log(f"WM: no eligible candidate this slot (sessiz pas geç)")
        wf = state.setdefault('walmart',{}).setdefault('consecutive_fail', 0)
        state['walmart']['consecutive_fail'] = wf + 1
        # 8 slot * ortalama 30dk = 4 saat ardı ardına aday yoksa anormal
        if state['walmart']['consecutive_fail'] >= 8:
            notify.critical(TOKEN, _cfg.get('admin_chat_id'),
                f"⚠️ Walmart 4+ saat ardı ardına eligible candidate bulamadı.\n"
                f"savings101 down olabilir veya tüm post'lar zaten paylaşıldı. Kontrol et.",
                _cfg.get('alert_throttle_per_hour', 5))
            state['walmart']['consecutive_fail'] = 0
        save_state(state)
        return
    if dry:
        log(f"WM DRY ready: {ready['us_item_id']} {ready['name'][:50]}")
        return
    # FACEBOOK ÖNCE (Walmart bot'unun asıl hedefi — Buffer üzerinden FB sayfasına planla)
    fb_token = _cfg.get('buffer_access_token')
    fb_channel = _cfg.get('fb_channel_id')
    if not fb_token or not fb_channel:
        log("WM: buffer_access_token veya fb_channel_id yok — FB öncelikli, atlanıyor")
        return
    fb_post_id = wm.send_to_facebook(fb_token, fb_channel, ready, log, schedule_mode="shareNow")
    if not fb_post_id:
        log(f"WM FB SEND FAIL {ready['us_item_id']} — Telegram'a da gönderilmiyor")
        notify.critical(TOKEN, _cfg.get('admin_chat_id'),
            f"Walmart {ready['us_item_id']} Buffer/FB'ye gönderilemedi — Telegram da atlandı.",
            _cfg.get('alert_throttle_per_hour', 5))
        return
    log(f"WM SENT FB {ready['us_item_id']} buffer_id={fb_post_id} -{ready['disc_pct']}% off")

    # TELEGRAM (aynası — FB başarılıysa Telegram'a da yolla)
    msg_id = wm.send_to_telegram(TOKEN, CHANNEL, ready, log)
    if msg_id:
        log(f"WM SENT TG (mirror) {ready['us_item_id']} msg_id={msg_id}")
    else:
        log(f"WM TG mirror fail {ready['us_item_id']} (FB başarılı yine de)")

    state['posted'].append({
        'id': ready['post_uid'],
        'asin': '',
        'title_key': title_key(ready['name']),
        'posted_at': datetime.now().isoformat(timespec='seconds'),
        'msg_id': msg_id,
        'fb_post_id': fb_post_id,
        'source': 'walmart',
    })
    state['hashes'][ready['post_uid']] = ready['hash']
    wm_st = state.setdefault('walmart',{})
    wm_st['consecutive_fail'] = 0
    # KRİTİK: last_fire_at burada güncellenir — manuel veya daemon fark etmez
    wm_st['last_fire_at'] = datetime.now().isoformat(timespec='seconds')
    wm_st.pop('next_fire_at', None)  # daemon yeni jittered slot hesaplasın
    save_state(state)


def is_walmart_cooldown(state):
    cd = state.get('walmart',{}).get('cooldown_until')
    if not cd: return False
    try:
        return datetime.fromisoformat(cd) > datetime.now()
    except Exception: return False


# ---------- main loop ----------

def main():
    args = sys.argv[1:]
    dry  = "--dry-run" in args
    once = "--once" in args
    refresh_now = "--refresh" in args

    # Hook: any uncaught exception → log full traceback + admin alert
    def _excepthook(exc_type, exc_value, exc_tb):
        import traceback
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log(f"UNCAUGHT EXCEPTION:\n{tb}")
        try:
            import notify
            notify.critical(TOKEN, _cfg.get('admin_chat_id'),
                f"💥 Beklenmedik hata: {exc_type.__name__}: {str(exc_value)[:200]}",
                _cfg.get('alert_throttle_per_hour', 5))
        except Exception: pass
    sys.excepthook = _excepthook

    state = load_state()
    if refresh_now or not os.path.exists(PRODUCTS_PATH):
        refresh_products(state, force=True)

    sched = _cfg.get('schedule', {})
    strapi_min     = int(sched.get('strapi_minute', 0))
    strapi_cadence = int(sched.get('strapi_cadence_min', 10))  # Amazon: 10 dk (DEĞİŞMEZ)
    walmart_min     = int(sched.get('walmart_minute', 15))
    walmart_cadence = int(sched.get('walmart_cadence_min', 30))  # Walmart: 30 dk @ :15, :45
    log(f"DAEMON START — Strapi every {strapi_cadence}m @:{strapi_min:02d}, "
        f"Walmart every {walmart_cadence}m @:{walmart_min:02d} | dry={dry}")

    last_slot = None  # ('strapi'|'walmart', minute_of_hour) — debounce
    while True:
      try:
        check_for_update()
        prune_state(state)
        now = datetime.now()
        m = now.minute
        # Refresh enable flags from disk every loop (panel can toggle them)
        state_snap = load_state()
        strapi_enabled  = state_snap.get('sources',{}).get('strapi',  {}).get('enabled', True)
        walmart_enabled = state_snap.get('sources',{}).get('walmart', {}).get('enabled', True)

        # ---- Strapi slot (configurable cadence) ----
        if last_slot != ('strapi', m) and (m - strapi_min) % strapi_cadence == 0 and strapi_enabled:
            refresh_products(state)
            try:
                products = json.load(open(PRODUCTS_PATH))
                next_p = None
                for p in products:
                    skip, _ = should_skip(p, state)
                    if not skip: next_p = p; break
                if next_p:
                    log(f"--- Strapi slot @ :{m:02d} ---")
                    result = process_one(next_p, state, dry_run=dry)
                    if result == "DUPLICATE_ABORT":
                        log("BOT EXITED — duplicate hash"); sys.exit(2)
                    last_slot = ('strapi', m)
            except Exception as e:
                log(f"strapi slot err: {e}")

        # ---- Walmart slot (cadence-based — son fire'dan +cadence_min ± 5dk jitter) ----
        import random as _rnd
        wm_state = state.setdefault('walmart', {})
        last_fire_iso = wm_state.get('last_fire_at')
        if last_fire_iso:
            last_fire_dt = datetime.fromisoformat(last_fire_iso)
        else:
            # İlk fire: bugün :walmart_min'e ayarla (geçtiyse +1 saat)
            last_fire_dt = now.replace(minute=walmart_min, second=0, microsecond=0) - timedelta(minutes=walmart_cadence)
        next_fire_iso = wm_state.get('next_fire_at')
        if not next_fire_iso:
            jitter = _rnd.randint(-5, 5)
            next_fire = last_fire_dt + timedelta(minutes=walmart_cadence + jitter)
            wm_state['next_fire_at'] = next_fire.isoformat()
            save_state(state)
            log(f"WM: bir sonraki slot → {next_fire.strftime('%H:%M')} (cadence {walmart_cadence}dk + jitter)")
        else:
            next_fire = datetime.fromisoformat(next_fire_iso)
        is_wm_slot = (last_slot != ('walmart', m) and now >= next_fire)
        if is_wm_slot and walmart_enabled:
            if is_walmart_cooldown(state):
                log(f"WM: cooldown active until {state['walmart']['cooldown_until']}")
            else:
                log(f"--- Walmart slot @ {now.strftime('%H:%M')} ---")
                run_walmart_slot(state, dry=dry)
                last_slot = ('walmart', m)
                # Mark fired + clear next_fire so loop recomputes
                wm_state['last_fire_at'] = now.isoformat()
                wm_state.pop('next_fire_at', None)
                save_state(state)
        elif is_wm_slot and not walmart_enabled:
            log("WM: kaynak panel'den kapatılmış, slot atlandı")
            last_slot = ('walmart', m)
            wm_state['last_fire_at'] = now.isoformat()
            wm_state.pop('next_fire_at', None)
            save_state(state)

        if once:
            log("--once flag set, exiting")
            break

        # Sleep until next minute boundary
        sleep_secs = 60 - now.second
        time.sleep(max(15, sleep_secs))
      except Exception as _loop_err:
        import traceback
        tb = traceback.format_exc()
        log(f"LOOP ERROR (recovered): {_loop_err}\n{tb}")
        try:
            import notify
            notify.critical(TOKEN, _cfg.get('admin_chat_id'),
                f"⚠️ Loop iterasyonunda hata (daemon devam ediyor): {_loop_err}",
                _cfg.get('alert_throttle_per_hour', 5))
        except Exception: pass
        time.sleep(30)  # back off briefly before retrying


if __name__ == "__main__":
    main()
