#!/usr/bin/env python3
"""
Luuln Bot — Privacy-First Edition (Render)
Full data → DM | Group → masked + auto-delete | Zero leak
"""

import os
import asyncio, concurrent.futures, csv, io, json, logging, random, re, sqlite3
import string, threading, time, zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import PurePath

from dotenv import load_dotenv
load_dotenv()

import httpx
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, ContextTypes, filters)

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ALLOWED_GROUP = os.getenv("ALLOWED_GROUP", "engtopwin1").strip()
GROUP_NOTICE_TTL = int(os.getenv("GROUP_NOTICE_TTL", "60"))
MAX_CARDS = 10000
TXT_THRESHOLD = 10
BIN_TIMEOUT = 8.0
MAX_FILE_PROXIES = 10000
PORT = int(os.getenv("PORT", "8080"))
POOL = ThreadPoolExecutor(max_workers=32)

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
# Silence noisy libs (getUpdates spam, werkzeug request spam, urllib3 retries)
for _noisy in ("httpx", "httpcore", "telegram", "telegram.ext",
               "werkzeug", "urllib3", "urllib3.connectionpool"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
log = logging.getLogger("luuln")

BOT_USERNAME = None
BIN_CACHE = {}
RATE = {}
DB = os.getenv("DB_PATH", "luuln.db")

SUB_TIERS = {
    "free":  {"rate": 10,   "max": 1000,  "label": "Free",  "emoji": "🆓"},
    "trial": {"rate": 30,   "max": 2000,  "label": "Trial", "emoji": "🎁"},
    "pro":   {"rate": 100,  "max": 5000,  "label": "Pro",   "emoji": "⭐"},
    "vip":   {"rate": 9999, "max": 10000, "label": "VIP",   "emoji": "👑"},
}
SUB_DURATIONS = {"1d":1,"7d":7,"30d":30,"90d":90,"180d":180,"365d":365,"life":36500}
SUB_EMOJI = {"free":"🆓","trial":"🎁","pro":"⭐","vip":"👑"}
USER_TIERS = {}
ALL_USERS = set()

# ═══════════════════════════════════════════════════════════════════
# HEALTH SERVER
# ═══════════════════════════════════════════════════════════════════
health_app = Flask(__name__)

@health_app.route("/")
def _index():
    bot = BOT_USERNAME or "starting"
    now = datetime.utcnow().isoformat() + "Z"
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Luuln Bot</title></head>"
        "<body style='font-family:system-ui,sans-serif;background:#0d1117;"
        "color:#c9d1d9;display:flex;align-items:center;justify-content:center;"
        "min-height:100vh;margin:0'><div style='text-align:center;padding:24px'>"
        "<h1 style='color:#58a6ff;margin:0 0 16px'>✅ Luuln Bot is Online</h1>"
        f"<p style='margin:6px 0'>Bot: <code>@{bot}</code></p>"
        f"<p style='margin:6px 0'>Time: <code>{now}</code></p>"
        "<p style='color:#3fb950;font-weight:bold;margin-top:16px'>Status: running</p>"
        "<p style='color:#8b949e;margin-top:24px;font-size:13px'>"
        "Ping: <code>/ping</code> · Health: <code>/health</code></p>"
        "</div></body></html>"
    )
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}

@health_app.route("/health")
def _health():
    return jsonify({"status": "ok", "bot": BOT_USERNAME or "starting",
                    "time": datetime.utcnow().isoformat() + "Z"}), 200

@health_app.route("/ping")
@health_app.route("/cron")
def _ping():
    return "ok", 200

def _run_health():
    log.info(f"🌐 Health server on :{PORT}")
    health_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)

# ═══════════════════════════════════════════════════════════════════
# USAGE HELPER
# ═══════════════════════════════════════════════════════════════════
def usage(*lines):
    """Build a copyable usage block. Each line already has formatting."""
    body = "\n".join(lines)
    return f"❓ <b>Usage</b>\n{body}"

async def reply_usage(update, *lines):
    await update.message.reply_text(usage(*lines), parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════
# PRIVACY HELPERS
# ═══════════════════════════════════════════════════════════════════
def mask_card_full(card):
    if not card: return "████ ████ ████ ████"
    groups = (len(card) + 3) // 4
    return " ".join(["█" * 4] * groups)

def mask_card_partial(card):
    if len(card) <= 8: return "█" * len(card)
    return card[:4] + " " + "*" * (len(card) - 8) + " " + card[-4:]

def mask_proxy_partial(proxy_str):
    try:
        ip, port = proxy_str.rsplit(":", 1)
        parts = ip.split(".")
        if len(parts) == 4:
            port_masked = port[:2] + "█" * max(0, len(port) - 2)
            return f"{parts[0]}.**.**.{parts[3]}:{port_masked}"
    except Exception:
        pass
    return "█" * min(len(proxy_str), 20)

def mask_bin(bp):
    if not bp: return "••••••"
    return "•" * len(bp)

async def auto_delete_message(msg, delay=None):
    if delay is None:
        delay = GROUP_NOTICE_TTL
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass

def _user_label(user):
    if user.username:
        return f"@{user.username}"
    return user.first_name or "User"

async def send_group_notice(update, ctx, title, lines, extra_buttons=None):
    if not in_group(update):
        return None
    user = update.effective_user
    text = (
        f"┌───────────────────────────────┐\n"
        f"│   {title}\n"
        f"└───────────────────────────────┘\n\n"
        f"👤 By: {_user_label(user)}\n"
    )
    for line in lines:
        text += f"{line}\n"
    text += f"🕐 {datetime.utcnow().strftime('%H:%M:%S')}\n\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n"
    text += "🔒 Full data sent to your DM\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━"
    buttons = list(extra_buttons or [])
    buttons.append([InlineKeyboardButton("📩 Open DM",
        url=f"https://t.me/{BOT_USERNAME or 'your_bot'}?start=1")])
    try:
        msg = await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
        asyncio.create_task(auto_delete_message(msg))
        return msg
    except Exception as e:
        log.warning(f"group notice failed: {e}")
        return None

# ═══════════════════════════════════════════════════════════════════
# PROGRESS HELPERS
# ═══════════════════════════════════════════════════════════════════
def _bar(done, total, width=20):
    if total <= 0: return "░" * width
    pct = min(done / total, 1.0)
    return "█" * int(pct * width) + "░" * (width - int(pct * width))

def _pct(done, total):
    if total <= 0: return "0%"
    return f"{int(done / total * 100)}%"

def _fmt_eta(seconds):
    seconds = int(max(0, seconds))
    if seconds < 60: return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60: return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"

def _progress_block(title, done, total, extra=None):
    lines = [f"<b>{title}</b>", "", f"<code>{_bar(done, total)}</code> <b>{_pct(done, total)}</b>",
             f"📊 <code>{done:,}</code> / <code>{total:,}</code>"]
    if extra: lines.append(""); lines.extend(extra)
    return "\n".join(lines)

async def _safe_edit(msg_obj, text, reply_markup=None):
    try:
        await msg_obj.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════
# GUARDS
# ═══════════════════════════════════════════════════════════════════
def is_admin(update):
    return update.effective_user is not None and update.effective_user.id == ADMIN_ID

def admin_dm_required(update):
    return is_admin(update) and update.effective_chat.type == "private"

# ═══════════════════════════════════════════════════════════════════
# DB
# ═══════════════════════════════════════════════════════════════════
def db_init():
    c = sqlite3.connect(DB)
    for sql in [
        "CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, bin TEXT, count INTEGER, mode TEXT, created_at TEXT)",
        "CREATE TABLE IF NOT EXISTS favorites (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, bin TEXT, label TEXT, created_at TEXT)",
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, tier TEXT DEFAULT 'free', first_seen TEXT)",
        "CREATE TABLE IF NOT EXISTS subscriptions (user_id INTEGER PRIMARY KEY, tier TEXT, expires_at TEXT, started_at TEXT, by_admin INTEGER)",
        "CREATE TABLE IF NOT EXISTS codes (code TEXT PRIMARY KEY, tier TEXT, days INTEGER, used_by INTEGER, used_at TEXT, created_at TEXT, created_by INTEGER)",
        "CREATE TABLE IF NOT EXISTS bans (user_id INTEGER PRIMARY KEY, reason TEXT, banned_by INTEGER, banned_at TEXT)",
        "CREATE TABLE IF NOT EXISTS referrals (inviter INTEGER, invitee INTEGER PRIMARY KEY, rewarded INTEGER DEFAULT 0, created_at TEXT)",
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE IF NOT EXISTS proxy_cache (user_id INTEGER PRIMARY KEY, proxies TEXT, updated_at TEXT)",
    ]:
        c.execute(sql)
    c.commit(); c.close()

def db_log(uid, un, bp, cnt, mode):
    try:
        c = sqlite3.connect(DB)
        c.execute("INSERT INTO requests (user_id, username, bin, count, mode, created_at) VALUES (?,?,?,?,?,?)",
                  (uid, un or "", bp, cnt, mode, datetime.utcnow().isoformat()))
        c.commit(); c.close()
    except Exception: pass

def db_user_track(uid, un, fn, ref_by=None):
    try:
        c = sqlite3.connect(DB)
        if not c.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone():
            c.execute("INSERT INTO users (id, username, first_name, tier, first_seen) VALUES (?,?,?,'free',?)",
                      (uid, un or "", fn or "", datetime.utcnow().isoformat()))
            if ref_by and ref_by != uid:
                try: c.execute("INSERT OR IGNORE INTO referrals (inviter, invitee, created_at) VALUES (?,?,?)",
                               (ref_by, uid, datetime.utcnow().isoformat()))
                except Exception: pass
        else:
            c.execute("UPDATE users SET username=?, first_name=? WHERE id=?", (un or "", fn or "", uid))
        c.commit(); c.close()
        ALL_USERS.add(uid)
    except Exception: pass

def db_stats(uid=None):
    c = sqlite3.connect(DB)
    q = "SELECT COUNT(*), COALESCE(SUM(count),0) FROM requests"
    r = c.execute(q + (" WHERE user_id=?" if uid else ""), (uid,) if uid else ()).fetchone()
    c.close(); return r

def db_top_users(limit=10):
    c = sqlite3.connect(DB)
    r = c.execute("SELECT user_id, username, COUNT(*), SUM(count) FROM requests GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT ?", (limit,)).fetchall()
    c.close(); return r

def db_all_users():
    c = sqlite3.connect(DB)
    r = c.execute("SELECT id, tier FROM users").fetchall()
    c.close(); return r

def db_fav_add(uid, bp, lb):
    c = sqlite3.connect(DB)
    c.execute("INSERT INTO favorites (user_id, bin, label, created_at) VALUES (?,?,?,?)",
              (uid, bp, lb, datetime.utcnow().isoformat()))
    c.commit(); c.close()

def db_fav_list(uid):
    c = sqlite3.connect(DB)
    r = c.execute("SELECT bin, label FROM favorites WHERE user_id=? ORDER BY id DESC LIMIT 20", (uid,)).fetchall()
    c.close(); return r

def db_get_sub(uid):
    try:
        c = sqlite3.connect(DB)
        r = c.execute("SELECT tier, expires_at FROM subscriptions WHERE user_id=?", (uid,)).fetchone()
        c.close()
        if not r: return None, None
        tier, exp = r
        if exp and datetime.utcnow() > datetime.fromisoformat(exp): return None, None
        return tier, exp
    except Exception: return None, None

def db_set_sub(uid, tier, days, by_admin=0):
    try:
        now = datetime.utcnow(); exp = (now + timedelta(days=days)).isoformat()
        c = sqlite3.connect(DB)
        c.execute("INSERT OR REPLACE INTO subscriptions (user_id, tier, expires_at, started_at, by_admin) VALUES (?,?,?,?,?)",
                  (uid, tier, exp, now.isoformat(), by_admin))
        c.commit(); c.close(); return True
    except Exception: return False

def db_gen_code(tier, days, by_admin):
    code = "-".join(["".join(random.choices(string.ascii_uppercase + string.digits, k=4)) for _ in range(3)])
    try:
        c = sqlite3.connect(DB)
        c.execute("INSERT INTO codes (code, tier, days, created_at, created_by) VALUES (?,?,?,?,?)",
                  (code, tier, days, datetime.utcnow().isoformat(), by_admin))
        c.commit(); c.close(); return code
    except Exception: return None

def db_gen_bulk(tier, days, n, by_admin):
    out = []
    for _ in range(n):
        c = db_gen_code(tier, days, by_admin)
        if c: out.append(c)
    return out

def db_redeem(code, uid):
    try:
        c = sqlite3.connect(DB)
        r = c.execute("SELECT tier, days, used_by FROM codes WHERE code=?", (code,)).fetchone()
        if not r: c.close(); return False, "Code not found"
        tier, days, used_by = r
        if used_by: c.close(); return False, "Code already used"
        c.execute("UPDATE codes SET used_by=?, used_at=? WHERE code=?",
                  (uid, datetime.utcnow().isoformat(), code))
        c.commit(); c.close()
        db_set_sub(uid, tier, days, by_admin=0)
        return True, (tier, days)
    except Exception: return False, "Error"

def db_list_codes(limit=20):
    try:
        c = sqlite3.connect(DB)
        r = c.execute("SELECT code, tier, days, used_by FROM codes ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        c.close(); return r
    except Exception: return []

def db_list_subs(limit=20):
    try:
        c = sqlite3.connect(DB)
        r = c.execute("SELECT user_id, tier, expires_at FROM subscriptions ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        c.close(); return r
    except Exception: return []

def db_ban(uid, reason, by):
    try:
        c = sqlite3.connect(DB)
        c.execute("INSERT OR REPLACE INTO bans (user_id, reason, banned_by, banned_at) VALUES (?,?,?,?)",
                  (uid, reason, by, datetime.utcnow().isoformat()))
        c.commit(); c.close(); return True
    except Exception: return False

def db_unban(uid):
    try:
        c = sqlite3.connect(DB)
        c.execute("DELETE FROM bans WHERE user_id=?", (uid,))
        c.commit(); c.close(); return True
    except Exception: return False

def db_is_banned(uid):
    try:
        c = sqlite3.connect(DB)
        r = c.execute("SELECT 1 FROM bans WHERE user_id=?", (uid,)).fetchone()
        c.close(); return bool(r)
    except Exception: return False

def db_ban_list(limit=30):
    try:
        c = sqlite3.connect(DB)
        r = c.execute("SELECT user_id, reason, banned_at FROM bans ORDER BY banned_at DESC LIMIT ?", (limit,)).fetchall()
        c.close(); return r
    except Exception: return []

def db_setting_get(key, default=None):
    try:
        c = sqlite3.connect(DB)
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        c.close(); return r[0] if r else default
    except Exception: return default

def db_setting_set(key, value):
    try:
        c = sqlite3.connect(DB)
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))
        c.commit(); c.close(); return True
    except Exception: return False

def db_referral_count(uid):
    try:
        c = sqlite3.connect(DB)
        r = c.execute("SELECT COUNT(*) FROM referrals WHERE inviter=?", (uid,)).fetchone()
        c.close(); return r[0] if r else 0
    except Exception: return 0

