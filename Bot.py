#!/usr/bin/env python3
"""Luuln Bot — Merged Edition"""

import os, asyncio, concurrent.futures, csv, io, json, logging, random, re, sqlite3
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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, BotCommand
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, ContextTypes, filters

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# ═══════════════════════════════════════════════════════════════════
# ═══ ONE-LINE CONFIG ═══
# ═══════════════════════════════════════════════════════════════════
CONFIG = {
    "bot_token":       os.getenv("BOT_TOKEN", "9144552229:AAFSdDI_d5u1dY-u_va1gXKaMWmBhHbuY6A").strip(),
    "admin_id":        int(os.getenv("ADMIN_ID", "6207431030")),
    "group":           os.getenv("ALLOWED_GROUP", "engtopwin1").strip(),
    "port":            int(os.getenv("PORT", "8080")),
    "group_ttl":       int(os.getenv("GROUP_NOTICE_TTL", "60")),
    "db_path":         os.getenv("DB_PATH", "luuln.db"),
    "max_cards":       10000,
    "txt_threshold":   10,
    "bin_timeout":     8.0,
    "identity_timeout":12.0,
    "country_timeout": 8.0,
    "postal_timeout":  6.0,
    "max_file_proxies":10000,
    "tiers": {
        "free":  {"rate": 10,   "max": 1000,  "label": "Free",  "emoji": "🆓"},
        "trial": {"rate": 30,   "max": 2000,  "label": "Trial", "emoji": "🎁"},
        "pro":   {"rate": 100,  "max": 5000,  "label": "Pro",   "emoji": "⭐"},
        "vip":   {"rate": 9999, "max": 10000, "label": "VIP",   "emoji": "👑"},
    },
    "sub_durations": {"1d":1,"7d":7,"30d":30,"90d":90,"180d":180,"365d":365,"life":36500},
}

# ═══════════════════════════════════════════════════════════════════
# ═══ ONE-LINE COMMANDS ═══
# ═══════════════════════════════════════════════════════════════════
COMMAND_MAP = {
    "start":       "start",
    "help":        "help",
    "menu":        "menu",
    "ping":        "ping",
    "gen":         "gen",
    "genv":        "genv",
    "bin":         "bin",
    "info":        "info",
    "check":       "check",
    "checkfile":   "checkfile",
    "discover":    "discover",
    "fav":         "fav",
    "favadd":      "favadd",
    "stats":       "stats",
    "me":          "me",
    "sub":         "sub",
    "redeem":      "redeem",
    "ref":         "ref",
    "board":       "leaderboard",
    "setcountry":  "setcountry",
    "cancel":      "cancel",
    "clear":       "clear",
    "ip":          "proxy_menu",
    "chkip":       "chkip",
    "ipm":         "ipm",
    "chkp":        "chkp",
    "rt":          "retest",
    "ipl":         "plist",
    "ips":         "pstats",
    "admin":       "admin",
    "addsub":      "addsub",
    "ban":         "ban",
    "unban":       "unban",
    "broadcast":   "broadcast",
    "bulkcode":    "bulkcode",
    "notify":      "notify",
    "setwhitelist":"setwhitelist",
    "setcmd":      "setcmd",
    "delcmd":      "delcmd",
    "cmds":        "cmds",
}

BOT_TOKEN    = CONFIG["bot_token"]
ADMIN_ID     = CONFIG["admin_id"]
ALLOWED_GROUP= CONFIG["group"]
PORT         = CONFIG["port"]
GROUP_NOTICE_TTL = CONFIG["group_ttl"]
MAX_CARDS    = CONFIG["max_cards"]
TXT_THRESHOLD= CONFIG["txt_threshold"]
BIN_TIMEOUT  = CONFIG["bin_timeout"]
IDENTITY_TIMEOUT = CONFIG["identity_timeout"]
COUNTRY_TIMEOUT  = CONFIG["country_timeout"]
POSTAL_TIMEOUT   = CONFIG["postal_timeout"]
MAX_FILE_PROXIES = CONFIG["max_file_proxies"]
SUB_TIERS    = CONFIG["tiers"]
SUB_DURATIONS= CONFIG["sub_durations"]
DB           = CONFIG["db_path"]

POOL = ThreadPoolExecutor(max_workers=32)
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
for _n in ("httpx","httpcore","telegram","telegram.ext","werkzeug","urllib3","urllib3.connectionpool"):
    logging.getLogger(_n).setLevel(logging.WARNING)
log = logging.getLogger("luuln")

BOT_USERNAME = None
BIN_CACHE = {}
COUNTRY_CACHE = {}
RATE = {}
ALL_USERS = set()
SUB_EMOJI = {k: v["emoji"] for k, v in SUB_TIERS.items()}

# ═══════════════════════════════════════════════════════════════════
# HEALTH SERVER
# ═══════════════════════════════════════════════════════════════════
health_app = Flask(__name__)

@health_app.route("/")
def _idx():
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Luuln</title></head>"
        "<body style='font-family:system-ui;background:#0d1117;color:#c9d1d9;text-align:center;"
        "padding:80px'><h1 style='color:#58a6ff'>✅ Luuln Bot</h1>"
        f"<p>@{BOT_USERNAME or 'starting'}</p>"
        f"<p style='color:#3fb950'>Running</p></body></html>", 200, {"Content-Type":"text/html"})

@health_app.route("/health")
def _health():
    return jsonify({"status":"ok","bot":BOT_USERNAME or "starting",
                    "time": datetime.utcnow().isoformat()+"Z"}), 200

@health_app.route("/ping")
def _ping():
    return "ok", 200

def _run_health():
    log.info(f"🌐 Health on :{PORT}")
    health_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)

# ═══════════════════════════════════════════════════════════════════
# DB
# ═══════════════════════════════════════════════════════════════════
def db_init():
    c = sqlite3.connect(DB)
    for sql in [
        "CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,username TEXT,bin TEXT,count INTEGER,mode TEXT,created_at TEXT)",
        "CREATE TABLE IF NOT EXISTS favorites (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,bin TEXT,label TEXT,created_at TEXT)",
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,tier TEXT DEFAULT 'free',first_seen TEXT)",
        "CREATE TABLE IF NOT EXISTS subscriptions (user_id INTEGER PRIMARY KEY,tier TEXT,expires_at TEXT,started_at TEXT,by_admin INTEGER)",
        "CREATE TABLE IF NOT EXISTS codes (code TEXT PRIMARY KEY,tier TEXT,days INTEGER,used_by INTEGER,used_at TEXT,created_at TEXT,created_by INTEGER)",
        "CREATE TABLE IF NOT EXISTS bans (user_id INTEGER PRIMARY KEY,reason TEXT,banned_by INTEGER,banned_at TEXT)",
        "CREATE TABLE IF NOT EXISTS referrals (inviter INTEGER,invitee INTEGER PRIMARY KEY,rewarded INTEGER DEFAULT 0,created_at TEXT)",
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT)",
        "CREATE TABLE IF NOT EXISTS proxy_cache (user_id INTEGER PRIMARY KEY,proxies TEXT,updated_at TEXT)",
        "CREATE TABLE IF NOT EXISTS cmd_overrides (old_alias TEXT PRIMARY KEY,new_alias TEXT,handler TEXT)",
    ]:
        c.execute(sql)
    c.commit(); c.close()

def _db():
    return sqlite3.connect(DB)

def db_log(uid, un, bp, cnt, mode):
    try:
        c = _db(); c.execute("INSERT INTO requests (user_id,username,bin,count,mode,created_at) VALUES (?,?,?,?,?,?)",
                            (uid, un or "", bp, cnt, mode, datetime.utcnow().isoformat()))
        c.commit(); c.close()
    except: pass

def db_user_track(uid, un, fn, ref_by=None):
    try:
        c = _db()
        if not c.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone():
            c.execute("INSERT INTO users (id,username,first_name,tier,first_seen) VALUES (?,?,?,'free',?)",
                     (uid, un or "", fn or "", datetime.utcnow().isoformat()))
            if ref_by and ref_by != uid:
                try: c.execute("INSERT OR IGNORE INTO referrals (inviter,invitee,created_at) VALUES (?,?,?)",
                              (ref_by, uid, datetime.utcnow().isoformat()))
                except: pass
        else:
            c.execute("UPDATE users SET username=?,first_name=? WHERE id=?", (un or "", fn or "", uid))
        c.commit(); c.close(); ALL_USERS.add(uid)
    except: pass

def db_stats(uid=None):
    try:
        c = _db()
        q = "SELECT COUNT(*),COALESCE(SUM(count),0) FROM requests"
        r = c.execute(q + (" WHERE user_id=?" if uid else ""), (uid,) if uid else ()).fetchone()
        c.close(); return r
    except: return (0,0)

def db_top_users(limit=10):
    try:
        c = _db(); r = c.execute("SELECT user_id,username,COUNT(*),SUM(count) FROM requests GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT ?", (limit,)).fetchall()
        c.close(); return r
    except: return []

def db_all_users():
    try:
        c = _db(); r = c.execute("SELECT id,tier FROM users").fetchall()
        c.close(); return r
    except: return []

def db_fav_add(uid, bp, lb):
    try:
        c = _db(); c.execute("INSERT INTO favorites (user_id,bin,label,created_at) VALUES (?,?,?,?)",
                            (uid, bp, lb, datetime.utcnow().isoformat())); c.commit(); c.close()
    except: pass

def db_fav_list(uid):
    try:
        c = _db(); r = c.execute("SELECT bin,label FROM favorites WHERE user_id=? ORDER BY id DESC LIMIT 20", (uid,)).fetchall()
        c.close(); return r
    except: return []

def db_get_sub(uid):
    try:
        c = _db(); r = c.execute("SELECT tier,expires_at FROM subscriptions WHERE user_id=?", (uid,)).fetchone(); c.close()
        if not r: return None, None
        tier, exp = r
        if exp and datetime.utcnow() > datetime.fromisoformat(exp): return None, None
        return tier, exp
    except: return None, None

def db_set_sub(uid, tier, days, by_admin=0):
    try:
        now = datetime.utcnow(); exp = (now + timedelta(days=days)).isoformat()
        c = _db(); c.execute("INSERT OR REPLACE INTO subscriptions (user_id,tier,expires_at,started_at,by_admin) VALUES (?,?,?,?,?)",
                            (uid, tier, exp, now.isoformat(), by_admin)); c.commit(); c.close(); return True
    except: return False

def db_gen_code(tier, days, by_admin):
    code = "-".join(["".join(random.choices(string.ascii_uppercase+string.digits, k=4)) for _ in range(3)])
    try:
        c = _db(); c.execute("INSERT INTO codes (code,tier,days,created_at,created_by) VALUES (?,?,?,?,?)",
                            (code, tier, days, datetime.utcnow().isoformat(), by_admin)); c.commit(); c.close(); return code
    except: return None

def db_gen_bulk(tier, days, n, by_admin):
    return [c for _ in range(n) if (c := db_gen_code(tier, days, by_admin))]

def db_redeem(code, uid):
    try:
        c = _db(); r = c.execute("SELECT tier,days,used_by FROM codes WHERE code=?", (code,)).fetchone()
        if not r: c.close(); return False, "Code not found"
        tier, days, used_by = r
        if used_by: c.close(); return False, "Already used"
        c.execute("UPDATE codes SET used_by=?,used_at=? WHERE code=?", (uid, datetime.utcnow().isoformat(), code))
        c.commit(); c.close(); db_set_sub(uid, tier, days); return True, (tier, days)
    except: return False, "Error"

def db_list_codes(limit=20):
    try:
        c = _db(); r = c.execute("SELECT code,tier,days,used_by FROM codes ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall(); c.close(); return r
    except: return []

def db_list_subs(limit=20):
    try:
        c = _db(); r = c.execute("SELECT user_id,tier,expires_at FROM subscriptions ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall(); c.close(); return r
    except: return []

def db_ban(uid, reason, by):
    try:
        c = _db(); c.execute("INSERT OR REPLACE INTO bans (user_id,reason,banned_by,banned_at) VALUES (?,?,?,?)",
                            (uid, reason, by, datetime.utcnow().isoformat())); c.commit(); c.close(); return True
    except: return False

def db_unban(uid):
    try:
        c = _db(); c.execute("DELETE FROM bans WHERE user_id=?", (uid,)); c.commit(); c.close(); return True
    except: return False

def db_is_banned(uid):
    try:
        c = _db(); r = c.execute("SELECT 1 FROM bans WHERE user_id=?", (uid,)).fetchone(); c.close(); return bool(r)
    except: return False

def db_ban_list(limit=30):
    try:
        c = _db(); r = c.execute("SELECT user_id,reason,banned_at FROM bans ORDER BY banned_at DESC LIMIT ?", (limit,)).fetchall(); c.close(); return r
    except: return []

def db_setting_get(key, default=None):
    try:
        c = _db(); r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone(); c.close(); return r[0] if r else default
    except: return default

def db_setting_set(key, value):
    try:
        c = _db(); c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, str(value))); c.commit(); c.close(); return True
    except: return False

def db_referral_count(uid):
    try:
        c = _db(); r = c.execute("SELECT COUNT(*) FROM referrals WHERE inviter=?", (uid,)).fetchone(); c.close(); return r[0] if r else 0
    except: return 0

def db_save_cmd(old, new, handler):
    try:
        c = _db(); c.execute("INSERT OR REPLACE INTO cmd_overrides (old_alias,new_alias,handler) VALUES (?,?,?)",
                            (old, new, handler)); c.commit(); c.close()
    except: pass

def db_load_cmd_overrides():
    try:
        c = _db(); r = c.execute("SELECT old_alias,new_alias,handler FROM cmd_overrides").fetchall(); c.close(); return r
    except: return []

def db_del_cmd(alias):
    try:
        c = _db(); c.execute("DELETE FROM cmd_overrides WHERE new_alias=?", (alias,)); c.commit(); c.close()
    except: pass

def get_effective_tier(uid):
    if uid == ADMIN_ID: return "vip"
    tier, _ = db_get_sub(uid)
    return tier or "free"

def check_rate(uid):
    tier = get_effective_tier(uid); cfg = SUB_TIERS[tier]
    now = time.time()
    RATE.setdefault(uid, [])
    RATE[uid] = [t for t in RATE[uid] if now - t < 3600]
    if len(RATE[uid]) >= cfg["rate"]:
        wait = int(min(RATE[uid]) + 3600 - now)
        return False, max(1, wait), tier, cfg["max"]
    RATE[uid].append(now)
    return True, cfg["rate"] - len(RATE[uid]), tier, cfg["max"]

# ═══════════════════════════════════════════════════════════════════
# BRANDS + LUHN
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

