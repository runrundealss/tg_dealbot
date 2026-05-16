"""
validators.py — 17-step validation gauntlet for Walmart deal posts.

Every step is a guard. Any FAIL → return (False, step_number, reason).
Used by sources/walmart.py before any Telegram/FB post.
"""
import os, re, urllib.request, hashlib
from PIL import Image


def _img_size_ok(path, min_bytes=10_000):
    return os.path.exists(path) and os.path.getsize(path) >= min_bytes


def validate(ctx):
    """
    Run all 17 checks in order. ctx is a dict with keys filled as pipeline progresses:
      ctx['post']         — savings101 post JSON (step 1-3)
      ctx['mavely_url']   — extracted Mavely link (step 3)
      ctx['walmart_url']  — resolved Walmart URL (step 4)
      ctx['us_item_id']   — usItemId (step 5)
      ctx['walmart_html'] — Walmart page HTML (step 6)
      ctx['next_data']    — parsed __NEXT_DATA__ dict (step 6)
      ctx['product']      — product dict from NEXT_DATA (step 7)
      ctx['cur_price']    — current price (step 8)
      ctx['was_price']    — was price (step 9)
      ctx['images']       — list of image URLs (step 10)
      ctx['img_paths']    — list of local file paths (step 11)
      ctx['collage_path'] — final collage path (step 13-15)
      ctx['hash']         — md5 of collage (step 16)
      ctx['posted_hashes']— set of previously-posted hashes (step 16)
      ctx['bot_ok']       — getMe + channel admin check passed (step 17)

    Returns (ok: bool, step_idx: int, reason: str).
    """
    p = ctx
    # 1) savings101 post present and valid
    if not p.get('post'):                       return False, 1, "savings101 post missing"
    if not isinstance(p['post'], dict):         return False, 1, "post not dict"
    if not p['post'].get('content'):            return False, 1, "post has no content"

    # 2) Walmart Deals category tag exists
    terms = p['post'].get('_embedded',{}).get('wp:term',[[]])[0]
    if not any(t.get('name')=='Walmart Deals' for t in terms):
        return False, 2, "not in 'Walmart Deals' category"

    # 3) Mavely link extractable
    if not p.get('mavely_url'):                 return False, 3, "no Mavely link in content"
    if not p['mavely_url'].startswith("https://mavely.app.link/"):
        return False, 3, "mavely_url has wrong host"

    # 4) Mavely resolves to a usable Walmart URL
    if not p.get('walmart_url'):                return False, 4, "Mavely resolve returned empty"
    if 'walmart.com' not in p['walmart_url']:   return False, 4, "resolved URL is not walmart.com"

    # 5) usItemId extractable
    if not p.get('us_item_id'):                 return False, 5, "no usItemId in URL"
    if not re.fullmatch(r"\d{6,15}", str(p['us_item_id'])):
        return False, 5, "usItemId malformed"

    # 6) Walmart fetch + NEXT_DATA OK
    if not p.get('walmart_html'):               return False, 6, "Walmart page fetch failed"
    if '__NEXT_DATA__' not in p['walmart_html']:
        return False, 6, "no __NEXT_DATA__ in Walmart page (blocked?)"
    if not p.get('next_data'):                  return False, 6, "NEXT_DATA parse failed"

    # 7) product object not None
    if not p.get('product'):                    return False, 7, "product is None (404/discontinued)"

    # 8) current price valid
    cur = p.get('cur_price')
    if cur is None or cur <= 0:                 return False, 8, f"current price invalid: {cur}"

    # 9) was price valid (REQUIRED — indirimsiz post yok)
    was = p.get('was_price')
    if was is None or was <= 0:                 return False, 9, "wasPrice is None — skip"
    if was <= cur:                              return False, 9, f"wasPrice {was} not greater than {cur}"

    # 10) at least 3 images
    imgs = p.get('images') or []
    if len(imgs) < 3:                           return False, 10, f"only {len(imgs)} images, need 3"

    # 11) each image downloaded successfully
    for i, path in enumerate(p.get('img_paths') or []):
        if not _img_size_ok(path):              return False, 11, f"img {i+1} download too small / missing"

    # 12) each image opens as valid PNG/JPG
    for i, path in enumerate(p.get('img_paths') or []):
        try:
            with Image.open(path) as im:
                im.verify()
        except Exception as e:
            return False, 12, f"img {i+1} invalid: {e}"

    # 13) collage produced
    if not p.get('collage_path') or not os.path.exists(p['collage_path']):
        return False, 13, "collage file missing"

    # 14) collage has watermark + price card by markers? Check size — full canvas is ~700KB-1.2MB
    sz = os.path.getsize(p['collage_path'])
    if sz < 200_000:                            return False, 14, f"collage suspiciously small: {sz}B"

    # 15) collage is valid PNG
    try:
        with Image.open(p['collage_path']) as im:
            if im.size != (1080, 1350):
                return False, 15, f"collage size wrong: {im.size}"
    except Exception as e:
        return False, 15, f"collage invalid: {e}"

    # 16) hash is unique among previously posted
    if not p.get('hash'):                       return False, 16, "no md5 hash on collage"
    if p['hash'] in (p.get('posted_hashes') or set()):
        return False, 16, "duplicate image hash — already posted"

    # 17) Telegram bot reachable + channel admin (caller should verify before pipeline)
    if not p.get('bot_ok'):                     return False, 17, "Telegram bot health check failed"

    return True, 17, "all checks passed"