def get_effective_tier(uid):
    if uid == ADMIN_ID: return "vip"
    tier, _ = db_get_sub(uid)
    return tier or "free"

def check_rate(uid):
    tier = get_effective_tier(uid); cfg = SUB_TIERS[tier]
    now = time.time()
    RATE.setdefault(uid, [])
    RATE[uid] = [t for t in RATE[uid] if now - t < 3600]
    if len(RATE[uid]) >= cfg["rate"]: return False, 0, tier, cfg["max"]
    RATE[uid].append(now)
    return True, cfg["rate"] - len(RATE[uid]), tier, cfg["max"]

# ═══════════════════════════════════════════════════════════════════
# LUHN
# ═══════════════════════════════════════════════════════════════════
BRANDS = [
    (["4"], [13,16,19], 16, "VISA"),
    (["51","52","53","54","55"], [16], 16, "MASTERCARD"),
    (["2221","2222","2223","2224","2225","2226","2227","2228","2229"], [16], 16, "MASTERCARD"),
    (["223","224","225","226","227","228","229"], [16], 16, "MASTERCARD"),
    (["23","24","25","26","270","271","2720"], [16], 16, "MASTERCARD"),
    (["34","37"], [15], 15, "AMEX"),
    (["6011","65","644","645","646","647","648","649"], [16,19], 16, "DISCOVER"),
    (["3528","3529","353","354","355","356","357","358"], [16,17,18,19], 16, "JCB"),
    (["300","301","302","303","304","305","3095","36","38","39"], [14,16], 14, "DINERS"),
    (["62"], [16,17,18,19], 16, "UNIONPAY"),
]

def detect_brand(bp):
    for ps, ls, dl, b in BRANDS:
        for p in ps:
            if bp.startswith(p): return b, ls, dl
    return "UNKNOWN", [], 0

def validate_bin(bp):
    if not bp or not bp.isdigit(): return False, "invalid"
    if not (4 <= len(bp) <= 19): return False, "length"
    brand, lens, dl = detect_brand(bp[:8])
    if brand == "UNKNOWN": return False, "unknown brand"
    return True, {"brand": brand, "lengths": lens, "default_length": dl}

def luhn_check(c):
    ds = [int(d) for d in c if d.isdigit()]
    if not ds: return False
    s = 0
    for i, d in enumerate(reversed(ds)):
        if i % 2 == 1:
            d *= 2
            if d > 9: d -= 9
        s += d
    return s % 10 == 0

def luhn_digit(p):
    s = 0
    for i, d in enumerate(reversed([int(x) for x in p])):
        if i % 2 == 0:
            d *= 2
            if d > 9: d -= 9
        s += d
    return (10 - (s % 10)) % 10

def gen_from_bin(bp, tl):
    ml = tl - len(bp) - 1
    if ml < 0: raise ValueError("BIN too long")
    b = bp + "".join(str(random.randint(0,9)) for _ in range(ml))
    return b + str(luhn_digit(b))

def complete_partial(p, tl):
    n = len(p)
    if tl < n: b = p[:tl-1]; return b + str(luhn_digit(b))
    if tl == n: b = p[:-1]; return b + str(luhn_digit(b))
    if tl == n + 1: return p + str(luhn_digit(p))
    b = p + "".join(str(random.randint(0,9)) for _ in range(tl - n - 1))
    return b + str(luhn_digit(b))

def generate_cards(bp, count, tl, partial=None):
    out, seen, g = [], set(), 0
    while len(out) < count and g < count * 200:
        g += 1
        c = complete_partial(partial, tl) if partial else gen_from_bin(bp, tl)
        if c not in seen: seen.add(c); out.append(c)
    return out

def rand_exp(): return f"{random.randint(1,12):02d}", f"{random.randint(26,32):02d}"
def rand_cvv(b, fixed=None):
    if fixed is not None: return fixed
    return f"{random.randint(1000,9999)}" if b == "AMEX" else f"{random.randint(100,999)}"

async def lookup_bin(bp):
    k = bp[:8]
    if k in BIN_CACHE: return BIN_CACHE[k]
    for plen in (8, 6, 4):
        if len(bp) < plen: continue
        try:
            async with httpx.AsyncClient(timeout=BIN_TIMEOUT, headers={"Accept-Version":"3","User-Agent":"curl/8.5.0"}) as c:
                r = await c.get(f"https://lookup.binlist.net/{bp[:plen]}")
        except Exception: continue
        if r.status_code == 429: return {"error": "rate_limit"}
        if r.status_code != 200: continue
        try: d = r.json()
        except Exception: continue
        cd = d.get("country") or {}; bd = d.get("bank") or {}
        out = {"scheme": (d.get("scheme") or "—").upper(), "type": (d.get("type") or "—").upper(),
               "brand": d.get("brand") or "—", "bank": bd.get("name") or "—",
               "country": cd.get("name") or "—", "country_code": (cd.get("alpha2") or "").upper() or None,
               "emoji": cd.get("emoji") or "", "length": (d.get("number") or {}).get("length")}
        BIN_CACHE[k] = out; return out
    BIN_CACHE[k] = None
    return None

# ═══════════════════════════════════════════════════════════════════
# PROXY SCRAPER
# ═══════════════════════════════════════════════════════════════════
PROXY_SOURCES = [
    'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',
    'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/proxy.txt',
    'https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt',
    'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt',
    'https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all',
    'https://www.proxy-list.download/api/v1/get?type=http',
    'https://www.sslproxies.org/',
    'https://free-proxy-list.net/',
    'https://www.us-proxy.org/',
]
PROXY_TEST_EP = [
    ('http://checkip.amazonaws.com/', 'text'),
    ('https://api.ipify.org/', 'text'),
    ('http://icanhazip.com/', 'text'),
    ('http://httpbin.org/ip', 'json'),
]
PROXY_UA = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
]
PROXY_STATE = {}

def _pstate(uid):
    if uid not in PROXY_STATE:
        PROXY_STATE[uid] = {
            "running": False, "stop_flag": False, "retesting": False, "file_mode": False,
            "valid": [],
            "stats": {"collected": 0, "valid": 0, "rejected": 0, "sources_ok": 0, "sources_fail": 0},
            "last_run": None,
        }
    return PROXY_STATE[uid]

def _p_ip_ok(ip):
    if not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', ip): return False
    return all(0 <= int(p) <= 255 for p in ip.split('.'))

def _save_proxy_live(uid, proxy_str):
    try:
        c = sqlite3.connect(DB)
        r = c.execute("SELECT proxies FROM proxy_cache WHERE user_id=?", (uid,)).fetchone()
        existing = (r[0] + "\n" + proxy_str) if r and r[0] else proxy_str
        c.execute("INSERT OR REPLACE INTO proxy_cache (user_id, proxies, updated_at) VALUES (?,?,?)",
                  (uid, existing, datetime.utcnow().isoformat()))
        c.commit(); c.close()
    except Exception as e:
        log.warning(f"save_proxy_live: {e}")

def _p_scrape_all():
    proxies = set(); stats = {"sources_ok": 0, "sources_fail": 0}
    headers = {'User-Agent': random.choice(PROXY_UA)}
    for src in PROXY_SOURCES:
        try:
            r = requests.get(src, headers=headers, timeout=15, verify=False)
            if r.status_code != 200:
                stats["sources_fail"] += 1; continue
            if any(x in src for x in ('raw.githubusercontent', 'proxyscrape', 'proxy-list.download')):
                for line in r.text.splitlines():
                    line = line.strip()
                    if ':' in line:
                        parts = line.split(':')
                        if len(parts) >= 2:
                            ip, port = parts[0].strip(), parts[1].strip()
                            if _p_ip_ok(ip) and port.isdigit():
                                proxies.add(f"{ip}:{port}")
            else:
                soup = BeautifulSoup(r.text, 'html.parser')
                for row in soup.find_all('tr'):
                    cols = row.find_all('td')
                    if len(cols) >= 2:
                        ip, port = cols[0].text.strip(), cols[1].text.strip()
                        if _p_ip_ok(ip) and port.isdigit():
                            proxies.add(f"{ip}:{port}")
            stats["sources_ok"] += 1
        except Exception:
            stats["sources_fail"] += 1
    return list(proxies), stats

def _p_test_one(proxy_str):
    try:
        pr = {'http': f'http://{proxy_str}', 'https': f'http://{proxy_str}'}
        h = {'User-Agent': random.choice(PROXY_UA), 'Connection': 'close'}
        for url, _ in PROXY_TEST_EP:
            try:
                r = requests.get(url, proxies=pr, timeout=6, headers=h, verify=False)
                if r.status_code == 200: return True
            except Exception: continue
        return False
    except Exception: return False

def _p_test_detailed(proxy_str):
    results = []; ok_count = 0; fastest = None; detected_ip = None
    try:
        pr = {'http': f'http://{proxy_str}', 'https': f'http://{proxy_str}'}
        h = {'User-Agent': random.choice(PROXY_UA), 'Connection': 'close'}
        for url, kind in PROXY_TEST_EP:
            start = time.time()
            try:
                r = requests.get(url, proxies=pr, timeout=8, headers=h, verify=False)
                latency = int((time.time() - start) * 1000)
                if r.status_code == 200:
                    ok_count += 1
                    if fastest is None or latency < fastest: fastest = latency
                    if not detected_ip:
                        try:
                            if kind == 'json':
                                data = r.json()
                                detected_ip = data.get('origin') or data.get('ip') or str(data)[:40]
                            else:
                                detected_ip = r.text.strip()[:40]
                        except Exception: pass
                    results.append((url.split('//')[1].split('/')[0], True, latency))
                else:
                    results.append((url.split('//')[1].split('/')[0], False, latency))
            except Exception:
                results.append((url.split('//')[1].split('/')[0], False, None))
        return {"proxy": proxy_str, "alive": ok_count > 0, "working": ok_count,
                "total": len(PROXY_TEST_EP), "latency": fastest,
                "detected_ip": detected_ip, "results": results}
    except Exception as e:
        return {"proxy": proxy_str, "alive": False, "error": str(e),
                "working": 0, "total": len(PROXY_TEST_EP), "results": []}

def _parse_proxy_file(content):
    out = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('#'): continue
        m = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})[:\s,|]+(\d{1,5})', line)
        if m:
            ip, port = m.group(1), m.group(2)
            if _p_ip_ok(ip) and (1 <= int(port) <= 65535):
                out.append(f"{ip}:{port}")
    return out

async def _do_scrape_live(ctx, uid, msg_obj, workers=20):
    st = _pstate(uid)
    st["running"] = True; st["stop_flag"] = False; st["retesting"] = False; st["file_mode"] = False
    st["last_run"] = datetime.utcnow().isoformat(); st["valid"] = []
    try:
        await _safe_edit(msg_obj,
            f"🕸️ <b>Phase 1/2: Collecting</b>\n\n<code>{_bar(0, 9, 20)}</code> 0%\n\n<i>Fetching...</i>",
            reply_markup=kb_scrape_running())
        all_p, src_stats = await asyncio.get_event_loop().run_in_executor(POOL, _p_scrape_all)
        if not all_p:
            await _safe_edit(msg_obj,
                f"❌ <b>No proxies</b>\nOK: {src_stats['sources_ok']} · Fail: {src_stats['sources_fail']}",
                reply_markup=kb_proxy_menu()); return
        total = len(all_p)
        st["stats"] = {"collected": total, "valid": 0, "rejected": 0,
                       "sources_ok": src_stats["sources_ok"], "sources_fail": src_stats["sources_fail"]}
        progress = {"done": 0, "valid": 0, "rejected": 0, "last_5": []}
        lock = threading.Lock()

        def test_with_callback(proxy_str):
            if st["stop_flag"]: return None
            ok = _p_test_one(proxy_str)
            with lock:
                progress["done"] += 1
                if ok:
                    progress["valid"] += 1
                    st["valid"].append(proxy_str)
                    _save_proxy_live(uid, proxy_str)
                    progress["last_5"].append(proxy_str)
                    if len(progress["last_5"]) > 5: progress["last_5"].pop(0)
                else:
                    progress["rejected"] += 1
            return ok

        start = time.time()
        def run_batch():
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                futures = [ex.submit(test_with_callback, p) for p in all_p]
                for f in concurrent.futures.as_completed(futures):
                    try: f.result()
                    except Exception: pass

        batch_future = asyncio.get_event_loop().run_in_executor(POOL, run_batch)
        while not batch_future.done():
            await asyncio.sleep(1.5)
            if batch_future.done(): break
            if st["stop_flag"]: break
            done = progress["done"]
            if done == 0: continue
            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 0
            eta = (total - done) / rate if rate > 0 else 0
            recent = progress["last_5"][-3:] if progress["last_5"] else []
            recent_txt = "\n".join(f"  ✅ <code>{mask_proxy_partial(p)}</code>" for p in recent) if recent else "  <i>waiting...</i>"
            await _safe_edit(msg_obj,
                f"🕸️ <b>Phase 2/2: Testing</b>\n\n"
                f"<code>{_bar(done, total)}</code> <b>{_pct(done, total)}</b>\n"
                f"📊 <code>{done:,}</code> / <code>{total:,}</code>\n\n"
                f"✅ Valid: <b>{progress['valid']}</b>\n"
                f"❌ Rejected: {progress['rejected']}\n"
                f"⚡ <code>{rate:.1f}/s</code>\n"
                f"⏱️ ETA: <b>{_fmt_eta(eta)}</b>\n\n"
                f"<b>Latest:</b>\n{recent_txt}",
                reply_markup=kb_scrape_running())
        await batch_future
        elapsed = time.time() - start
        was_stopped = st["stop_flag"]
        valid = st["valid"]
        st["stats"]["valid"] = len(valid)
        st["stats"]["rejected"] = progress["done"] - len(valid)
        header = "⏹️ <b>Stopped</b>" if was_stopped else "✅ <b>Complete</b>"
        if valid:
            buf = io.BytesIO("\n".join(valid).encode())
            buf.name = f"proxies_{len(valid)}.txt"; buf.seek(0)
            try:
                await ctx.bot.send_document(uid, document=InputFile(buf, filename=buf.name),
                    caption=(f"{header}\n\nTested: {progress['done']:,}/{total:,}\n"
                             f"✅ Valid: <b>{len(valid)}</b>\n"
                             f"❌ Rejected: {progress['rejected']}\n"
                             f"⏱️ {_fmt_eta(elapsed)}"),
                    parse_mode="HTML")
            except Exception: pass
            await _safe_edit(msg_obj,
                f"{header}\n\n✅ Valid: <b>{len(valid)}</b>\n"
                f"❌ Rejected: {progress['rejected']}\n"
                f"⏱️ {_fmt_eta(elapsed)}\n\n📄 File sent ☝️",
                reply_markup=kb_proxy_after())
        else:
            await _safe_edit(msg_obj,
                f"{header}\n\nNo valid proxies\nTested: {progress['done']:,}",
                reply_markup=kb_proxy_menu())
    except Exception as e:
        log.exception("scrape")
        await _safe_edit(msg_obj, f"❌ {e}", reply_markup=kb_proxy_menu())
    finally:
        st["running"] = False; st["stop_flag"] = False