KNOWN_BINS = {
    "visa": ["453201","453900","491174","402400","448527","476134","414720","428576","400028","400158"],
    "mastercard": ["542523","552311","511234","525678","538901","543210","511220","510510","540400"],
    "amex": ["378282","371449","340000","373728","374245","378734","370000","372880"],
    "discover": ["601111","601100","601127","601186","601198","601199","622126","622127"],
    "jcb": ["356600","356611","356620","356645","356650","356880"],
    "unionpay": ["620000","621234","622500","623000","624000","625000"],
    "diners": ["300000","301000","305000","360000","380000","390000"],
}

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
    if tl <= n: b = p[:tl-1]; return b + str(luhn_digit(b))
    if tl == n+1: return p + str(luhn_digit(p))
    b = p + "".join(str(random.randint(0,9)) for _ in range(tl-n-1))
    return b + str(luhn_digit(b))

def generate_cards(bp, count, tl, partial=None):
    out, seen, g = [], set(), 0
    while len(out) < count and g < count * 200:
        g += 1
        c = complete_partial(partial, tl) if partial else gen_from_bin(bp, tl)
        if c not in seen: seen.add(c); out.append(c)
    return out

def overflow_cards(base, target, brand, fixed_exp):
    out = list(base)
    if len(out) >= target: return out[:target]
    lo, hi, cl = (1000,10000,4) if brand == "AMEX" else (100,1000,3)
    for base_card in base:
        for yy in range(26, 33):
            for mm in range(1, 13):
                if fixed_exp:
                    em, ey = fixed_exp
                    if em != f"{mm:02d}" or ey != f"{yy:02d}": continue
                for cvv in range(lo, hi):
                    out.append((base_card, f"{mm:02d}", f"{yy:02d}", f"{cvv:0{cl}d}"))
                    if len(out) >= target: return out
    return out[:target]

def rand_exp(): return f"{random.randint(1,12):02d}", f"{random.randint(26,32):02d}"
def rand_cvv(b, fixed=None):
    if fixed is not None: return fixed
    return f"{random.randint(1000,9999)}" if b == "AMEX" else f"{random.randint(100,999)}"

# ═══════════════════════════════════════════════════════════════════
# BIN LOOKUP
# ═══════════════════════════════════════════════════════════════════
async def lookup_bin(bp):
    k = bp[:8]
    if k in BIN_CACHE: return BIN_CACHE[k]
    for plen in (8, 6, 4):
        if len(bp) < plen: continue
        try:
            async with httpx.AsyncClient(timeout=BIN_TIMEOUT, headers={"Accept-Version":"3","User-Agent":"curl/8.5.0"}) as c:
                r = await c.get(f"https://lookup.binlist.net/{bp[:plen]}")
        except: continue
        if r.status_code == 429: return {"error": "rate_limit"}
        if r.status_code != 200: continue
        try: d = r.json()
        except: continue
        cd = d.get("country") or {}; bd = d.get("bank") or {}; nd = d.get("number") or {}
        out = {"scheme": (d.get("scheme") or "—").upper(), "type": (d.get("type") or "—").upper(),
               "brand": d.get("brand") or "—", "bank": bd.get("name") or "—",
               "country": cd.get("name") or "—", "country_code": (cd.get("alpha2") or "").upper() or None,
               "emoji": cd.get("emoji") or "", "currency": cd.get("currency") or "—",
               "lat": cd.get("latitude","—"), "lng": cd.get("longitude","—"),
               "bank_url": bd.get("url") or "—", "bank_phone": bd.get("phone") or "—",
               "bank_city": bd.get("city") or "—",
               "length": nd.get("length"), "luhn_valid": nd.get("luhn"),
               "prepaid": d.get("prepaid"), "source": "binlist.net"}
        BIN_CACHE[k] = out; return out
    BIN_CACHE[k] = None; return None

def fmt_bin_block(bp, vinfo, info, card=None):
    brand = vinfo.get("brand") or "—"
    lengths = vinfo.get("lengths") or []
    default_len = vinfo.get("default_length") or 16
    lengths_str = ", ".join(map(str, lengths)) if lengths else "—"

    L = []
    if card:
        L.append(f"<b>Card</b>     <code>{card}</code>")
        L.append(f"<b>Luhn</b>     {'✅ valid' if luhn_check(card) else '❌ invalid'}")
        L.append(f"<b>BIN</b>      <code>{bp}</code>")
    else:
        L.append(f"<b>BIN</b>      <code>{bp}</code>")
    L.append(f"<b>Brand</b>    {brand}")
    L.append(f"<b>Lengths</b>  {lengths_str}")
    L.append(f"<b>Default</b>  {default_len}")

    if not info or info.get("error"):
        L.append("")
        L.append("<i>⚠️ Lookup rate limited — offline data only.</i>"
                 if info and info.get("error") == "rate_limit"
                 else "<i>⚠️ Lookup unavailable — offline data only.</i>")
        return "\n".join(L)

    prepaid = info.get("prepaid")
    badge = "✅ Yes" if prepaid is True else ("❌ No" if prepaid is False else "—")
    L += ["", f"<b>Scheme</b>   {info.get('scheme') or '—'}",
              f"<b>Type</b>     {info.get('type') or '—'}",
              f"<b>Prepaid</b>  {badge}"]

    cc_part = f" · <code>{info['country_code']}</code>" if info.get("country_code") else ""
    L += ["", f"<b>Country</b>  {info.get('emoji','')} {info.get('country','—')}{cc_part}",
              f"<b>Currency</b> {info.get('currency') or '—'}"]

    L += ["", f"<b>Bank</b>     {info.get('bank') or '—'}"]
    for label, key in (("City", "bank_city"), ("Phone", "bank_phone"), ("URL", "bank_url")):
        v = info.get(key) or ""
        if v and v != "—":
            L.append(f"<b>{label}</b>     <code>{v}</code>" if key == "bank_phone" else f"<b>{label}</b>     {v}")
    return "\n".join(L)

# ═══════════════════════════════════════════════════════════════════
# IDENTITY
# ═══════════════════════════════════════════════════════════════════
RU_NATS = {"AU","BR","CA","CH","DE","DK","ES","FI","FR","GB","IE","IN","IR","MX","NL","NO","NZ","RS","TR","UA","US"}
FK_LOC = {"US":"en_US","GB":"en_GB","AU":"en_AU","CA":"en_CA","IE":"en_IE","FR":"fr_FR","DE":"de_DE",
          "ES":"es_ES","IT":"it_IT","PT":"pt_PT","BR":"pt_BR","MX":"es_MX","AR":"es_AR","RU":"ru_RU",
          "PL":"pl_PL","NL":"nl_NL","SE":"sv_SE","NO":"nb_NO","DK":"da_DK","FI":"fi_FI","GR":"el_GR",
          "TR":"tr_TR","SA":"ar_SA","EG":"ar_EG","AE":"ar_AE","MA":"ar_MA","JO":"ar_JO","KW":"ar_KW",
          "QA":"ar_QA","CN":"zh_CN","TW":"zh_TW","JP":"ja_JP","KR":"ko_KR","TH":"th_TH","VN":"vi_VN",
          "ID":"id_ID","MY":"ms_MY","PH":"en_PH","SG":"en_SG","IN":"hi_IN","PK":"ur_PK","BD":"bn_BD","IR":"fa_IR"}

async def _ru_id(cc):
    if cc not in RU_NATS: return None
    try:
        async with httpx.AsyncClient(timeout=IDENTITY_TIMEOUT) as c:
            r = await c.get("https://randomuser.me/api/", params={"results":1,"nat":cc.lower(),"noinfo":""})
            r.raise_for_status(); d = r.json()
    except: return None
    if not d.get("results"): return None
    u = d["results"][0]; loc = u.get("location",{}); st = loc.get("street",{}); n = u.get("name",{})
    dob = u.get("dob",{}); co = loc.get("coordinates",{})
    return {"name": f"{n.get('first','')} {n.get('last','')}".strip(),
            "phone": u.get("phone","—"), "email": u.get("email","—"),
            "street": f"{st.get('number','')} {st.get('name','')}".strip(),
            "city": loc.get("city","—"), "postal": str(loc.get("postcode","—")),
            "country": (u.get("nat") or cc).upper(),
            "dob": (dob.get("date") or "")[:10] or "—", "age": dob.get("age","—"),
            "lat": co.get("latitude","—"), "lng": co.get("longitude","—")}

async def _fk_id(locale, cc):
    try:
        async with httpx.AsyncClient(timeout=IDENTITY_TIMEOUT) as c:
            r = await c.get("https://fakerapi.it/api/v1/persons", params={"_quantity":1,"_locale":locale})
            r.raise_for_status(); d = r.json()
    except: return None
    if d.get("status") != "OK" or not d.get("data"): return None
    p = d["data"][0]; a = p.get("address",{}) or {}
    return {"name": f"{p.get('firstname','')} {p.get('lastname','')}".strip(),
            "phone": p.get("phone","—"), "email": p.get("email","—"),
            "street": a.get("street","—") or a.get("streetName","—"),
            "city": a.get("city","—"), "postal": str(a.get("zipcode","—")),
            "country": cc, "dob": p.get("birthday","—"), "age": "—",
            "lat": a.get("latitude","—"), "lng": a.get("longitude","—")}

async def fetch_identity(cc):
    if not cc: return None
    cc = cc.upper()
    i = await _ru_id(cc)
    if i: return i
    l = FK_LOC.get(cc)
    if l:
        i = await _fk_id(l, cc)
        if i: return i
    return await _fk_id("en_US", cc)

async def country_info(cc):
    if not cc: return None
    cc = cc.upper()
    if cc in COUNTRY_CACHE: return COUNTRY_CACHE[cc]
    try:
        async with httpx.AsyncClient(timeout=COUNTRY_TIMEOUT) as c:
            r = await c.get(f"https://restcountries.com/v3.1/alpha/{cc}",
                           params={"fields":"name,capital,currencies,region,flag,timezones"})
    except: COUNTRY_CACHE[cc]=None; return None
    if r.status_code != 200: COUNTRY_CACHE[cc]=None; return None
    try: arr = r.json()
    except: COUNTRY_CACHE[cc]=None; return None
    if not arr or not isinstance(arr, list): COUNTRY_CACHE[cc]=None; return None
    d = arr[0]; cs = d.get("currencies") or {}; ck = next(iter(cs),"—"); cn = cs.get(ck,{}).get("name","—") if cs else "—"
    info = {"name": (d.get("name") or {}).get("common","—"), "capital": (d.get("capital") or ["—"])[0],
            "region": d.get("region","—"), "flag": d.get("flag",""),
            "currency": f"{ck} ({cn})", "timezones": (d.get("timezones") or ["—"])[0]}
    COUNTRY_CACHE[cc] = info; return info

async def postal_check(cc, postal):
    if not cc or not postal or postal == "—": return None
    try:
        async with httpx.AsyncClient(timeout=POSTAL_TIMEOUT) as c:
            r = await c.get(f"https://api.zippopotam.us/{cc.lower()}/{postal}")
    except: return None
    if r.status_code != 200: return None
    try: d = r.json()
    except: return None
    pl = d.get("places") or []
    if not pl: return None
    p = pl[0]
    return {"city": p.get("place name","—"), "state": p.get("state","—"), "state_ab": p.get("state abbreviation","—")}

async def resolve_country(bp, param=None, default=None):
    bd = await lookup_bin(bp)
    if param: return param.upper(), bd
    if isinstance(bd, dict) and bd.get("country_code"): return bd["country_code"], bd
    if default: return default.upper(), bd
    return None, bd

# ═══════════════════════════════════════════════════════════════════
# CARD IMAGE
# ═══════════════════════════════════════════════════════════════════
def make_card_image(card, name, mm, yy, cvv, brand):
    if not HAS_PIL: return None
    W, H = 800, 500
    img = Image.new("RGB", (W, H), "#1a1a2e")
    d = ImageDraw.Draw(img)
    for y in range(H):
        d.line([(0,y),(W,y)], fill=(int(26+y/10), int(26+y/15), int(46+y/8)))
    d.rounded_rectangle([60,130,130,190], radius=10, fill="#d4af37", outline="#b8941f", width=2)
    for i in range(3):
        d.line([(70,145+i*12),(120,145+i*12)], fill="#b8941f", width=2)
    try:
        fbig = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
        fmid = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32)
        fsm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except: fbig = fmid = fsm = ImageFont.load_default()
    d.text((W-200,40), brand, fill="#ffffff", font=fbig)
    d.text((W-200,95), "PRO", fill="#d4af37", font=fsm)
    groups = " ".join(card[i:i+4] for i in range(0, len(card), 4))
    d.text((60,240), groups, fill="#ffffff", font=fmid)
    d.text((60,340), name.upper(), fill="#e0e0e0", font=fsm)
    d.text((60,380), "VALID THRU", fill="#a0a0a0", font=ImageFont.load_default())
    d.text((60,400), f"{mm}/{yy}", fill="#ffffff", font=fmid)
    d.text((W-140,400), cvv, fill="#ffffff", font=fmid)
    d.ellipse([W-180,340,W-60,460], outline="#d4af37", width=2)
    buf = BytesIO(); img.save(buf, format="PNG", optimize=True); buf.seek(0)
    buf.name = f"card_{card[-4:]}.png"; return buf

# ═══════════════════════════════════════════════════════════════════
# MASK HELPERS
# ═══════════════════════════════════════════════════════════════════
def mask_card_full(card):
    if not card: return "████ ████ ████ ████"
    groups = (len(card) + 3) // 4
    return " ".join(["█"*4]*groups)

def mask_card_partial(card):
    if len(card) <= 8: return "█"*len(card)
    return card[:4] + " " + "*"*(len(card)-8) + " " + card[-4:]

def mask_proxy_partial(p):
    try:
        ip, port = p.rsplit(":", 1)
        parts = ip.split(".")
        if len(parts) == 4:
            pm = port[:2] + "█"*max(0, len(port)-2)
            return f"{parts[0]}.**.**.{parts[3]}:{pm}"
    except: pass
    return "█"*min(len(p), 20)

def mask_bin(bp):
    if not bp: return "••••••"
    return "•"*len(bp)

def _user_label(u):
    return f"@{u.username}" if u.username else (u.first_name or "User")

# ═══════════════════════════════════════════════════════════════════
# PROGRESS HELPERS
# ═══════════════════════════════════════════════════════════════════
def _bar(done, total, width=20):
    if total <= 0: return "░"*width
    pct = min(done/total, 1.0)
    return "█"*int(pct*width) + "░"*(width-int(pct*width))

def _pct(done, total):
    return "0%" if total <= 0 else f"{int(done/total*100)}%"

def _fmt_eta(sec):
    sec = int(max(0, sec))
    if sec < 60: return f"{sec}s"
    m, s = divmod(sec, 60)
    if m < 60: return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"

async def _safe_edit(msg, text, reply_markup=None):
    try: await msg.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except: pass

# ═══════════════════════════════════════════════════════════════════
# EXPORTS
# ═══════════════════════════════════════════════════════════════════
def build_txt(lines, bp, brand, ln, exp):
    buf = io.BytesIO()
    e = f"{exp[0]}/{exp[1]}" if exp else "random"
    h = f"# Luuln BIN {bp} {brand} len {ln} exp {e}\n# {len(lines)}\n\n"
    buf.write((h + "\n".join(lines) + "\n").encode()); buf.seek(0)
    buf.name = f"luuln_{bp}_{len(lines)}.txt"; return buf

