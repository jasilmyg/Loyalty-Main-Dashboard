"""
UTM Link Builder & Cloud-Synced First-Party Click Tracking Service
------------------------------------------------------------------
Connects your live deployed dashboard domain (`https://loyalty-main-dashboard.onrender.com/go/<slug>/`)
and short links (`https://spoo.me/myg-mgl-rcs`) with your shared DigitalOcean PostgreSQL database
(plus local SQLite WAL mirror for offline resilience).

How it works:
  1. Shareable Domain Link: `https://loyalty-main-dashboard.onrender.com/go/mgl-rcs/`
     (never `127.0.0.1:8000`, even when viewing the dashboard locally).
  2. Cloud-Synced Persistence: `utm_links` and `utm_clicks` are stored in DigitalOcean PostgreSQL
     and mirrored to local SQLite (`utm_tracking.sqlite3`), so clicks on Render (`loyalty-main-dashboard.onrender.com`)
     appear live on both your local dashboard and production dashboard, and custom links created locally
     immediately work on `https://loyalty-main-dashboard.onrender.com/go/<slug>/`.
  3. External Short Link Bridge: Also pulls live click telemetry from `https://spoo.me/myg-mgl-*`
     so clicks on either your Render domain link or short link are unified.
"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import re
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

from django.conf import settings
from django.db import connection

IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_TARGET_URL = "https://mygoffers.in/karnataka/dakshina-kannada/myg-future-mangaluru"
DEFAULT_CAMPAIGN = "myg_future_mangaluru"
PUBLIC_BASE_URL = getattr(
    settings,
    "PUBLIC_TRACKING_BASE_URL",
    "https://loyalty-main-dashboard.onrender.com",
).rstrip("/")

DB_PATH = os.path.join(settings.BASE_DIR, "utm_tracking.sqlite3")

PRESET_LINKS = [
    {
        "slug": "mgl-rcs",
        "live_short_url": "https://spoo.me/myg-mgl-rcs",
        "title": "RCS Rich Card — myG Future Mangaluru",
        "channel": "RCS",
        "target_url": DEFAULT_TARGET_URL,
        "utm_source": "rcs",
        "utm_medium": "messaging",
        "utm_campaign": DEFAULT_CAMPAIGN,
        "utm_term": "dakshina_kannada",
        "utm_content": "rcs_rich_card",
        "notes": "RCS Business Messaging rich carousel / CTA button",
    },
    {
        "slug": "mgl-whatsapp",
        "live_short_url": "https://spoo.me/myg-mgl-whatsapp",
        "title": "WhatsApp Broadcast — myG Future Mangaluru",
        "channel": "WhatsApp",
        "target_url": DEFAULT_TARGET_URL,
        "utm_source": "whatsapp",
        "utm_medium": "broadcast",
        "utm_campaign": DEFAULT_CAMPAIGN,
        "utm_term": "dakshina_kannada",
        "utm_content": "wa_offer_broadcast",
        "notes": "Primary WhatsApp promotional broadcast link for Mangaluru store offers",
    },
    {
        "slug": "mgl-sms",
        "live_short_url": "https://spoo.me/myg-mgl-sms",
        "title": "SMS Blast — myG Future Mangaluru",
        "channel": "SMS",
        "target_url": DEFAULT_TARGET_URL,
        "utm_source": "sms",
        "utm_medium": "text_message",
        "utm_campaign": DEFAULT_CAMPAIGN,
        "utm_term": "dakshina_kannada",
        "utm_content": "sms_promo_blast",
        "notes": "Promotional SMS campaign short/redirect link",
    },
    {
        "slug": "mgl-instagram",
        "live_short_url": "https://spoo.me/myg-mgl-instagra",
        "title": "Instagram Reels & Bio — myG Future Mangaluru",
        "channel": "Instagram",
        "target_url": DEFAULT_TARGET_URL,
        "utm_source": "instagram",
        "utm_medium": "social_paid",
        "utm_campaign": DEFAULT_CAMPAIGN,
        "utm_term": "mangaluru_shoppers",
        "utm_content": "ig_reel_ad",
        "notes": "Instagram Story / Reel CTA & Profile Bio link",
    },
    {
        "slug": "mgl-facebook",
        "live_short_url": "https://spoo.me/myg-mgl-facebook",
        "title": "Facebook Feed & Stories — myG Future Mangaluru",
        "channel": "Facebook",
        "target_url": DEFAULT_TARGET_URL,
        "utm_source": "facebook",
        "utm_medium": "social_paid",
        "utm_campaign": DEFAULT_CAMPAIGN,
        "utm_term": "mangaluru_shoppers",
        "utm_content": "fb_carousel_ad",
        "notes": "Meta / Facebook regional ad set for Dakshina Kannada",
    },
    {
        "slug": "mgl-google-ads",
        "live_short_url": "https://spoo.me/myg-mgl-gads",
        "title": "Google Search & PMax Ads — myG Future Mangaluru",
        "channel": "Google Ads",
        "target_url": DEFAULT_TARGET_URL,
        "utm_source": "google",
        "utm_medium": "cpc",
        "utm_campaign": DEFAULT_CAMPAIGN,
        "utm_term": "myg_future_mangaluru_offers",
        "utm_content": "search_responsive_ad",
        "notes": "Google Search & Performance Max local store campaign",
    },
    {
        "slug": "mgl-gbp",
        "live_short_url": "https://spoo.me/myg-mgl-gbp",
        "title": "Google Business Profile — myG Future Mangaluru",
        "channel": "Google Business",
        "target_url": DEFAULT_TARGET_URL,
        "utm_source": "google_business",
        "utm_medium": "organic_local",
        "utm_campaign": DEFAULT_CAMPAIGN,
        "utm_term": "mangaluru_local",
        "utm_content": "gbp_profile_link",
        "notes": "Google Maps / Google Business Profile website & offers button",
    },
    {
        "slug": "mgl-qr",
        "live_short_url": "https://spoo.me/myg-mgl-qr",
        "title": "In-Store & Print Flyer QR — myG Future Mangaluru",
        "channel": "QR Code",
        "target_url": DEFAULT_TARGET_URL,
        "utm_source": "qr_code",
        "utm_medium": "offline_print",
        "utm_campaign": DEFAULT_CAMPAIGN,
        "utm_term": "in_store_flyer",
        "utm_content": "standee_qr_scan",
        "notes": "QR code on newspaper ads, brochures, and store standees",
    },
    {
        "slug": "mgl-youtube",
        "live_short_url": "https://spoo.me/myg-mgl-yt",
        "title": "YouTube Shorts & Video — myG Future Mangaluru",
        "channel": "YouTube",
        "target_url": DEFAULT_TARGET_URL,
        "utm_source": "youtube",
        "utm_medium": "video_ad",
        "utm_campaign": DEFAULT_CAMPAIGN,
        "utm_term": "mangaluru_tech",
        "utm_content": "yt_shorts_cta",
        "notes": "YouTube Shorts & regional video bumper campaign",
    },
    {
        "slug": "mgl-influencer",
        "live_short_url": "https://spoo.me/myg-mgl-inf",
        "title": "Influencer & Creator Collab — myG Future Mangaluru",
        "channel": "Influencer",
        "target_url": DEFAULT_TARGET_URL,
        "utm_source": "influencer",
        "utm_medium": "referral",
        "utm_campaign": DEFAULT_CAMPAIGN,
        "utm_term": "mangaluru_creators",
        "utm_content": "creator_story_link",
        "notes": "Local Mangaluru influencer & vlogger tracking link",
    },
]


def _get_sqlite_conn():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def get_public_base_origin(request=None) -> str:
    """
    Returns the public live domain for shareable `/go/<slug>/` links.
    If accessed on a real public domain (not 127.0.0.1 / localhost), uses that host;
    if accessed on localhost/127.0.0.1, uses `PUBLIC_TRACKING_BASE_URL`
    (`https://loyalty-main-dashboard.onrender.com`).
    """
    if request is not None:
        try:
            host = (request.get_host() or "").strip()
            if host and not any(
                local_h in host.lower()
                for local_h in ("127.0.0.1", "localhost", "0.0.0.0", "testserver")
            ):
                scheme = "https" if request.is_secure() or "onrender.com" in host else (request.scheme or "https")
                return f"{scheme}://{host}".rstrip("/")
        except Exception:
            pass
    return PUBLIC_BASE_URL


def build_full_utm_url(
    target_url: str,
    utm_source: str,
    utm_medium: str,
    utm_campaign: str,
    utm_term: str = "",
    utm_content: str = "",
) -> str:
    """Constructs the final destination URL with properly encoded UTM query parameters."""
    base = (target_url or DEFAULT_TARGET_URL).strip()
    parsed = urlparse(base)
    existing_params = dict(parse_qsl(parsed.query, keep_blank_values=True))

    utm_params = {}
    if utm_source:
        utm_params["utm_source"] = utm_source.strip()
    if utm_medium:
        utm_params["utm_medium"] = utm_medium.strip()
    if utm_campaign:
        utm_params["utm_campaign"] = utm_campaign.strip()
    if utm_term:
        utm_params["utm_term"] = utm_term.strip()
    if utm_content:
        utm_params["utm_content"] = utm_content.strip()

    merged = {**existing_params, **utm_params}
    new_query = urlencode(merged)
    return urlunparse(
        (parsed.scheme or "https", parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
    )


def provision_live_spoo_shortlink(target_to_shorten: str, slug: str) -> str:
    """
    Calls https://spoo.me/ to create a real, live HTTPS short link pointing to
    your domain redirect link (`https://loyalty-main-dashboard.onrender.com/go/<slug>/`)
    or destination URL.
    """
    clean_alias = re.sub(r"[^a-zA-Z0-9\-_]", "", f"myg-{slug}")[:15]
    for candidate_alias in (clean_alias, f"{clean_alias[:11]}{int(datetime.now().timestamp()) % 1000}"):
        try:
            data = urllib.parse.urlencode({"url": target_to_shorten, "alias": candidate_alias}).encode("utf-8")
            req = urllib.request.Request(
                "https://spoo.me/",
                data=data,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4.5) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                short_url = res.get("short_url", "")
                if short_url:
                    return short_url.replace("http://", "https://")
        except Exception:
            continue
    return ""


def fetch_spoo_live_stats(live_short_url: str) -> dict:
    """Queries https://spoo.me/stats/<alias> to retrieve real-time click telemetry."""
    if not live_short_url or "spoo.me/" not in live_short_url:
        return {}
    alias = live_short_url.rstrip("/").split("/")[-1]
    if not alias:
        return {}
    try:
        req = urllib.request.Request(
            f"https://spoo.me/stats/{alias}",
            data=b"",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}