async def _do_retest(ctx, uid, msg_obj, workers=20):
    st = _pstate(uid)
    st["running"] = True; st["retesting"] = True; st["stop_flag"] = False; st["file_mode"] = False
    st["last_run"] = datetime.utcnow().isoformat()
    existing = list(st["valid"])
    if not existing:
        try:
            c = sqlite3.connect(DB)
            r = c.execute("SELECT proxies FROM proxy_cache WHERE user_id=?", (uid,)).fetchone()
            c.close()
            if r and r[0]: existing = [p for p in r[0].split("\n") if p]
        except Exception: pass
    if not existing:
        await _safe_edit(msg_obj, "❌ <b>No saved proxies</b>\n\nRun /chkip first.",
                           reply_markup=kb_proxy_menu())
        st["running"] = False; st["retesting"] = False; return
    total = len(existing)
    alive = []
    progress = {"done": 0, "alive": 0, "dead": 0, "last_5": []}
    lock = threading.Lock()

    def test_one(proxy_str):
        if st["stop_flag"]: return None
        ok = _p_test_one(proxy_str)
        with lock:
            progress["done"] += 1
            if ok:
                progress["alive"] += 1
                alive.append(proxy_str)
                progress["last_5"].append(proxy_str)
                if len(progress["last_5"]) > 5: progress["last_5"].pop(0)
            else:
                progress["dead"] += 1
        return ok

    start = time.time()
    def run_batch():
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(test_one, p) for p in existing]
            for f in concurrent.futures.as_completed(futures):
                try: f.result()
                except Exception: pass

    batch_future = asyncio.get_event_loop().run_in_executor(POOL, run_batch)
    while not batch_future.done():
        await asyncio.sleep(1.5)
        if batch_future.done(): break
        if st["stop_flag"]: break
        done = progress["done"]
        if done == 0: continue
        elapsed = time.time() - start
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate if rate > 0 else 0
        recent = progress["last_5"][-3:] if progress["last_5"] else []
        recent_txt = "\n".join(f"  ✅ <code>{mask_proxy_partial(p)}</code>" for p in recent) if recent else "  <i>testing...</i>"
        await _safe_edit(msg_obj,
            f"🔄 <b>Retesting</b>\n\n"
            f"<code>{_bar(done, total)}</code> <b>{_pct(done, total)}</b>\n"
            f"📊 <code>{done:,}</code> / <code>{total:,}</code>\n\n"
            f"✅ Alive: <b>{progress['alive']}</b>\n"
            f"💀 Dead: {progress['dead']}\n"
            f"⚡ <code>{rate:.1f}/s</code>\n"
            f"⏱️ ETA: <b>{_fmt_eta(eta)}</b>\n\n"
            f"<b>Latest:</b>\n{recent_txt}",
            reply_markup=kb_scrape_running())
    await batch_future
    elapsed = time.time() - start
    was_stopped = st["stop_flag"]
    st["valid"] = alive
    st["stats"]["valid"] = len(alive)
    st["stats"]["rejected"] = progress["dead"]
    st["stats"]["collected"] = total
    try:
        c = sqlite3.connect(DB)
        c.execute("INSERT OR REPLACE INTO proxy_cache (user_id, proxies, updated_at) VALUES (?,?,?)",
                  (uid, "\n".join(alive), datetime.utcnow().isoformat()))
        c.commit(); c.close()
    except Exception: pass
    header = "⏹️ <b>Stopped</b>" if was_stopped else "✅ <b>Retest Complete</b>"
    if alive:
        buf = io.BytesIO("\n".join(alive).encode())
        buf.name = f"proxies_alive_{len(alive)}.txt"; buf.seek(0)
        try:
            await ctx.bot.send_document(uid, document=InputFile(buf, filename=buf.name),
                caption=(f"{header}\n\nTested: {progress['done']:,}/{total:,}\n"
                         f"✅ Alive: <b>{len(alive)}</b>\n💀 Dead: {progress['dead']}\n"
                         f"📈 {len(alive)*100//max(progress['done'],1)}%\n"
                         f"⏱️ {_fmt_eta(elapsed)}"),
                parse_mode="HTML")
        except Exception: pass
        await _safe_edit(msg_obj,
            f"{header}\n\n✅ Alive: <b>{len(alive)}</b>\n💀 Dead: {progress['dead']}\n"
            f"📈 {len(alive)*100//max(progress['done'],1)}%\n⏱️ {_fmt_eta(elapsed)}\n\n📄 File ☝️",
            reply_markup=kb_proxy_after())
    else:
        await _safe_edit(msg_obj,
            f"{header}\n\n💀 All {progress['done']} dead.",
            reply_markup=kb_proxy_menu())
    st["running"] = False; st["retesting"] = False; st["stop_flag"] = False

async def _do_file_check(ctx, uid, msg_obj, proxy_list, workers=30):
    st = _pstate(uid)
    st["running"] = True; st["stop_flag"] = False; st["retesting"] = False; st["file_mode"] = True
    st["last_run"] = datetime.utcnow().isoformat(); st["valid"] = []
    total = len(proxy_list)
    if total == 0:
        await _safe_edit(msg_obj, "❌ No valid proxies.", reply_markup=kb_proxy_menu())
        st["running"] = False; st["file_mode"] = False; return
    st["stats"] = {"collected": total, "valid": 0, "rejected": 0, "sources_ok": 0, "sources_fail": 0}
    alive = []; dead = []
    progress = {"done": 0, "alive": 0, "dead": 0, "last_5": []}
    lock = threading.Lock()

    def test_one(proxy_str):
        if st["stop_flag"]: return None
        ok = _p_test_one(proxy_str)
        with lock:
            progress["done"] += 1
            if ok:
                progress["alive"] += 1
                alive.append(proxy_str)
                _save_proxy_live(uid, proxy_str)
                progress["last_5"].append(proxy_str)
                if len(progress["last_5"]) > 5: progress["last_5"].pop(0)
            else:
                progress["dead"] += 1
                dead.append(proxy_str)
        return ok

    start = time.time()
    def run_batch():
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(test_one, p) for p in proxy_list]
            for f in concurrent.futures.as_completed(futures):
                try: f.result()
                except Exception: pass

    batch_future = asyncio.get_event_loop().run_in_executor(POOL, run_batch)
    while not batch_future.done():
        await asyncio.sleep(1.5)
        if batch_future.done(): break
        if st["stop_flag"]: break
        done = progress["done"]
        if done == 0: continue
        elapsed = time.time() - start
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate if rate > 0 else 0
        recent = progress["last_5"][-3:] if progress["last_5"] else []
        recent_txt = "\n".join(f"  ✅ <code>{mask_proxy_partial(p)}</code>" for p in recent) if recent else "  <i>testing...</i>"
        await _safe_edit(msg_obj,
            f"📄 <b>File Check</b>\n\n<code>{_bar(done, total)}</code> <b>{_pct(done, total)}</b>\n"
            f"📊 <code>{done:,}</code> / <code>{total:,}</code>\n\n"
            f"✅ Alive: <b>{progress['alive']}</b>\n💀 Dead: {progress['dead']}\n"
            f"⚡ <code>{rate:.1f}/s</code>\n⏱️ ETA: <b>{_fmt_eta(eta)}</b>\n\n"
            f"<b>Latest:</b>\n{recent_txt}",
            reply_markup=kb_scrape_running())
    await batch_future
    elapsed = time.time() - start
    was_stopped = st["stop_flag"]
    st["valid"] = alive
    st["stats"]["valid"] = len(alive)
    st["stats"]["rejected"] = len(dead)
    try:
        c = sqlite3.connect(DB)
        c.execute("INSERT OR REPLACE INTO proxy_cache (user_id, proxies, updated_at) VALUES (?,?,?)",
                  (uid, "\n".join(alive), datetime.utcnow().isoformat()))
        c.commit(); c.close()
    except Exception: pass
    header = "⏹️ <b>Stopped</b>" if was_stopped else "✅ <b>Complete</b>"
    if alive:
        alive_buf = io.BytesIO("\n".join(alive).encode())
        alive_buf.name = f"file_alive_{len(alive)}.txt"; alive_buf.seek(0)
        try:
            await ctx.bot.send_document(uid, document=InputFile(alive_buf, filename=alive_buf.name),
                caption=(f"{header}\n\nTested: {progress['done']:,}/{total:,}\n"
                         f"✅ Alive: <b>{len(alive)}</b>\n💀 Dead: {len(dead)}\n"
                         f"📈 Survival: <b>{len(alive)*100//max(progress['done'],1)}%</b>\n"
                         f"⏱️ {_fmt_eta(elapsed)}"),
                parse_mode="HTML")
        except Exception: pass
    if dead:
        dead_buf = io.BytesIO("\n".join(dead).encode())
        dead_buf.name = f"file_dead_{len(dead)}.txt"; dead_buf.seek(0)
        try:
            await ctx.bot.send_document(uid, document=InputFile(dead_buf, filename=dead_buf.name),
                caption=f"💀 Dead: <b>{len(dead)}</b>", parse_mode="HTML")
        except Exception: pass
    await _safe_edit(msg_obj,
        f"{header}\n\n📊 Tested: <code>{progress['done']:,}</code> / {total:,}\n"
        f"✅ Alive: <b>{len(alive)}</b>\n💀 Dead: {len(dead)}\n"
        f"📈 Survival: <b>{len(alive)*100//max(progress['done'],1)}%</b>\n"
        f"⏱️ {_fmt_eta(elapsed)}\n\n📄 Files sent ☝️",
        reply_markup=kb_proxy_after())
    st["running"] = False; st["file_mode"] = False; st["stop_flag"] = False

# ═══════════════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════════════
S = {}
def state(uid):
    if uid not in S:
        S[uid] = {"last_lines": [], "last_bin": "", "last_brand": "", "last_length": 16,
                  "last_expiry": None, "last_card": None, "default_country": None,
                  "page": 0, "awaiting": None}
    return S[uid]

def in_group(update):
    c = update.effective_chat
    if not c or c.type == "private": return False
    return bool(c.username and c.username.lower() == ALLOWED_GROUP.lower())

def in_dm(update):
    return update.effective_chat is not None and update.effective_chat.type == "private"

async def send_dm(ctx, uid, text=None, document=None, reply_markup=None):
    try:
        if document:
            await ctx.bot.send_document(uid, document=InputFile(document, filename=document.name),
                                        caption=text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await ctx.bot.send_message(uid, text, parse_mode="HTML", reply_markup=reply_markup)
        return True
    except Exception: return False

# ═══════════════════════════════════════════════════════════════════
# KEYBOARDS
# ═══════════════════════════════════════════════════════════════════
def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎴 Generate", callback_data="g")],
        [InlineKeyboardButton("🔍 BIN Info", callback_data="b"),
         InlineKeyboardButton("📋 Check Card", callback_data="cd")],
        [InlineKeyboardButton("🕸️ Proxy Scraper", callback_data="proxy_menu"),
         InlineKeyboardButton("🧹 File Cleaner", callback_data="m:clear")],
        [InlineKeyboardButton("⭐ Favs", callback_data="fav"),
         InlineKeyboardButton("📊 Stats", callback_data="st")],
        [InlineKeyboardButton("🎫 My Sub", callback_data="m:sub"),
         InlineKeyboardButton("🎟️ Redeem", callback_data="m:redeem")],
        [InlineKeyboardButton("🎁 Referral", callback_data="m:ref"),
         InlineKeyboardButton("🏆 Leaderboard", callback_data="m:lead")],
        [InlineKeyboardButton("📖 Help", callback_data="m:help")],
        [InlineKeyboardButton("📩 Open DM", url=f"https://t.me/{BOT_USERNAME or 'your_bot'}?start=1")],
    ])

def kb_help():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎴 Cards", callback_data="help:cards")],
        [InlineKeyboardButton("🕸️ Proxy", callback_data="help:proxy")],
        [InlineKeyboardButton("🧹 Cleaner", callback_data="help:clear")],
        [InlineKeyboardButton("🎫 Subs", callback_data="help:subs")],
        [InlineKeyboardButton("📊 Stats", callback_data="help:stats")],
        [InlineKeyboardButton("⬅️ Back", callback_data="bk")],
    ])

def kb_proxy_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Start Scrape (20)", callback_data="proxy:start:20")],
        [InlineKeyboardButton("⚡ Fast (40)", callback_data="proxy:start:40"),
         InlineKeyboardButton("🐢 Slow (10)", callback_data="proxy:start:10")],
        [InlineKeyboardButton("📄 Check From File", callback_data="proxy:filecheck")],
        [InlineKeyboardButton("🔍 Check Single", callback_data="proxy:checkone")],
        [InlineKeyboardButton("🔄 Retest Saved", callback_data="proxy:retest:20")],
        [InlineKeyboardButton("📄 Download", callback_data="proxy:download"),
         InlineKeyboardButton("📊 Stats", callback_data="proxy:stats")],
        [InlineKeyboardButton("📖 How it works", callback_data="proxy:howto")],
        [InlineKeyboardButton("⬅️ Back", callback_data="bk")],
    ])

def kb_proxy_after():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Download Again", callback_data="proxy:download"),
         InlineKeyboardButton("🔄 Retest", callback_data="proxy:retest:20")],
        [InlineKeyboardButton("🕸️ New Scrape", callback_data="proxy:start:20"),
         InlineKeyboardButton("⬅️ Menu", callback_data="bk")],
    ])

def kb_scrape_running():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏹️ Stop", callback_data="proxy:stop")]])

def kb_admin():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Stats", callback_data="adm:stats"),
         InlineKeyboardButton("👥 Users", callback_data="adm:users")],
        [InlineKeyboardButton("🎫 Subs", callback_data="adm:subs"),
         InlineKeyboardButton("🎟️ Codes", callback_data="adm:listcodes")],
        [InlineKeyboardButton("🎟️ Gen Code", callback_data="adm:gencode"),
         InlineKeyboardButton("📦 Bulk Codes", callback_data="adm:bulk")],
        [InlineKeyboardButton("➕ Add by ID", callback_data="adm:addsub")],
        [InlineKeyboardButton("🚫 Ban", callback_data="adm:ban"),
         InlineKeyboardButton("✅ Unban", callback_data="adm:unban"),
         InlineKeyboardButton("📋 Ban List", callback_data="adm:banlist")],
        [InlineKeyboardButton("⚙️ Whitelist", callback_data="adm:whitelist"),
         InlineKeyboardButton("📢 Broadcast", callback_data="adm:bc")],
        [InlineKeyboardButton("🔔 Notify", callback_data="adm:notify")],
    ])

def kb_admin_tier_for_code():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 Trial", callback_data="adm:gen:trial"),
         InlineKeyboardButton("⭐ Pro", callback_data="adm:gen:pro"),
         InlineKeyboardButton("👑 VIP", callback_data="adm:gen:vip")],
        [InlineKeyboardButton("◀ Back", callback_data="adm:back")],
    ])