def build_csv(lines):
    s = io.StringIO(); w = csv.writer(s); w.writerow(["card","month","year","cvv"])
    for ln in lines: w.writerow(ln.split("|"))
    b = io.BytesIO(); b.write(s.getvalue().encode()); b.seek(0); b.name = "luuln.csv"; return b

def build_json(lines):
    d = [dict(zip(["card","month","year","cvv"], l.split("|"))) for l in lines]
    b = io.BytesIO(); b.write(json.dumps(d, indent=2).encode()); b.seek(0); b.name = "luuln.json"; return b

def build_zip(lines, bp):
    pwd = "".join(random.choices(string.ascii_letters+string.digits, k=20))
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
    buf.seek(0); buf.name = f"luuln_{bp}.zip"; return buf, pwd, method

# ═══════════════════════════════════════════════════════════════════
# STATE / GROUP / DM
# ═══════════════════════════════════════════════════════════════════
S = {}
def state(uid):
    if uid not in S:
        S[uid] = {"last_lines": [], "last_bin": "", "last_brand": "", "last_length": 16,
                  "last_expiry": None, "last_card": None, "last_identity": None,
                  "default_country": None, "page": 0, "awaiting": None}
    return S[uid]

def in_group(update):
    c = update.effective_chat
    if not c or c.type == "private": return False
    return bool(c.username and c.username.lower() == ALLOWED_GROUP.lower())

def in_dm(update):
    return update.effective_chat is not None and update.effective_chat.type == "private"

async def send_dm(ctx, uid, text=None, document=None, photo=None, caption=None, reply_markup=None):
    try:
        if photo: await ctx.bot.send_photo(uid, photo=photo, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
        elif document: await ctx.bot.send_document(uid, document=InputFile(document, filename=document.name), caption=caption, parse_mode="HTML", reply_markup=reply_markup)
        else: await ctx.bot.send_message(uid, text, parse_mode="HTML", reply_markup=reply_markup)
        return True
    except: return False

async def auto_delete(msg, delay=None):
    await asyncio.sleep(delay or GROUP_NOTICE_TTL)
    try: await msg.delete()
    except: pass

async def send_group_notice(update, ctx, title, lines, extra_buttons=None):
    if not in_group(update): return None
    u = update.effective_user
    text = f"┌───────────────────────────┐\n│   {title}\n└───────────────────────────┘\n\n👤 By: {_user_label(u)}\n"
    for line in lines: text += f"{line}\n"
    text += f"🕐 {datetime.utcnow().strftime('%H:%M:%S')}\n\n━━━━━━━━━━━━━━━━━━━━━━\n🔒 Full data sent to your DM\n━━━━━━━━━━━━━━━━━━━━━━"
    btns = list(extra_buttons or [])
    btns.append([InlineKeyboardButton("📩 Open DM", url=f"https://t.me/{BOT_USERNAME or 'your_bot'}?start=1")])
    try:
        msg = await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))
        asyncio.create_task(auto_delete(msg)); return msg
    except: return None

# ═══════════════════════════════════════════════════════════════════
# KEYBOARDS
# ═══════════════════════════════════════════════════════════════════
def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎴 Generate", callback_data="g"),
         InlineKeyboardButton("🕸️ Proxy", callback_data="proxy_menu")],
        [InlineKeyboardButton("🔍 BIN Info", callback_data="b"),
         InlineKeyboardButton("📋 Check Card", callback_data="cd")],
        [InlineKeyboardButton("🧹 File Cleaner", callback_data="m:clear"),
         InlineKeyboardButton("📄 Check File", callback_data="m:checkfile")],
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
        [InlineKeyboardButton("⬅️ Back", callback_data="bk")],
    ])

def kb_proxy_after():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Download", callback_data="proxy:download"),
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
         InlineKeyboardButton("📦 Bulk", callback_data="adm:bulk")],
        [InlineKeyboardButton("🚫 Ban", callback_data="adm:ban"),
         InlineKeyboardButton("✅ Unban", callback_data="adm:unban"),
         InlineKeyboardButton("📋 List", callback_data="adm:banlist")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="adm:bc")],
        [InlineKeyboardButton("🛠️ Commands", callback_data="adm:cmds")],
    ])

def kb_admin_tier():
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
        [InlineKeyboardButton("♾️ Life", callback_data=f"adm:genday:{tier}:life")],
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
    if HAS_PIL: rows.append([InlineKeyboardButton("🖼 Card Image", callback_data=f"img|{bp}")])
    return InlineKeyboardMarkup(rows)

def kb_identity():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Copy", callback_data="ci"),
         InlineKeyboardButton("🖼 Image", callback_data="idimg")],
        [InlineKeyboardButton("🔄 New", callback_data="ni"),
         InlineKeyboardButton("⬅️", callback_data="bk")],
    ])

def kb_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="bk")]])

def kb_dm_only():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📩 Open DM",
        url=f"https://t.me/{BOT_USERNAME or 'your_bot'}?start=1")]])

def kb_clear_prompt():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Cancel", callback_data="bk")]])
   
# ═══════════════════════════════════════════════════════════════════
# PARSE GEN
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
    except: return None
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
        except: pass
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
            e = parse_expiry(before[-2], before[-1])
            if e: expiry = e
        if after:
            try: count = max(1, min(max_cards, int(after[0])))
            except: pass
    else:
        if len(nums) == 1:
            try: count = max(1, min(max_cards, int(nums[0])))
            except: pass
        elif len(nums) == 2:
            e = parse_expiry(nums[0], nums[1])
            if e: expiry = e
        elif len(nums) == 3:
            e = parse_expiry(nums[0], nums[1])
            if e:
                expiry = e
                try: count = max(1, min(max_cards, int(nums[2])))
                except: pass
        elif len(nums) >= 4:
            e = parse_expiry(nums[0], nums[1])
            if e:
                expiry = e
                fixed_cvv = nums[2].zfill(4 if detect_brand(bin_token[:8])[0] == "AMEX" else 3)
                try: count = max(1, min(max_cards, int(nums[3])))
                except: pass
    return {"bins": bins, "count": count, "expiry": expiry, "fixed_cvv": fixed_cvv,
            "partial": partial, "input_len": input_len}

# ═══════════════════════════════════════════════════════════════════
# GEN CORE
# ═══════════════════════════════════════════════════════════════════
async def do_gen(update, ctx, bins, count, expiry, fixed_cvv, partial=None, input_len=None):
    u = update.effective_user; s = state(u.id)
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    for bp in bins:
        okv, vinfo = validate_bin(bp)
        if not okv:
            await update.message.reply_text(f"❌ Invalid BIN <code>{bp}</code>: {vinfo}", parse_mode="HTML"); return
    from_group = in_group(update)
    lines = []; brand_first = None; real_len = 16; main_bp = bins[0]
    per = max(1, count // len(bins))
    extra = count - per * len(bins)
    for i, bp in enumerate(bins):
        brand, lens, dl = detect_brand(bp)
        if not brand_first: brand_first = brand
        info = await lookup_bin(bp)
        rl = dl
        if partial and input_len and input_len in lens: rl = input_len
        elif isinstance(info, dict) and not info.get("error") and info.get("length") in lens: rl = info["length"]
        if i == 0: real_len = rl
        this_count = per + (1 if i < extra else 0)
        cards = await asyncio.get_event_loop().run_in_executor(POOL, generate_cards, bp, this_count, rl, partial)
        if len(cards) < this_count and partial:
            cards = overflow_cards(cards, this_count, brand or "VISA", expiry)
        for c in cards[:this_count]:
            if isinstance(c, tuple):
                card, mm, yy, cvv = c
            else:
                card = c; mm, yy = (expiry if expiry else rand_exp()); cvv = rand_cvv(brand or "VISA", fixed_cvv)
            lines.append(f"{card}|{mm}|{yy}|{cvv}")
    lines = lines[:count]
    s["last_lines"] = lines; s["last_bin"] = main_bp; s["last_brand"] = brand_first or ""
    s["last_length"] = real_len; s["last_expiry"] = expiry
    db_log(u.id, u.username, main_bp, len(lines), "gen")

    h = f"┌─ <b>L U U L N</b> ─┐\n│ BIN: <code>{main_bp}</code>\n│ Brand: {brand_first or '—'}\n│ Length: {real_len}\n│ Count: {len(lines):,}\n└────────────────────┘"
    try:
        if len(lines) <= TXT_THRESHOLD:
            txt = h + "\n\n" + "\n".join(f"<code>{c}</code>" for c in lines)
            await ctx.bot.send_message(u.id, txt, parse_mode="HTML", reply_markup=kb_after(lines, main_bp, len(lines)))
        else:
            buf = build_txt(lines, main_bp, brand_first or "", real_len, expiry)
            preview = h + "\n\n" + "\n".join(f"<code>{c}</code>" for c in lines[:10]) + f"\n\n<i>... +{len(lines)-10:,} more</i>"
            await ctx.bot.send_message(u.id, preview, parse_mode="HTML")
            await ctx.bot.send_document(u.id, document=InputFile(buf, filename=buf.name),
                                       caption=f"{len(lines):,} cards", parse_mode="HTML",
                                       reply_markup=kb_after(lines, main_bp, len(lines)))
    except:
        try: await update.effective_message.reply_text("📩 Open DM first", reply_markup=kb_dm_only())
        except: pass
        return
    if from_group:
        await send_group_notice(update, ctx, "🎴 GENERATION", [
            f"🔢 BIN: {mask_bin(main_bp)}", f"💳 Brand: {brand_first or '—'}",
            f"📦 Cards: {len(lines):,}", f"💳 {mask_card_full(lines[0].split('|')[0])}"])

# ═══════════════════════════════════════════════════════════════════
# PUBLIC COMMANDS
# ═══════════════════════════════════════════════════════════════════
async def cmd_start(update, ctx):
    u = update.effective_user; ref_by = None
    if ctx.args and ctx.args[0].startswith("ref_"):
        try: ref_by = int(ctx.args[0][4:])
        except: pass
    db_user_track(u.id, u.username, u.first_name, ref_by=ref_by)
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    existing_tier, _ = db_get_sub(u.id)
    if not existing_tier and u.id != ADMIN_ID and not db_setting_get(f"trial_{u.id}"):
        db_set_sub(u.id, "trial", 3); db_setting_set(f"trial_{u.id}", "1")
        try: await ctx.bot.send_message(u.id, "🎁 <b>3 days Trial!</b>", parse_mode="HTML")
        except: pass
    if ref_by and ref_by != u.id and not db_setting_get(f"ref_rewarded_{u.id}"):
        if db_set_sub(ref_by, "pro", 7):
            try: await ctx.bot.send_message(ref_by, "🎁 <b>Referral: 7d Pro!</b>", parse_mode="HTML")
            except: pass
        db_setting_set(f"ref_rewarded_{u.id}", "1")
    await update.message.reply_text(
        f"👋 <b>Welcome {u.first_name}</b>\n\n"
        f"<b>Commands:</b>\n"
        f"/gen · /genv · /bin · /info · /check\n"
        f"/checkfile · /discover · /fav · /favadd\n"
        f"/stats · /me · /sub · /redeem · /ref · /board\n"
        f"/setcountry · /clear · /ip · /chkip · /rt · /chkp · /ipm · /ipl · /ips\n\n"
        f"🔒 Results → DM · Group masked", parse_mode="HTML", reply_markup=kb_main())

async def cmd_menu(update, ctx):
    await update.message.reply_text("📖 <b>Luuln Menu</b>\n\nPick a section:",
                                    parse_mode="HTML", reply_markup=kb_main())

async def cmd_ping(update, ctx):
    t0 = time.time()
    msg = await update.message.reply_text("🏓 Pinging...")
    ms = int((time.time() - t0) * 1000)
    tier = get_effective_tier(update.effective_user.id)
    await msg.edit_text(
        f"🏓 <b>Pong!</b>\n\n"
        f"⚡ Latency: <code>{ms}ms</code>\n"
        f"👤 Tier: {SUB_EMOJI.get(tier,'')} <b>{tier.upper()}</b>\n"
        f"🕐 {datetime.utcnow().strftime('%H:%M:%S')} UTC",
        parse_mode="HTML")

async def cmd_help(update, ctx):
    text = (
        "📖 <b>Luuln Bot — Help</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔒 All results → DM · Group masked (60s auto-delete)\n\n"
        "🎴 <b>Cards:</b>\n"
        "<code>/gen BIN</code> — 10 cards\n"
        "<code>/gen BIN 50</code> — 50 cards\n"
        "<code>/gen BIN|MM|YY</code> — fixed expiry\n"
        "<code>/gen BIN|MM|YY|cvv|N</code> — with CVV\n"
        "<code>/gen \"453201,542523\"|50</code> — multi-BIN\n"
        "<code>/gen 453201-453210|50</code> — range\n"
        "<code>/genv BIN|MM|YY</code> — all CVVs\n\n"
        "🔍 <b>Lookup:</b>\n"
        "<code>/bin BIN</code> · <code>/info CARD</code>\n"
        "<code>/check CARD</code> · <code>/checkfile</code>\n"
        "<code>/discover visa</code>\n\n"
        "🕸️ <b>Proxy:</b>\n"
        "<code>/ip</code> — menu · <code>/chkip</code> — scrape\n"
        "<code>/chkp IP:PORT</code> · <code>/ipm</code> — file\n"
        "<code>/rt</code> — retest · <code>/ipl</code> · <code>/ips</code>\n\n"
        "🧹 <code>/clear</code> — file cleaner (DM)\n\n"
        "🎫 <code>/sub</code> · <code>/redeem CODE</code> · <code>/ref</code>\n\n"
        "📊 <code>/stats</code> · <code>/me</code> · <code>/board</code>\n\n"
        "👑 <code>/admin</code> (admin only)\n"
        "🏓 <code>/ping</code> — latency"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb_help())

async def cmd_me(update, ctx):
    u = update.effective_user; tier = get_effective_tier(u.id)
    total, cards = db_stats(u.id)
    text = (f"👤 <b>Profile</b>\n\nID: <code>{u.id}</code>\n"
            f"Tier: {SUB_EMOJI.get(tier,'')} <b>{tier.upper()}</b>\n"
            f"Limit: <code>{SUB_TIERS[tier]['rate']}/hr</code>\n"
            f"Requests: {total}\nCards: {cards:,}\nReferrals: {db_referral_count(u.id)}")
    ok = await send_dm(ctx, u.id, text=text, reply_markup=kb_back())
    if in_group(update) and ok: await send_group_notice(update, ctx, "👤 PROFILE", ["📩 DM"])
    elif in_group(update): await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only())

async def cmd_sub(update, ctx):
    u = update.effective_user; tier = get_effective_tier(u.id); _, exp = db_get_sub(u.id)
    if u.id == ADMIN_ID:
        await update.message.reply_text("👑 Admin — VIP", reply_markup=kb_back()); return
    if tier == "free":
        await update.message.reply_text(
            "📋 Free tier\n\n🔓 Unlock with:\n<code>/redeem CODE</code>", parse_mode="HTML", reply_markup=kb_back()); return
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
        await update.message.reply_text(
            "🎟️ <b>Redeem Code</b>\n\nUsage:\n<code>/redeem CODE</code>\n\nExample:\n<code>/redeem ABCD-1234-EFGH</code>",
            parse_mode="HTML"); return
    ok, res = db_redeem(ctx.args[0].strip().upper(), u.id)
    if ok:
        await update.message.reply_text(f"✅ {res[0].upper()} for {res[1]} days", parse_mode="HTML", reply_markup=kb_main())
    else:
        await update.message.reply_text(f"❌ {res}")