def _init_pg_tables() -> bool:
    """Initializes `utm_links` and `utm_clicks` on the shared DigitalOcean PostgreSQL database if reachable."""
    try:
        with connection.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS utm_links (
                    id SERIAL PRIMARY KEY,
                    slug VARCHAR(128) UNIQUE NOT NULL,
                    live_short_url TEXT DEFAULT '',
                    title TEXT NOT NULL,
                    channel VARCHAR(64) NOT NULL DEFAULT 'Custom',
                    target_url TEXT NOT NULL,
                    utm_source VARCHAR(128) NOT NULL,
                    utm_medium VARCHAR(128) NOT NULL,
                    utm_campaign VARCHAR(128) NOT NULL,
                    utm_term VARCHAR(128) DEFAULT '',
                    utm_content VARCHAR(128) DEFAULT '',
                    full_utm_url TEXT NOT NULL,
                    notes TEXT DEFAULT '',
                    is_active INTEGER DEFAULT 1,
                    created_at VARCHAR(32) NOT NULL
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS utm_clicks (
                    id SERIAL PRIMARY KEY,
                    link_id INTEGER,
                    slug VARCHAR(128) NOT NULL,
                    utm_source VARCHAR(128) DEFAULT '',
                    utm_medium VARCHAR(128) DEFAULT '',
                    utm_campaign VARCHAR(128) DEFAULT '',
                    utm_term VARCHAR(128) DEFAULT '',
                    utm_content VARCHAR(128) DEFAULT '',
                    visitor_hash VARCHAR(64) NOT NULL,
                    ip_masked VARCHAR(64) DEFAULT '',
                    device_type VARCHAR(32) DEFAULT 'Mobile',
                    os_name VARCHAR(64) DEFAULT 'Other',
                    browser_name VARCHAR(64) DEFAULT 'Other',
                    referrer TEXT DEFAULT 'Direct',
                    user_agent TEXT DEFAULT '',
                    event_type VARCHAR(32) DEFAULT 'redirect',
                    clicked_at VARCHAR(32) NOT NULL,
                    clicked_date VARCHAR(16) NOT NULL,
                    clicked_hour VARCHAR(16) NOT NULL
                );
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pg_utm_clicks_slug ON utm_clicks(slug);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pg_utm_clicks_date ON utm_clicks(clicked_date);")

            now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
            for preset in PRESET_LINKS:
                full_url = build_full_utm_url(
                    preset["target_url"],
                    preset["utm_source"],
                    preset["utm_medium"],
                    preset["utm_campaign"],
                    preset.get("utm_term", ""),
                    preset.get("utm_content", ""),
                )
                cur.execute(
                    """
                    INSERT INTO utm_links (
                        slug, live_short_url, title, channel, target_url,
                        utm_source, utm_medium, utm_campaign, utm_term, utm_content,
                        full_utm_url, notes, is_active, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s)
                    ON CONFLICT (slug) DO UPDATE
                    SET live_short_url = EXCLUDED.live_short_url,
                        full_utm_url = EXCLUDED.full_utm_url;
                    """,
                    (
                        preset["slug"],
                        preset.get("live_short_url", ""),
                        preset["title"],
                        preset["channel"],
                        preset["target_url"],
                        preset["utm_source"],
                        preset["utm_medium"],
                        preset["utm_campaign"],
                        preset.get("utm_term", ""),
                        preset.get("utm_content", ""),
                        full_url,
                        preset.get("notes", ""),
                        now_str,
                    ),
                )
        return True
    except Exception:
        return False


def init_utm_db():
    """Initializes local SQLite WAL store AND attempts cloud PostgreSQL sync."""
    with _get_sqlite_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS utm_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                live_short_url TEXT DEFAULT '',
                title TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'Custom',
                target_url TEXT NOT NULL,
                utm_source TEXT NOT NULL,
                utm_medium TEXT NOT NULL,
                utm_campaign TEXT NOT NULL,
                utm_term TEXT DEFAULT '',
                utm_content TEXT DEFAULT '',
                full_utm_url TEXT NOT NULL,
                notes TEXT DEFAULT '',
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        cols = [r[1] for r in conn.execute("PRAGMA table_info(utm_links)").fetchall()]
        if "live_short_url" not in cols:
            conn.execute("ALTER TABLE utm_links ADD COLUMN live_short_url TEXT DEFAULT '';")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS utm_clicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link_id INTEGER,
                slug TEXT NOT NULL,
                utm_source TEXT DEFAULT '',
                utm_medium TEXT DEFAULT '',
                utm_campaign TEXT DEFAULT '',
                utm_term TEXT DEFAULT '',
                utm_content TEXT DEFAULT '',
                visitor_hash TEXT NOT NULL,
                ip_masked TEXT DEFAULT '',
                device_type TEXT DEFAULT 'Mobile',
                os_name TEXT DEFAULT 'Other',
                browser_name TEXT DEFAULT 'Other',
                referrer TEXT DEFAULT 'Direct',
                user_agent TEXT DEFAULT '',
                event_type TEXT DEFAULT 'redirect',
                clicked_at TEXT NOT NULL,
                clicked_date TEXT NOT NULL,
                clicked_hour TEXT NOT NULL,
                FOREIGN KEY(link_id) REFERENCES utm_links(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_utm_clicks_slug ON utm_clicks(slug);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_utm_clicks_date ON utm_clicks(clicked_date);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_utm_clicks_source ON utm_clicks(utm_source);")

        now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        for preset in PRESET_LINKS:
            full_url = build_full_utm_url(
                preset["target_url"],
                preset["utm_source"],
                preset["utm_medium"],
                preset["utm_campaign"],
                preset.get("utm_term", ""),
                preset.get("utm_content", ""),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO utm_links (
                    slug, live_short_url, title, channel, target_url,
                    utm_source, utm_medium, utm_campaign, utm_term, utm_content,
                    full_utm_url, notes, is_active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    preset["slug"],
                    preset.get("live_short_url", ""),
                    preset["title"],
                    preset["channel"],
                    preset["target_url"],
                    preset["utm_source"],
                    preset["utm_medium"],
                    preset["utm_campaign"],
                    preset.get("utm_term", ""),
                    preset.get("utm_content", ""),
                    full_url,
                    preset.get("notes", ""),
                    now_str,
                ),
            )
            if preset.get("live_short_url"):
                conn.execute(
                    "UPDATE utm_links SET live_short_url = ? WHERE slug = ?",
                    (preset["live_short_url"], preset["slug"]),
                )
        conn.commit()

    _init_pg_tables()


def parse_user_agent(ua_str: str) -> dict:
    """Lightweight User-Agent parser for Device, OS, and In-App Browser detection."""
    ua = (ua_str or "").strip()
    ua_lower = ua.lower()

    if any(b in ua_lower for b in ("bot", "spider", "crawler", "slurp", "facebookexternalhit", "telegrambot")):
        device = "Mobile" if "whatsapp" in ua_lower else "Bot"
    elif any(t in ua_lower for t in ("ipad", "tablet", "playbook", "silk")) or ("android" in ua_lower and "mobile" not in ua_lower):
        device = "Tablet"
    elif any(m in ua_lower for m in ("mobi", "iphone", "ipod", "android", "windows phone")):
        device = "Mobile"
    else:
        device = "Desktop"

    if "android" in ua_lower:
        os_name = "Android"
    elif any(x in ua_lower for x in ("iphone", "ipad", "ipod", "ios")):
        os_name = "iOS"
    elif "windows" in ua_lower:
        os_name = "Windows"
    elif "mac os" in ua_lower or "macintosh" in ua_lower:
        os_name = "macOS"
    elif "linux" in ua_lower:
        os_name = "Linux"
    else:
        os_name = "Other"

    if "whatsapp" in ua_lower:
        browser = "WhatsApp In-App"
    elif "instagram" in ua_lower:
        browser = "Instagram In-App"
    elif "fban" in ua_lower or "fbav" in ua_lower or "facebook" in ua_lower:
        browser = "Facebook In-App"
    elif "edg/" in ua_lower or "edga/" in ua_lower or "edgios/" in ua_lower:
        browser = "Edge"
    elif "opr/" in ua_lower or "opera" in ua_lower:
        browser = "Opera"
    elif "chrome/" in ua_lower or "crios/" in ua_lower:
        browser = "Chrome"
    elif "firefox/" in ua_lower or "fxios/" in ua_lower:
        browser = "Firefox"
    elif "safari/" in ua_lower:
        browser = "Safari"
    else:
        browser = "Other"

    return {
        "device_type": device,
        "os_name": os_name,
        "browser_name": browser,
    }


def extract_client_meta(request) -> dict:
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    raw_ip = xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR", "0.0.0.0")
    ua = request.META.get("HTTP_USER_AGENT", "") or ""
    referer = request.META.get("HTTP_REFERER", "") or ""

    if "." in raw_ip:
        parts = raw_ip.split(".")
        ip_masked = f"{parts[0]}.{parts[1]}.{parts[2]}.xxx" if len(parts) == 4 else raw_ip
    elif ":" in raw_ip:
        ip_masked = raw_ip[:12] + "::xxx"
    else:
        ip_masked = "unknown"

    visitor_hash = hashlib.sha256(f"{raw_ip}|{ua}".encode("utf-8")).hexdigest()[:16]

    ref_label = "Direct / Messaging"
    if referer:
        try:
            ref_host = urlparse(referer).netloc.lower()
            if ref_host:
                ref_label = ref_host
        except Exception:
            ref_label = referer[:60]

    ua_info = parse_user_agent(ua)
    return {
        "visitor_hash": visitor_hash,
        "ip_masked": ip_masked,
        "referrer": ref_label,
        "user_agent": ua[:300],
        **ua_info,
    }


def record_click_and_get_target(slug: str, request, extra_params: dict = None) -> str:
    """
    Logs a click event for `slug` in both DigitalOcean PostgreSQL (cloud shared) and local SQLite,
    and returns the full UTM destination URL.
    """
    init_utm_db()
    meta = extract_client_meta(request)
    now_dt = datetime.now(IST)
    clicked_at = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    clicked_date = now_dt.strftime("%Y-%m-%d")
    clicked_hour = now_dt.strftime("%H:00")

    target_url = None
    link_id = None
    utm_source = (extra_params or {}).get("utm_source", "direct_shortlink")
    utm_medium = (extra_params or {}).get("utm_medium", "referral")
    utm_campaign = (extra_params or {}).get("utm_campaign", DEFAULT_CAMPAIGN)
    utm_term = (extra_params or {}).get("utm_term", "")
    utm_content = (extra_params or {}).get("utm_content", slug)

    # 1. Try PostgreSQL first so Render & Local share all clicks and custom links
    pg_logged = False
    try:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT id, utm_source, utm_medium, utm_campaign, utm_term, utm_content, full_utm_url FROM utm_links WHERE slug = %s",
                (slug,),
            )
            pg_row = cur.fetchone()
            if pg_row:
                link_id, utm_source, utm_medium, utm_campaign, utm_term, utm_content, target_url = pg_row
            else:
                target_url = build_full_utm_url(
                    DEFAULT_TARGET_URL, utm_source, utm_medium, utm_campaign, utm_term, utm_content
                )
            cur.execute(
                """
                INSERT INTO utm_clicks (
                    link_id, slug, utm_source, utm_medium, utm_campaign, utm_term, utm_content,
                    visitor_hash, ip_masked, device_type, os_name, browser_name,
                    referrer, user_agent, event_type, clicked_at, clicked_date, clicked_hour
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'redirect', %s, %s, %s)
                """,
                (
                    link_id,
                    slug,
                    utm_source,
                    utm_medium,
                    utm_campaign,
                    utm_term or "",
                    utm_content or "",
                    meta["visitor_hash"],
                    meta["ip_masked"],
                    meta["device_type"],
                    meta["os_name"],
                    meta["browser_name"],
                    meta["referrer"],
                    meta["user_agent"],
                    clicked_at,
                    clicked_date,
                    clicked_hour,
                ),
            )
            pg_logged = True
    except Exception:
        pass

    # 2. Always mirror to local SQLite
    with _get_sqlite_conn() as conn:
        row = conn.execute("SELECT * FROM utm_links WHERE slug = ?", (slug,)).fetchone()
        if row:
            link_id = row["id"]
            utm_source = row["utm_source"]
            utm_medium = row["utm_medium"]
            utm_campaign = row["utm_campaign"]
            utm_term = row["utm_term"] or ""
            utm_content = row["utm_content"] or ""
            target_url = row["full_utm_url"]
        elif not target_url:
            target_url = build_full_utm_url(
                DEFAULT_TARGET_URL, utm_source, utm_medium, utm_campaign, utm_term, utm_content
            )

        if not pg_logged:
            conn.execute(
                """
                INSERT INTO utm_clicks (
                    link_id, slug, utm_source, utm_medium, utm_campaign, utm_term, utm_content,
                    visitor_hash, ip_masked, device_type, os_name, browser_name,
                    referrer, user_agent, event_type, clicked_at, clicked_date, clicked_hour
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'redirect', ?, ?, ?)
                """,
                (
                    link_id,
                    slug,
                    utm_source,
                    utm_medium,
                    utm_campaign,
                    utm_term,
                    utm_content,
                    meta["visitor_hash"],
                    meta["ip_masked"],
                    meta["device_type"],
                    meta["os_name"],
                    meta["browser_name"],
                    meta["referrer"],
                    meta["user_agent"],
                    clicked_at,
                    clicked_date,
                    clicked_hour,
                ),
            )
            conn.commit()

    return target_url


def record_external_utm_hit(request, payload: dict = None) -> dict:
    init_utm_db()
    meta = extract_client_meta(request)
    data = payload or {}

    utm_source = (data.get("utm_source") or request.GET.get("utm_source") or "direct").strip()
    utm_medium = (data.get("utm_medium") or request.GET.get("utm_medium") or "none").strip()
    utm_campaign = (data.get("utm_campaign") or request.GET.get("utm_campaign") or DEFAULT_CAMPAIGN).strip()
    utm_term = (data.get("utm_term") or request.GET.get("utm_term") or "").strip()
    utm_content = (data.get("utm_content") or request.GET.get("utm_content") or "").strip()
    slug = (data.get("slug") or request.GET.get("slug") or f"pixel-{utm_source}").strip()

    now_dt = datetime.now(IST)
    clicked_at = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    clicked_date = now_dt.strftime("%Y-%m-%d")
    clicked_hour = now_dt.strftime("%H:00")

    pg_logged = False
    resolved_slug = slug
    try:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT id, slug FROM utm_links WHERE slug = %s OR (utm_source = %s AND utm_medium = %s AND utm_campaign = %s) LIMIT 1",
                (slug, utm_source, utm_medium, utm_campaign),
            )
            r = cur.fetchone()
            link_id = r[0] if r else None
            if r:
                resolved_slug = r[1]
            cur.execute(
                """
                INSERT INTO utm_clicks (
                    link_id, slug, utm_source, utm_medium, utm_campaign, utm_term, utm_content,
                    visitor_hash, ip_masked, device_type, os_name, browser_name,
                    referrer, user_agent, event_type, clicked_at, clicked_date, clicked_hour
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pixel', %s, %s, %s)
                """,
                (
                    link_id,
                    resolved_slug,
                    utm_source,
                    utm_medium,
                    utm_campaign,
                    utm_term,
                    utm_content,
                    meta["visitor_hash"],
                    meta["ip_masked"],
                    meta["device_type"],
                    meta["os_name"],
                    meta["browser_name"],
                    meta["referrer"],
                    meta["user_agent"],
                    clicked_at,
                    clicked_date,
                    clicked_hour,
                ),
            )
            pg_logged = True
    except Exception:
        pass

    if not pg_logged:
        with _get_sqlite_conn() as conn:
            row = conn.execute(
                "SELECT id, slug FROM utm_links WHERE slug = ? OR (utm_source = ? AND utm_medium = ? AND utm_campaign = ?) LIMIT 1",
                (slug, utm_source, utm_medium, utm_campaign),
            ).fetchone()
            link_id = row["id"] if row else None
            resolved_slug = row["slug"] if row else slug

            conn.execute(
                """
                INSERT INTO utm_clicks (
                    link_id, slug, utm_source, utm_medium, utm_campaign, utm_term, utm_content,
                    visitor_hash, ip_masked, device_type, os_name, browser_name,
                    referrer, user_agent, event_type, clicked_at, clicked_date, clicked_hour
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pixel', ?, ?, ?)
                """,
                (
                    link_id,
                    resolved_slug,
                    utm_source,
                    utm_medium,
                    utm_campaign,
                    utm_term,
                    utm_content,
                    meta["visitor_hash"],
                    meta["ip_masked"],
                    meta["device_type"],
                    meta["os_name"],
                    meta["browser_name"],
                    meta["referrer"],
                    meta["user_agent"],
                    clicked_at,
                    clicked_date,
                    clicked_hour,
                ),
            )
            conn.commit()

    return {"status": "ok", "slug": resolved_slug}