def kb_admin_days(tier):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1d", callback_data=f"adm:genday:{tier}:1d"),
         InlineKeyboardButton("7d", callback_data=f"adm:genday:{tier}:7d"),
         InlineKeyboardButton("30d", callback_data=f"adm:genday:{tier}:30d")],
        [InlineKeyboardButton("90d", callback_data=f"adm:genday:{tier}:90d"),
         InlineKeyboardButton("180d", callback_data=f"adm:genday:{tier}:180d"),
         InlineKeyboardButton("365d", callback_data=f"adm:genday:{tier}:365d")],
        [InlineKeyboardButton("♾️ Lifetime", callback_data=f"adm:genday:{tier}:life")],
        [InlineKeyboardButton("◀ Back", callback_data="adm:gencode")],
    ])

def kb_after(lines, bp, count):
    rows = []
    if len(lines) <= TXT_THRESHOLD:
        for i, ln in enumerate(lines[:10], 1):
            card = ln.split("|")[0]
            rows.append([InlineKeyboardButton(f"📋 Copy #{i}", callback_data=f"cp|{card}")])
    rows.append([InlineKeyboardButton("📋 Copy All", callback_data=f"ca|{bp}"),
                 InlineKeyboardButton("🔄 Regen", callback_data=f"rg|{bp}|{count}")])
    rows.append([InlineKeyboardButton("📄 TXT", callback_data=f"x|txt|{bp}"),
                 InlineKeyboardButton("📊 CSV", callback_data=f"x|csv|{bp}"),
                 InlineKeyboardButton("🔗 JSON", callback_data=f"x|json|{bp}"),
                 InlineKeyboardButton("🔐 ZIP", callback_data=f"x|zip|{bp}")])
    return InlineKeyboardMarkup(rows)

def kb_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="bk")]])

def kb_dm_only():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📩 Open DM",
        url=f"https://t.me/{BOT_USERNAME or 'your_bot'}?start=1")]])

def kb_clear_prompt():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Cancel", callback_data="bk")]])

# ═══════════════════════════════════════════════════════════════════
# EXPORTS
# ═══════════════════════════════════════════════════════════════════
def build_txt(lines, bp, brand, ln, exp):
    buf = io.BytesIO()
    e = f"{exp[0]}/{exp[1]}" if exp else "random"
    h = f"# Luuln BIN {bp} {brand} len {ln} exp {e}\n# {len(lines)}\n\n"
    buf.write((h + "\n".join(lines) + "\n").encode()); buf.seek(0)
    buf.name = f"luuln_{bp}_{len(lines)}.txt"
    return buf

def build_csv(lines):
    s = io.StringIO(); w = csv.writer(s); w.writerow(["card","month","year","cvv"])
    for ln in lines: w.writerow(ln.split("|"))
    b = io.BytesIO(); b.write(s.getvalue().encode()); b.seek(0); b.name = "luuln.csv"; return b

def build_json(lines):
    d = [dict(zip(["card","month","year","cvv"], l.split("|"))) for l in lines]
    b = io.BytesIO(); b.write(json.dumps(d, indent=2).encode()); b.seek(0); b.name = "luuln.json"; return b

def build_zip(lines, bp):
    pwd = "".join(random.choices(string.ascii_letters + string.digits, k=20))
    content = "\n".join(lines) + "\n"
    buf = io.BytesIO()
    try:
        import pyzipper
        with pyzipper.AESZipFile(buf, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as z:
            z.setpassword(pwd.encode())
            z.writestr(f"luuln_{bp}_{len(lines)}.txt", content)
        method = "AES-256"
    except ImportError:
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"luuln_{bp}.txt", content)
            z.writestr("_PASSWORD.txt", f"PASSWORD: {pwd}")
        method = "ZIP"
    buf.seek(0); buf.name = f"luuln_{bp}.zip"
    return buf, pwd, method

# ═══════════════════════════════════════════════════════════════════
# PARSER
# ═══════════════════════════════════════════════════════════════════
def resolve_input(raw):
    d = re.sub(r"\D", "", raw)
    if not d or len(d) < 4: return None
    if 4 <= len(d) <= 8: return {"mode":"bin","bin_prefix":d,"partial":None,"input_len":len(d)}
    if 9 <= len(d) <= 19: return {"mode":"partial","bin_prefix":d[:8],"partial":d,"input_len":len(d)}
    return None

def parse_expiry(mm, yy):
    mm = re.sub(r"\D","", mm or ""); yy = re.sub(r"\D","", yy or "")
    if not mm or not yy: return None
    try: mi = int(mm)
    except Exception: return None
    if not (1 <= mi <= 12): return None
    if len(yy) == 4: yy = yy[-2:]
    if len(yy) != 2: return None
    return f"{mi:02d}", yy

def parse_gen(raw, max_cards=10000):
    raw = raw.replace('"', '').replace("|", " | ")
    parts = [p for p in re.split(r"\s+", raw.strip()) if p and p != "|"]
    if not parts: return None
    bin_token = None; rest = []
    for p in parts:
        if bin_token is None: bin_token = p
        elif p.isalpha() and p.lower() == "cvv": rest.append("cvv")
        elif "-" in p and p.replace("-","").isdigit(): rest.append(p)
        elif "," in p: rest.append(p)
        elif p.isdigit(): rest.append(p)
    if not bin_token: return None
    clean = re.sub(r"\D", "", bin_token)
    partial = None; input_len = len(clean)
    if 9 <= input_len <= 19:
        partial = clean; bins = [clean[:8]]
    elif "-" in bin_token and bin_token.replace("-","").isdigit():
        bins = []
        try:
            a, b = bin_token.split("-")
            if len(a) == len(b) and a.isdigit() and b.isdigit():
                rng = [str(i) for i in range(int(a), int(b)+1)]
                bins = [r for r in rng if detect_brand(r)[0] != "UNKNOWN"]
        except Exception: pass
        if not bins: bins = [clean]
    elif "," in bin_token: bins = [x.strip() for x in bin_token.split(",") if x.strip()]
    else: bins = [clean]
    count = 10; expiry = None; fixed_cvv = None
    nums = [r for r in rest if r.isdigit()]
    if "cvv" in rest:
        ci = rest.index("cvv")
        before = [r for r in rest[:ci] if r.isdigit()]
        after = [r for r in rest[ci+1:] if r.isdigit()]
        if len(before) >= 2:
            exp = parse_expiry(before[-2], before[-1])
            if exp: expiry = exp
        if after:
            try: count = max(1, min(max_cards, int(after[0])))
            except Exception: pass
    else:
        if len(nums) == 1:
            try: count = max(1, min(max_cards, int(nums[0])))
            except Exception: pass
        elif len(nums) == 2:
            exp = parse_expiry(nums[0], nums[1])
            if exp: expiry = exp
        elif len(nums) == 3:
            exp = parse_expiry(nums[0], nums[1])
            if exp:
                expiry = exp
                try: count = max(1, min(max_cards, int(nums[2])))
                except Exception: pass
        elif len(nums) >= 4:
            exp = parse_expiry(nums[0], nums[1])
            if exp:
                expiry = exp
                brand_pre = detect_brand(bin_token[:8])[0]
                fixed_cvv = nums[2].zfill(4 if brand_pre == "AMEX" else 3)
                try: count = max(1, min(max_cards, int(nums[3])))
                except Exception: pass
    return {"bins": bins, "count": count, "expiry": expiry, "fixed_cvv": fixed_cvv,
            "partial": partial, "input_len": input_len}

# ═══════════════════════════════════════════════════════════════════
# GEN CORE (privacy: full to DM, notice to group)
# ═══════════════════════════════════════════════════════════════════
async def do_gen(update, ctx, bins, count, expiry, fixed_cvv, partial=None, input_len=None):
    u = update.effective_user; s = state(u.id)
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    all_bins = []
    for bp in bins:
        okv, vinfo = validate_bin(bp)
        if not okv:
            await update.message.reply_text(
                f"❌ Invalid BIN <code>{bp}</code>\nReason: {vinfo}", parse_mode="HTML")
            return
        all_bins.append(bp)
    if not all_bins: return
    from_group = in_group(update)
    progress_msg = None
    if from_group and count > 100:
        try:
            progress_msg = await ctx.bot.send_message(u.id,
                _progress_block("🎴 Generating...", 0, count), parse_mode="HTML")
        except Exception:
            await update.message.reply_text("⚠️ Open DM first: /start", reply_markup=kb_dm_only())
            return
    lines = []; brand_first = None; real_len = 16; main_bp = all_bins[0]
    per_bin = max(1, count // len(all_bins))
    extra = count - per_bin * len(all_bins)
    total_made = 0
    for i, bp in enumerate(all_bins):
        brand, lens, dl = detect_brand(bp)
        if not brand_first: brand_first = brand
        info = await lookup_bin(bp)
        rl = dl
        if partial and input_len and input_len in lens: rl = input_len
        elif partial and input_len: rl = dl
        elif isinstance(info, dict) and not info.get("error") and info.get("length") in lens:
            rl = info["length"]
        if i == 0: real_len = rl
        this_count = per_bin + (1 if i < extra else 0)
        cards = await asyncio.get_event_loop().run_in_executor(POOL, generate_cards, bp, this_count, rl, partial)
        for c in cards:
            e = expiry if expiry else rand_exp()
            cvv = fixed_cvv if fixed_cvv else rand_cvv(brand or "VISA")
            lines.append(f"{c}|{e[0]}|{e[1]}|{cvv}")
            total_made += 1
        if progress_msg and (i % 3 == 0 or i == len(all_bins) - 1):
            await _safe_edit(progress_msg,
                _progress_block("🎴 Generating...", total_made, count, extra=[f"BIN: <code>{mask_bin(bp)}</code>"]))
    lines = lines[:count]
    if progress_msg:
        await _safe_edit(progress_msg, _progress_block("✅ Complete", count, count))
    s["last_lines"] = lines; s["last_bin"] = main_bp
    s["last_brand"] = brand_first or ""; s["last_length"] = real_len
    s["last_expiry"] = expiry
    db_log(u.id, u.username, main_bp, len(lines), "gen")

    h = (f"┌─ <b>L U U L N</b> ─┐\n│ BIN: <code>{main_bp}</code>\n│ Brand: {brand_first or '—'}\n"
         f"│ Length: {real_len}\n│ Count: {len(lines):,}\n└────────────────────┘")
    dm_ok = True
    try:
        if len(lines) <= TXT_THRESHOLD:
            txt = h + "\n\n" + "\n".join(f"<code>{c}</code>" for c in lines)
            await ctx.bot.send_message(u.id, txt, parse_mode="HTML",
                                       reply_markup=kb_after(lines, main_bp, len(lines)))
        else:
            buf = build_txt(lines, main_bp, brand_first or "", real_len, expiry)
            preview = h + "\n\n" + "\n".join(f"<code>{c}</code>" for c in lines[:10])
            preview += f"\n\n<i>... +{len(lines)-10:,} more</i>"
            await ctx.bot.send_message(u.id, preview, parse_mode="HTML")
            await ctx.bot.send_document(u.id, document=InputFile(buf, filename=buf.name),
                                        caption=f"{len(lines):,} cards", parse_mode="HTML",
                                        reply_markup=kb_after(lines, main_bp, len(lines)))
    except Exception:
        dm_ok = False

    if from_group:
        if dm_ok:
            await send_group_notice(update, ctx, "🎴 GENERATION COMPLETE", [
                f"🔢 BIN: {mask_bin(main_bp)}",
                f"💳 Brand: {brand_first or '—'}",
                f"📦 Cards: {len(lines):,}",
                f"💳 {mask_card_full(lines[0].split('|')[0])}",
            ], extra_buttons=[[InlineKeyboardButton("🔄 Regenerate",
                callback_data=f"rg|{main_bp}|{len(lines)}")]])
        else:
            await update.message.reply_text(
                "⚠️ Cannot deliver to DM. Tap below to open and retry:",
                parse_mode="HTML", reply_markup=kb_dm_only())

# ═══════════════════════════════════════════════════════════════════
# PUBLIC COMMANDS
# ═══════════════════════════════════════════════════════════════════
async def cmd_start(update, ctx):
    u = update.effective_user
    ref_by = None
    if ctx.args and ctx.args[0].startswith("ref_"):
        try: ref_by = int(ctx.args[0][4:])
        except Exception: pass
    db_user_track(u.id, u.username, u.first_name, ref_by=ref_by)
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    existing_tier, _ = db_get_sub(u.id)
    if not existing_tier and u.id != ADMIN_ID and not db_setting_get(f"trial_{u.id}"):
        db_set_sub(u.id, "trial", 3, by_admin=0)
        db_setting_set(f"trial_{u.id}", "1")
        try: await ctx.bot.send_message(u.id, "🎁 <b>3 days Trial</b>!", parse_mode="HTML")
        except Exception: pass
    if ref_by and ref_by != u.id and not db_setting_get(f"ref_rewarded_{u.id}"):
        if db_set_sub(ref_by, "pro", 7, by_admin=0):
            try: await ctx.bot.send_message(ref_by, "🎁 <b>Referral: 7d Pro!</b>", parse_mode="HTML")
            except Exception: pass
        db_setting_set(f"ref_rewarded_{u.id}", "1")
    await update.message.reply_text(
        f"👋 Welcome <b>{u.first_name}</b>\n\n"
        f"📖 /help — all commands\n🕸️ /ip — proxy menu\n🎴 /gen BIN — generate",
        parse_mode="HTML", reply_markup=kb_main())

async def cmd_help(update, ctx):
    text = (
        "📖 <b>Luuln Bot — Help</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔒 <b>PRIVACY:</b> All results are sent to your DM.\n"
        "Group messages are masked and auto-deleted after 60s.\n\n"
        "🎴 <b>CARDS</b>\n"
        "<code>/gen BIN</code>\n"
        "<code>/gen BIN COUNT</code>\n"
        "<code>/gen BIN|MM|YY</code>\n"
        "<code>/gen BIN|MM|YY|cvv|N</code>\n"
        "Examples:\n"
        "<code>/gen 519468436466760</code>\n"
        "<code>/gen 519468436466760|50</code>\n"
        "<code>/gen 519468436466760|02|28</code>\n\n"
        "<code>/genv BIN|MM|YY</code> — all CVVs\n"
        "<code>/bin BIN</code> — BIN lookup\n"
        "<code>/check CARD</code> — Luhn + brand\n"
        "<code>/info CARD</code> — bank/country\n"
        "<code>/checkfile</code> — bulk analyze (DM)\n\n"
        "🕸️ <b>PROXY</b>\n"
        "<code>/ip</code> — proxy menu\n"
        "<code>/chkip</code> — scrape 20 proxies\n"
        "<code>/chkip 40</code> / <code>/chkip 10</code>\n"
        "<code>/ipm</code> — check proxies from file (DM)\n"
        "<code>/chkp 1.2.3.4:8080</code> — check single\n"
        "<code>/rt</code> — retest saved\n"
        "<code>/pl</code> — download · <code>/ps</code> — stats\n\n"
        "🧹 <b>CLEANER</b> (DM): <code>/clear</code>\n\n"
        "🎫 <b>SUBS</b>\n"
        "<code>/sub</code> · <code>/redeem CODE</code> · <code>/ref</code>\n\n"
        "📊 <b>STATS</b>\n"
        "<code>/me</code> · <code>/stats</code> · <code>/leaderboard</code>\n"
        "<code>/fav</code> · <code>/favadd BIN</code> · <code>/setcountry CC</code>\n\n"
        "💡 <i>Tap any code block above to copy it.</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb_help())

async def cmd_me(update, ctx):
    u = update.effective_user
    tier = get_effective_tier(u.id)
    total, cards = db_stats(u.id)
    text = (
        f"👤 <b>Profile</b>\n\nID: <code>{u.id}</code>\n"
        f"Tier: {SUB_EMOJI.get(tier,'')} <b>{tier.upper()}</b>\n"
        f"Limit: <code>{SUB_TIERS[tier]['rate']}/hr</code>\n"
        f"Requests: {total}\nCards: {cards:,}\nReferrals: {db_referral_count(u.id)}"
    )
    ok = await send_dm(ctx, u.id, text=text, reply_markup=kb_back())
    if not ok:
        await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only()); return
    if in_group(update):
        await send_group_notice(update, ctx, "👤 PROFILE", [f"📩 Sent to DM"])

async def cmd_sub(update, ctx):
    u = update.effective_user
    tier = get_effective_tier(u.id); _, exp = db_get_sub(u.id)
    if u.id == ADMIN_ID:
        await update.message.reply_text("👑 Admin — VIP", reply_markup=kb_back()); return
    if tier == "free":
        await update.message.reply_text(
            "📋 Free tier\n\n"
            "🔓 Unlock more with a code:\n"
            "<code>/redeem CODE</code>",
            parse_mode="HTML", reply_markup=kb_back()); return
    exp_dt = datetime.fromisoformat(exp) if exp else None
    days_left = max(0, (exp_dt - datetime.utcnow()).days) if exp_dt else 0
    await update.message.reply_text(
        f"📋 <b>{SUB_TIERS[tier]['label']}</b>\n"
        f"Expires: {exp_dt.strftime('%Y-%m-%d') if exp_dt else '—'}\n"
        f"Days left: {days_left}\nRate: {SUB_TIERS[tier]['rate']}/hr",
        parse_mode="HTML", reply_markup=kb_back())

async def cmd_redeem(update, ctx):
    u = update.effective_user
    if not ctx.args:
        await reply_usage(update,
            "<code>/redeem CODE</code>\n",
            "Example:\n",
            "<code>/redeem ABCD-1234-EFGH</code>")
        return
    ok, res = db_redeem(ctx.args[0].strip().upper(), u.id)
    if ok:
        await update.message.reply_text(f"✅ {res[0].upper()} for {res[1]} days",
                                          parse_mode="HTML", reply_markup=kb_main())
    else:
        await update.message.reply_text(f"❌ {res}")

async def cmd_ref(update, ctx):
    u = update.effective_user
    link = f"https://t.me/{BOT_USERNAME or 'bot'}?start=ref_{u.id}"
    await update.message.reply_text(
        f"🎁 <b>Your referral link</b>\n\n<code>{link}</code>\n\n"
        f"Invites: {db_referral_count(u.id)}\n\n"
        f"<i>Each new user = 7d Pro for you.</i>",
        parse_mode="HTML", reply_markup=kb_back())

async def cmd_leaderboard(update, ctx):
    u = update.effective_user
    top = db_top_users(10)
    txt = "🏆 <b>Top 10</b>\n\n"
    for i, (uid, un, cnt, _) in enumerate(top, 1):
        medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"{i}."
        txt += f"{medal} <code>{uid}</code> · {cnt} req\n"
    ok = await send_dm(ctx, u.id, text=txt, reply_markup=kb_back())
    if in_group(update):
        if ok: await send_group_notice(update, ctx, "🏆 LEADERBOARD", ["📩 Sent to DM"])
        else: await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only())