async def cmd_ref(update, ctx):
    u = update.effective_user
    link = f"https://t.me/{BOT_USERNAME or 'bot'}?start=ref_{u.id}"
    await update.message.reply_text(
        f"🎁 <b>Referral</b>\n\n<code>{link}</code>\n\n"
        f"Invites: {db_referral_count(u.id)}\n\n"
        f"<i>Each new user = 7d Pro</i>", parse_mode="HTML", reply_markup=kb_back())

async def cmd_leaderboard(update, ctx):
    u = update.effective_user; top = db_top_users(10)
    txt = "🏆 <b>Top 10</b>\n\n"
    for i, (uid, un, cnt, _) in enumerate(top, 1):
        medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"{i}."
        txt += f"{medal} <code>{uid}</code> · {cnt} req\n"
    ok = await send_dm(ctx, u.id, text=txt, reply_markup=kb_back())
    if in_group(update) and ok: await send_group_notice(update, ctx, "🏆 BOARD", ["📩 DM"])
    elif in_group(update): await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only())

async def cmd_stats(update, ctx):
    u = update.effective_user
    total, cards = db_stats(u.id); gl, gc = db_stats(None)
    txt = f"📊 <b>Stats</b>\n\nYou: {total} req · {cards:,} cards\nGlobal: {gl} req · {gc:,} cards"
    ok = await send_dm(ctx, u.id, text=txt, reply_markup=kb_back())
    if in_group(update) and ok: await send_group_notice(update, ctx, "📊 STATS", ["📩 DM"])
    elif in_group(update): await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only())

async def cmd_setcountry(update, ctx):
    if not ctx.args or len(ctx.args[0]) != 2 or not ctx.args[0].isalpha():
        await update.message.reply_text("<code>/setcountry CC</code>\n\nExample: <code>/setcountry tr</code>", parse_mode="HTML"); return
    state(update.effective_user.id)["default_country"] = ctx.args[0].upper()
    await update.message.reply_text(f"✅ Default country: <b>{ctx.args[0].upper()}</b>", parse_mode="HTML")

async def cmd_fav(update, ctx):
    u = update.effective_user; rows = db_fav_list(u.id)
    if not rows:
        await update.message.reply_text(
            "⭐ <b>No favorites</b>\n\nAdd:\n<code>/favadd 519468436466760</code>", parse_mode="HTML"); return
    btns = [[InlineKeyboardButton(r[0], callback_data=f"g|{r[0]}")] for r in rows]
    btns.append([InlineKeyboardButton("⬅️", callback_data="bk")])
    ok = await send_dm(ctx, u.id, text="⭐ <b>Favorites</b>", reply_markup=InlineKeyboardMarkup(btns))
    if in_group(update) and ok: await send_group_notice(update, ctx, "⭐ FAVORITES", ["📩 DM"])

async def cmd_favadd(update, ctx):
    if not ctx.args:
        await update.message.reply_text("<code>/favadd BIN [label]</code>\n\nExample:\n<code>/favadd 519468436466760 visa-test</code>", parse_mode="HTML"); return
    u = update.effective_user; bp = ctx.args[0]
    okv, _ = validate_bin(bp)
    if not okv:
        await update.message.reply_text(f"❌ Invalid BIN: <code>{bp}</code>", parse_mode="HTML"); return
    db_fav_add(u.id, bp, " ".join(ctx.args[1:]))
    await update.message.reply_text(f"✅ {mask_bin(bp)} added")

async def cmd_gen(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    if not ctx.args:
        await update.message.reply_text(
            "🎴 <b>Generate</b>\n\n"
            "<code>/gen BIN</code>\n<code>/gen BIN 50</code>\n<code>/gen BIN|MM|YY</code>\n<code>/gen BIN|MM|YY|cvv|N</code>\n\n"
            "Examples:\n<code>/gen 519468436466760</code>\n<code>/gen 519468436466760|50</code>",
            parse_mode="HTML"); return
    ok, rem, tier, max_cards = check_rate(u.id)
    if not ok:
        mins, secs = rem // 60, rem % 60
        await update.message.reply_text(
            f"⚠️ <b>Rate limit — {tier.upper()}</b>\n\n"
            f"Limit: <code>{SUB_TIERS[tier]['rate']}/hr</code>\n"
            f"Try again in <b>{mins}m {secs}s</b>\n\n"
            f"Upgrade: <code>/redeem CODE</code>", parse_mode="HTML"); return
    p = parse_gen(" ".join(ctx.args), max_cards)
    if not p:
        await update.message.reply_text(
            "❌ Parse error\n\nFormat: <code>/gen BIN[|MM|YY][|COUNT]</code>\nExample: <code>/gen 519468436466760|5</code>",
            parse_mode="HTML"); return
    if p["count"] >= max_cards and max_cards < 10000:
        await update.message.reply_text(
            f"ℹ️ Generating <b>{p['count']:,}</b> — max for <b>{tier.upper()}</b> tier.\n\n"
            f"Upgrade: <code>/redeem CODE</code>", parse_mode="HTML")
    await do_gen(update, ctx, **p)

async def cmd_genv(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    if not ctx.args:
        await update.message.reply_text(
            "🎴 <b>Generate CVVs</b>\n\n<code>/genv BIN|MM|YY [COUNT]</code>\n\n"
            "Example:\n<code>/genv 519468436466760|02|28</code>", parse_mode="HTML"); return
    ok, rem, tier, max_cards = check_rate(u.id)
    if not ok:
        mins, secs = rem // 60, rem % 60
        await update.message.reply_text(
            f"⚠️ Rate limit — {tier.upper()}\n\nTry again in <b>{mins}m {secs}s</b>", parse_mode="HTML"); return
    parts = [p for p in re.split(r"\s+", " ".join(ctx.args).replace("|"," | ")) if p and p != "|"]
    if not parts:
        await update.message.reply_text("❌ Usage: <code>/genv BIN|MM|YY</code>", parse_mode="HTML"); return
    bin_token = parts[0]; nums = [p for p in parts[1:] if p.isdigit()]
    mm = yy = count = None
    if len(nums) >= 2:
        e = parse_expiry(nums[0], nums[1])
        if e: mm, yy = e
        if len(nums) >= 3:
            try: count = int(nums[2])
            except: pass
    if not mm or not yy:
        await update.message.reply_text("❌ Missing expiry\nFormat: <code>/genv BIN|MM|YY</code>", parse_mode="HTML"); return
    r = resolve_input(bin_token)
    if not r:
        await update.message.reply_text("❌ Invalid BIN", parse_mode="HTML"); return
    bp = r["bin_prefix"]; okv, vinfo = validate_bin(bp)
    if not okv:
        await update.message.reply_text(f"❌ {vinfo}"); return
    brand = vinfo["brand"]; dl = vinfo["default_length"]
    card = complete_partial(r["partial"], dl) if r["partial"] else gen_from_bin(bp, dl)
    c_len = 4 if brand == "AMEX" else 3; max_cvv = 10000 if c_len == 4 else 1000
    limit = min(count or max_cvv, max_cvv)
    lines = [f"{card}|{mm}|{yy}|{('{:04d}' if c_len==4 else '{:03d}').format(i)}" for i in range(limit)]
    db_log(u.id, u.username, bp, len(lines), "genv")
    s = state(u.id); s["last_lines"] = lines; s["last_bin"] = bp; s["last_brand"] = brand
    s["last_expiry"] = (mm, yy); s["last_length"] = dl
    buf = build_txt(lines, bp, brand, dl, (mm, yy))
    try:
        await ctx.bot.send_document(u.id, document=InputFile(buf, filename=buf.name),
                                    caption=f"🔢 {len(lines):,} CVVs · <code>{mask_card_partial(card)}</code>",
                                    parse_mode="HTML", reply_markup=kb_after(lines, bp, len(lines)))
        await send_group_notice(update, ctx, "🎴 CVV GEN", [
            f"💳 {mask_card_full(card)}", f"📦 CVVs: {len(lines):,}"])
    except:
        await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only())

async def cmd_bin(update, ctx):
    u = update.effective_user
    if not ctx.args:
        await update.message.reply_text("<code>/bin BIN</code>\n\nExample:\n<code>/bin 519468</code>", parse_mode="HTML"); return
    r = resolve_input(" ".join(ctx.args))
    if not r:
        await update.message.reply_text("❌ Need 4+ digits", parse_mode="HTML"); return
    bp = r["bin_prefix"]; okv, vinfo = validate_bin(bp)
    if not okv:
        await update.message.reply_text(f"❌ {vinfo}"); return
    info = await lookup_bin(bp)
    txt = f"🔍 <b>BIN Details</b>\n━━━━━━━━━━━━━━━━━━━\n\n{fmt_bin_block(bp, vinfo, info)}"
    ok = await send_dm(ctx, u.id, text=txt, reply_markup=kb_back())
    if ok and in_group(update): await send_group_notice(update, ctx, "🔍 BIN", [f"🔢 {mask_bin(bp)}"])
    elif in_group(update): await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only())

async def cmd_check(update, ctx):
    u = update.effective_user
    if not ctx.args:
        await update.message.reply_text("<code>/check CARD</code>\n\nExample:\n<code>/check 4532015112830366</code>", parse_mode="HTML"); return
    d = re.sub(r"\D","", " ".join(ctx.args))
    if len(d) < 4:
        await update.message.reply_text("❌ Need 4+ digits"); return
    brand, lens, _ = detect_brand(d[:8]); v = luhn_check(d); lo = len(d) in lens if lens else False
    info = await lookup_bin(d[:8])
    txt = f"🔍 <b>Check</b>\n<code>{d}</code>\n\nLuhn: {'✅' if v else '❌'}\nBrand: {brand}\nLength: {'✅' if lo else '❌'}\n"
    if info and not info.get("error"):
        txt += f"\n🏦 {info.get('bank','—')}\n🌍 {info.get('emoji','')} {info.get('country','—')}"
    ok = await send_dm(ctx, u.id, text=txt, reply_markup=kb_back())
    if ok and in_group(update): await send_group_notice(update, ctx, "🔍 CHECK", [f"💳 {mask_card_full(d)}"])
    elif in_group(update): await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only())

async def cmd_info(update, ctx):
    u = update.effective_user
    if not ctx.args:
        await update.message.reply_text("<code>/info CARD[|CC]</code>\n\nExample:\n<code>/info 4532015112830366</code>", parse_mode="HTML"); return
    parts = [p for p in re.split(r"\s+", " ".join(ctx.args).replace("|"," | ")) if p and p != "|"]
    param = None
    if parts and parts[-1].isalpha() and len(parts[-1]) == 2:
        param = parts[-1].upper(); parts = parts[:-1]
    d = re.sub(r"\D","", "".join(parts))
    if not d or len(d) < 4:
        await update.message.reply_text("❌ Need 4+ digits"); return
    bp = d[:8]; brand, lens, dl = detect_brand(bp)
    if brand == "UNKNOWN":
        await update.message.reply_text("❌ Unknown BIN"); return
    if len(d) <= 8: card = gen_from_bin(d, dl)
    elif len(d) < 16: card = complete_partial(d, dl)
    else: card = d[:-1] + str(luhn_digit(d[:-1]))

    vinfo = {"brand": brand, "lengths": lens, "default_length": dl}
    bin_info = await lookup_bin(bp)
    bin_block = fmt_bin_block(bp, vinfo, bin_info, card=card)

    cc, _ = await resolve_country(bp, param, state(u.id).get("default_country"))
    if not cc:
        await update.message.reply_text(f"⚠️ Country unknown. <code>/info {d}|tr</code>", parse_mode="HTML"); return
    ident = await fetch_identity(cc)
    if not ident:
        await update.message.reply_text(f"⚠️ No identity {cc}"); return
    pi = await postal_check(cc, ident["postal"]); ci = await country_info(cc)
    state(u.id)["last_card"] = card; state(u.id)["last_identity"] = ident; state(u.id)["last_brand"] = brand

    country_line = f"🌍 <code>{ci['flag']} {ci['name']}</code>" if ci else f"🌍 <code>{ident['country']}</code>"
    txt = (f"👤 <b>Card Info</b>\n━━━━━━━━━━━━━━━━━━━\n\n{bin_block}\n\n"
           f"━━━━━━━━━━━━━━━━━━━\n🪪 <b>Identity</b>\n\n"
           f"👤 <code>{ident['name']}</code>\n📞 <code>{ident['phone']}</code>\n"
           f"📧 <code>{ident.get('email','—')}</code>\n"
           f"🎂 <code>{ident.get('dob','—')}</code> ({ident.get('age','—')})\n"
           f"🏠 <code>{ident['street']}</code>\n🏙 <code>{ident['city']}</code>\n"
           f"📮 <code>{ident['postal']}</code>\n{country_line}\n"
           f"📍 <code>{ident.get('lat','—')}, {ident.get('lng','—')}</code>")
    if pi: txt += f"\n\n📬 <code>{pi['city']}, {pi['state']} ({pi['state_ab']})</code>"
    if ci: txt += f"\n\n🌐 {ci['flag']} <b>{ci['name']}</b>\n🏛 <code>{ci['capital']}</code> · 💰 <code>{ci['currency']}</code>"
    ok = await send_dm(ctx, u.id, text=txt, reply_markup=kb_identity())
    if ok and in_group(update): await send_group_notice(update, ctx, "👤 CARD", [f"💳 {mask_card_partial(card)}"])
    elif in_group(update): await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only())

async def cmd_checkfile(update, ctx):
    u = update.effective_user
    if not in_dm(update):
        await update.message.reply_text("📎 DM only\n\n📩 Open DM", reply_markup=kb_dm_only()); return
    state(u.id)["awaiting"] = "checkfile"
    await update.message.reply_text("📎 Upload .txt/.csv (one card per line, max 5MB)\n\n<i>/cancel to abort</i>",
                                     parse_mode="HTML", reply_markup=kb_back())

async def cmd_cancel(update, ctx):
    state(update.effective_user.id)["awaiting"] = None
    await update.message.reply_text("Cancelled.", reply_markup=kb_main())