def create_or_update_utm_link(data: dict) -> dict:
    """Creates or updates a custom UTM link in both DigitalOcean PostgreSQL and local SQLite."""
    init_utm_db()
    target_url = (data.get("target_url") or DEFAULT_TARGET_URL).strip()
    title = (data.get("title") or "Custom Campaign Link").strip()
    channel = (data.get("channel") or "Custom").strip()
    utm_source = re.sub(r"\s+", "_", (data.get("utm_source") or "custom").strip().lower())
    utm_medium = re.sub(r"\s+", "_", (data.get("utm_medium") or "referral").strip().lower())
    utm_campaign = re.sub(r"\s+", "_", (data.get("utm_campaign") or DEFAULT_CAMPAIGN).strip().lower())
    utm_term = re.sub(r"\s+", "_", (data.get("utm_term") or "").strip().lower())
    utm_content = re.sub(r"\s+", "_", (data.get("utm_content") or "").strip().lower())
    notes = (data.get("notes") or "").strip()

    raw_slug = (data.get("slug") or f"mgl-{utm_source}-{utm_medium}").strip().lower()
    slug = re.sub(r"[^a-z0-9\-_]", "-", raw_slug).strip("-")
    if not slug:
        slug = f"mgl-{int(datetime.now(IST).timestamp())}"

    full_utm_url = build_full_utm_url(
        target_url, utm_source, utm_medium, utm_campaign, utm_term, utm_content
    )
    domain_redirect_url = f"{PUBLIC_BASE_URL}/go/{slug}/"
    # Connect spoo.me shortlink to the user's domain link!
    live_short_url = provision_live_spoo_shortlink(domain_redirect_url, slug) or provision_live_spoo_shortlink(full_utm_url, slug)
    now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

    try:
        with connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO utm_links (
                    slug, live_short_url, title, channel, target_url,
                    utm_source, utm_medium, utm_campaign, utm_term, utm_content,
                    full_utm_url, notes, is_active, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s)
                ON CONFLICT (slug) DO UPDATE
                SET live_short_url = COALESCE(NULLIF(EXCLUDED.live_short_url, ''), utm_links.live_short_url),
                    title = EXCLUDED.title,
                    channel = EXCLUDED.channel,
                    target_url = EXCLUDED.target_url,
                    utm_source = EXCLUDED.utm_source,
                    utm_medium = EXCLUDED.utm_medium,
                    utm_campaign = EXCLUDED.utm_campaign,
                    utm_term = EXCLUDED.utm_term,
                    utm_content = EXCLUDED.utm_content,
                    full_utm_url = EXCLUDED.full_utm_url,
                    notes = EXCLUDED.notes;
                """,
                (
                    slug,
                    live_short_url,
                    title,
                    channel,
                    target_url,
                    utm_source,
                    utm_medium,
                    utm_campaign,
                    utm_term,
                    utm_content,
                    full_utm_url,
                    notes,
                    now_str,
                ),
            )
    except Exception:
        pass

    with _get_sqlite_conn() as conn:
        existing = conn.execute("SELECT id, live_short_url FROM utm_links WHERE slug = ?", (slug,)).fetchone()
        if existing:
            keep_live = live_short_url or existing["live_short_url"] or ""
            conn.execute(
                """
                UPDATE utm_links
                SET live_short_url = ?, title = ?, channel = ?, target_url = ?,
                    utm_source = ?, utm_medium = ?, utm_campaign = ?,
                    utm_term = ?, utm_content = ?, full_utm_url = ?, notes = ?
                WHERE slug = ?
                """,
                (
                    keep_live,
                    title,
                    channel,
                    target_url,
                    utm_source,
                    utm_medium,
                    utm_campaign,
                    utm_term,
                    utm_content,
                    full_utm_url,
                    notes,
                    slug,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO utm_links (
                    slug, live_short_url, title, channel, target_url,
                    utm_source, utm_medium, utm_campaign, utm_term, utm_content,
                    full_utm_url, notes, is_active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    slug,
                    live_short_url,
                    title,
                    channel,
                    target_url,
                    utm_source,
                    utm_medium,
                    utm_campaign,
                    utm_term,
                    utm_content,
                    full_utm_url,
                    notes,
                    now_str,
                ),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM utm_links WHERE slug = ?", (slug,)).fetchone()
        res = dict(row)
        res["domain_redirect_url"] = domain_redirect_url
        return res


def delete_utm_link(slug: str) -> bool:
    init_utm_db()
    try:
        with connection.cursor() as cur:
            cur.execute("DELETE FROM utm_links WHERE slug = %s", (slug,))
    except Exception:
        pass
    with _get_sqlite_conn() as conn:
        cur = conn.execute("DELETE FROM utm_links WHERE slug = ?", (slug,))
        conn.commit()
        return cur.rowcount > 0


def get_utm_dashboard_data(
    request,
    start_date: str = "",
    end_date: str = "",
    source_filter: str = "",
    campaign_filter: str = "",
) -> dict:
    """
    Returns full analytics payload for the UTM Link Tracker dashboard, combining:
      1. Cloud PostgreSQL clicks (from `https://loyalty-main-dashboard.onrender.com/go/<slug>/`)
      2. Local SQLite clicks (if any offline test clicks)
      3. Live `spoo.me` shortlink click stats (`https://spoo.me/myg-mgl-*`)
    """
    init_utm_db()
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    public_origin = get_public_base_origin(request)

    raw_links = []
    stats_by_slug = {}
    pg_used = False

    # 1. Try reading from shared DigitalOcean PostgreSQL first
    try:
        pg_where = ["1=1"]
        pg_params = []
        if start_date:
            pg_where.append("clicked_date >= %s")
            pg_params.append(start_date)
        if end_date:
            pg_where.append("clicked_date <= %s")
            pg_params.append(end_date)
        if source_filter:
            pg_where.append("utm_source = %s")
            pg_params.append(source_filter)
        if campaign_filter:
            pg_where.append("utm_campaign = %s")
            pg_params.append(campaign_filter)
        pg_where_sql = " AND ".join(pg_where)

        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT id, slug, live_short_url, title, channel, target_url,
                       utm_source, utm_medium, utm_campaign, utm_term, utm_content,
                       full_utm_url, notes, is_active, created_at
                FROM utm_links
                ORDER BY id ASC
                """
            )
            cols = [c[0] for c in cur.description]
            raw_links = [dict(zip(cols, r)) for r in cur.fetchall()]

            cur.execute(
                f"""
                SELECT
                    slug,
                    COUNT(*) AS total_clicks,
                    COUNT(DISTINCT visitor_hash) AS unique_visitors,
                    SUM(CASE WHEN clicked_date = %s THEN 1 ELSE 0 END) AS today_clicks,
                    MAX(clicked_at) AS last_clicked_at
                FROM utm_clicks
                WHERE {pg_where_sql}
                GROUP BY slug
                """,
                [today_str] + pg_params,
            )
            agg_cols = [c[0] for c in cur.description]
            for r in cur.fetchall():
                d_agg = dict(zip(agg_cols, r))
                stats_by_slug[d_agg["slug"]] = d_agg
            pg_used = True
    except Exception:
        pg_used = False

    # 2. Fallback / merge with local SQLite
    where_clauses = ["1=1"]
    params = []
    if start_date:
        where_clauses.append("clicked_date >= ?")
        params.append(start_date)
    if end_date:
        where_clauses.append("clicked_date <= ?")
        params.append(end_date)
    if source_filter:
        where_clauses.append("utm_source = ?")
        params.append(source_filter)
    if campaign_filter:
        where_clauses.append("utm_campaign = ?")
        params.append(campaign_filter)
    where_sql = " AND ".join(where_clauses)

    with _get_sqlite_conn() as conn:
        if not raw_links:
            links_rows = conn.execute("SELECT * FROM utm_links ORDER BY id ASC").fetchall()
            raw_links = [dict(r) for r in links_rows]

        click_agg_rows = conn.execute(
            f"""
            SELECT
                slug,
                COUNT(*) AS total_clicks,
                COUNT(DISTINCT visitor_hash) AS unique_visitors,
                SUM(CASE WHEN clicked_date = ? THEN 1 ELSE 0 END) AS today_clicks,
                MAX(clicked_at) AS last_clicked_at
            FROM utm_clicks
            WHERE {where_sql}
            GROUP BY slug
            """,
            [today_str] + params,
        ).fetchall()
        for r in click_agg_rows:
            s_slug = r["slug"]
            if s_slug not in stats_by_slug:
                stats_by_slug[s_slug] = dict(r)
            else:
                stats_by_slug[s_slug]["total_clicks"] = int(stats_by_slug[s_slug].get("total_clicks") or 0) + int(r["total_clicks"] or 0)
                stats_by_slug[s_slug]["unique_visitors"] = int(stats_by_slug[s_slug].get("unique_visitors") or 0) + int(r["unique_visitors"] or 0)
                stats_by_slug[s_slug]["today_clicks"] = int(stats_by_slug[s_slug].get("today_clicks") or 0) + int(r["today_clicks"] or 0)
                if (r["last_clicked_at"] or "") > (stats_by_slug[s_slug].get("last_clicked_at") or ""):
                    stats_by_slug[s_slug]["last_clicked_at"] = r["last_clicked_at"]

    # 3. Fetch live stats from spoo.me concurrently
    live_stats_map = {}
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            future_map = {
                pool.submit(fetch_spoo_live_stats, d.get("live_short_url", "")): d["slug"]
                for d in raw_links
                if d.get("live_short_url")
            }
            for fut, slug in future_map.items():
                try:
                    live_stats_map[slug] = fut.result(timeout=3.5) or {}
                except Exception:
                    live_stats_map[slug] = {}
    except Exception:
        pass

    daily_map = {}
    source_agg = {}
    mobile_clicks_ext = 0
    total_clicks_all = 0
    unique_visitors_all = 0
    today_clicks_all = 0
    live_synthetic_logs = []

    links = []
    for d in raw_links:
        slug = d["slug"]
        local_st = stats_by_slug.get(slug, {})
        ext_st = live_stats_map.get(slug, {})

        ext_total = int(ext_st.get("total-clicks") or 0)
        ext_uniq = int(ext_st.get("unique-clicks") or 0)
        ext_counter = ext_st.get("counter") or {}
        ext_today = int(ext_counter.get(today_str) or 0)
        ext_last = ext_st.get("last-click") or ""

        loc_total = int(local_st.get("total_clicks") or 0)
        loc_uniq = int(local_st.get("unique_visitors") or 0)
        loc_today = int(local_st.get("today_clicks") or 0)
        loc_last = local_st.get("last_clicked_at") or ""

        combined_total = loc_total + ext_total
        combined_uniq = loc_uniq + ext_uniq
        combined_today = loc_today + ext_today
        last_clicked = loc_last if (loc_last and loc_last > ext_last) else (ext_last or loc_last or "Never")

        d["total_clicks"] = combined_total
        d["unique_visitors"] = combined_uniq
        d["today_clicks"] = combined_today
        d["last_clicked_at"] = last_clicked

        # Primary live domain link on the user's Render domain + connected short link
        d["domain_redirect_url"] = f"{public_origin}/go/{slug}/"
        d["short_redirect_url"] = f"{public_origin}/go/{slug}/"
        d["live_short_url"] = d.get("live_short_url") or f"{public_origin}/go/{slug}/"
        links.append(d)

        total_clicks_all += combined_total
        unique_visitors_all += combined_uniq
        today_clicks_all += combined_today

        src_key = (d["utm_source"], d["utm_medium"])
        if combined_total > 0:
            cur_s = source_agg.get(src_key, {"clicks": 0, "unique_visitors": 0})
            cur_s["clicks"] += combined_total
            cur_s["unique_visitors"] += combined_uniq
            source_agg[src_key] = cur_s

        for dt_str, cnt in ext_counter.items():
            if start_date and dt_str < start_date:
                continue
            if end_date and dt_str > end_date:
                continue
            cur_d = daily_map.get(dt_str, {"clicks": 0, "unique_visitors": 0})
            cur_d["clicks"] += int(cnt or 0)
            cur_d["unique_visitors"] += max(1, int(cnt or 0))
            daily_map[dt_str] = cur_d

        os_dict = ext_st.get("os_name") or {}
        for os_k, os_cnt in os_dict.items():
            if any(m in os_k.lower() for m in ("android", "ios", "iphone", "ipad", "mobile")):
                mobile_clicks_ext += int(os_cnt or 0)

        if ext_total > 0 and ext_last:
            live_synthetic_logs.append({
                "id": f"LIVE-{slug}",
                "slug": slug,
                "utm_source": d["utm_source"],
                "utm_medium": d["utm_medium"],
                "utm_campaign": d["utm_campaign"],
                "utm_term": d["utm_term"],
                "utm_content": d["utm_content"],
                "ip_masked": "Public Mobile/Web",
                "device_type": "Mobile" if any(m in str(ext_st.get("last-click-os", "")).lower() for m in ("android", "ios", "iphone")) else "Desktop/Mobile",
                "os_name": ext_st.get("last-click-os") or "Public OS",
                "browser_name": ext_st.get("last-click-browser") or "Public Browser",
                "referrer": d.get("live_short_url", "spoo.me"),
                "event_type": "live_shortlink",
                "clicked_at": f"{ext_last} ({ext_total} clicks)",
            })

    mobile_clicks_db = 0
    db_recent_clicks = []

    if pg_used:
        try:
            with connection.cursor() as cur:
                cur.execute(
                    f"SELECT COALESCE(SUM(CASE WHEN device_type IN ('Mobile', 'Tablet') THEN 1 ELSE 0 END), 0) FROM utm_clicks WHERE {pg_where_sql}",
                    pg_params,
                )
                mobile_clicks_db += int(cur.fetchone()[0] or 0)

                cur.execute(
                    f"SELECT clicked_date, COUNT(*), COUNT(DISTINCT visitor_hash) FROM utm_clicks WHERE {pg_where_sql} GROUP BY clicked_date ORDER BY clicked_date ASC",
                    pg_params,
                )
                for dt_str, c_cnt, u_cnt in cur.fetchall():
                    cur_d = daily_map.get(dt_str, {"clicks": 0, "unique_visitors": 0})
                    cur_d["clicks"] += int(c_cnt or 0)
                    cur_d["unique_visitors"] += int(u_cnt or 0)
                    daily_map[dt_str] = cur_d

                cur.execute(
                    f"""
                    SELECT id, slug, utm_source, utm_medium, utm_campaign, utm_term, utm_content,
                           ip_masked, device_type, os_name, browser_name, referrer, event_type, clicked_at
                    FROM utm_clicks
                    WHERE {pg_where_sql}
                    ORDER BY id DESC
                    LIMIT 100
                    """,
                    pg_params,
                )
                r_cols = [c[0] for c in cur.description]
                db_recent_clicks.extend([dict(zip(r_cols, r)) for r in cur.fetchall()])
        except Exception:
            pass

    with _get_sqlite_conn() as conn:
        kpi_row = conn.execute(
            f"SELECT SUM(CASE WHEN device_type IN ('Mobile', 'Tablet') THEN 1 ELSE 0 END) AS mobile_clicks FROM utm_clicks WHERE {where_sql}",
            params,
        ).fetchone()
        mobile_clicks_db += int(kpi_row["mobile_clicks"] or 0)

        daily_rows = conn.execute(
            f"SELECT clicked_date AS date, COUNT(*) AS clicks, COUNT(DISTINCT visitor_hash) AS unique_visitors FROM utm_clicks WHERE {where_sql} GROUP BY clicked_date ORDER BY clicked_date ASC",
            params,
        ).fetchall()
        for r in daily_rows:
            dt_str = r["date"]
            cur_d = daily_map.get(dt_str, {"clicks": 0, "unique_visitors": 0})
            cur_d["clicks"] += int(r["clicks"] or 0)
            cur_d["unique_visitors"] += int(r["unique_visitors"] or 0)
            daily_map[dt_str] = cur_d

        recent_rows = conn.execute(
            f"""
            SELECT id, slug, utm_source, utm_medium, utm_campaign, utm_term, utm_content,
                   ip_masked, device_type, os_name, browser_name, referrer, event_type, clicked_at
            FROM utm_clicks
            WHERE {where_sql}
            ORDER BY id DESC
            LIMIT 100
            """,
            params,
        ).fetchall()
        db_recent_clicks.extend([dict(r) for r in recent_rows])

        sources_list = [
            r[0]
            for r in conn.execute("SELECT DISTINCT utm_source FROM utm_links WHERE utm_source != '' ORDER BY utm_source").fetchall()
        ]
        campaigns_list = [
            r[0]
            for r in conn.execute("SELECT DISTINCT utm_campaign FROM utm_links WHERE utm_campaign != '' ORDER BY utm_campaign").fetchall()
        ]

    recent_clicks = live_synthetic_logs + db_recent_clicks[:100]
    total_mobile = mobile_clicks_db + mobile_clicks_ext
    mobile_share_pct = round(total_mobile / total_clicks_all * 100, 1) if total_clicks_all > 0 else 0.0

    by_source = sorted(
        [
            {
                "utm_source": k[0],
                "utm_medium": k[1],
                "clicks": v["clicks"],
                "unique_visitors": v["unique_visitors"],
                "share_pct": round(v["clicks"] / total_clicks_all * 100, 1) if total_clicks_all else 0.0,
            }
            for k, v in source_agg.items()
        ],
        key=lambda x: -x["clicks"],
    )
    top_source = by_source[0]["utm_source"] if by_source else "rcs"

    daily_trend = [
        {"date": dt, "clicks": vals["clicks"], "unique_visitors": vals["unique_visitors"]}
        for dt, vals in sorted(daily_map.items())
    ]

    return {
        "status": "ok",
        "default_target_url": DEFAULT_TARGET_URL,
        "default_campaign": DEFAULT_CAMPAIGN,
        "base_origin": public_origin,
        "cloud_pg_synced": pg_used,
        "kpis": {
            "total_clicks": total_clicks_all,
            "unique_visitors": unique_visitors_all,
            "today_clicks": today_clicks_all,
            "active_links": len(links),
            "top_source": top_source,
            "mobile_share_pct": mobile_share_pct,
        },
        "links": links,
        "by_source": by_source,
        "daily_trend": daily_trend,
        "recent_clicks": recent_clicks,
        "filter_options": {
            "sources": sources_list,
            "campaigns": campaigns_list,
        },
    }