async def cmd_stats(update, ctx):
    u = update.effective_user
    total, cards = db_stats(u.id); gl, gc = db_stats(None)
    txt = f"📊 <b>Your Stats</b>\n\nYou: {total} req · {cards:,} cards\nGlobal: {gl} req · {gc:,} cards"
    ok = await send_dm(ctx, u.id, text=txt, reply_markup=kb_back())
    if in_group(update):
        if ok: await send_group_notice(update, ctx, "📊 STATS", ["📩 Sent to DM"])
        else: await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only())

async def cmd_setcountry(update, ctx):
    if not ctx.args or len(ctx.args[0]) != 2 or not ctx.args[0].isalpha():
        await reply_usage(update,
            "<code>/setcountry CC</code>\n",
            "CC = 2-letter country code.\n",
            "Example:\n",
            "<code>/setcountry tr</code>")
        return
    state(update.effective_user.id)["default_country"] = ctx.args[0].upper()
    await update.message.reply_text(f"✅ Default country set to <b>{ctx.args[0].upper()}</b>",
                                      parse_mode="HTML")

async def cmd_fav(update, ctx):
    u = update.effective_user
    rows = db_fav_list(u.id)
    if not rows:
        await update.message.reply_text(
            "⭐ <b>No favorites yet</b>\n\n"
            "Add one:\n<code>/favadd 519468436466760</code>",
            parse_mode="HTML"); return
    btns = [[InlineKeyboardButton(r[0], callback_data=f"g|{r[0]}")] for r in rows]
    btns.append([InlineKeyboardButton("⬅️", callback_data="bk")])
    ok = await send_dm(ctx, u.id, text="⭐ <b>Favorites</b>", reply_markup=InlineKeyboardMarkup(btns))
    if in_group(update) and ok:
        await send_group_notice(update, ctx, "⭐ FAVORITES", ["📩 Sent to DM"])

async def cmd_favadd(update, ctx):
    if not ctx.args:
        await reply_usage(update,
            "<code>/favadd BIN [label]</code>\n",
            "Example:\n",
            "<code>/favadd 519468436466760 visa-test</code>")
        return
    u = update.effective_user; bp = ctx.args[0]
    okv, _ = validate_bin(bp)
    if not okv:
        await update.message.reply_text(
            f"❌ Invalid BIN <code>{bp}</code>\n\nTry a known brand prefix:\n"
            "<code>4</code> Visa · <code>5</code> MC · <code>34</code> Amex",
            parse_mode="HTML"); return
    db_fav_add(u.id, bp, " ".join(ctx.args[1:]))
    await update.message.reply_text(f"✅ {mask_bin(bp)} added to favorites")

async def cmd_gen(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    if not ctx.args:
        await reply_usage(update,
            "<code>/gen BIN</code>\n",
            "<code>/gen BIN COUNT</code>\n",
            "<code>/gen BIN|MM|YY</code>\n",
            "<code>/gen BIN|MM|YY|cvv|N</code>\n\n",
            "Examples:\n",
            "<code>/gen 519468436466760</code>\n",
            "<code>/gen 519468436466760|50</code>\n",
            "<code>/gen 519468436466760|02|28</code>")
        return
    ok, rem, tier, max_cards = check_rate(u.id)
    if not ok:
        await update.message.reply_text(
            f"⚠️ Rate limit reached for <b>{tier}</b> tier: {SUB_TIERS[tier]['rate']}/hr\n\n"
            f"Upgrade with <code>/redeem CODE</code>",
            parse_mode="HTML"); return
    p = parse_gen(" ".join(ctx.args), max_cards)
    if not p:
        await reply_usage(update,
            "Could not parse input.\n\n",
            "Format: <code>/gen BIN[|MM|YY][|COUNT]</code>\n",
            "Example: <code>/gen 519468436466760|5</code>")
        return
    await do_gen(update, ctx, **p)

async def cmd_genv(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    if not ctx.args:
        await reply_usage(update,
            "<code>/genv BIN|MM|YY [COUNT]</code>\n\n",
            "Example:\n",
            "<code>/genv 519468436466760|02|28</code>\n",
            "<code>/genv 519468436466760|02|28|100</code>")
        return
    ok, rem, tier, max_cards = check_rate(u.id)
    if not ok:
        await update.message.reply_text(f"⚠️ Rate limit ({tier}): {SUB_TIERS[tier]['rate']}/hr",
                                          parse_mode="HTML"); return
    parts = [p for p in re.split(r"\s+", " ".join(ctx.args).replace("|"," | ")) if p and p != "|"]
    if not parts:
        await reply_usage(update, "<code>/genv BIN|MM|YY</code>"); return
    bin_token = parts[0]
    nums = [p for p in parts[1:] if p.isdigit()]
    mm = yy = count = None
    if len(nums) >= 2:
        exp = parse_expiry(nums[0], nums[1])
        if exp: mm, yy = exp
        if len(nums) >= 3:
            try: count = int(nums[2])
            except Exception: pass
    if not mm or not yy:
        await reply_usage(update,
            "Missing or invalid expiry.\n\n",
            "Format: <code>/genv BIN|MM|YY</code>\n",
            "Example: <code>/genv 519468436466760|02|28</code>")
        return
    r = resolve_input(bin_token)
    if not r:
        await reply_usage(update,
            "Invalid BIN/prefix (need 4–19 digits).\n\n",
            "Example: <code>/genv 519468436466760|02|28</code>")
        return
    bp = r["bin_prefix"]
    okv, vinfo = validate_bin(bp)
    if not okv:
        await update.message.reply_text(f"❌ Invalid BIN: {vinfo}"); return
    brand = vinfo["brand"]; dl = vinfo["default_length"]
    card = complete_partial(r["partial"], dl) if r["partial"] else gen_from_bin(bp, dl)
    c_len = 4 if brand == "AMEX" else 3
    max_cvv = 10000 if c_len == 4 else 1000
    limit = min(count or max_cvv, max_cvv)
    lines = [f"{card}|{mm}|{yy}|{('{:04d}' if c_len==4 else '{:03d}').format(i)}" for i in range(limit)]
    db_log(u.id, u.username, bp, len(lines), "genv")
    s = state(u.id); s["last_lines"] = lines; s["last_bin"] = bp
    s["last_brand"] = brand; s["last_expiry"] = (mm, yy)
    buf = build_txt(lines, bp, brand, dl, (mm, yy))
    try:
        await ctx.bot.send_document(u.id, document=InputFile(buf, filename=buf.name),
                                    caption=f"🔢 {len(lines):,} CVVs for <code>{mask_card_partial(card)}</code>",
                                    parse_mode="HTML", reply_markup=kb_after(lines, bp, len(lines)))
        await send_group_notice(update, ctx, "🎴 CVV GENERATION", [
            f"💳 {mask_card_full(card)}", f"📦 CVVs: {len(lines):,}",
        ])
    except Exception:
        await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only())

async def cmd_bin(update, ctx):
    u = update.effective_user
    if not ctx.args:
        await reply_usage(update,
            "<code>/bin BIN</code>\n\n",
            "Example:\n<code>/bin 519468436466760</code>")
        return
    r = resolve_input(" ".join(ctx.args))
    if not r:
        await reply_usage(update, "Need 4–19 digits of a BIN/card.\n\nExample: <code>/bin 519468</code>")
        return
    bp = r["bin_prefix"]
    okv, vinfo = validate_bin(bp)
    if not okv:
        await update.message.reply_text(f"❌ Invalid BIN: {vinfo}"); return
    info = await lookup_bin(bp)
    brand = vinfo["brand"]
    txt = f"🔍 <b>BIN Details</b>\n<code>{bp}</code>\nBrand: {brand}\nLengths: {', '.join(map(str,vinfo['lengths']))}\n"
    if info and not info.get("error"):
        txt += f"\nScheme: {info['scheme']}\nType: {info['type']}\nBank: {info['bank']}\nCountry: {info['emoji']} {info['country']}"
    ok = await send_dm(ctx, u.id, text=txt, reply_markup=kb_back())
    if ok: await send_group_notice(update, ctx, "🔍 BIN LOOKUP", [f"🔢 BIN: {mask_bin(bp)}"])
    else: await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only())

async def cmd_check(update, ctx):
    u = update.effective_user
    if not ctx.args:
        await reply_usage(update,
            "<code>/check CARD</code>\n\n",
            "Example:\n<code>/check 4532015112830366</code>")
        return
    d = re.sub(r"\D","", " ".join(ctx.args))
    if len(d) < 4:
        await reply_usage(update, "Need at least 4 digits.\nExample: <code>/check 4532015112830366</code>")
        return
    brand, lens, _ = detect_brand(d[:8])
    v = luhn_check(d); lo = len(d) in lens if lens else False
    info = await lookup_bin(d[:8])
    txt = f"🔍 <b>Card Check</b>\n<code>{d}</code>\n\nLuhn: {'✅' if v else '❌'}\nBrand: {brand}\nLength: {'✅' if lo else '❌'}\n"
    if info and not info.get("error"):
        txt += f"\n🏦 {info.get('bank','—')}\n🌍 {info.get('emoji','')} {info.get('country','—')}"
    ok = await send_dm(ctx, u.id, text=txt, reply_markup=kb_back())
    if ok: await send_group_notice(update, ctx, "🔍 CARD CHECK", [f"💳 {mask_card_full(d)}"])
    else: await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only())

async def cmd_info(update, ctx):
    u = update.effective_user
    if not ctx.args:
        await reply_usage(update,
            "<code>/info CARD</code>\n\n",
            "Example:\n<code>/info 4532015112830366</code>")
        return
    d = re.sub(r"\D","", " ".join(ctx.args))
    if len(d) < 8:
        await reply_usage(update, "Need 8+ digits.\nExample: <code>/info 4532015112830366</code>")
        return
    okv, vinfo = validate_bin(d[:8])
    if not okv:
        await update.message.reply_text(f"❌ Invalid BIN prefix: {vinfo}"); return
    card = d if len(d) == 16 else complete_partial(d, vinfo["default_length"])
    info = await lookup_bin(d[:8])
    if not info or info.get("error"):
        await update.message.reply_text("⚠️ BIN lookup failed (rate limit or unknown)"); return
    txt = f"👤 <b>Card Data</b>\n<code>{card}</code>\nCountry: {info['emoji']} {info['country']}\nBank: {info['bank']}"
    state(u.id)["last_card"] = card
    ok = await send_dm(ctx, u.id, text=txt, reply_markup=kb_back())
    if ok: await send_group_notice(update, ctx, "👤 IDENTITY LOOKUP", [f"💳 {mask_card_full(card)}"])
    else: await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only())

async def cmd_checkfile(update, ctx):
    u = update.effective_user
    if not in_dm(update):
        await update.message.reply_text(
            "📎 <b>File check works in DM only</b>\n\nTap below to open DM, then /checkfile again.",
            parse_mode="HTML", reply_markup=kb_dm_only()); return
    state(u.id)["awaiting"] = "checkfile"
    await update.message.reply_text(
        "📎 <b>Upload a card file</b>\n\nOne card per line.\nMax 5MB.\n\n<i>/cancel to abort</i>",
        parse_mode="HTML", reply_markup=kb_back())

