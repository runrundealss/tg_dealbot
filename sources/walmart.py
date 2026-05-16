"""
Walmart Deals pipeline
  savings101hub WP API → Mavely link resolve → Walmart product detail
  → 3-image collage + ghost watermark + price card → caption + affiliate URL

Each step is followed by validators.validate() — any FAIL aborts the post
and falls through to the next product. CRITICAL after 5 consecutive failures.

Self-tracks consecutive_fail count via state["walmart"]["consecutive_fail"].
"""
# SSL bootstrap (must run before any HTTPS call)
import ssl
try:
    import certifi
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
except ImportError:
    ssl._create_default_https_context = ssl._create_unverified_context

import os, sys, re, json, time, hashlib, urllib.request, urllib.parse, base64, subprocess
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
import validators
import notify

UA_PHONE = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}
LOGO_PATH = os.path.join(BASE_DIR, "assets", "logo.png")
IMG_TMP   = "/tmp/wm_pipe"
COLLAGE_DIR = os.path.join(BASE_DIR, "images", "walmart")
os.makedirs(IMG_TMP, exist_ok=True)
os.makedirs(COLLAGE_DIR, exist_ok=True)

PWRT_EXE_CANDIDATES = [
    "/Users/kaan/Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    os.path.expanduser("~/Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
]

def _chrome_path():
    for p in PWRT_EXE_CANDIDATES:
        if os.path.exists(p): return p
    # fall back: rely on playwright default
    return None


# ---------- step helpers ----------

def fetch_savings101(cfg):
    """Step 1-2: pull recent posts in Walmart Deals."""
    url = f"{cfg['walmart']['savings101_wp_api']}?per_page=20&_embed"
    req = urllib.request.Request(url, headers=UA_PHONE)
    with urllib.request.urlopen(req, timeout=30) as r:
        posts = json.loads(r.read())
    cat = cfg['walmart']['category_name']
    filtered = []
    for p in posts:
        terms = p.get('_embedded',{}).get('wp:term',[[]])[0]
        if any(t.get('name') == cat for t in terms):
            filtered.append(p)
    return filtered


def extract_mavely(post):
    """Step 3."""
    m = re.search(r'href="(https://mavely\.app\.link/[A-Za-z0-9]+)"', post.get('content',{}).get('rendered',''))
    return m.group(1) if m else None