async def cmd_discover(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    query = (ctx.args[0].lower() if ctx.args else "")
    if not query:
        await update.message.reply_text(
            f"🧭 <b>BIN Discovery</b>\n\n<code>/discover visa</code>\n<code>/discover 4</code>\n\n"
            f"Brands: {', '.join(KNOWN_BINS.keys())}", parse_mode="HTML"); return
    results = []
    for brand, bins in KNOWN_BINS.items():
        if query in brand or query.isdigit():
            for b in bins:
                if query.isdigit() and not b.startswith(query): continue
                results.append((brand, b))
    if not results:
        await update.message.reply_text("❌ No BINs found"); return
    results = results[:20]
    btns = [[InlineKeyboardButton(f"{b} · {brand.upper()}", callback_data=f"g|{b}")] for brand, b in results]
    btns.append([InlineKeyboardButton("⬅️", callback_data="bk")])
    if in_group(update):
        await update.message.reply_text(f"🧭 Found {len(results)} BINs → DM",
                                          parse_mode="HTML", reply_markup=kb_dm_only())
        try: await ctx.bot.send_message(u.id, f"🧭 <b>BIN Discovery</b> — {query}",
                                         parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))
        except: pass
    else:
        await update.message.reply_text(f"🧭 <b>{len(results)} BINs</b>",
                                          parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))

async def cmd_clear(update, ctx):
    u = update.effective_user
    if not in_dm(update):
        await update.message.reply_text("📎 DM only", reply_markup=kb_dm_only()); return
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    state(u.id)["awaiting"] = "clear_file"
    await update.message.reply_text("🧹 <b>File Cleaner</b>\n\n📎 Send .txt/.csv (max 5MB)\nRemoves empty + duplicates\n\n<i>/cancel</i>",
                                     parse_mode="HTML", reply_markup=kb_clear_prompt())

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
PROXY_TEST_EP = [('http://checkip.amazonaws.com/', 'text'), ('https://api.ipify.org/', 'text'),
                 ('http://icanhazip.com/', 'text'), ('http://httpbin.org/ip', 'json')]
PROXY_UA = ['Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36']
PROXY_STATE = {}

def _pstate(uid):
    if uid not in PROXY_STATE:
        PROXY_STATE[uid] = {"running": False, "stop_flag": False, "retesting": False, "file_mode": False,
                            "valid": [], "stats": {"collected":0,"valid":0,"rejected":0,"sources_ok":0,"sources_fail":0},
                            "last_run": None}
    return PROXY_STATE[uid]

def _p_ip_ok(ip):
    if not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', ip): return False
    return all(0 <= int(p) <= 255 for p in ip.split('.'))

def _save_proxy_live(uid, proxy):
    try:
        c = _db(); r = c.execute("SELECT proxies FROM proxy_cache WHERE user_id=?", (uid,)).fetchone()
        existing = (r[0]+"\n"+proxy) if r and r[0] else proxy
        c.execute("INSERT OR REPLACE INTO proxy_cache (user_id,proxies,updated_at) VALUES (?,?,?)",
                  (uid, existing, datetime.utcnow().isoformat())); c.commit(); c.close()
    except: pass

def _p_scrape_all():
    proxies, stats = set(), {"sources_ok":0,"sources_fail":0}
    headers = {'User-Agent': random.choice(PROXY_UA)}
    for src in PROXY_SOURCES:
        try:
            r = requests.get(src, headers=headers, timeout=15, verify=False)
            if r.status_code != 200: stats["sources_fail"] += 1; continue
            if any(x in src for x in ('raw.githubusercontent','proxyscrape','proxy-list.download')):
                for line in r.text.splitlines():
                    line = line.strip()
                    if ':' in line:
                        parts = line.split(':')
                        if len(parts) >= 2:
                            ip, port = parts[0].strip(), parts[1].strip()
                            if _p_ip_ok(ip) and port.isdigit(): proxies.add(f"{ip}:{port}")
            else:
                soup = BeautifulSoup(r.text, 'html.parser')
                for row in soup.find_all('tr'):
                    cols = row.find_all('td')
                    if len(cols) >= 2:
                        ip, port = cols[0].text.strip(), cols[1].text.strip()
                        if _p_ip_ok(ip) and port.isdigit(): proxies.add(f"{ip}:{port}")
            stats["sources_ok"] += 1
        except: stats["sources_fail"] += 1
    return list(proxies), stats

def _p_test_one(proxy):
    try:
        pr = {'http': f'http://{proxy}', 'https': f'http://{proxy}'}
        h = {'User-Agent': random.choice(PROXY_UA), 'Connection': 'close'}
        for url, _ in PROXY_TEST_EP:
            try:
                r = requests.get(url, proxies=pr, timeout=6, headers=h, verify=False)
                if r.status_code == 200: return True
            except: continue
        return False
    except: return False

def _p_test_detailed(proxy):
    results = []; ok = 0; fastest = None; detected = None
    try:
        pr = {'http': f'http://{proxy}', 'https': f'http://{proxy}'}
        h = {'User-Agent': random.choice(PROXY_UA), 'Connection': 'close'}
        for url, kind in PROXY_TEST_EP:
            start = time.time()
            try:
                r = requests.get(url, proxies=pr, timeout=8, headers=h, verify=False)
                lat = int((time.time()-start)*1000)
                if r.status_code == 200:
                    ok += 1
                    if fastest is None or lat < fastest: fastest = lat
                    if not detected:
                        try:
                            if kind == 'json':
                                data = r.json(); detected = data.get('origin') or data.get('ip') or str(data)[:40]
                            else: detected = r.text.strip()[:40]
                        except: pass
                    results.append((url.split('//')[1].split('/')[0], True, lat))
                else: results.append((url.split('//')[1].split('/')[0], False, lat))
            except: results.append((url.split('//')[1].split('/')[0], False, None))
        return {"proxy": proxy, "alive": ok>0, "working": ok, "total": len(PROXY_TEST_EP),
                "latency": fastest, "detected_ip": detected, "results": results}
    except Exception as e:
        return {"proxy": proxy, "alive": False, "error": str(e), "working": 0, "total": len(PROXY_TEST_EP), "results": []}

def _parse_proxy_file(content):
    out = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('#'): continue
        m = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})[:\s,|]+(\d{1,5})', line)
        if m:
            ip, port = m.group(1), m.group(2)
            if _p_ip_ok(ip) and (1 <= int(port) <= 65535): out.append(f"{ip}:{port}")
    return out

async def _do_scrape_live(ctx, uid, msg, workers=20):
    st = _pstate(uid)
    st["running"] = True; st["stop_flag"] = False; st["retesting"] = False; st["file_mode"] = False
    st["last_run"] = datetime.utcnow().isoformat(); st["valid"] = []
    try:
        await _safe_edit(msg, f"🕸️ <b>Phase 1/2: Collecting</b>\n\n<code>{_bar(0,9,20)}</code> 0%\n\n<i>Fetching...</i>",
                          reply_markup=kb_scrape_running())
        all_p, src = await asyncio.get_event_loop().run_in_executor(POOL, _p_scrape_all)
        if not all_p:
            await _safe_edit(msg, f"❌ No proxies · OK: {src['sources_ok']} · Fail: {src['sources_fail']}",
                              reply_markup=kb_proxy_menu()); return
        total = len(all_p)
        st["stats"] = {"collected": total, "valid": 0, "rejected": 0, "sources_ok": src["sources_ok"], "sources_fail": src["sources_fail"]}
        prog = {"done": 0, "valid": 0, "rejected": 0, "last5": []}
        lock = threading.Lock()
        def test_cb(p):
            if st["stop_flag"]: return None
            ok = _p_test_one(p)
            with lock:
                prog["done"] += 1
                if ok:
                    prog["valid"] += 1; st["valid"].append(p); _save_proxy_live(uid, p)
                    prog["last5"].append(p)
                    if len(prog["last5"]) > 5: prog["last5"].pop(0)
                else: prog["rejected"] += 1
            return ok
        start = time.time()
        def run_batch():
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                futures = [ex.submit(test_cb, p) for p in all_p]
                for f in concurrent.futures.as_completed(futures):
                    try: f.result()
                    except: pass
        bf = asyncio.get_event_loop().run_in_executor(POOL, run_batch)
        while not bf.done():
            await asyncio.sleep(1.5)
            if bf.done(): break
            if st["stop_flag"]: break
            done = prog["done"]
            if done == 0: continue
            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 0
            eta = (total - done) / rate if rate > 0 else 0
            recent = prog["last5"][-3:] if prog["last5"] else []
            recent_txt = "\n".join(f"  ✅ <code>{mask_proxy_partial(p)}</code>" for p in recent) if recent else "  <i>waiting...</i>"
            await _safe_edit(msg,
                f"🕸️ <b>Phase 2/2: Testing</b>\n\n<code>{_bar(done,total)}</code> <b>{_pct(done,total)}</b>\n"
                f"📊 <code>{done:,}</code>/<code>{total:,}</code>\n\n"
                f"✅ Valid: <b>{prog['valid']}</b>\n❌ Rejected: {prog['rejected']}\n"
                f"⚡ <code>{rate:.1f}/s</code> · ⏱️ <b>{_fmt_eta(eta)}</b>\n\n"
                f"<b>Latest:</b>\n{recent_txt}", reply_markup=kb_scrape_running())
        await bf
        elapsed = time.time() - start
        was_stopped = st["stop_flag"]; valid = st["valid"]
        st["stats"]["valid"] = len(valid); st["stats"]["rejected"] = prog["done"] - len(valid)
        header = "⏹️ Stopped" if was_stopped else "✅ Complete"
        if valid:
            buf = io.BytesIO("\n".join(valid).encode()); buf.name = f"proxies_{len(valid)}.txt"; buf.seek(0)
            try:
                await ctx.bot.send_document(uid, document=InputFile(buf, filename=buf.name),
                    caption=f"{header}\n\nTested: {prog['done']:,}/{total:,}\n✅ Valid: <b>{len(valid)}</b>\n❌ Rejected: {prog['rejected']}\n⏱️ {_fmt_eta(elapsed)}",
                    parse_mode="HTML")
            except: pass
            await _safe_edit(msg, f"{header}\n\n✅ Valid: <b>{len(valid)}</b>\n❌ Rejected: {prog['rejected']}\n⏱️ {_fmt_eta(elapsed)}\n\n📄 File ☝️",
                              reply_markup=kb_proxy_after())
        else:
            await _safe_edit(msg, f"{header}\n\nNo valid proxies · Tested: {prog['done']:,}", reply_markup=kb_proxy_menu())
    except Exception as e:
        log.exception("scrape"); await _safe_edit(msg, f"❌ {e}", reply_markup=kb_proxy_menu())
    finally:
        st["running"] = False; st["stop_flag"] = False

async def _do_retest(ctx, uid, msg, workers=20):
    st = _pstate(uid)
    st["running"] = True; st["retesting"] = True; st["stop_flag"] = False; st["file_mode"] = False
    existing = list(st["valid"])
    if not existing:
        try:
            c = _db(); r = c.execute("SELECT proxies FROM proxy_cache WHERE user_id=?", (uid,)).fetchone(); c.close()
            if r and r[0]: existing = [p for p in r[0].split("\n") if p]
        except: pass
    if not existing:
        await _safe_edit(msg, "❌ No saved proxies. Use /chkip", reply_markup=kb_proxy_menu())
        st["running"] = False; st["retesting"] = False; return
    total = len(existing); alive = []
    prog = {"done": 0, "alive": 0, "dead": 0, "last5": []}
    lock = threading.Lock()
    def test_cb(p):
        if st["stop_flag"]: return None
        ok = _p_test_one(p)
        with lock:
            prog["done"] += 1
            if ok:
                prog["alive"] += 1; alive.append(p); prog["last5"].append(p)
                if len(prog["last5"]) > 5: prog["last5"].pop(0)
            else: prog["dead"] += 1
        return ok
    start = time.time()
    def run_batch():
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(test_cb, p) for p in existing]
            for f in concurrent.futures.as_completed(futures):
                try: f.result()
                except: pass
    bf = asyncio.get_event_loop().run_in_executor(POOL, run_batch)
    while not bf.done():
        await asyncio.sleep(1.5)
        if bf.done(): break
        if st["stop_flag"]: break
        done = prog["done"]
        if done == 0: continue
        elapsed = time.time() - start
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate if rate > 0 else 0
        recent = prog["last5"][-3:] if prog["last5"] else []
        recent_txt = "\n".join(f"  ✅ <code>{mask_proxy_partial(p)}</code>" for p in recent) if recent else "  <i>testing...</i>"
        await _safe_edit(msg,
            f"🔄 <b>Retesting</b>\n\n<code>{_bar(done,total)}</code> <b>{_pct(done,total)}</b>\n"
            f"📊 <code>{done:,}</code>/<code>{total:,}</code>\n\n"
            f"✅ Alive: <b>{prog['alive']}</b>\n💀 Dead: {prog['dead']}\n"
            f"⚡ <code>{rate:.1f}/s</code> · ⏱️ <b>{_fmt_eta(eta)}</b>\n\n"
            f"<b>Latest:</b>\n{recent_txt}", reply_markup=kb_scrape_running())
    await bf
    elapsed = time.time() - start
    st["valid"] = alive
    try:
        c = _db(); c.execute("INSERT OR REPLACE INTO proxy_cache (user_id,proxies,updated_at) VALUES (?,?,?)",
                            (uid, "\n".join(alive), datetime.utcnow().isoformat())); c.commit(); c.close()
    except: pass
    header = "⏹️ Stopped" if st["stop_flag"] else "✅ Complete"
    if alive:
        buf = io.BytesIO("\n".join(alive).encode()); buf.name = f"alive_{len(alive)}.txt"; buf.seek(0)
        try:
            await ctx.bot.send_document(uid, document=InputFile(buf, filename=buf.name),
                caption=f"{header}\n\n✅ Alive: <b>{len(alive)}</b>\n💀 Dead: {prog['dead']}\n📈 {len(alive)*100//max(prog['done'],1)}%",
                parse_mode="HTML")
        except: pass
        await _safe_edit(msg, f"{header}\n\n✅ Alive: <b>{len(alive)}</b>\n💀 Dead: {prog['dead']}\n⏱️ {_fmt_eta(elapsed)}\n\n📄 File ☝️",
                          reply_markup=kb_proxy_after())
    else:
        await _safe_edit(msg, f"{header}\n\n💀 All {prog['done']} dead", reply_markup=kb_proxy_menu())
    st["running"] = False; st["retesting"] = False; st["stop_flag"] = False