async def cmd_cancel(update, ctx):
    state(update.effective_user.id)["awaiting"] = None
    await update.message.reply_text("Cancelled.", reply_markup=kb_main())

# ═══════════════════════════════════════════════════════════════════
# /clear
# ═══════════════════════════════════════════════════════════════════
async def cmd_clear(update, ctx):
    u = update.effective_user
    if not in_dm(update):
        await update.message.reply_text("📎 DM only", reply_markup=kb_dm_only()); return
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    state(u.id)["awaiting"] = "clear_file"
    await update.message.reply_text(
        "🧹 <b>File Cleaner</b>\n\n"
        "📎 Send .txt or .csv\n"
        "Removes empty lines and duplicates.\n"
        "Max 5MB.\n\n"
        "<i>/cancel to abort</i>",
        parse_mode="HTML", reply_markup=kb_clear_prompt())

async def _process_clear(update, ctx, file_bytes, filename):
    try: text = file_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}"); return
    progress_msg = await update.message.reply_text(
        _progress_block("🧹 Processing...", 0, 1), parse_mode="HTML")
    raw = text.splitlines()
    stripped = [ln.strip() for ln in raw]
    non_empty = [ln for ln in stripped if ln]
    seen = set(); unique = []
    for i, ln in enumerate(non_empty):
        if ln not in seen: seen.add(ln); unique.append(ln)
        if i % 500 == 0 and len(non_empty) > 1000:
            await _safe_edit(progress_msg, _progress_block("🧹 Deduplicating...", i, len(non_empty)))
    stem = PurePath(filename).stem or "file"
    suffix = PurePath(filename).suffix or ".txt"
    out_name = f"{stem}_clean{suffix}"
    buf = io.BytesIO(("\n".join(unique) + "\n").encode()); buf.name = out_name; buf.seek(0)
    try:
        await ctx.bot.send_document(update.effective_user.id,
            document=InputFile(buf, filename=out_name),
            caption=f"🧹 <b>Cleaned</b>\n\n📄 {len(raw):,}\n🚫 Empty: {len(raw)-len(non_empty):,}\n"
                    f"♻️ Dupes: {len(non_empty)-len(unique):,}\n✅ Unique: <b>{len(unique):,}</b>",
            parse_mode="HTML")
        await _safe_edit(progress_msg, _progress_block("✅ Complete", len(non_empty), len(non_empty)))
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

# ═══════════════════════════════════════════════════════════════════
# PROXY COMMANDS
# ═══════════════════════════════════════════════════════════════════
async def cmd_proxy(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    st = _pstate(u.id)
    running = "⏳ Running" if st["running"] else "🟢 Idle"
    s = st["stats"]
    text = (
        f"🕸️ <b>Proxy Scraper</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Status:</b> {running}\n"
        f"<b>Cached:</b> <code>{len(st['valid'])}</code>\n"
        f"<b>Collected:</b> <code>{s.get('collected', 0)}</code> → <b>{s.get('valid', 0)}</b> valid\n\n"
        f"<b>Commands:</b>\n"
        f"<code>/ip</code> — this menu\n"
        f"<code>/chkip [N]</code> — scrape (default 20)\n"
        f"<code>/ipm</code> — check from file (DM)\n"
        f"<code>/chkp IP:PORT</code> — single check\n"
        f"<code>/rt</code> — retest saved\n"
        f"<code>/pl</code> — download · <code>/ps</code> — stats\n\n"
        f"🔒 All results go to your DM."
    )
    if in_dm(update):
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb_proxy_menu())
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb_dm_only())

async def cmd_scrape(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    st = _pstate(u.id)
    if st["running"]:
        await update.message.reply_text("⚠️ Already running. Wait or tap ⏹️ Stop."); return
    workers = 20
    if ctx.args:
        if not ctx.args[0].isdigit():
            await reply_usage(update,
                "<code>/chkip [WORKERS]</code>\n\n",
                "WORKERS = 10, 20, 40 (max 50). Default 20.\n\n",
                "Examples:\n",
                "<code>/chkip</code>\n",
                "<code>/chkip 40</code>")
            return
        workers = min(int(ctx.args[0]), 50)
    from_group = in_group(update)
    try:
        dm_msg = await ctx.bot.send_message(u.id, "🕸️ Starting scrape...")
    except Exception:
        await update.message.reply_text(
            "📩 Open DM first: /start\n\nThen re-run <code>/chkip</code>",
            parse_mode="HTML", reply_markup=kb_dm_only())
        return
    if from_group:
        await send_group_notice(update, ctx, "🕸️ SCRAPE STARTED", [
            f"⚙️ Workers: {workers}", "🔄 Progress → your DM",
        ])
    await _do_scrape_live(ctx, u.id, dm_msg, workers)

async def cmd_retest(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    st = _pstate(u.id)
    if st["running"]:
        await update.message.reply_text("⚠️ Already running"); return
    workers = 20
    if ctx.args:
        if not ctx.args[0].isdigit():
            await reply_usage(update,
                "<code>/rt [WORKERS]</code>\n\n",
                "Default 20. Max 50.\n\n",
                "Example:\n<code>/rt</code>\n<code>/rt 40</code>")
            return
        workers = min(int(ctx.args[0]), 50)
    from_group = in_group(update)
    try:
        dm_msg = await ctx.bot.send_message(u.id, "🔄 Loading saved proxies...")
    except Exception:
        await update.message.reply_text("📩 Open DM first: /start", reply_markup=kb_dm_only())
        return
    if from_group:
        await send_group_notice(update, ctx, "🔄 RETEST STARTED", ["🔄 Progress → your DM"])
    await _do_retest(ctx, u.id, dm_msg, workers)

async def cmd_checkproxy(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    if not ctx.args:
        await reply_usage(update,
            "<code>/chkp IP:PORT</code>\n\n",
            "Example:\n<code>/chkp 1.2.3.4:8080</code>")
        return
    raw = " ".join(ctx.args).strip()
    m = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})[:\s]+(\d{1,5})', raw)
    if not m:
        await reply_usage(update,
            "Could not parse IP:PORT.\n\n",
            "Format: <code>/chkp 1.2.3.4:8080</code>")
        return
    ip, port = m.group(1), m.group(2)
    if not _p_ip_ok(ip) or not (1 <= int(port) <= 65535):
        await update.message.reply_text(
            "❌ Invalid IP or port.\n\nFormat: <code>/chkp 1.2.3.4:8080</code>",
            parse_mode="HTML"); return
    proxy_str = f"{ip}:{port}"
    from_group = in_group(update)
    try:
        msg = await ctx.bot.send_message(u.id, f"🔍 Testing: <code>{proxy_str}</code>\n\n<i>Checking 4 endpoints...</i>", parse_mode="HTML")
    except Exception:
        await update.message.reply_text("📩 Open DM first: /start", reply_markup=kb_dm_only())
        return
    if from_group:
        await send_group_notice(update, ctx, "🔍 PROXY CHECK", [f"🌐 {mask_proxy_partial(proxy_str)}"])
    result = await asyncio.get_event_loop().run_in_executor(POOL, _p_test_detailed, proxy_str)
    if result.get("alive"):
        results_txt = ""
        for name, ok, lat in result["results"]:
            emoji = "✅" if ok else "❌"
            lat_txt = f"{lat}ms" if lat is not None else "timeout"
            results_txt += f"  {emoji} {name}: {lat_txt}\n"
        text = (f"✅ <b>ALIVE</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n🌐 <code>{proxy_str}</code>\n\n"
                f"📊 {result['working']}/{result['total']} · ⚡ {result['latency']}ms\n"
                f"📡 {result.get('detected_ip') or '—'}\n\n<b>Endpoints:</b>\n{results_txt}\n"
                f"💡 <code>http://{proxy_str}</code>")
        if not any(p == proxy_str for p in _pstate(u.id)["valid"]):
            _pstate(u.id)["valid"].append(proxy_str)
            _save_proxy_live(u.id, proxy_str)
        await _safe_edit(msg, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Test Again", callback_data=f"chk|{proxy_str}")],
            [InlineKeyboardButton("⬅️ Menu", callback_data="proxy_menu")]]))
    else:
        await _safe_edit(msg, f"❌ <b>DEAD</b>\n\n🌐 <code>{proxy_str}</code>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Again", callback_data=f"chk|{proxy_str}")],
                [InlineKeyboardButton("⬅️ Menu", callback_data="proxy_menu")]]))