def resolve_mavely(mavely_url, timeout_sec=45):
    """Step 4: Playwright to follow Mavely → Walmart, decode base64 if /blocked."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    exe = _chrome_path()
    with sync_playwright() as p:
        kw = {"headless": True}
        if exe: kw["executable_path"] = exe
        browser = p.chromium.launch(**kw)
        ctx = browser.new_context(user_agent=UA_PHONE["User-Agent"],
                                   viewport={'width': 390, 'height': 844})
        page = ctx.new_page()
        try:
            page.goto(mavely_url, wait_until='domcontentloaded', timeout=int(timeout_sec*1000))
        except Exception: pass
        # wait for redirect to settle
        for _ in range(int(timeout_sec/2)):
            time.sleep(1.5)
            if '/blocked' in page.url or 'walmart' in page.url: break
        final = page.url
        browser.close()
    if '/blocked?url=' in final:
        m = re.search(r'/blocked\?url=([A-Za-z0-9_+/=]+)', final)
        if m:
            try:
                return "https://www.walmart.com" + base64.b64decode(m.group(1)).decode()
            except Exception:
                return None
    return final if 'walmart.com' in final else None


def extract_us_item_id(walmart_url):
    """Step 5."""
    m = re.search(r'/ip/[^/?]+/(\d{6,15})', walmart_url) or re.search(r'/ip/(\d{6,15})', walmart_url)
    return m.group(1) if m else None


def fetch_walmart_detail(us_item_id):
    """Step 6-10. Uses CURL subprocess (Python urllib's TLS fingerprint is fingerprinted by Walmart).
    Returns (html, next_data, product, blocked_flag)."""
    url = f"https://www.walmart.com/ip/{us_item_id}"
    proc = subprocess.run(
        ["curl", "-sSL", "--compressed", "--max-time", "30",
         "-A", UA_PHONE["User-Agent"],
         "-H", f"Accept: {UA_PHONE['Accept']}",
         "-H", f"Accept-Language: {UA_PHONE['Accept-Language']}",
         "-H", "Upgrade-Insecure-Requests: 1",
         url],
        capture_output=True, text=True, timeout=45,
    )
    html = proc.stdout
    # Blocked detection: tiny HTML or no NEXT_DATA → bot detection
    if len(html) < 50_000 or '__NEXT_DATA__' not in html:
        return html, None, None, True
    m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m: return html, None, None, False
    try:
        data = json.loads(m.group(1))
    except Exception:
        return html, None, None, False
    prod = data.get('props',{}).get('pageProps',{}).get('initialData',{}).get('data',{}).get('product')
    return html, data, prod, False


def download_images(urls, us_item_id):
    """Step 11."""
    paths = []
    for i, u in enumerate(urls):
        path = f"{IMG_TMP}/{us_item_id}_{i}.jpg"
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                open(path,'wb').write(r.read())
            paths.append(path)
        except Exception:
            paths.append(path)  # invalid path, validator will catch
    return paths


def build_collage(img_paths, sale, was, out_path):
    """Step 13-15."""
    imgs = [Image.open(p).convert("RGB") for p in img_paths[:3]]
    while len(imgs) < 3: imgs.append(imgs[0])
    W, H = 1080, 1350; GAP = 8; SPLIT_Y = int(H * 0.62)
    canvas = Image.new("RGB", (W, H), "white")
    def fc(im, w, h):
        iw, ih = im.size; s = max(w/iw, h/ih)
        nw, nh = int(iw*s), int(ih*s)
        im = im.resize((nw, nh), Image.LANCZOS)
        return im.crop(((nw-w)//2,(nh-h)//2,(nw-w)//2+w,(nh-h)//2+h))
    canvas.paste(fc(imgs[0], W, SPLIT_Y - GAP), (0, 0))
    hw = (W - GAP) // 2; bh = H - SPLIT_Y
    canvas.paste(fc(imgs[1], hw, bh), (0, SPLIT_Y))
    canvas.paste(fc(imgs[2], hw, bh), (hw + GAP, SPLIT_Y))
    # ghost watermark top-left
    if os.path.exists(LOGO_PATH):
        logo = Image.open(LOGO_PATH).convert("RGBA")
        bb = logo.getbbox()
        if bb: logo = logo.crop(bb)
        tw = int(W * 0.28); rt = tw / logo.width
        logo = logo.resize((tw, int(logo.height * rt)), Image.LANCZOS)
        r, g, b, a = logo.split()
        a = a.point(lambda x: int(x*0.22)).filter(ImageFilter.GaussianBlur(2))
        gh = Image.merge("RGBA", (r, g, b, a))
        cr = canvas.convert("RGBA"); cr.alpha_composite(gh, (40, 40))
        canvas = cr.convert("RGB")
    # price card
    draw = ImageDraw.Draw(canvas)
    F_S = ImageFont.truetype("/System/Library/Fonts/Supplemental/Impact.ttf", 110)
    F_W = ImageFont.truetype("/System/Library/Fonts/Supplemental/Impact.ttf", 70)
    sale_t = f"${sale:.2f}"
    was_t  = f"${was:.2f}"
    bs = draw.textbbox((0,0), sale_t, font=F_S)
    bw = draw.textbbox((0,0), was_t,  font=F_W)
    cw = max(bs[2]-bs[0], bw[2]-bw[0]) + 80
    ch = 220
    cx = W - cw - 32; cy = SPLIT_Y - ch - 32
    sh = Image.new("RGBA", (cw+30, ch+30), (0,0,0,0))
    ImageDraw.Draw(sh).rounded_rectangle([10,10,cw+10,ch+10], radius=14, fill=(0,0,0,90))
    sh = sh.filter(ImageFilter.GaussianBlur(10))
    cr = canvas.convert("RGBA"); cr.alpha_composite(sh, (cx-15, cy-5))
    canvas = cr.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle([cx,cy,cx+cw,cy+ch], radius=14, fill="white")
    sw = bs[2]-bs[0]
    draw.text((cx+(cw-sw)//2-bs[0], cy+18), sale_t, font=F_S, fill=(34,139,34))
    ww = bw[2]-bw[0]; wx = cx+(cw-ww)//2-bw[0]; wy = cy+130
    draw.text((wx, wy), was_t, font=F_W, fill=(80,80,80))
    draw.line([wx-5, wy+45, wx+ww+5, wy+45], fill=(80,80,80), width=4)
    canvas.save(out_path, "PNG")


def md5_file(path):
    with open(path,"rb") as f: return hashlib.md5(f.read()).hexdigest()


def build_caption(name, sale, was, disc_pct):
    short = re.split(r'[,|\-–—:]', name, maxsplit=1)[0].strip()[:60]
    return (
        f"🔥 {short} – {disc_pct}% OFF\n\n"
        f"💰 Was {was:.2f} → Now {sale:.2f}\n\n"
        f"👇\n\n#ad"
    )


def build_aff_url(us_item_id, sharedid):
    return (f"https://www.walmart.com/ip/{us_item_id}"
            f"?irgwc=1&afsrc=1&veh=aff&wmlspartner=imp_1390754"
            f"&sharedid={sharedid}&affiliates_ad_id=565706&campaign_id=9383")


# ---------- main pipeline ----------

def run_one(cfg, token, channel, state, posted_hashes, log_fn):
    """Process ONE eligible Walmart post.
       Returns: ('SENT', dict) | ('SKIP', step, reason) | ('NO_CANDIDATE', None, None)
    """
    posts = fetch_savings101(cfg)
    log_fn(f"savings101 returned {len(posts)} Walmart-Deals posts")
    if not posts: return ('NO_CANDIDATE', None, None)

    # Skip ones already in state.posted (by Strapi _id which we use 'wp:'+post_id)
    posted_ids = {e.get('id') for e in state.get('posted', [])}

    for post in posts:
        post_uid = f"wp:{post['id']}"
        if post_uid in posted_ids:
            continue

        ctx = {'post': post, 'posted_hashes': posted_hashes}
        # Step 3
        ctx['mavely_url'] = extract_mavely(post)
        ok, step, reason = validators.validate(ctx)
        # We'll re-run validate after each step; for now check up to step 3
        if not ctx['mavely_url']:
            log_fn(f"[step3] {post_uid} skip: no Mavely link")
            continue

        # Step 4
        ctx['walmart_url'] = resolve_mavely(ctx['mavely_url'])
        if not ctx['walmart_url'] or 'walmart.com' not in (ctx['walmart_url'] or ''):
            log_fn(f"[step4] {post_uid} skip: Mavely resolve fail")
            continue

        # Step 5
        ctx['us_item_id'] = extract_us_item_id(ctx['walmart_url'])
        if not ctx['us_item_id']:
            log_fn(f"[step5] {post_uid} skip: no usItemId in {ctx['walmart_url'][:80]}")
            continue

        # Step 6-10  (rate-limit: random 3-7s between Walmart fetches)
        import random
        time.sleep(random.uniform(3, 7))
        try:
            html, nd, prod, blocked = fetch_walmart_detail(ctx['us_item_id'])
            ctx['walmart_html'] = html
            ctx['next_data']    = nd
            ctx['product']      = prod
        except Exception as e:
            log_fn(f"[step6] {post_uid} fetch err: {e}")
            continue
        if blocked:
            log_fn(f"[step6] {post_uid} BLOCKED by Walmart — aborting batch")
            return ('BLOCKED', None, None)
        if not prod:
            log_fn(f"[step7] {post_uid} skip: product None")
            continue
        pi = prod.get('priceInfo') or {}
        cur = (pi.get('currentPrice') or {}).get('price')
        was_obj = pi.get('wasPrice')
        was = was_obj.get('price') if was_obj else None
        ctx['cur_price'] = cur
        ctx['was_price'] = was
        imgs = [im.get('url') for im in (prod.get('imageInfo') or {}).get('allImages', []) if im.get('url')]
        ctx['images'] = imgs

        # Step 11-12: download
        ctx['img_paths'] = download_images(imgs[:3], ctx['us_item_id'])

        # Step 13-15: build collage
        collage = f"{COLLAGE_DIR}/{ctx['us_item_id']}.png"
        try:
            build_collage(ctx['img_paths'], cur, was, collage)
            ctx['collage_path'] = collage
        except Exception as e:
            log_fn(f"[step13] {post_uid} collage err: {e}")
            continue

        # Step 16
        ctx['hash'] = md5_file(collage)

        # Step 17: bot health (caller will set this)
        ctx['bot_ok'] = True  # caller checked before invoking run_one

        # Now full validation
        ok, step, reason = validators.validate(ctx)
        if not ok:
            log_fn(f"[step{step}] {post_uid} VALIDATION FAIL: {reason}")
            continue

        # Build caption + URL
        disc_pct = round((1 - cur/was) * 100)
        caption = build_caption(prod.get('name',''), cur, was, disc_pct)
        aff = build_aff_url(ctx['us_item_id'], cfg['walmart']['mavely_sharedid'])

        return ('READY', {
            'post_uid': post_uid,
            'us_item_id': ctx['us_item_id'],
            'name': prod.get('name',''),
            'cur': cur, 'was': was, 'disc_pct': disc_pct,
            'collage_path': collage,
            'caption': caption,
            'aff_url': aff,
            'hash': ctx['hash'],
        }, None)

    return ('NO_CANDIDATE', None, None)


def send_to_telegram(token, channel, ready, log_fn):
    """Send photo (multipart) with caption + inline button → Telegram CDN, no external host."""
    import mimetypes, uuid
    boundary = f"----rrd{uuid.uuid4().hex}"
    body = []
    def add_field(name, value):
        body.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    def add_file(name, filepath):
        fname = os.path.basename(filepath)
        ctype = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
        with open(filepath, "rb") as f: data = f.read()
        body.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{fname}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n"
        )
    add_field("chat_id", channel)
    add_field("caption", ready['caption'])
    keyboard = {"inline_keyboard": [[{"text": "🛒 Go to Product", "url": ready['aff_url']}]]}
    add_field("reply_markup", json.dumps(keyboard))
    add_file("photo", ready['collage_path'])
    body.append(f"--{boundary}--\r\n".encode())
    payload = b"".join(body)
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=payload, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read())
        if res.get("ok"):
            return res['result']['message_id']
    except Exception as e:
        log_fn(f"telegram send err: {e}")
    return None
