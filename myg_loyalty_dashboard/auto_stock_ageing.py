"""
auto_stock_ageing.py
====================
Fetch the daily "Auto Mailer Stock Reports ALL PRODUCTS" email, download the zip it links to,
take the "Detail" sheet of the Excel inside and load it into the ClickHouse `stock_ageing_report`
table (via load_stock_ageing.load_file: duplicate-safe, verified against the source file).

How it finds the file
    The email (from mygauditing@gdot.in, subject "... DATED : DD-MM-YYYY") contains a link like
    https://pubmedia.myg.in/STOCK/18-11-2026/stock_report_all_products_18-11-2026_23:36:33.556030.zip
    The time part of the file name changes every day, so the link is read from the mailbox.
    The snapshot date is the date in the subject (same convention as the manual file
    stock_report_18_November_2026_all_products.xlsx -> 2026-11-18).

Setup (once) - put these in myg_loyalty_dashboard/.env (or set as environment variables):
    STOCK_MAIL_USER=<mailbox that receives the stock email, e.g. you@myg.in>
    STOCK_MAIL_APP_PASSWORD=<16-char Google App Password for that mailbox>
    (Google account -> Security -> 2-Step Verification -> App passwords.  IMAP must be enabled
     in Gmail settings.  The job only READS mail; it never deletes or marks messages.)

Usage (run from the myg_loyalty_dashboard folder):
    python auto_stock_ageing.py                       # check last 5 days of mail, load any missing day
    python auto_stock_ageing.py --days 10             # wider catch-up window
    python auto_stock_ageing.py --dry-run             # show what would be loaded, change nothing
    python auto_stock_ageing.py --url <zip link>      # skip the mailbox, load this link directly
    python auto_stock_ageing.py --url <zip link> --date 2026-11-18 --replace
"""
import argparse
import email
import html
import imaplib
import io
import os
import re
import sys
import tempfile
import time
import zipfile
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)
sys.path.insert(0, str(HERE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from dotenv import dotenv_values, load_dotenv

load_dotenv(HERE / ".env", override=False)

import load_stock_ageing as stock_loader
from analytics.portal_refresh import invalidate_all_caches, trigger_remote_portal_refresh

ENV = {**dotenv_values(HERE / ".env"), **os.environ}
IMAP_HOST = ENV.get("STOCK_MAIL_IMAP_HOST", "imap.gmail.com")
MAIL_USER = ENV.get("STOCK_MAIL_USER", "")
MAIL_PASSWORD = (ENV.get("STOCK_MAIL_APP_PASSWORD", "") or "").replace(" ", "")
MAIL_FOLDER = ENV.get("STOCK_MAIL_FOLDER", "INBOX")
SENDER = ENV.get("STOCK_MAIL_SENDER", "mygauditing@gdot.in")
SUBJECT_KEY = "auto mailer stock reports all products"

LINK_RE = re.compile(r"https?://pubmedia\.myg\.in/STOCK/[^\s\"'<>]+?\.zip", re.I)
SUBJECT_DATE_RE = re.compile(r"DATED\s*:?\s*(\d{2})-(\d{2})-(\d{4})", re.I)
URL_DATE_RE = re.compile(r"all_products_(\d{2})-(\d{2})-(\d{4})", re.I)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# On GitHub Actions the repo (and its run logs) may be public: never print the download links
# (anyone with a link can fetch the report) or business figures there.
IN_CI = os.environ.get("GITHUB_ACTIONS") == "true"


def _shown(url):
    return "<link hidden in CI log>" if IN_CI else url


def _imap_date(d):
    return f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"


def _message_text(msg):
    chunks = []
    for part in msg.walk():
        if part.get_content_type() in ("text/plain", "text/html"):
            payload = part.get_payload(decode=True)
            if payload:
                chunks.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
    return html.unescape("\n".join(chunks))


def find_stock_links(days):
    """Return {report_date: zip_url} for stock emails received in the last `days` days."""
    if not MAIL_USER or not MAIL_PASSWORD:
        sys.exit("ERROR: set STOCK_MAIL_USER and STOCK_MAIL_APP_PASSWORD in .env (see the header of this file), "
                 "or pass --url <zip link>.")
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(MAIL_USER, MAIL_PASSWORD)
    except imaplib.IMAP4.error as e:
        sys.exit(f"ERROR: mailbox login failed ({e}). Use a Google App Password and make sure IMAP is enabled.")
    try:
        status, _ = imap.select(MAIL_FOLDER, readonly=True)       # read-only: never alters the mailbox
        if status != "OK":
            sys.exit(f"ERROR: cannot open mail folder {MAIL_FOLDER!r}")
        since = _imap_date(date.today() - timedelta(days=days))
        status, data = imap.search(None, f'(FROM "{SENDER}" SINCE {since})')
        ids = data[0].split() if status == "OK" and data and data[0] else []
        log(f"{len(ids)} mail(s) from {SENDER} since {since}")
        found = {}
        for mid in ids:
            status, msg_data = imap.fetch(mid, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject = str(make_header(decode_header(msg.get("Subject", ""))))
            if SUBJECT_KEY not in subject.lower():
                continue
            m = SUBJECT_DATE_RE.search(subject)
            link = LINK_RE.search(_message_text(msg))
            if not m or not link:
                log(f"  skipped mail {subject!r}: no date in subject or no zip link in body")
                continue
            d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            found[d] = link.group(0)            # later mail for the same date wins
            log(f"  found {d}: {_shown(link.group(0))}")
        return found
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def download_xlsx(url, workdir, retries=4):
    """Download the zip, return the path of the Excel file inside it."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            log(f"Downloading {_shown(url)} (attempt {attempt}/{retries})")
            r = requests.get(url, timeout=(30, 600))
            r.raise_for_status()
            zf = zipfile.ZipFile(io.BytesIO(r.content))
            names = [n for n in zf.namelist() if n.lower().endswith((".xlsx", ".xlsm")) and not n.startswith("__MACOSX")]
            if not names:
                raise RuntimeError(f"no Excel file inside zip (members: {zf.namelist()})")
            target = Path(workdir) / Path(names[0]).name
            target.write_bytes(zf.read(names[0]))
            log(f"  extracted {target.name} ({target.stat().st_size / 1e6:,.1f} MB)")
            return target
        except Exception as e:
            last = e
            log(f"  download failed: {type(e).__name__}: {str(e).replace(url, _shown(url))}")
            time.sleep(15)
    raise RuntimeError(f"could not download {_shown(url)}: {str(last).replace(url, _shown(url))}")


def date_from_url(url):
    m = URL_DATE_RE.search(url)
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=5, help="how many days of mail to look back (default 5)")
    ap.add_argument("--url", help="load this zip link directly instead of reading the mailbox")
    ap.add_argument("--date", help="snapshot date YYYY-MM-DD for --url (default: parsed from the link)")
    ap.add_argument("--replace", action="store_true", help="drop & reload a day that already exists")
    ap.add_argument("--dry-run", action="store_true", help="only show which days would be loaded")
    ap.add_argument("--no-refresh", action="store_true", help="skip triggering portal cache refresh after load")
    args = ap.parse_args()

    def _set_gh_output(updated: bool) -> None:
        gh_out = os.environ.get("GITHUB_OUTPUT")
        if gh_out:
            try:
                with open(gh_out, "a", encoding="utf-8") as fh:
                    fh.write(f"updated={'true' if updated else 'false'}\n")
            except Exception:
                pass

    if args.url:
        d = date.fromisoformat(args.date) if args.date else date_from_url(args.url)
        if d is None:
            sys.exit("ERROR: cannot read the date from the link; pass --date YYYY-MM-DD")
        todo = {d: args.url}
    else:
        todo = find_stock_links(args.days)
        if not todo:
            log("No stock-report email found in the window - nothing to do.")
            _set_gh_output(False)
            return 0

    client = stock_loader.connect()
    exists = {d: client.query(f"SELECT count() FROM {stock_loader.TABLE} WHERE report_date = '{d}'").result_rows[0][0]
              if client.query(f"EXISTS TABLE {stock_loader.TABLE}").result_rows[0][0] else 0
              for d in todo}
    pending = {d: u for d, u in todo.items() if args.replace or not exists[d]}
    for d in sorted(todo):
        log(f"{d}: {'already loaded (' + format(exists[d], ',') + ' rows)' if exists[d] else 'NOT loaded yet'}"
            f"{' -> will be replaced' if exists[d] and args.replace else ''}")
    if args.dry_run or not pending:
        log("Dry run - nothing loaded." if args.dry_run else "All days already loaded - nothing to do.")
        _set_gh_output(False)
        return 0

    failures = 0
    loaded_days = 0
    for d in sorted(pending):                       # oldest first so a missed run catches up in order
        with tempfile.TemporaryDirectory(prefix="stock_ageing_") as tmp:
            try:
                xlsx = download_xlsx(pending[d], tmp)
                status = stock_loader.load_file(client, str(xlsx), d, replace=args.replace)
            except Exception as e:
                log(f"{d}: FAILED - {type(e).__name__}: {e}")
                failures += 1
                continue
        log(f"{d}: {status.upper()}")
        if status == "mismatch":
            failures += 1
        else:
            loaded_days += 1

    _set_gh_output(loaded_days > 0)
    if loaded_days > 0 and not args.no_refresh and not IN_CI:
        inv = invalidate_all_caches()
        log(f"Local caches cleared (epoch={inv['epoch']})")
        rem = trigger_remote_portal_refresh(stock_updated=True, sales_updated=False, source="auto_stock_ageing")
        log(f"Live portal refresh: {'OK' if rem.get('ok') else rem.get('error') or rem.get('reason')}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