async def cmd_mcheckproxy(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    if not in_dm(update):
        await update.message.reply_text(
            "📎 <b>File check works in DM only</b>\n\n"
            "Tap below, then /ipm again.",
            parse_mode="HTML", reply_markup=kb_dm_only()); return
    st = _pstate(u.id)
    if st["running"]:
        await update.message.reply_text("⚠️ Already running. Wait or tap ⏹️ Stop."); return
    state(u.id)["awaiting"] = "mchk_file"
    await update.message.reply_text(
        "📄 <b>Check Proxies From File</b>\n\n"
        "📎 Send a .txt file, one proxy per line:\n"
        "<code>1.2.3.4:8080</code>\n\n"
        f"Max: <b>{MAX_FILE_PROXIES:,}</b>\n\n"
        "You'll get back: <code>file_alive_N.txt</code> + <code>file_dead_N.txt</code>\n\n"
        "<i>/cancel to abort</i>", parse_mode="HTML", reply_markup=kb_clear_prompt())

async def _process_mchk_file(update, ctx, file_bytes, filename):
    try: content = file_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        await update.message.reply_text(f"❌ Decode: {e}"); return
    proxies = _parse_proxy_file(content)
    if not proxies:
        await update.message.reply_text(
            "❌ <b>No valid proxies</b>\n\n"
            "Make sure each line is:\n<code>1.2.3.4:8080</code>",
            parse_mode="HTML"); return
    seen = set(); unique = []
    for p in proxies:
        if p not in seen: seen.add(p); unique.append(p)
    total = len(unique)
    if total > MAX_FILE_PROXIES:
        unique = unique[:MAX_FILE_PROXIES]; total = MAX_FILE_PROXIES
    msg = await update.message.reply_text(
        f"📄 <b>File Loaded</b>\n\n"
        f"File: <code>{filename}</code>\n"
        f"Proxies: <code>{len(proxies):,}</code>\n"
        f"After dedup: <code>{total:,}</code>\n\n"
        f"<i>Testing with 30 workers...</i>", parse_mode="HTML")
    await _do_file_check(ctx, update.effective_user.id, msg, unique, workers=30)

async def cmd_proxylist(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    st = _pstate(u.id)
    valid = st["valid"]
    if not valid:
        try:
            c = sqlite3.connect(DB)
            r = c.execute("SELECT proxies FROM proxy_cache WHERE user_id=?", (u.id,)).fetchone()
            c.close()
            if r and r[0]: valid = [p for p in r[0].split("\n") if p]
        except Exception: pass
    if not valid:
        await update.message.reply_text(
            "📭 <b>No proxies saved</b>\n\nRun <code>/chkip</code> first.",
            parse_mode="HTML", reply_markup=kb_proxy_menu()); return
    buf = io.BytesIO("\n".join(valid).encode()); buf.name = f"proxies_{len(valid)}.txt"; buf.seek(0)
    try:
        await ctx.bot.send_document(u.id, document=InputFile(buf, filename=buf.name),
                                     caption=f"📄 <b>{len(valid)}</b> proxies", parse_mode="HTML")
    except Exception:
        await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only())

async def cmd_proxystats(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    st = _pstate(u.id); s = st["stats"]
    db_count = 0
    try:
        c = sqlite3.connect(DB)
        r = c.execute("SELECT proxies FROM proxy_cache WHERE user_id=?", (u.id,)).fetchone()
        c.close()
        if r and r[0]: db_count = len([p for p in r[0].split("\n") if p])
    except Exception: pass
    txt = (f"📊 <b>Proxy Stats</b>\n\n"
           f"Running: {st['running']}\n"
           f"In memory: {len(st['valid'])}\n"
           f"In DB: {db_count}\n"
           f"Collected: {s.get('collected', 0)}\n"
           f"Valid: {s.get('valid', 0)}\n"
           f"Rejected: {s.get('rejected', 0)}")
    ok = await send_dm(ctx, u.id, text=txt, reply_markup=kb_proxy_menu())
    if in_group(update) and ok:
        await send_group_notice(update, ctx, "📊 PROXY STATS", ["📩 Sent to DM"])

# ═══════════════════════════════════════════════════════════════════
# ADMIN
# ═══════════════════════════════════════════════════════════════════
async def cmd_admin(update, ctx):
    if not is_admin(update):
        await update.message.reply_text("⛔ Denied"); return
    if not in_dm(update):
        await update.message.reply_text("🔒 DM only", reply_markup=kb_dm_only()); return
    await update.message.reply_text("🎛️ <b>Admin Panel</b>", parse_mode="HTML", reply_markup=kb_admin())

async def cmd_addsub(update, ctx):
    if not admin_dm_required(update): return
    if len(ctx.args) < 3:
        await reply_usage(update,
            "<code>/addsub UID TIER DAYS</code>\n\n",
            "TIER: free | trial | pro | vip\n",
            "DAYS: 1d, 7d, 30d, 90d, 180d, 365d, life (or number)\n\n",
            "Example:\n",
            "<code>/addsub 123456789 pro 30d</code>")
        return
    try: uid = int(ctx.args[0])
    except Exception:
        await reply_usage(update, "UID must be a number.\n<code>/addsub 123456789 pro 30d</code>"); return
    tier = ctx.args[1].lower()
    if tier not in SUB_TIERS:
        await reply_usage(update, f"TIER must be one of: {', '.join(SUB_TIERS.keys())}"); return
    try: days = SUB_DURATIONS.get(ctx.args[2].lower()) or int(ctx.args[2])
    except Exception:
        await reply_usage(update, "DAYS must be e.g. 30d or 30"); return
    if db_set_sub(uid, tier, days, by_admin=update.effective_user.id):
        try: await ctx.bot.send_message(uid, f"🎉 {tier.upper()} for {days}d!", parse_mode="HTML")
        except Exception: pass
        await update.message.reply_text(f"✅ {uid} → {tier} ({days}d)")

async def cmd_ban(update, ctx):
    if not admin_dm_required(update): return
    if not ctx.args:
        await reply_usage(update,
            "<code>/ban UID [reason]</code>\n\n",
            "Example:\n<code>/ban 123456789 spam</code>")
        return
    try: uid = int(ctx.args[0])
    except Exception:
        await reply_usage(update, "UID must be a number.\n<code>/ban 123456789 reason</code>"); return
    db_ban(uid, " ".join(ctx.args[1:]) or "no reason", update.effective_user.id)
    await update.message.reply_text(f"🚫 {uid} banned")

async def cmd_unban(update, ctx):
    if not admin_dm_required(update): return
    if not ctx.args:
        await reply_usage(update, "<code>/unban UID</code>\n\nExample:\n<code>/unban 123456789</code>")
        return
    try: uid = int(ctx.args[0])
    except Exception:
        await reply_usage(update, "UID must be a number.\n<code>/unban 123456789</code>"); return
    db_unban(uid)
    await update.message.reply_text(f"✅ {uid} unbanned")

async def cmd_broadcast(update, ctx):
    if not admin_dm_required(update): return
    if not ctx.args:
        await reply_usage(update,
            "<code>/broadcast MESSAGE</code>\n\n",
            "Example:\n<code>/broadcast New proxies available!</code>")
        return
    msg = " ".join(ctx.args); users = db_all_users()
    msg_obj = await update.message.reply_text(f"📢 Sending to {len(users)}...")
    sent = fail = 0
    for i, (uid, _) in enumerate(users):
        try: await ctx.bot.send_message(uid, f"📢 {msg}", parse_mode="HTML"); sent += 1
        except Exception: fail += 1
        if i % 10 == 0:
            await _safe_edit(msg_obj, _progress_block("📢...", i+1, len(users), extra=[f"✅ {sent}", f"❌ {fail}"]))
    await _safe_edit(msg_obj, _progress_block("✅ Done", len(users), len(users), extra=[f"✅ {sent}", f"❌ {fail}"]))

async def cmd_bulkcode(update, ctx):
    if not admin_dm_required(update): return
    if len(ctx.args) < 3:
        await reply_usage(update,
            "<code>/bulkcode TIER DAYS COUNT</code>\n\n",
            "TIER: free | trial | pro | vip\n",
            "DAYS: 1d, 7d, 30d, 90d, 180d, 365d, life\n",
            "COUNT: 1-100\n\n",
            "Example:\n<code>/bulkcode pro 30d 10</code>")
        return
    tier = ctx.args[0].lower()
    if tier not in SUB_TIERS:
        await reply_usage(update, f"TIER must be one of: {', '.join(SUB_TIERS.keys())}"); return
    try:
        days = SUB_DURATIONS.get(ctx.args[1].lower()) or int(ctx.args[1])
        n = min(int(ctx.args[2]), 100)
    except Exception:
        await reply_usage(update, "DAYS and COUNT must be valid.\n<code>/bulkcode pro 30d 10</code>"); return
    codes = db_gen_bulk(tier, days, n, update.effective_user.id)
    buf = io.BytesIO("\n".join(codes).encode()); buf.name = f"codes_{tier}_{days}d.txt"; buf.seek(0)
    await update.message.reply_document(document=InputFile(buf, filename=buf.name),
                                         caption=f"✅ {len(codes)} codes")

async def cmd_notify(update, ctx):
    if not admin_dm_required(update): return
    try:
        c = sqlite3.connect(DB)
        now = datetime.utcnow().isoformat()
        soon = (datetime.utcnow() + timedelta(days=3)).isoformat()
        r = c.execute("SELECT user_id, tier, expires_at FROM subscriptions WHERE expires_at>? AND expires_at<?",
                      (now, soon)).fetchall()
        c.close()
    except Exception: r = []
    sent = 0
    for uid, tier, exp in r:
        try: await ctx.bot.send_message(uid, f"⏰ {tier.upper()} expires {exp[:10]}", parse_mode="HTML"); sent += 1
        except Exception: pass
    await update.message.reply_text(f"🔔 Notified {sent} users (expiring within 3 days)")

async def cmd_setwhitelist(update, ctx):
    if not admin_dm_required(update): return
    if not ctx.args or ctx.args[0].lower() not in ("on", "off"):
        await reply_usage(update, "<code>/setwhitelist on</code> or <code>/setwhitelist off</code>")
        return
    v = ctx.args[0].lower()
    db_setting_set("whitelist_mode", v)
    await update.message.reply_text(f"✅ {v.upper()}")

# ═══════════════════════════════════════════════════════════════════
# CALLBACKS
# ═══════════════════════════════════════════════════════════════════
async def on_cb(update, ctx):
    q = update.callback_query; d = q.data or ""
    u = q.from_user; s = state(u.id)
    if not in_group(update) and not in_dm(update):
        await q.answer("Denied", show_alert=True); return
    await q.answer()

    if d.startswith("help:"):
        cat = d.split(":")[1]
        texts = {
            "cards": (
                "🎴 <b>Cards</b>\n\n"
                "<code>/gen 519468436466760</code>\n"
                "<code>/gen 519468436466760|50</code>\n"
                "<code>/gen 519468436466760|02|28</code>\n"
                "<code>/genv 519468436466760|02|28</code>\n"
                "<code>/bin 519468</code>\n"
                "<code>/check 4532015112830366</code>\n"
                "<code>/info 4532015112830366</code>\n"
                "<code>/checkfile</code>"
            ),
            "proxy": (
                "🕸️ <b>Proxy</b>\n\n"
                "<code>/ip</code> — menu\n"
                "<code>/chkip</code> — scrape 20\n"
                "<code>/chkip 40</code>\n"
                "<code>/ipm</code> — file check\n"
                "<code>/chkp 1.2.3.4:8080</code>\n"
                "<code>/rt</code> · <code>/pl</code> · <code>/ps</code>"
            ),
            "clear": (
                "🧹 <b>Cleaner</b> (DM)\n\n"
                "<code>/clear</code>\n\n"
                "Then upload .txt or .csv"
            ),
            "subs": (
                "🎫 <b>Subs</b>\n\n"
                "<code>/sub</code>\n"
                "<code>/redeem CODE</code>\n"
                "<code>/ref</code>"
            ),
            "stats": (
                "📊 <b>Stats</b>\n\n"
                "<code>/me</code>\n"
                "<code>/stats</code>\n"
                "<code>/leaderboard</code>\n"
                "<code>/fav</code>\n"
                "<code>/favadd 519468436466760</code>\n"
                "<code>/setcountry tr</code>"
            ),
        }
        await q.edit_message_text(texts.get(cat, "—"), parse_mode="HTML", reply_markup=kb_help()); return

    if d == "proxy_menu":
        st = _pstate(u.id); s2 = st["stats"]
        running = "⏳ Running" if st["running"] else "🟢 Idle"
        txt = (f"🕸️ <b>Proxy Scraper</b>\n\n"
               f"Status: {running}\n"
               f"Cached: {len(st['valid'])}\n"
               f"Collected: {s2.get('collected', 0)} → Valid: {s2.get('valid', 0)}\n\n"
               f"🔒 All results go to your DM.\n\n"
               f"<code>/ip</code> · <code>/chkip</code> · <code>/ipm</code> · <code>/chkp</code>")
        if in_dm(update):
            await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb_proxy_menu())
        else:
            await q.edit_message_text(txt + "\n\n📩 Tap below:", parse_mode="HTML", reply_markup=kb_dm_only())
        return
    if d == "proxy:stop":
        st = _pstate(u.id)
        if not st["running"]:
            await q.answer("Not running", show_alert=True); return
        st["stop_flag"] = True
        await q.answer("⏹️ Stopping...", show_alert=True); return
    if d == "proxy:filecheck":
        if not in_dm(update):
            await q.answer("DM only — tap Open DM", show_alert=True); return
        st = _pstate(u.id)
        if st["running"]:
            await q.answer("Already running", show_alert=True); return
        state(u.id)["awaiting"] = "mchk_file"
        await q.edit_message_text(
            "📄 <b>Check From File</b>\n\n"
            "📎 Send .txt (one per line)\n"
            "<code>1.2.3.4:8080</code>\n\n"
            f"Max: {MAX_FILE_PROXIES:,}\n\n"
            "<i>/cancel to abort</i>",
            parse_mode="HTML", reply_markup=kb_clear_prompt()); return
    if d == "proxy:howto":
        await q.edit_message_text(
            "🕸️ <b>How It Works</b>\n\n"
            "① Collect from 9 sources\n"
            "② Deduplicate\n"
            "③ Test each with 4 endpoints\n"
            "④ Confirm working proxies\n"
            "⑤ Save to your cache\n\n"
            "⏹️ Stop anytime · 🔄 Retest later · 📄 File check\n"
            "🔒 All in DM only\n\n"
            "📌 Commands:\n"
            "<code>/ip</code> · <code>/chkip</code> · <code>/ipm</code> · <code>/chkp</code> · <code>/rt</code> · <code>/pl</code> · <code>/ps</code>",
            parse_mode="HTML", reply_markup=kb_proxy_menu()); return
    if d == "proxy:checkone":
        if not in_dm(update):
            await q.answer("DM only", show_alert=True); return
        state(u.id)["awaiting"] = "checkproxy_input"
        await q.edit_message_text(
            "🔍 <b>Check Single Proxy</b>\n\n"
            "Send:\n<code>1.2.3.4:8080</code>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Cancel", callback_data="proxy_menu")]])); return
    if d.startswith("proxy:start:"):
        workers = int(d.split(":")[-1])
        st = _pstate(u.id)
        if st["running"]:
            await q.answer("Already running", show_alert=True); return
        from_group = in_group(update)
        try:
            dm_msg = await ctx.bot.send_message(u.id, f"🕸️ Starting {workers} workers...")
        except Exception:
            await q.answer("Open DM first", show_alert=True); return
        if from_group:
            await send_group_notice(update, ctx, "🕸️ SCRAPE STARTED", [f"⚙️ {workers} workers", "🔄 Progress → DM"])
        asyncio.create_task(_do_scrape_live(ctx, u.id, dm_msg, workers)); return
    if d.startswith("proxy:retest:"):
        workers = int(d.split(":")[-1])
        st = _pstate(u.id)
        if st["running"]:
            await q.answer("Already running", show_alert=True); return
        from_group = in_group(update)
        try:
            dm_msg = await ctx.bot.send_message(u.id, "🔄 Loading saved proxies...")
        except Exception:
            await q.answer("Open DM first", show_alert=True); return
        if from_group:
            await send_group_notice(update, ctx, "🔄 RETEST STARTED", ["🔄 Progress → DM"])
        asyncio.create_task(_do_retest(ctx, u.id, dm_msg, workers)); return
    if d == "proxy:download":
        st = _pstate(u.id)
        valid = st["valid"]
        if not valid:
            try:
                c = sqlite3.connect(DB)
                r = c.execute("SELECT proxies FROM proxy_cache WHERE user_id=?", (u.id,)).fetchone()
                c.close()
                if r and r[0]: valid = [p for p in r[0].split("\n") if p]
            except Exception: pass
        if not valid:
            await q.answer("No proxies. Run /chkip first.", show_alert=True); return
        buf = io.BytesIO("\n".join(valid).encode()); buf.name = f"proxies_{len(valid)}.txt"; buf.seek(0)
        try:
            await ctx.bot.send_document(u.id, document=InputFile(buf, filename=buf.name),
                                         caption=f"📄 {len(valid)} proxies")
        except Exception:
            await q.answer("Open DM first", show_alert=True)
        return
    if d == "proxy:stats":
        st = _pstate(u.id); s2 = st["stats"]
        await q.edit_message_text(
            f"📊 Running: {st['running']}\n"
            f"Cached: {len(st['valid'])}\n"
            f"Collected: {s2.get('collected', 0)}\n"
            f"Valid: {s2.get('valid', 0)}\n"
            f"Rejected: {s2.get('rejected', 0)}",
            parse_mode="HTML", reply_markup=kb_proxy_menu()); return
    if d.startswith("chk|"):
        proxy_str = d.split("|", 1)[1]
        if not in_dm(update):
            await q.answer("DM only", show_alert=True); return
        await q.edit_message_text(f"🔍 Testing: <code>{proxy_str}</code>\n\n<i>Checking...</i>", parse_mode="HTML")
        result = await asyncio.get_event_loop().run_in_executor(POOL, _p_test_detailed, proxy_str)
        if result.get("alive"):
            results_txt = ""
            for name, ok, lat in result["results"]:
                emoji = "✅" if ok else "❌"
                lat_txt = f"{lat}ms" if lat is not None else "timeout"
                results_txt += f"  {emoji} {name}: {lat_txt}\n"
            text = (f"✅ <b>ALIVE</b>\n\n"
                    f"🌐 <code>{proxy_str}</code>\n"
                    f"📊 {result['working']}/{result['total']} · ⚡ {result['latency']}ms\n"
                    f"📡 {result.get('detected_ip') or '—'}\n\n"
                    f"<b>Endpoints:</b>\n{results_txt}")
            if not any(p == proxy_str for p in _pstate(u.id)["valid"]):
                _pstate(u.id)["valid"].append(proxy_str)
                _save_proxy_live(u.id, proxy_str)
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Again", callback_data=f"chk|{proxy_str}")],
                [InlineKeyboardButton("⬅️ Menu", callback_data="proxy_menu")]]))
        else:
            await q.edit_message_text(f"❌ <b>DEAD</b>\n\n🌐 <code>{proxy_str}</code>",
                parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Again", callback_data=f"chk|{proxy_str}")],
                    [InlineKeyboardButton("⬅️ Menu", callback_data="proxy_menu")]]))
        return

    if d.startswith("adm:"):
        if u.id != ADMIN_ID or update.effective_chat.type != "private":
            await q.answer("⛔ Denied", show_alert=True); return
        if d == "adm:back":
            await q.edit_message_text("🎛️ <b>Admin Panel</b>", parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:stats":
            gl, gc = db_stats(None)
            await q.edit_message_text(
                f"📊 <b>Global Stats</b>\n\n"
                f"Requests: {gl:,}\n"
                f"Cards: {gc:,}\n"
                f"Users: {len(db_all_users())}",
                parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:users":
            users = db_all_users()[:20]; txt = "👥 <b>Users (last 20)</b>\n\n"
            for uid, _ in users:
                t_, e = db_get_sub(uid); eff = t_ or "free"
                txt += f"<code>{uid}</code> · {SUB_EMOJI.get(eff,'')} {eff}\n"
            await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:subs":
            subs = db_list_subs(20)
            txt = "🎫 <b>Subs (last 20)</b>\n\n" + "\n".join(f"<code>{uid}</code> · {tier} · {exp[:10]}" for uid, tier, exp in subs)
            await q.edit_message_text(txt or "None", parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:gencode":
            await q.edit_message_text("🎟️ <b>Pick a tier</b>", parse_mode="HTML", reply_markup=kb_admin_tier_for_code()); return
        if d.startswith("adm:gen:"):
            tier = d.split(":")[2]
            await q.edit_message_text(f"<b>{tier}</b> — pick duration:", parse_mode="HTML", reply_markup=kb_admin_days(tier)); return
        if d.startswith("adm:genday:"):
            _, _, tier, dur = d.split(":")
            code = db_gen_code(tier, SUB_DURATIONS[dur], u.id)
            if code:
                await q.edit_message_text(
                    f"✅ <b>Code generated</b>\n\n"
                    f"<code>{code}</code>\n\n"
                    f"{tier} · {SUB_DURATIONS[dur]}d\n\n"
                    f"Share with the user. They redeem via:\n"
                    f"<code>/redeem {code}</code>",
                    parse_mode="HTML", reply_markup=kb_admin())
            return
        if d == "adm:listcodes":
            codes = db_list_codes(20)
            txt = "🎟️ <b>Recent codes</b>\n\n" + "\n".join(f"<code>{c}</code> · {t} · {dd}d · {'✅' if ub else '🟢'}" for c, t, dd, ub in codes)
            await q.edit_message_text(txt or "None", parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:addsub":
            await q.edit_message_text(
                "➕ <b>Add Sub by ID</b>\n\n"
                "<code>/addsub UID TIER DAYS</code>\n\n"
                "Example:\n"
                "<code>/addsub 123456789 pro 30d</code>",
                parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:bulk":
            await q.edit_message_text(
                "📦 <b>Bulk Codes</b>\n\n"
                "<code>/bulkcode TIER DAYS COUNT</code>\n\n"
                "Example:\n"
                "<code>/bulkcode pro 30d 10</code>",
                parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:ban":
            await q.edit_message_text(
                "🚫 <b>Ban</b>\n\n<code>/ban UID reason</code>\n\nExample:\n<code>/ban 123456789 spam</code>",
                parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:unban":
            await q.edit_message_text(
                "✅ <b>Unban</b>\n\n<code>/unban UID</code>\n\nExample:\n<code>/unban 123456789</code>",
                parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:banlist":
            bl = db_ban_list(20)
            txt = "🚫 <b>Ban List</b>\n\n" + "\n".join(f"<code>{uid}</code> · {reason}" for uid, reason, _ in bl)
            await q.edit_message_text(txt or "None", parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:whitelist":
            wl = db_setting_get("whitelist_mode", "off")
            await q.edit_message_text(
                f"⚙️ <b>Whitelist mode</b>: {wl.upper()}\n\n"
                f"Toggle with:\n<code>/setwhitelist on</code>\n<code>/setwhitelist off</code>",
                parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:bc":
            await q.edit_message_text(
                "📢 <b>Broadcast</b>\n\n<code>/broadcast MESSAGE</code>\n\nExample:\n<code>/broadcast Maintenance in 5min</code>",
                parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:notify":
            await cmd_notify(update, ctx); return
        return

    if d == "noop": return
    if d == "bk":
        await q.edit_message_text("📖 <b>Main Menu</b>", parse_mode="HTML", reply_markup=kb_main()); return
    if d == "g":
        await q.edit_message_text(
            "🎴 <b>Generate Cards</b>\n\n"
            "Formats:\n"
            "<code>/gen 519468436466760</code>\n"
            "<code>/gen 519468436466760|50</code>\n"
            "<code>/gen 519468436466760|02|28</code>\n\n"
            "Tap any to copy.",
            parse_mode="HTML"); return
    if d == "b":
        await q.edit_message_text(
            "🔍 <b>BIN Lookup</b>\n\n"
            "Send:\n<code>/bin 519468</code>",
            parse_mode="HTML"); return
    if d == "cd":
        await q.edit_message_text(
            "📋 <b>Check Card</b>\n\n"
            "Send:\n<code>/check 4532015112830366</code>",
            parse_mode="HTML"); return
    if d == "m:help":
        await q.edit_message_text("📖 <b>Help categories</b>", parse_mode="HTML", reply_markup=kb_help()); return
    if d == "m:sub":
        tier = get_effective_tier(u.id); _, exp = db_get_sub(u.id)
        await q.edit_message_text(
            f"🎫 <b>Your Sub</b>\n\n"
            f"Tier: {tier.upper()}\n"
            f"Expires: {exp[:10] if exp else '—'}\n"
            f"Rate: {SUB_TIERS[tier]['rate']}/hr",
            parse_mode="HTML", reply_markup=kb_back()); return
    if d == "m:redeem":
        await q.edit_message_text(
            "🎟️ <b>Redeem</b>\n\nSend:\n<code>/redeem CODE</code>",
            parse_mode="HTML"); return
    if d == "m:ref":
        link = f"https://t.me/{BOT_USERNAME or 'bot'}?start=ref_{u.id}"
        await q.edit_message_text(
            f"🎁 <b>Your Referral Link</b>\n\n<code>{link}</code>\n\nInvites: {db_referral_count(u.id)}",
            parse_mode="HTML", reply_markup=kb_back()); return
    if d == "m:lead":
        top = db_top_users(10); txt = "🏆 <b>Top 10</b>\n\n"
        for i, (uid, un, cnt, _) in enumerate(top, 1):
            medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"{i}."
            txt += f"{medal} <code>{uid}</code> · {cnt}\n"
        await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb_back()); return
    if d == "m:clear":
        if not in_dm(update): await q.answer("DM only", show_alert=True); return
        state(u.id)["awaiting"] = "clear_file"
        await q.edit_message_text(
            "🧹 <b>File Cleaner</b>\n\nUpload .txt or .csv\n<i>/cancel to abort</i>",
            parse_mode="HTML", reply_markup=kb_clear_prompt()); return
    if d == "fav":
        rows = db_fav_list(u.id)
        if not rows:
            await q.edit_message_text(
                "⭐ <b>No favorites</b>\n\nAdd one:\n<code>/favadd 519468436466760</code>",
                parse_mode="HTML", reply_markup=kb_back()); return
        btns = [[InlineKeyboardButton(r[0], callback_data=f"g|{r[0]}")] for r in rows]
        btns.append([InlineKeyboardButton("⬅️", callback_data="bk")])
        await q.edit_message_text("⭐ <b>Favorites</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns)); return
    if d == "st":
        total, cards = db_stats(u.id)
        await q.edit_message_text(f"📊 {total} requests · {cards:,} cards", reply_markup=kb_back()); return
    if d.startswith("cp|"):
        await q.answer(f"{d.split('|',1)[1]}", show_alert=True); return
    if d.startswith("ca|"):
        block = "\n".join(s.get("last_lines") or [])
        if block:
            try: await q.message.reply_text(f"<code>{block}</code>", parse_mode="HTML")
            except Exception: await q.answer("Use TXT export instead", show_alert=True)
        else:
            await q.answer("No cards to copy — generate first", show_alert=True)
        return
    if d.startswith("rg|"):
        parts = d.split("|")
        await do_gen(update, ctx, [parts[1]], int(parts[2]), None, None); return
    if d.startswith("x|"):
        parts = d.split("|"); fmt = parts[1]; bp = parts[2]
        lines = s.get("last_lines") or []
        if not lines:
            await q.answer("No cards to export — generate first", show_alert=True); return
        if fmt == "txt": buf = build_txt(lines, bp, s.get("last_brand",""), s.get("last_length",16), s.get("last_expiry"))
        elif fmt == "csv": buf = build_csv(lines)
        elif fmt == "json": buf = build_json(lines)
        elif fmt == "zip":
            buf, pwd, method = build_zip(lines, bp)
            await q.message.reply_document(document=InputFile(buf, filename=buf.name),
                                            caption=f"🔐 {method}\n<code>{pwd}</code>",
                                            parse_mode="HTML"); return
        else: return
        await q.message.reply_document(document=InputFile(buf, filename=buf.name),
                                        caption=f"<b>{fmt.upper()}</b>"); return

# ═══════════════════════════════════════════════════════════════════
# TEXT + DOCUMENT
# ═══════════════════════════════════════════════════════════════════
async def on_text(update, ctx):
    u = update.effective_user; s = state(u.id)
    awaiting = s.get("awaiting")

    if awaiting == "clear_file":
        if not in_dm(update): return
        await update.message.reply_text("📎 Upload the file (not text).", parse_mode="HTML"); return

    if awaiting == "mchk_file":
        if not in_dm(update): return
        await update.message.reply_text("📎 Upload a .txt file, not text.", parse_mode="HTML"); return

    if awaiting == "checkproxy_input":
        s["awaiting"] = None
        txt = (update.message.text or "").strip()
        m = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})[:\s]+(\d{1,5})', txt)
        if not m:
            await reply_usage(update, "Send: <code>1.2.3.4:8080</code>"); return
        ip, port = m.group(1), m.group(2)
        if not _p_ip_ok(ip) or not (1 <= int(port) <= 65535):
            await update.message.reply_text("❌ Invalid IP/port.\nFormat: <code>1.2.3.4:8080</code>",
                                              parse_mode="HTML"); return
        proxy_str = f"{ip}:{port}"
        msg = await update.message.reply_text(f"🔍 Testing: <code>{proxy_str}</code>", parse_mode="HTML")
        result = await asyncio.get_event_loop().run_in_executor(POOL, _p_test_detailed, proxy_str)
        if result.get("alive"):
            if not any(p == proxy_str for p in _pstate(u.id)["valid"]):
                _pstate(u.id)["valid"].append(proxy_str)
                _save_proxy_live(u.id, proxy_str)
            await _safe_edit(msg, f"✅ <b>ALIVE</b>\n\n🌐 <code>{proxy_str}</code>\n"
                f"📊 {result['working']}/{result['total']} · ⚡ {result['latency']}ms",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Again", callback_data=f"chk|{proxy_str}")],
                    [InlineKeyboardButton("⬅️ Menu", callback_data="proxy_menu")]]))
        else:
            await _safe_edit(msg, f"❌ <b>DEAD</b>\n\n🌐 <code>{proxy_str}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Menu", callback_data="proxy_menu")]]))
        return

    if not in_group(update): return
    if db_is_banned(u.id): return
    if awaiting == "checkfile":
        s["awaiting"] = None; return
    txt = (update.message.text or "").strip()
    d = re.sub(r"\D","", txt)
    if 4 <= len(d) <= 19:
        r = resolve_input(d)
        if r:
            okv, vinfo = validate_bin(r["bin_prefix"])
            if okv:
                await update.message.reply_text(
                    f"✅ BIN valid\n"
                    f"Try:\n<code>/gen {d}|5</code>\n<code>/genv {d}|02|28</code>",
                    parse_mode="HTML")
            else:
                await update.message.reply_text(f"❌ {vinfo}", parse_mode="HTML")

async def on_document(update, ctx):
    u = update.effective_user; s = state(u.id); doc = update.message.document
    awaiting = s.get("awaiting")

    if awaiting == "clear_file":
        s["awaiting"] = None
        if not in_dm(update) or not doc: return
        if doc.file_size and doc.file_size > 5 * 1024 * 1024:
            await update.message.reply_text("❌ 5MB max"); return
        try:
            file = await ctx.bot.get_file(doc.file_id)
            raw = await file.download_as_bytearray()
        except Exception as e:
            await update.message.reply_text(f"❌ {e}"); return
        await _process_clear(update, ctx, bytes(raw), doc.file_name or "file.txt")
        return

    if awaiting == "mchk_file":
        s["awaiting"] = None
        if not in_dm(update) or not doc: return
        if doc.file_size and doc.file_size > 5 * 1024 * 1024:
            await update.message.reply_text("❌ 5MB max"); return
        try:
            file = await ctx.bot.get_file(doc.file_id)
            raw = await file.download_as_bytearray()
        except Exception as e:
            await update.message.reply_text(f"❌ {e}"); return
        await _process_mchk_file(update, ctx, bytes(raw), doc.file_name or "proxies.txt")
        return

    if not in_dm(update) or awaiting != "checkfile" or not doc: return
    s["awaiting"] = None
    try:
        file = await ctx.bot.get_file(doc.file_id)
        raw = await file.download_as_bytearray()
        content = raw.decode("utf-8", errors="ignore")
    except Exception: return
    lines = [l.strip() for l in content.splitlines() if l.strip() and not l.startswith("#")]
    if not lines:
        await update.message.reply_text("❌ File is empty."); return
    if len(lines) > 10000:
        await update.message.reply_text("❌ Max 10,000 lines."); return
    progress_msg = await update.message.reply_text(
        _progress_block("📊 Analyzing...", 0, len(lines)), parse_mode="HTML")
    total = len(lines); ok_n = fail_n = 0; brands = {}
    for i, ln in enumerate(lines):
        card = re.sub(r"\D","", ln.split("|")[0] if "|" in ln else ln)
        if not card: continue
        if luhn_check(card): ok_n += 1
        else: fail_n += 1
        b, _, _ = detect_brand(card[:8])
        brands[b] = brands.get(b, 0) + 1
        if i % 100 == 0:
            await _safe_edit(progress_msg,
                _progress_block("📊 Analyzing...", i+1, total, extra=[f"✅ {ok_n}", f"❌ {fail_n}"]))
    report = f"📊 <b>Checker</b>\nTotal: {total}\n✅ Luhn: {ok_n}\n❌ Fail: {fail_n}\n\n"
    for b, c in sorted(brands.items(), key=lambda x: -x[1]):
        report += f"  {b}: {c}\n"
    await _safe_edit(progress_msg, _progress_block("✅ Done", total, total))
    await update.message.reply_text(report, parse_mode="HTML", reply_markup=kb_back())

# ═══════════════════════════════════════════════════════════════════
# INIT
# ═══════════════════════════════════════════════════════════════════
async def on_err(update, ctx):
    log.exception("Handler", exc_info=ctx.error)

async def post_init(app):
    global BOT_USERNAME
    me = await app.bot.get_me()
    BOT_USERNAME = me.username
    db_init()
    log.info(f"✅ @{BOT_USERNAME} ready")

def main():
    if not BOT_TOKEN or ":" not in BOT_TOKEN or len(BOT_TOKEN) < 40:
        raise SystemExit("❌ BOT_TOKEN missing or invalid.")
    if ADMIN_ID == 0:
        raise SystemExit("❌ ADMIN_ID missing.")

    threading.Thread(target=_run_health, daemon=True).start()
    log.info(f"🌐 Health server :{PORT}")

    app = (Application.builder().token(BOT_TOKEN).concurrent_updates(256).post_init(post_init).build())

    for cmd, fn in [
        ("start", cmd_start), ("help", cmd_help), ("cancel", cmd_cancel),
        ("gen", cmd_gen), ("genv", cmd_genv), ("bin", cmd_bin),
        ("check", cmd_check), ("info", cmd_info), ("checkfile", cmd_checkfile),
        ("stats", cmd_stats), ("me", cmd_me),
        ("fav", cmd_fav), ("favadd", cmd_favadd), ("setcountry", cmd_setcountry),
        ("sub", cmd_sub), ("redeem", cmd_redeem), ("ref", cmd_ref),
        ("leaderboard", cmd_leaderboard), ("clear", cmd_clear),
        ("proxy", cmd_proxy), ("scrape", cmd_scrape), ("retest", cmd_retest),
        ("checkproxy", cmd_checkproxy), ("proxylist", cmd_proxylist),
        ("proxystats", cmd_proxystats), ("mcheckproxy", cmd_mcheckproxy),
        # old short
        ("px", cmd_proxy), ("sc", cmd_scrape), ("rt", cmd_retest),
        ("chkp", cmd_checkproxy), ("pl", cmd_proxylist),
        ("ps", cmd_proxystats), ("mchk", cmd_mcheckproxy),
        # new short
        ("ip", cmd_proxy), ("ipm", cmd_mcheckproxy), ("chkip", cmd_scrape),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    for cmd, fn in [
        ("admin", cmd_admin), ("addsub", cmd_addsub),
        ("ban", cmd_ban), ("unban", cmd_unban),
        ("broadcast", cmd_broadcast), ("bulkcode", cmd_bulkcode),
        ("notify", cmd_notify), ("setwhitelist", cmd_setwhitelist),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_err)
    log.info("🚀 Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