async def _do_file_check(ctx, uid, msg, proxies, workers=30):
    st = _pstate(uid)
    st["running"] = True; st["stop_flag"] = False; st["file_mode"] = True; st["valid"] = []
    total = len(proxies); alive, dead = [], []
    prog = {"done": 0, "alive": 0, "dead": 0, "last5": []}
    lock = threading.Lock()
    def test_cb(p):
        if st["stop_flag"]: return None
        ok = _p_test_one(p)
        with lock:
            prog["done"] += 1
            if ok:
                prog["alive"] += 1; alive.append(p); _save_proxy_live(uid, p)
                prog["last5"].append(p)
                if len(prog["last5"]) > 5: prog["last5"].pop(0)
            else: prog["dead"] += 1; dead.append(p)
        return ok
    start = time.time()
    def run_batch():
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(test_cb, p) for p in proxies]
            for f in concurrent.futures.as_completed(futures):
                try: f.result()
                except: pass
    bf = asyncio.get_event_loop().run_in_executor(POOL, run_batch)
    while not bf.done():
        await asyncio.sleep(1.5)
        if bf.done(): break
        if st["stop_flag"]: break
        done = prog["done"]
        if done == 0: continue
        elapsed = time.time() - start
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate if rate > 0 else 0
        recent = prog["last5"][-3:] if prog["last5"] else []
        recent_txt = "\n".join(f"  ✅ <code>{mask_proxy_partial(p)}</code>" for p in recent) if recent else "  <i>testing...</i>"
        await _safe_edit(msg,
            f"📄 <b>File Check</b>\n\n<code>{_bar(done,total)}</code> <b>{_pct(done,total)}</b>\n"
            f"📊 <code>{done:,}</code>/<code>{total:,}</code>\n\n"
            f"✅ Alive: <b>{prog['alive']}</b>\n💀 Dead: {prog['dead']}\n"
            f"⚡ <code>{rate:.1f}/s</code> · ⏱️ <b>{_fmt_eta(eta)}</b>\n\n<b>Latest:</b>\n{recent_txt}",
            reply_markup=kb_scrape_running())
    await bf
    elapsed = time.time() - start
    st["valid"] = alive
    try:
        c = _db(); c.execute("INSERT OR REPLACE INTO proxy_cache (user_id,proxies,updated_at) VALUES (?,?,?)",
                            (uid, "\n".join(alive), datetime.utcnow().isoformat())); c.commit(); c.close()
    except: pass
    header = "⏹️ Stopped" if st["stop_flag"] else "✅ Complete"
    if alive:
        buf = io.BytesIO("\n".join(alive).encode()); buf.name = f"file_alive_{len(alive)}.txt"; buf.seek(0)
        try:
            await ctx.bot.send_document(uid, document=InputFile(buf, filename=buf.name),
                caption=f"{header}\n\n✅ Alive: <b>{len(alive)}</b>\n💀 Dead: {len(dead)}\n📈 {len(alive)*100//max(prog['done'],1)}%",
                parse_mode="HTML")
        except: pass
    if dead:
        dbuf = io.BytesIO("\n".join(dead).encode()); dbuf.name = f"file_dead_{len(dead)}.txt"; dbuf.seek(0)
        try: await ctx.bot.send_document(uid, document=InputFile(dbuf, filename=dbuf.name), caption=f"💀 Dead: <b>{len(dead)}</b>", parse_mode="HTML")
        except: pass
    await _safe_edit(msg, f"{header}\n\n📊 {prog['done']:,}/{total:,}\n✅ Alive: <b>{len(alive)}</b>\n💀 Dead: {len(dead)}\n⏱️ {_fmt_eta(elapsed)}",
                      reply_markup=kb_proxy_after())
    st["running"] = False; st["file_mode"] = False; st["stop_flag"] = False

async def cmd_proxy(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    st = _pstate(u.id)
    running = "⏳ Running" if st["running"] else "🟢 Idle"
    text = (f"🕸️ <b>Proxy Scraper</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>Status:</b> {running}\n<b>Cached:</b> <code>{len(st['valid'])}</code>\n"
            f"<b>Collected:</b> {st['stats'].get('collected',0)}\n\n"
            f"<b>Commands:</b>\n<code>/ip</code> · <code>/chkip</code> · <code>/ipm</code>\n"
            f"<code>/chkp</code> · <code>/rt</code> · <code>/ipl</code> · <code>/ips</code>\n\n"
            f"🔒 All → DM")
    if in_dm(update): await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb_proxy_menu())
    else: await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb_dm_only())

async def cmd_scrape(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    st = _pstate(u.id)
    if st["running"]:
        await update.message.reply_text("⚠️ Already running"); return
    workers = 20
    if ctx.args:
        if not ctx.args[0].isdigit():
            await update.message.reply_text("<code>/chkip [WORKERS]</code>\n\nExample: <code>/chkip 40</code>", parse_mode="HTML"); return
        workers = min(int(ctx.args[0]), 50)
    from_group = in_group(update)
    try:
        dm_msg = await ctx.bot.send_message(u.id, "🕸️ Starting...")
    except:
        await update.message.reply_text("📩 Open DM: /start", reply_markup=kb_dm_only()); return
    if from_group: await send_group_notice(update, ctx, "🕸️ SCRAPE", [f"⚙️ {workers} workers", "🔄 → DM"])
    await _do_scrape_live(ctx, u.id, dm_msg, workers)

async def cmd_retest(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    st = _pstate(u.id)
    if st["running"]:
        await update.message.reply_text("⚠️ Running"); return
    workers = 20
    if ctx.args:
        if not ctx.args[0].isdigit():
            await update.message.reply_text("<code>/rt [WORKERS]</code>", parse_mode="HTML"); return
        workers = min(int(ctx.args[0]), 50)
    from_group = in_group(update)
    try:
        dm_msg = await ctx.bot.send_message(u.id, "🔄 Loading saved...")
    except:
        await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only()); return
    if from_group: await send_group_notice(update, ctx, "🔄 RETEST", ["🔄 → DM"])
    await _do_retest(ctx, u.id, dm_msg, workers)

async def cmd_checkproxy(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    if not ctx.args:
        await update.message.reply_text("<code>/chkp IP:PORT</code>\n\nExample: <code>/chkp 1.2.3.4:8080</code>", parse_mode="HTML"); return
    m = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})[:\s]+(\d{1,5})', " ".join(ctx.args))
    if not m:
        await update.message.reply_text("❌ Format: <code>/chkp 1.2.3.4:8080</code>", parse_mode="HTML"); return
    ip, port = m.group(1), m.group(2)
    if not _p_ip_ok(ip) or not (1 <= int(port) <= 65535):
        await update.message.reply_text("❌ Invalid IP/port"); return
    proxy = f"{ip}:{port}"
    try:
        msg = await ctx.bot.send_message(u.id, f"🔍 Testing <code>{proxy}</code>...", parse_mode="HTML")
    except:
        await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only()); return
    if in_group(update): await send_group_notice(update, ctx, "🔍 CHECK", [f"🌐 {mask_proxy_partial(proxy)}"])
    result = await asyncio.get_event_loop().run_in_executor(POOL, _p_test_detailed, proxy)
    if result.get("alive"):
        results_txt = "".join(f"  {'✅' if ok else '❌'} {name}: {lat}ms\n" if lat is not None else f"  ❌ {name}: timeout\n"
                              for name, ok, lat in result["results"])
        text = (f"✅ <b>ALIVE</b>\n━━━━━━━━━━━━━━━━━━━\n\n🌐 <code>{proxy}</code>\n\n"
                f"📊 {result['working']}/{result['total']} · ⚡ {result['latency']}ms\n"
                f"📡 {result.get('detected_ip') or '—'}\n\n<b>Endpoints:</b>\n{results_txt}")
        if proxy not in _pstate(u.id)["valid"]:
            _pstate(u.id)["valid"].append(proxy); _save_proxy_live(u.id, proxy)
        await _safe_edit(msg, text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Again", callback_data=f"chk|{proxy}")],
            [InlineKeyboardButton("⬅️ Menu", callback_data="proxy_menu")]]))
    else:
        await _safe_edit(msg, f"❌ <b>DEAD</b>\n\n🌐 <code>{proxy}</code>",
                          reply_markup=InlineKeyboardMarkup([
                              [InlineKeyboardButton("🔄 Again", callback_data=f"chk|{proxy}")],
                              [InlineKeyboardButton("⬅️ Menu", callback_data="proxy_menu")]]))

