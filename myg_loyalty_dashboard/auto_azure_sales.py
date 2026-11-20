"""
auto_azure_sales.py
===================
Automatic version of the manual daily sales load:
    invoice_wise_sales_report/*_DD-MM-YYYY_*.csv -> ClickHouse `azure_invoice_report`
    item_wise_sales_report/*_DD-MM-YYYY_*.csv    -> ClickHouse `azure_sales_report`

The blob files are published at ~03:00 IST and their NAME carries the day AFTER the data, e.g.
    ..._19-11-2026_03_00_02_165275.csv  ->  data date 2026-11-18
For every blob file date in the last --days days this script:
    * skips a table that already has rows for that data day (safe to run any number of times),
    * downloads the CSV, checks that every row is dated exactly the expected data day,
    * inserts it in duplicate-safe batches, verifies row count + total against the CSV,
    * records the file in `azure_ingestion_log`.
Rows are loaded exactly as in the CSV (no SMC / EI / head-office filtering), same as the manual loads.

Setup - in myg_loyalty_dashboard/.env (or environment variables / GitHub secret):
    AZURE_BLOB_SAS=<SAS token of the sales-reports container, "sp=...&sig=..." (a full URL also works)>
ClickHouse credentials come from the same CLICKHOUSE_* settings as the rest of the project.

Usage (run from the myg_loyalty_dashboard folder):
    python auto_azure_sales.py                 # last 6 days, load whatever is missing
    python auto_azure_sales.py --dry-run       # show what would be loaded, change nothing
    python auto_azure_sales.py --days 15       # wider catch-up window
"""
import argparse
import io
import os
import re
import sys
import time
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)
sys.path.insert(0, str(HERE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

import clickhouse_connect
import pandas as pd
from azure.storage.blob import ContainerClient
from dotenv import dotenv_values, load_dotenv

load_dotenv(HERE / ".env", override=False)

from analytics import starrocks_service as sr
from analytics.portal_refresh import invalidate_all_caches, trigger_remote_portal_refresh

ENV = {**dotenv_values(HERE / ".env"), **os.environ}
ACCOUNT_URL = ENV.get("AZURE_BLOB_ACCOUNT_URL", "https://stmygoalposreports.blob.core.windows.net").rstrip("/")
CONTAINER = ENV.get("AZURE_BLOB_CONTAINER", "sales-reports")
IN_CI = os.environ.get("GITHUB_ACTIONS") == "true"       # CI logs may be public: hide money figures there
IST = timezone(timedelta(hours=5, minutes=30))
BLOB_RE = re.compile(r"_(\d{2})-(\d{2})-(\d{4})_\d{2}_\d{2}_\d{2}_\d+\.csv$")

ITEM_COL_MAP = {
    "Date": "date", "Invoice No": "invoice_no", "Invoice No.": "invoice_no",
    "Branch": "branch", "Item Code": "item_code", "IMEI/Batch": "imei_batch",
    "IMEI/Batch No": "imei_batch", "IMEI/Batch No.": "imei_batch",
    "QTY": "qty", "Qty": "qty", "Quantity": "qty",
    "MOP": "mop", "Discount": "discount", "Buyback": "buyback",
    "Sold Price": "sold_price", "Taxable": "taxable",
}
INV_COL_MAP = {
    "Date": "date", "Time": "time", "Invoice No": "invoice_no", "Invoice No.": "invoice_no",
    "Branch": "branch", "RBM": "rbm", "BDM": "bdm",
    "Customer Bill To No": "customer_bill_to_no", "Customer Bill To No.": "customer_bill_to_no",
    "Customer Bill To Pincode": "customer_bill_to_pincode",
    "Customer Bill To GSTIN": "customer_bill_to_gstin", "Customer Bill to GSTIN": "customer_bill_to_gstin",
    "Customer Type": "customer_type", "Sales Staff Code": "sales_staff_code",
    "Billing Staff Code": "billing_staff_code", "Invoice Total": "invoice_total",
    "Discount": "discount", "Buyback": "buyback", "Deductions (Indirect)": "deductions__indirect_",
    "Exchange": "exchange", "Financier Code": "financier_code", "Financier Name": "financier_name",
    "Scheme": "scheme", "Loan Amount": "loan_amount",
}
RENAMES = [("customer_bill_to_no", "customer_mobile"), ("customer_bill_to_pincode", "customer_pincode"),
           ("customer_bill_to_gstin", "customer_gstin"), ("deductions__indirect_", "deductions")]

KINDS = {
    "invoice": dict(
        prefix="invoice_wise_sales_report/", table="azure_invoice_report", cmap=INV_COL_MAP,
        cols=["date", "time", "invoice_no", "branch", "rbm", "bdm", "customer_mobile", "customer_pincode",
              "customer_gstin", "customer_type", "sales_staff_code", "billing_staff_code", "invoice_total",
              "discount", "buyback", "deductions", "exchange", "financier_code", "financier_name", "scheme",
              "loan_amount"],
        floats=["invoice_total", "discount", "buyback", "deductions", "exchange", "loan_amount"],
        sum_col="invoice_total"),
    "item": dict(
        prefix="item_wise_sales_report/", table="azure_sales_report", cmap=ITEM_COL_MAP,
        cols=["date", "invoice_no", "branch", "item_code", "imei_batch", "qty", "mop", "discount", "buyback",
              "sold_price", "taxable"],
        floats=["qty", "mop", "discount", "buyback", "sold_price", "taxable"],
        sum_col="sold_price"),
}


def scrub(text):
    """Never let a SAS signature reach a log."""
    return re.sub(r"(sig=)[^&\s'\"]+", r"\1<hidden>", str(text))


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def money(v):
    return "<hidden in CI log>" if IN_CI else f"Rs.{v:,.0f}"


def sas_query():
    raw = (ENV.get("AZURE_BLOB_SAS") or "").strip().strip('"').strip("'")
    if not raw:
        sys.exit("ERROR: AZURE_BLOB_SAS is not set (SAS token of the sales-reports container). See the header of this file.")
    if "?" in raw:
        raw = raw.split("?", 1)[1]
    return raw.lstrip("?").replace("restype=container&comp=list&", "")


# ---------------------------------------------------------------- ClickHouse (retrying)
def ch_client(retries=6, wait=10):
    last = None
    passwords = [sr.CH_BACKUP_PASSWORD]
    if sr._CH_DEFAULT_PASSWORD not in passwords:
        passwords.append(sr._CH_DEFAULT_PASSWORD)
    for attempt in range(1, retries + 1):
        for pw in passwords:
            try:
                return clickhouse_connect.get_client(
                    host=sr.CH_BACKUP_HOST, port=sr.CH_BACKUP_PORT, username=sr.CH_BACKUP_USER, password=pw,
                    database=sr.CH_BACKUP_DATABASE, secure=sr.CH_BACKUP_SECURE,
                    connect_timeout=30, send_receive_timeout=600)
            except Exception as e:
                last = e
        log(f"  ClickHouse connect attempt {attempt}/{retries} failed ({type(last).__name__}); retry in {wait}s")
        time.sleep(wait)
    raise last


def ch_query(box, sql):
    for _ in range(6):
        try:
            return box[0].query(sql).result_rows
        except Exception as e:
            log(f"  query retry after {type(e).__name__}...")
            time.sleep(10)
            box[0] = ch_client()
    raise RuntimeError("query failed after retries")


def day_count(box, table, d):
    return int(ch_query(box, f"SELECT count() FROM {table} WHERE toDate(date) = '{d}'")[0][0])


def safe_insert(box, table, df, d, sub=10_000):
    """Insert in batches; after any error re-check the server so a batch is never applied twice or lost."""
    base = day_count(box, table, d)
    done = 0
    while done < len(df):
        part = df.iloc[done:done + sub]
        for attempt in range(1, 7):
            try:
                box[0].insert_df(table, part)
                break
            except Exception as e:
                log(f"  batch error ({type(e).__name__}) attempt {attempt}/6; verifying server state...")
                time.sleep(10)
                box[0] = ch_client()
                have = day_count(box, table, d)
                if have == base + done + len(part):
                    log("  batch had been applied; continuing")
                    break
                if have != base + done:
                    raise RuntimeError(f"unexpected partial state: {have} vs {base + done}/{base + done + len(part)}")
        else:
            raise RuntimeError("batch failed after 6 attempts")
        done += len(part)
    return done


# ---------------------------------------------------------------- CSV preparation
def prepare(raw_bytes, cfg):
    df = pd.read_csv(io.BytesIO(raw_bytes), low_memory=False)
    df.rename(columns={c: cfg["cmap"][str(c).strip()] for c in df.columns if str(c).strip() in cfg["cmap"]}, inplace=True)
    for old, new in RENAMES:
        if old in df.columns:
            df.rename(columns={old: new}, inplace=True)
    for c in df.columns:
        df[c] = df[c].fillna("")
    df["date"] = pd.to_datetime(df["date"], format="%d-%m-%Y", errors="coerce")
    for c in cfg["floats"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce").fillna(0.0)
    file_dates = sorted({d.strftime("%Y-%m-%d") for d in df["date"].dropna()})
    cols = cfg["cols"]
    df = df[[c for c in cols if c in df.columns]].copy()
    for c in cols:
        if c not in df.columns:
            df[c] = 0.0 if c in cfg["floats"] else ""
    for c in cols:
        if c == "date":
            continue
        if c in cfg["floats"]:
            df[c] = df[c].fillna(0.0).astype(float)
        else:
            df[c] = df[c].astype(str).replace("nan", "").replace("NaN", "").replace("None", "")
            if c in ("customer_mobile", "customer_pincode"):
                df[c] = df[c].str.replace(r"\.0$", "", regex=True)
    return df[cols], file_dates


def download(cc, name, retries=4):
    last = None
    for attempt in range(1, retries + 1):
        try:
            return cc.get_blob_client(name).download_blob().readall()
        except Exception as e:
            last = e
            log(f"  download failed ({type(e).__name__}: {scrub(e)[:150]}); retry {attempt}/{retries}")
            time.sleep(15)
    raise RuntimeError(f"could not download {name}: {scrub(last)[:200]}")


# ---------------------------------------------------------------- main
def build_plan(cc, days):
    """{data_date: {kind: blob_name}} for blob file dates within the last `days` days (IST)."""
    cutoff = datetime.now(IST).date() - timedelta(days=days)
    plan = {}
    for kind, cfg in KINDS.items():
        newest = {}
        for b in cc.list_blobs(name_starts_with=cfg["prefix"]):
            m = BLOB_RE.search(b.name)
            if not m:
                continue
            fdate = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            if fdate < cutoff:
                continue
            if fdate not in newest or b.last_modified > newest[fdate][1]:
                newest[fdate] = (b.name, b.last_modified)
        for fdate, (name, _) in newest.items():
            plan.setdefault(fdate - timedelta(days=1), {})[kind] = name
    return plan


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=6, help="look back this many days of blob files (default 6)")
    ap.add_argument("--dry-run", action="store_true", help="only show what would be loaded")
    ap.add_argument("--no-refresh", action="store_true", help="skip triggering portal cache refresh after load")
    ap.add_argument("--table-suffix", default="", help=argparse.SUPPRESS)   # testing: load into <table><suffix>
    args = ap.parse_args()

    def _set_gh_output(updated: bool) -> None:
        gh_out = os.environ.get("GITHUB_OUTPUT")
        if gh_out:
            try:
                with open(gh_out, "a", encoding="utf-8") as fh:
                    fh.write(f"updated={'true' if updated else 'false'}\n")
            except Exception:
                pass

    cc = ContainerClient.from_container_url(f"{ACCOUNT_URL}/{CONTAINER}?{sas_query()}")
    plan = build_plan(cc, args.days)
    if not plan:
        log("No sales blobs found in the window - nothing to do.")
        _set_gh_output(False)
        return 0
    box = [ch_client()]
    failures = 0
    loaded_files = 0
    todo = []

    for d in sorted(plan):
        for kind, name in sorted(plan[d].items()):
            table = KINDS[kind]["table"] + args.table_suffix
            have = day_count(box, table, d)
            log(f"{d} {table}: {'already loaded (' + format(have, ',') + ' rows)' if have else 'NOT loaded yet'}")
            if not have:
                todo.append((d, kind, name))
        for kind in KINDS:
            if kind not in plan[d]:
                log(f"{d} {KINDS[kind]['table']}: blob not published yet")

    if args.dry_run or not todo:
        log("Dry run - nothing loaded." if args.dry_run else "Everything is already loaded - nothing to do.")
        _set_gh_output(False)
        return 0

    for d, kind, name in todo:
        cfg = KINDS[kind]
        table = cfg["table"] + args.table_suffix
        try:
            log(f"{d} {table}: downloading {name}")
            df, file_dates = prepare(download(cc, name), cfg)
            log(f"  parsed {len(df):,} rows; dates in file: {file_dates}")
            if file_dates != [str(d)]:
                log(f"  SKIPPED: file dates {file_dates} != expected [{str(d)!r}]")
                failures += 1
                continue
            src_n, src_sum = len(df), float(df[cfg["sum_col"]].sum())
            safe_insert(box, table, df, d)
            n, s = ch_query(box, f"SELECT count(), sum({cfg['sum_col']}) FROM {table} WHERE toDate(date) = '{d}'")[0]
            ok = n == src_n and abs(float(s) - src_sum) < max(1.0, abs(src_sum) * 1e-6)
            log(f"  {table} {d}: {n:,} rows ({cfg['sum_col']} {money(float(s))}) | source {src_n:,} rows "
                f"({money(src_sum)}) -> {'MATCH' if ok else 'MISMATCH'}")
            if not ok:
                failures += 1
                continue
            loaded_files += 1
            if not args.table_suffix:
                logged = {r[0] for r in ch_query(box, f"SELECT file_name FROM azure_ingestion_log WHERE file_name = '{name}'")}
                if name not in logged:
                    box[0].insert("azure_ingestion_log", [[name, datetime.now()]], column_names=["file_name", "ingested_at"])
                    log("  logged in azure_ingestion_log")
        except Exception as e:
            log(f"{d} {table}: FAILED - {type(e).__name__}: {scrub(e)[:300]}")
            failures += 1

    _set_gh_output(loaded_files > 0)
    if loaded_files > 0 and not args.no_refresh and not args.table_suffix and not IN_CI:
        inv = invalidate_all_caches()
        log(f"Local caches cleared (epoch={inv['epoch']})")
        rem = trigger_remote_portal_refresh(stock_updated=False, sales_updated=True, source="auto_azure_sales")
        log(f"Live portal refresh: {'OK' if rem.get('ok') else rem.get('error') or rem.get('reason')}")
    log("RESULT: " + ("ALL OK" if not failures else f"{failures} problem(s) - see above"))
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {scrub(exc)[:400]}", flush=True)
        sys.exit(1)