async def cmd_mcheckproxy(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    if not in_dm(update):
        await update.message.reply_text("📎 DM only", reply_markup=kb_dm_only()); return
    st = _pstate(u.id)
    if st["running"]:
        await update.message.reply_text("⚠️ Running"); return
    state(u.id)["awaiting"] = "mchk_file"
    await update.message.reply_text(
        f"📄 <b>Check Proxies From File</b>\n\n📎 Send .txt (one per line)\n<code>1.2.3.4:8080</code>\n\n"
        f"Max: <b>{MAX_FILE_PROXIES:,}</b>\n\n<i>/cancel</i>", parse_mode="HTML", reply_markup=kb_clear_prompt())

async def cmd_proxylist(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    st = _pstate(u.id); valid = st["valid"]
    if not valid:
        try:
            c = _db(); r = c.execute("SELECT proxies FROM proxy_cache WHERE user_id=?", (u.id,)).fetchone(); c.close()
            if r and r[0]: valid = [p for p in r[0].split("\n") if p]
        except: pass
    if not valid:
        await update.message.reply_text("📭 No proxies. Use /chkip", parse_mode="HTML", reply_markup=kb_proxy_menu()); return
    buf = io.BytesIO("\n".join(valid).encode()); buf.name = f"proxies_{len(valid)}.txt"; buf.seek(0)
    try:
        await ctx.bot.send_document(u.id, document=InputFile(buf, filename=buf.name),
                                     caption=f"📄 <b>{len(valid)}</b> proxies", parse_mode="HTML")
    except:
        await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only())

async def cmd_proxystats(update, ctx):
    u = update.effective_user
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    st = _pstate(u.id); s = st["stats"]
    db_count = 0
    try:
        c = _db(); r = c.execute("SELECT proxies FROM proxy_cache WHERE user_id=?", (u.id,)).fetchone(); c.close()
        if r and r[0]: db_count = len([p for p in r[0].split("\n") if p])
    except: pass
    txt = (f"📊 <b>Proxy Stats</b>\n\nRunning: {st['running']}\nIn memory: {len(st['valid'])}\n"
           f"In DB: {db_count}\nCollected: {s.get('collected',0)}\nValid: {s.get('valid',0)}\nRejected: {s.get('rejected',0)}")
    ok = await send_dm(ctx, u.id, text=txt, reply_markup=kb_proxy_menu())
    if in_group(update) and ok: await send_group_notice(update, ctx, "📊 PROXY", ["📩 DM"])

# ═══════════════════════════════════════════════════════════════════
# FILE PROCESSORS
# ═══════════════════════════════════════════════════════════════════
async def _process_checkfile(update, ctx, content, fname):
    u = update.effective_user
    try: text = content.decode("utf-8", errors="replace")
    except: await update.message.reply_text("❌ Decode error"); return
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
    if not lines:
        await update.message.reply_text("❌ Empty"); return
    if len(lines) > 10000:
        await update.message.reply_text("❌ Max 10,000"); return
    total = len(lines); luhn_ok = luhn_fail = 0; brands = {}; invalids = []; sample = []
    for ln in lines:
        card = re.sub(r"\D","", ln.split("|")[0] if "|" in ln else ln)
        if not card: continue
        if luhn_check(card): luhn_ok += 1
        else: luhn_fail += 1; invalids.append(card)
        b, _, _ = detect_brand(card[:8])
        brands[b] = brands.get(b,0) + 1
        if len(sample) < 5: sample.append(card)
    countries = {}
    for card in sample:
        info = await lookup_bin(card[:8])
        if info and not info.get("error") and info.get("country_code"):
            cc = info["country_code"]; countries[cc] = countries.get(cc,0)+1
    report = (f"📊 <b>Checker</b>\n━━━━━━━━━━━━━━━━━━━\n\nTotal: <code>{total:,}</code>\n"
              f"✅ Luhn: <code>{luhn_ok:,}</code> ({luhn_ok*100//max(total,1)}%)\n"
              f"❌ Fail: <code>{luhn_fail:,}</code>\n\n<b>Brands:</b>\n")
    for b, c in sorted(brands.items(), key=lambda x:-x[1]): report += f"  {b}: {c}\n"
    if countries:
        report += "\n<b>Countries:</b>\n" + "".join(f"  {cc}: {c}\n" for cc, c in countries.items())
    if invalids:
        report += "\n<b>First 5 invalid:</b>\n" + "".join(f"  <code>{c}</code>\n" for c in invalids[:5])
    ok = await send_dm(ctx, u.id, text=report, reply_markup=kb_back())
    if ok: await update.message.reply_text("✅ Report → DM 🔒")
    else: await update.message.reply_text("📩 Open DM", reply_markup=kb_dm_only())

async def _process_clear(update, ctx, content, fname):
    try: text = content.decode("utf-8", errors="replace")
    except: await update.message.reply_text("❌ Decode error"); return
    raw = text.splitlines(); stripped = [ln.strip() for ln in raw]
    non_empty = [ln for ln in stripped if ln]
    seen = set(); unique = []
    for ln in non_empty:
        if ln not in seen: seen.add(ln); unique.append(ln)
    stem = PurePath(fname).stem or "file"; suffix = PurePath(fname).suffix or ".txt"
    out_name = f"{stem}_clean{suffix}"
    buf = io.BytesIO(("\n".join(unique)+"\n").encode()); buf.name = out_name; buf.seek(0)
    try:
        await ctx.bot.send_document(update.effective_user.id, document=InputFile(buf, filename=out_name),
            caption=f"🧹 <b>Cleaned</b>\n\n📄 {len(raw):,}\n🚫 Empty: {len(raw)-len(non_empty):,}\n♻️ Dupes: {len(non_empty)-len(unique):,}\n✅ Unique: <b>{len(unique):,}</b>",
            parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def _process_mchk_file(update, ctx, content, fname):
    try: text = content.decode("utf-8", errors="replace")
    except: await update.message.reply_text("❌ Decode error"); return
    proxies = _parse_proxy_file(text)
    if not proxies:
        await update.message.reply_text("❌ No valid proxies", parse_mode="HTML"); return
    seen = set(); unique = [p for p in proxies if not (p in seen or seen.add(p))]
    if len(unique) > MAX_FILE_PROXIES: unique = unique[:MAX_FILE_PROXIES]
    msg = await update.message.reply_text(f"📄 Loaded {len(unique):,} proxies\n\n<i>Testing...</i>", parse_mode="HTML")
    await _do_file_check(ctx, update.effective_user.id, msg, unique, workers=30)

# ═══════════════════════════════════════════════════════════════════
# ADMIN
# ═══════════════════════════════════════════════════════════════════
async def cmd_admin(update, ctx):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Denied"); return
    if not in_dm(update):
        await update.message.reply_text("🔒 DM only", reply_markup=kb_dm_only()); return
    await update.message.reply_text("🎛️ <b>Admin</b>", parse_mode="HTML", reply_markup=kb_admin())

async def cmd_addsub(update, ctx):
    if not in_dm(update) or update.effective_user.id != ADMIN_ID: return
    if len(ctx.args) < 3:
        await update.message.reply_text("<code>/addsub UID TIER DAYS</code>\n\nExample:\n<code>/addsub 123456789 pro 30d</code>", parse_mode="HTML"); return
    try: uid = int(ctx.args[0])
    except: await update.message.reply_text("❌ UID number"); return
    tier = ctx.args[1].lower()
    if tier not in SUB_TIERS:
        await update.message.reply_text(f"❌ Tier: {', '.join(SUB_TIERS.keys())}"); return
    try: days = SUB_DURATIONS.get(ctx.args[2].lower()) or int(ctx.args[2])
    except: await update.message.reply_text("❌ DAYS invalid"); return
    if db_set_sub(uid, tier, days, by_admin=update.effective_user.id):
        try: await ctx.bot.send_message(uid, f"🎉 {tier.upper()} {days}d!", parse_mode="HTML")
        except: pass
        await update.message.reply_text(f"✅ {uid} → {tier} ({days}d)")

async def cmd_ban(update, ctx):
    if not in_dm(update) or update.effective_user.id != ADMIN_ID: return
    if not ctx.args:
        await update.message.reply_text("<code>/ban UID [reason]</code>", parse_mode="HTML"); return
    try: uid = int(ctx.args[0])
    except: await update.message.reply_text("❌ UID number"); return
    db_ban(uid, " ".join(ctx.args[1:]) or "no reason", update.effective_user.id)
    await update.message.reply_text(f"🚫 {uid} banned")

async def cmd_unban(update, ctx):
    if not in_dm(update) or update.effective_user.id != ADMIN_ID: return
    if not ctx.args:
        await update.message.reply_text("<code>/unban UID</code>", parse_mode="HTML"); return
    try: uid = int(ctx.args[0])
    except: await update.message.reply_text("❌ UID number"); return
    db_unban(uid); await update.message.reply_text(f"✅ {uid} unbanned")

async def cmd_broadcast(update, ctx):
    if not in_dm(update) or update.effective_user.id != ADMIN_ID: return
    if not ctx.args:
        await update.message.reply_text("<code>/broadcast MESSAGE</code>", parse_mode="HTML"); return
    msg = " ".join(ctx.args); users = db_all_users()
    obj = await update.message.reply_text(f"📢 Sending to {len(users)}...")
    sent = fail = 0
    for i, (uid, _) in enumerate(users):
        try: await ctx.bot.send_message(uid, f"📢 {msg}", parse_mode="HTML"); sent += 1
        except: fail += 1
        if i % 10 == 0:
            await _safe_edit(obj, f"📢 {i+1}/{len(users)}\n✅ {sent} · ❌ {fail}")
    await _safe_edit(obj, f"✅ Done\n\nSent: {sent}\nFailed: {fail}")

async def cmd_bulkcode(update, ctx):
    if not in_dm(update) or update.effective_user.id != ADMIN_ID: return
    if len(ctx.args) < 3:
        await update.message.reply_text("<code>/bulkcode TIER DAYS COUNT</code>\n\nExample:\n<code>/bulkcode pro 30d 10</code>", parse_mode="HTML"); return
    tier = ctx.args[0].lower()
    if tier not in SUB_TIERS:
        await update.message.reply_text(f"❌ Tier: {', '.join(SUB_TIERS.keys())}"); return
    try:
        days = SUB_DURATIONS.get(ctx.args[1].lower()) or int(ctx.args[1])
        n = min(int(ctx.args[2]), 100)
    except: await update.message.reply_text("❌ DAYS/COUNT invalid"); return
    codes = db_gen_bulk(tier, days, n, update.effective_user.id)
    buf = io.BytesIO("\n".join(codes).encode()); buf.name = f"codes_{tier}_{days}d.txt"; buf.seek(0)
    await update.message.reply_document(document=InputFile(buf, filename=buf.name), caption=f"✅ {len(codes)} codes")

async def cmd_notify(update, ctx):
    if not in_dm(update) or update.effective_user.id != ADMIN_ID: return
    try:
        c = _db(); now = datetime.utcnow().isoformat(); soon = (datetime.utcnow()+timedelta(days=3)).isoformat()
        r = c.execute("SELECT user_id,tier,expires_at FROM subscriptions WHERE expires_at>? AND expires_at<?", (now, soon)).fetchall(); c.close()
    except: r = []
    sent = 0
    for uid, tier, exp in r:
        try: await ctx.bot.send_message(uid, f"⏰ {tier.upper()} expires {exp[:10]}", parse_mode="HTML"); sent += 1
        except: pass
    await update.message.reply_text(f"🔔 Notified {sent}")

async def cmd_setwhitelist(update, ctx):
    if not in_dm(update) or update.effective_user.id != ADMIN_ID: return
    if not ctx.args or ctx.args[0].lower() not in ("on","off"):
        await update.message.reply_text("<code>/setwhitelist on|off</code>", parse_mode="HTML"); return
    v = ctx.args[0].lower(); db_setting_set("whitelist_mode", v)
    await update.message.reply_text(f"✅ {v.upper()}")

async def cmd_setcmd(update, ctx):
    if not in_dm(update) or update.effective_user.id != ADMIN_ID: return
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "🛠️ <b>Edit Command</b>\n\n<code>/setcmd OLD NEW</code>\n\n"
            "Example:\n<code>/setcmd gen generate</code>\n\n→ /gen becomes /generate",
            parse_mode="HTML"); return
    old = ctx.args[0].lower().lstrip("/"); new = ctx.args[1].lower().lstrip("/")
    if old not in COMMAND_MAP:
        await update.message.reply_text(f"❌ /{old} not found. Use /cmds"); return
    if new in COMMAND_MAP:
        await update.message.reply_text(f"❌ /{new} exists"); return
    handler = COMMAND_MAP.pop(old); COMMAND_MAP[new] = handler
    db_save_cmd(old, new, handler)
    await update.message.reply_text(f"✅ /{old} → /{new}")

async def cmd_delcmd(update, ctx):
    if not in_dm(update) or update.effective_user.id != ADMIN_ID: return
    if not ctx.args:
        await update.message.reply_text("<code>/delcmd ALIAS</code>", parse_mode="HTML"); return
    alias = ctx.args[0].lower().lstrip("/")
    if alias not in COMMAND_MAP:
        await update.message.reply_text(f"❌ /{alias} not found"); return
    COMMAND_MAP.pop(alias); db_del_cmd(alias)
    await update.message.reply_text(f"🗑️ /{alias} removed")

async def cmd_cmds(update, ctx):
    if not in_dm(update) or update.effective_user.id != ADMIN_ID: return
    txt = "🛠️ <b>Commands</b>\n\n"
    for alias, handler in sorted(COMMAND_MAP.items()):
        txt += f"/{alias} → <code>{handler}</code>\n"
    txt += "\n<i>/setcmd OLD NEW · /delcmd ALIAS</i>"
    await update.message.reply_text(txt, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════
# DISPATCHERS
# ═══════════════════════════════════════════════════════════════════
HANDLERS = {
    "start": cmd_start, "menu": cmd_menu, "ping": cmd_ping,
    "help": cmd_help, "me": cmd_me, "sub": cmd_sub,
    "redeem": cmd_redeem, "ref": cmd_ref, "leaderboard": cmd_leaderboard,
    "stats": cmd_stats, "setcountry": cmd_setcountry, "fav": cmd_fav,
    "favadd": cmd_favadd, "gen": cmd_gen, "genv": cmd_genv, "bin": cmd_bin,
    "check": cmd_check, "info": cmd_info, "checkfile": cmd_checkfile,
    "cancel": cmd_cancel, "discover": cmd_discover, "clear": cmd_clear,
    "proxy_menu": cmd_proxy, "chkip": cmd_scrape, "retest": cmd_retest,
    "chkp": cmd_checkproxy, "ipm": cmd_mcheckproxy, "plist": cmd_proxylist,
    "pstats": cmd_proxystats, "admin": cmd_admin, "addsub": cmd_addsub,
    "ban": cmd_ban, "unban": cmd_unban, "broadcast": cmd_broadcast,
    "bulkcode": cmd_bulkcode, "notify": cmd_notify, "setwhitelist": cmd_setwhitelist,
    "setcmd": cmd_setcmd, "delcmd": cmd_delcmd, "cmds": cmd_cmds,
}

async def on_cmd(update, ctx):
    msg = update.message
    if not msg or not msg.text: return
    parts = msg.text.split()
    if not parts: return
    cmd_full = parts[0][1:].split("@")[0].lower()
    handler_name = COMMAND_MAP.get(cmd_full)
    if not handler_name:
        try:
            await msg.reply_text(
                f"❓ Unknown command: <code>/{cmd_full}</code>\n\n"
                f"Use <code>/help</code> or tap the menu.",
                parse_mode="HTML", reply_markup=kb_main())
        except: pass
        return
    handler = HANDLERS.get(handler_name)
    if not handler:
        try: await msg.reply_text(f"⚠️ Handler missing for /{cmd_full}")
        except: pass
        return
    ctx.args = parts[1:]
    try:
        await handler(update, ctx)
    except Exception as e:
        log.exception(f"/{cmd_full}: {e}")
        try: await msg.reply_text(f"❌ Error: {str(e)[:200]}")
        except: pass

async def on_callback(update, ctx):
    try:
        await _on_callback_inner(update, ctx)
    except Exception as e:
        log.exception(f"callback: {e}")
        try: await update.callback_query.answer(f"❌ Error: {str(e)[:80]}", show_alert=True)
        except: pass

async def _on_callback_inner(update, ctx):
    q = update.callback_query; d = q.data or ""
    u = q.from_user; s = state(u.id)
    if not in_group(update) and not in_dm(update):
        await q.answer("Denied", show_alert=True); return
    await q.answer()

    if d == "proxy_menu":
        st = _pstate(u.id)
        running = "⏳ Running" if st["running"] else "🟢 Idle"
        txt = (f"🕸️ <b>Proxy Scraper</b>\n\nStatus: {running}\nCached: {len(st['valid'])}\n"
               f"Collected: {st['stats'].get('collected',0)}\n\n🔒 All → DM")
        if in_dm(update): await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb_proxy_menu())
        else: await q.edit_message_text(txt + "\n\n📩 Tap:", parse_mode="HTML", reply_markup=kb_dm_only())
        return
    if d == "proxy:stop":
        st = _pstate(u.id)
        if not st["running"]: await q.answer("Not running", show_alert=True); return
        st["stop_flag"] = True; await q.answer("Stopping...", show_alert=True); return
    if d == "proxy:filecheck":
        if not in_dm(update): await q.answer("DM only", show_alert=True); return
        if _pstate(u.id)["running"]: await q.answer("Running", show_alert=True); return
        state(u.id)["awaiting"] = "mchk_file"
        await q.edit_message_text(f"📄 Send .txt (max {MAX_FILE_PROXIES:,})\n\n<i>/cancel</i>", parse_mode="HTML", reply_markup=kb_clear_prompt()); return
    if d == "proxy:checkone":
        if not in_dm(update): await q.answer("DM only", show_alert=True); return
        state(u.id)["awaiting"] = "checkproxy_input"
        await q.edit_message_text("🔍 Send <code>IP:PORT</code>", parse_mode="HTML",
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️", callback_data="proxy_menu")]])); return
    if d.startswith("proxy:start:"):
        workers = int(d.split(":")[-1])
        if _pstate(u.id)["running"]: await q.answer("Running", show_alert=True); return
        from_group = in_group(update)
        try: dm_msg = await ctx.bot.send_message(u.id, f"🕸️ {workers} workers...")
        except: await q.answer("Open DM", show_alert=True); return
        if from_group: await send_group_notice(update, ctx, "🕸️ SCRAPE", [f"⚙️ {workers} workers"])
        asyncio.create_task(_do_scrape_live(ctx, u.id, dm_msg, workers)); return
    if d.startswith("proxy:retest:"):
        workers = int(d.split(":")[-1])
        if _pstate(u.id)["running"]: await q.answer("Running", show_alert=True); return
        from_group = in_group(update)
        try: dm_msg = await ctx.bot.send_message(u.id, "🔄 Loading...")
        except: await q.answer("Open DM", show_alert=True); return
        if from_group: await send_group_notice(update, ctx, "🔄 RETEST", [])
        asyncio.create_task(_do_retest(ctx, u.id, dm_msg, workers)); return
    if d == "proxy:download":
        st = _pstate(u.id); valid = st["valid"]
        if not valid:
            try:
                c = _db(); r = c.execute("SELECT proxies FROM proxy_cache WHERE user_id=?", (u.id,)).fetchone(); c.close()
                if r and r[0]: valid = [p for p in r[0].split("\n") if p]
            except: pass
        if not valid: await q.answer("No proxies. /chkip first", show_alert=True); return
        buf = io.BytesIO("\n".join(valid).encode()); buf.name = f"proxies_{len(valid)}.txt"; buf.seek(0)
        try: await ctx.bot.send_document(u.id, document=InputFile(buf, filename=buf.name), caption=f"📄 {len(valid)} proxies")
        except: await q.answer("Open DM", show_alert=True)
        return
    if d == "proxy:stats":
        st = _pstate(u.id); s2 = st["stats"]
        await q.edit_message_text(f"📊 Running: {st['running']}\nCached: {len(st['valid'])}\n"
                                    f"Collected: {s2.get('collected',0)}\nValid: {s2.get('valid',0)}\nRejected: {s2.get('rejected',0)}",
                                    parse_mode="HTML", reply_markup=kb_proxy_menu()); return
    if d.startswith("chk|"):
        proxy = d.split("|", 1)[1]
        if not in_dm(update): await q.answer("DM only", show_alert=True); return
        await q.edit_message_text(f"🔍 Testing <code>{proxy}</code>...", parse_mode="HTML")
        r = await asyncio.get_event_loop().run_in_executor(POOL, _p_test_detailed, proxy)
        if r.get("alive"):
            rt = "".join(
                f"  {'✅' if ok else '❌'} {name}: {lat}ms\n" if lat is not None
                else f"  ❌ {name}: timeout\n"
                for name, ok, lat in r["results"])
            text = (f"✅ <b>ALIVE</b>\n\n🌐 <code>{proxy}</code>\n\n"
                    f"📊 {r['working']}/{r['total']} · ⚡ {r['latency']}ms\n"
                    f"📡 {r.get('detected_ip') or '—'}\n\n<b>Endpoints:</b>\n{rt}")
            if proxy not in _pstate(u.id)["valid"]:
                _pstate(u.id)["valid"].append(proxy); _save_proxy_live(u.id, proxy)
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Again", callback_data=f"chk|{proxy}")],
                [InlineKeyboardButton("⬅️ Menu", callback_data="proxy_menu")]]))
        else:
            await q.edit_message_text(f"❌ <b>DEAD</b>\n\n🌐 <code>{proxy}</code>",
                                        parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                                            [InlineKeyboardButton("🔄 Again", callback_data=f"chk|{proxy}")],
                                            [InlineKeyboardButton("⬅️ Menu", callback_data="proxy_menu")]]))
        return

    if d.startswith("adm:"):
        if u.id != ADMIN_ID or update.effective_chat.type != "private":
            await q.answer("⛔", show_alert=True); return
        if d == "adm:back":
            await q.edit_message_text("🎛️ <b>Admin</b>", parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:stats":
            gl, gc = db_stats(None)
            await q.edit_message_text(f"📊 <b>Stats</b>\n\nRequests: {gl:,}\nCards: {gc:,}\nUsers: {len(db_all_users())}",
                                        parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:users":
            users = db_all_users()[:20]; txt = "👥 <b>Users</b>\n\n"
            for uid, _ in users:
                t_, _ = db_get_sub(uid); eff = t_ or "free"
                txt += f"<code>{uid}</code> · {SUB_EMOJI.get(eff,'')} {eff}\n"
            await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:subs":
            subs = db_list_subs(20)
            txt = "🎫 <b>Subs</b>\n\n" + ("\n".join(f"<code>{uid}</code> · {tier} · {exp[:10]}" for uid, tier, exp in subs) or "None")
            await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:gencode":
            await q.edit_message_text("🎟️ Pick tier", parse_mode="HTML", reply_markup=kb_admin_tier()); return
        if d.startswith("adm:gen:"):
            tier = d.split(":")[2]
            await q.edit_message_text(f"<b>{tier}</b> — duration:", parse_mode="HTML", reply_markup=kb_admin_days(tier)); return
        if d.startswith("adm:genday:"):
            _, _, tier, dur = d.split(":")
            code = db_gen_code(tier, SUB_DURATIONS[dur], u.id)
            if code:
                await q.edit_message_text(f"✅ <b>Code</b>\n\n<code>{code}</code>\n\n{tier} · {SUB_DURATIONS[dur]}d",
                                            parse_mode="HTML", reply_markup=kb_admin())
            return
        if d == "adm:listcodes":
            codes = db_list_codes(20)
            txt = "🎟️ <b>Recent</b>\n\n" + ("\n".join(f"<code>{c}</code> · {t} · {dd}d · {'✅' if ub else '🟢'}" for c, t, dd, ub in codes) or "None")
            await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:bulk":
            await q.edit_message_text("<code>/bulkcode TIER DAYS COUNT</code>", parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:ban":
            await q.edit_message_text("<code>/ban UID reason</code>", parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:unban":
            await q.edit_message_text("<code>/unban UID</code>", parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:banlist":
            bl = db_ban_list(20)
            txt = "🚫 <b>Bans</b>\n\n" + ("\n".join(f"<code>{uid}</code> · {reason}" for uid, reason, _ in bl) or "None")
            await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:bc":
            await q.edit_message_text("<code>/broadcast MESSAGE</code>", parse_mode="HTML", reply_markup=kb_admin()); return
        if d == "adm:cmds":
            await q.edit_message_text(
                "🛠️ <b>Edit Commands</b>\n\n"
                "<code>/setcmd OLD NEW</code>\n<code>/delcmd ALIAS</code>\n<code>/cmds</code>\n\n"
                "Changes are live + persisted",
                parse_mode="HTML", reply_markup=kb_admin()); return
        return

    if d.startswith("help:"):
        cat = d.split(":")[1]
        texts = {
            "cards": "🎴 <b>Cards</b>\n\n<code>/gen BIN</code>\n<code>/gen BIN 50</code>\n<code>/gen BIN|MM|YY</code>\n<code>/genv BIN|MM|YY</code>\n<code>/bin BIN</code>\n<code>/check CARD</code>\n<code>/info CARD</code>\n<code>/checkfile</code>",
            "proxy": "🕸️ <b>Proxy</b>\n\n<code>/ip</code> — menu\n<code>/chkip</code> — 20\n<code>/chkip 40</code>\n<code>/ipm</code> — file\n<code>/chkp IP:PORT</code>\n<code>/rt</code> · <code>/ipl</code> · <code>/ips</code>",
            "clear": "🧹 <b>Cleaner</b>\n\n<code>/clear</code>\nThen upload .txt/.csv (DM)",
            "subs": "🎫 <b>Subs</b>\n\n<code>/sub</code>\n<code>/redeem CODE</code>\n<code>/ref</code>",
        }
        await q.edit_message_text(texts.get(cat, "—"), parse_mode="HTML", reply_markup=kb_help()); return

    if d == "noop": return
    if d == "bk":
        await q.edit_message_text("📖 <b>Menu</b>", parse_mode="HTML", reply_markup=kb_main()); return
    if d == "g":
        await q.edit_message_text("🎴 <b>Generate</b>\n\n<code>/gen BIN</code>\n<code>/gen BIN 50</code>\n<code>/gen BIN|MM|YY</code>", parse_mode="HTML"); return
    if d == "b":
        await q.edit_message_text("🔍 Send <code>/bin BIN</code>", parse_mode="HTML"); return
    if d == "cd":
        await q.edit_message_text("📋 Send <code>/check CARD</code>", parse_mode="HTML"); return
    if d == "m:clear":
        if not in_dm(update): await q.answer("DM only", show_alert=True); return
        state(u.id)["awaiting"] = "clear_file"
        await q.edit_message_text("🧹 Send .txt/.csv\n\n<i>/cancel</i>", parse_mode="HTML", reply_markup=kb_clear_prompt()); return
    if d == "m:checkfile":
        if not in_dm(update): await q.answer("DM only", show_alert=True); return
        state(u.id)["awaiting"] = "checkfile"
        await q.edit_message_text("📄 Send .txt/.csv\n\n<i>/cancel</i>", parse_mode="HTML", reply_markup=kb_clear_prompt()); return
    if d == "m:sub": await cmd_sub(update, ctx); return
    if d == "m:redeem": await q.edit_message_text("🎟️ <code>/redeem CODE</code>", parse_mode="HTML"); return
    if d == "m:ref": await cmd_ref(update, ctx); return
    if d == "m:lead": await cmd_leaderboard(update, ctx); return
    if d == "m:help": await q.edit_message_text("📖 Help", parse_mode="HTML", reply_markup=kb_help()); return
    if d == "fav":
        rows = db_fav_list(u.id)
        if not rows: await q.edit_message_text("⭐ No favorites", reply_markup=kb_back()); return
        btns = [[InlineKeyboardButton(r[0], callback_data=f"g|{r[0]}")] for r in rows]
        btns.append([InlineKeyboardButton("⬅️", callback_data="bk")])
        await q.edit_message_text("⭐ <b>Favorites</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns)); return
    if d == "st":
        total, cards = db_stats(u.id)
        await q.edit_message_text(f"📊 {total} req · {cards:,} cards", parse_mode="HTML", reply_markup=kb_back()); return

    if d.startswith("cp|"):
        await q.answer(f"Copied: {d.split('|',1)[1]}", show_alert=True); return
    if d.startswith("ca|"):
        block = "\n".join(s.get("last_lines") or [])
        await q.message.reply_text(f"<b>All</b>\n<code>{block}</code>", parse_mode="HTML"); return
    if d.startswith("rg|"):
        parts = d.split("|"); bp = parts[1]; cnt = int(parts[2])
        await do_gen(update, ctx, [bp], cnt, None, None, None, None); return
    if d.startswith("x|"):
        parts = d.split("|"); fmt = parts[1]; bp = parts[2]
        lines = s.get("last_lines") or []
        if not lines: await q.answer("Generate first", show_alert=True); return
        if fmt == "txt": buf = build_txt(lines, bp, s.get("last_brand",""), s.get("last_length",16), s.get("last_expiry"))
        elif fmt == "csv": buf = build_csv(lines)
        elif fmt == "json": buf = build_json(lines)
        elif fmt == "zip":
            buf, pwd, method = build_zip(lines, bp)
            await q.message.reply_document(document=InputFile(buf, filename=buf.name),
                caption=f"🔐 <b>ZIP</b> ({method})\n\n<code>{pwd}</code>", parse_mode="HTML"); return
        else: await q.answer("Unknown"); return
        await q.message.reply_document(document=InputFile(buf, filename=buf.name), caption=f"<b>{fmt.upper()}</b>"); return
    if d.startswith("img|"):
        if not HAS_PIL: await q.answer("Pillow missing", show_alert=True); return
        lines = s.get("last_lines") or []
        if not lines: await q.answer("Generate first", show_alert=True); return
        parts = lines[0].split("|")
        card = parts[0].replace(" ","").replace("-","")
        mm = parts[1] if len(parts) > 1 else "02"
        yy = parts[2] if len(parts) > 2 else "28"
        cvv = parts[3] if len(parts) > 3 else "000"
        holder = s["last_identity"].get("name","CARD HOLDER") if s.get("last_identity") else "CARD HOLDER"
        img = make_card_image(card, holder, mm, yy, cvv, s.get("last_brand","BANK") or "BANK")
        if img:
            try: await q.message.reply_photo(photo=img, caption=f"💳 <code>{lines[0]}</code>", parse_mode="HTML")
            except: await q.answer("Failed", show_alert=True)
        return
    if d == "ci":
        if not s.get("last_identity"): await q.answer("No identity", show_alert=True); return
        i = s["last_identity"]; card = s.get("last_card","—")
        plain = (f"Card: {card}\nName: {i['name']}\nPhone: {i['phone']}\n"
                 f"Email: {i.get('email','—')}\nDOB: {i.get('dob','—')}\n"
                 f"Street: {i['street']}\nCity: {i['city']}\nPostal: {i['postal']}\nCountry: {i['country']}")
        try: await q.message.reply_text(f"<code>{plain}</code>", parse_mode="HTML")
        except: pass
        return
    if d == "idimg":
        if not HAS_PIL: await q.answer("Pillow missing", show_alert=True); return
        if not s.get("last_identity"): await q.answer("No identity", show_alert=True); return
        card = s.get("last_card",""); parts = (s.get("last_lines") or ["||||"])[0].split("|")
        mm = parts[1] if len(parts) > 3 else "02"
        yy = parts[2] if len(parts) > 3 else "28"
        cvv = parts[3] if len(parts) > 3 else "000"
        img = make_card_image(card, s["last_identity"]["name"], mm, yy, cvv, s.get("last_brand","BANK") or "BANK")
        if img:
            try: await q.message.reply_photo(photo=img, caption=f"👤 {s['last_identity']['name']}", parse_mode="HTML")
            except: pass
        return
    if d == "ni":
        if not s.get("last_card"): await q.answer("Use /info first", show_alert=True); return
        dd = re.sub(r"\D","", s["last_card"])
        cc, _ = await resolve_country(dd[:8], None, s.get("default_country"))
        if not cc: await q.answer("Country unknown", show_alert=True); return
        ident = await fetch_identity(cc)
        if not ident: await q.answer(f"No {cc}", show_alert=True); return
        s["last_identity"] = ident
        pi = await postal_check(cc, ident["postal"]); ci = await country_info(cc)
        txt = (f"👤 <code>{s['last_card']}</code>\n\n<code>{ident['name']}</code>\n"
               f"📞 <code>{ident['phone']}</code>\n🏠 <code>{ident['street']}</code>\n"
               f"🏙 <code>{ident['city']} {ident['postal']}</code>\n🌍 <code>{ident['country']}</code>")
        if ci: txt += f"\n\n🌐 {ci['flag']} <b>{ci['name']}</b>"
        try: await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb_identity())
        except: pass
        return

async def on_document(update, ctx):
    u = update.effective_user; s = state(u.id); awaiting = s.get("awaiting")
    if awaiting not in ("checkfile", "clear_file", "mchk_file"): return
    if not update.message.document: return
    if db_is_banned(u.id):
        await update.message.reply_text("🚫 Banned"); return
    try:
        file = await update.message.document.get_file()
        content = await file.download_as_bytearray()
        if len(content) / (1024*1024) > 5:
            await update.message.reply_text("❌ File > 5MB"); s["awaiting"] = None; return
    except Exception as e:
        await update.message.reply_text(f"❌ {e}"); s["awaiting"] = None; return
    fname = update.message.document.file_name or "file.txt"
    s["awaiting"] = None
    if awaiting == "checkfile": await _process_checkfile(update, ctx, content, fname)
    elif awaiting == "clear_file": await _process_clear(update, ctx, content, fname)
    elif awaiting == "mchk_file": await _process_mchk_file(update, ctx, content, fname)

async def on_text(update, ctx):
    u = update.effective_user; s = state(u.id); awaiting = s.get("awaiting")

    if awaiting == "checkproxy_input":
        txt = (update.message.text or "").strip()
        m = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})[:\s]+(\d{1,5})', txt)
        if not m:
            await update.message.reply_text(
                "❌ Format: <code>IP:PORT</code>\nExample: <code>1.2.3.4:8080</code>",
                parse_mode="HTML", reply_markup=kb_clear_prompt()); return
        ip, port = m.group(1), m.group(2)
        if not _p_ip_ok(ip) or not (1 <= int(port) <= 65535):
            await update.message.reply_text("❌ Invalid IP/port"); return
        s["awaiting"] = None
        proxy = f"{ip}:{port}"
        r = await asyncio.get_event_loop().run_in_executor(POOL, _p_test_detailed, proxy)
        if r.get("alive"):
            await update.message.reply_text(
                f"✅ <b>ALIVE</b>\n<code>{proxy}</code>\n⚡ {r['latency']}ms\n📡 {r.get('detected_ip') or '—'}",
                parse_mode="HTML", reply_markup=kb_proxy_menu())
            if proxy not in _pstate(u.id)["valid"]:
                _pstate(u.id)["valid"].append(proxy); _save_proxy_live(u.id, proxy)
        else:
            await update.message.reply_text(
                f"❌ <b>DEAD</b>\n<code>{proxy}</code>",
                parse_mode="HTML", reply_markup=kb_proxy_menu())
        return

    if awaiting in ("checkfile", "clear_file", "mchk_file"):
        await update.message.reply_text(
            "📎 <b>Upload a file, not text</b>\n\n"
            "Send .txt or .csv (max 5MB).\n\n<i>/cancel to abort</i>",
            parse_mode="HTML", reply_markup=kb_clear_prompt()); return

    if awaiting: return

    if not in_group(update): return
    d = re.sub(r"\D","", update.message.text or "")
    if 4 <= len(d) <= 19:
        r = resolve_input(d)
        if r:
            await update.message.reply_text(
                f"<b>{r['mode']}</b> · <code>{r['bin_prefix']}</code>\n\n"
                f"<code>/gen {d}|5</code> · <code>/genv {d}|02|28</code> · <code>/bin {d}</code>",
                parse_mode="HTML", reply_markup=kb_dm_only())

async def on_error(update, ctx):
    log.exception("Error", exc_info=ctx.error)

async def post_init(app):
    global BOT_USERNAME
    me = await app.bot.get_me(); BOT_USERNAME = me.username; db_init()
    for old, new, handler in db_load_cmd_overrides():
        if old in COMMAND_MAP: COMMAND_MAP.pop(old)
        COMMAND_MAP[new] = handler
    try:
        cmds_list = [BotCommand(alias, handler.capitalize()) for alias, handler in list(COMMAND_MAP.items())[:30]]
        await app.bot.set_my_commands(cmds_list)
    except: pass
    log.info(
        f"\n╔════════════════════════════════════════╗\n"
        f"║  ✅ Luuln Bot Online                   ║\n"
        f"║  @{BOT_USERNAME:<38}║\n"
        f"║  Commands: {len(COMMAND_MAP):<28}║\n"
        f"║  Tier limit: {SUB_TIERS['vip']['rate']} req/hr (VIP)        ║\n"
        f"╚════════════════════════════════════════╝"
    )

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing"); return
    threading.Thread(target=_run_health, daemon=True).start()
    app = (Application.builder().token(BOT_TOKEN).concurrent_updates(256).post_init(post_init).build())
    app.add_handler(MessageHandler(filters.Regex(r'^/[a-zA-Z]'), on_cmd))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    log.info("Starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
