"""
File Analyzer & Operations Engine for Jazz AI
----------------------------------------------
Supports in-depth profiling, data operations, and analysis of:
  • Excel (.xlsx, .xls)
  • CSV (.csv)
  • PDF (.pdf)

Features:
  - Tabular data (Excel & CSV) is registered into an in-memory DuckDB database
    allowing ultra-fast, zero-truncation ANSI SQL queries, group bys, aggregations,
    and filtering across all rows.
  - Multi-sheet Excel files register each sheet as a separate table ('sheet_<name>')
    and the primary sheet as 'uploaded_file'.
  - PDF files are parsed via pypdf (pages, tables, sections) and encoded for
    multimodal Gemini vision analysis.
  - In-memory cache allows follow-up questions in the chat session to continue
    querying and operating on the uploaded file without re-uploading.
"""

import base64
import csv
import io
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import duckdb
import pandas as pd
try:
    import pypdf
except ImportError:
    try:
        import PyPDF2 as pypdf
    except ImportError:
        pypdf = None

logger = logging.getLogger(__name__)

# Max file size accepted (50 MB)
MAX_FILE_SIZE = 50 * 1024 * 1024

# Session file cache: session_id -> { "file_ctx": dict, "ts": float }
_FILE_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()
CACHE_TTL_SEC = 3600  # 1 hour


def _format_size(num_bytes: int) -> str:
    """Format bytes into human-readable size."""
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


def _sanitize_table_name(name: str) -> str:
    """Sanitize sheet name to a valid SQL identifier."""
    clean = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip().lower()).strip("_")
    if not clean or clean[0].isdigit():
        clean = f"sheet_{clean}"
    return clean[:40]


def _profile_dataframe(df: pd.DataFrame, table_name: str) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Generate a rich text profile of a pandas DataFrame for LLM consumption,
    including row counts, columns, data types, statistical summary, and sample rows.
    """
    total_rows, total_cols = df.shape
    lines = [
        f"### Table: `{table_name}` ({total_rows:,} rows × {total_cols} columns)",
        "**Columns & Data Types:**",
    ]

    col_meta = []
    for col in df.columns:
        col_name = str(col)
        series = df[col]
        non_null = int(series.count())
        null_count = total_rows - non_null
        dtype = str(series.dtype)

        meta = {
            "name": col_name,
            "dtype": dtype,
            "null_count": null_count,
            "non_null": non_null,
        }

        # Numeric statistics
        if pd.api.types.is_numeric_dtype(series):
            clean_series = series.dropna()
            if not clean_series.empty:
                s_sum = float(clean_series.sum())
                s_mean = float(clean_series.mean())
                s_min = float(clean_series.min())
                s_max = float(clean_series.max())
                meta["stats"] = f"sum={s_sum:,.2f}, avg={s_mean:,.2f}, min={s_min:,.2f}, max={s_max:,.2f}"
                lines.append(f"  - `{col_name}` ({dtype}): {meta['stats']} (nulls: {null_count})")
            else:
                lines.append(f"  - `{col_name}` ({dtype}): all nulls")
        # Datetime statistics
        elif pd.api.types.is_datetime64_any_dtype(series):
            clean_series = series.dropna()
            if not clean_series.empty:
                meta["stats"] = f"from {clean_series.min()} to {clean_series.max()}"
                lines.append(f"  - `{col_name}` ({dtype}): range {meta['stats']} (nulls: {null_count})")
            else:
                lines.append(f"  - `{col_name}` ({dtype}): all nulls")
        # Categorical / string statistics
        else:
            nunique = int(series.nunique(dropna=True))
            top_vals = series.value_counts(dropna=True).head(3).to_dict()
            top_str = ", ".join(f"'{k}': {v}" for k, v in top_vals.items())
            meta["stats"] = f"{nunique} unique values. Top: [{top_str}]"
            lines.append(f"  - `{col_name}` ({dtype}): {nunique} unique values (e.g. {top_str})")

        col_meta.append(meta)

    # Sample rows (first 10) formatted as Markdown table
    sample_n = min(10, total_rows)
    if sample_n > 0:
        sample_df = df.head(sample_n).copy()
        lines.append(f"\n**First {sample_n} sample rows:**")
        # Format table
        cols_str = " | ".join(str(c) for c in sample_df.columns)
        sep_str = " | ".join(["---"] * len(sample_df.columns))
        lines.append(f"| {cols_str} |")
        lines.append(f"| {sep_str} |")
        for _, row in sample_df.iterrows():
            vals = [str(v).replace("\n", " ").replace("|", "\\|")[:80] if pd.notna(v) else "" for v in row]
            lines.append(f"| {' | '.join(vals)} |")

    return "\n".join(lines), col_meta


def _parse_excel(file_bytes: bytes, filename: str) -> Dict[str, Any]:
    """Parse an Excel file into DuckDB tables and build a structural profile."""
    bio = io.BytesIO(file_bytes)
    excel_file = pd.ExcelFile(bio)
    sheet_names = excel_file.sheet_names

    con = duckdb.connect(":memory:")
    table_names = []
    profiles = []
    total_rows = 0
    total_cols = 0

    primary_df = None
    primary_name = "uploaded_file"

    for idx, sname in enumerate(sheet_names):
        try:
            df = pd.read_excel(excel_file, sheet_name=sname)
        except Exception as e:
            logger.warning("Could not read sheet %s from %s: %s", sname, filename, e)
            continue

        if df.empty:
            continue

        # Clean column names for SQL safety (strip leading/trailing whitespace)
        df.columns = [str(c).strip() if c is not None else f"col_{i}" for i, c in enumerate(df.columns)]

        tbl_name = f"sheet_{_sanitize_table_name(sname)}"
        if idx == 0 or primary_df is None or len(df) > len(primary_df):
            primary_df = df
            primary_name = tbl_name

        try:
            con.register(tbl_name, df)
            table_names.append(tbl_name)
        except Exception as e:
            logger.error("Failed to register table %s in DuckDB: %s", tbl_name, e)

        prof, _ = _profile_dataframe(df, f"{sname} (SQL table: `{tbl_name}`)")
        profiles.append(prof)
        total_rows += len(df)
        total_cols = max(total_cols, len(df.columns))

    if primary_df is not None:
        con.register("uploaded_file", primary_df)
        if "uploaded_file" not in table_names:
            table_names.insert(0, "uploaded_file")

    summary_text = (
        f"**File Profile:** Excel Spreadsheet `{filename}`\n"
        f"- Total Sheets: {len(sheet_names)} ({', '.join(sheet_names)})\n"
        f"- Registered SQL Tables in DuckDB: {', '.join(f'`{t}`' for t in table_names)}\n"
        f"- The primary table is also accessible as `uploaded_file`.\n\n"
        + "\n\n".join(profiles)
    )

    return {
        "file_type": "excel",
        "filename": filename,
        "file_size": len(file_bytes),
        "file_size_str": _format_size(len(file_bytes)),
        "duckdb_conn": con,
        "tables": table_names,
        "summary_prompt": summary_text,
        "inline_parts": [],
        "meta": {"sheets": len(sheet_names), "rows": total_rows, "cols": total_cols},
    }


def _parse_csv(file_bytes: bytes, filename: str) -> Dict[str, Any]:
    """Parse a CSV file into DuckDB and build a structural profile."""
    # Attempt encoding detection
    text_sample = file_bytes[:8192]
    encoding = "utf-8"
    for enc in ["utf-8", "utf-8-sig", "latin1", "cp1252"]:
        try:
            text_sample.decode(enc)
            encoding = enc
            break
        except UnicodeDecodeError:
            continue

    bio = io.BytesIO(file_bytes)
    # Detect delimiter
    try:
        sample_str = text_sample.decode(encoding, errors="replace")
        sniffer = csv.Sniffer()
        dialect = sniffer.sniff(sample_str)
        sep = dialect.delimiter
    except Exception:
        sep = ","

    bio.seek(0)
    df = pd.read_csv(bio, sep=sep, encoding=encoding, low_memory=False)
    df.columns = [str(c).strip() if c is not None else f"col_{i}" for i, c in enumerate(df.columns)]

    con = duckdb.connect(":memory:")
    con.register("uploaded_file", df)
    table_names = ["uploaded_file"]

    prof, _ = _profile_dataframe(df, "uploaded_file")

    summary_text = (
        f"**File Profile:** CSV File `{filename}`\n"
        f"- Registered SQL Table: `uploaded_file`\n"
        f"- Rows: {len(df):,}, Columns: {len(df.columns)}\n\n"
        f"{prof}"
    )

    return {
        "file_type": "csv",
        "filename": filename,
        "file_size": len(file_bytes),
        "file_size_str": _format_size(len(file_bytes)),
        "duckdb_conn": con,
        "tables": table_names,
        "summary_prompt": summary_text,
        "inline_parts": [],
        "meta": {"rows": len(df), "cols": len(df.columns)},
    }


def _parse_pdf(file_bytes: bytes, filename: str) -> Dict[str, Any]:
    """Parse a PDF document using pypdf and prepare multimodal data for Gemini."""
    bio = io.BytesIO(file_bytes)
    reader = pypdf.PdfReader(bio)
    total_pages = len(reader.pages)

    extracted_pages = []
    total_chars = 0
    max_pages_to_extract = min(40, total_pages)

    for i in range(max_pages_to_extract):
        try:
            page_text = reader.pages[i].extract_text() or ""
            page_text = re.sub(r"[ \t]+", " ", page_text).strip()
            if page_text:
                extracted_pages.append(f"--- [Page {i + 1} of {total_pages}] ---\n{page_text}")
                total_chars += len(page_text)
                if total_chars > 60000:
                    extracted_pages.append(f"\n... (remaining {total_pages - i - 1} pages omitted for brevity) ...")
                    break
        except Exception as e:
            logger.warning("Error extracting page %d of %s: %s", i + 1, filename, e)

    full_text = "\n\n".join(extracted_pages) if extracted_pages else "(No extractable text found in PDF pages)"

    # Metadata extraction
    meta_info = []
    if reader.metadata:
        for k in ["/Title", "/Author", "/Subject", "/Creator", "/Producer"]:
            val = reader.metadata.get(k)
            if val:
                meta_info.append(f"{k.lstrip('/')}: {val}")

    summary_text = (
        f"**File Profile:** PDF Document `{filename}`\n"
        f"- Total Pages: {total_pages}\n"
        + (f"- Document Info: {', '.join(meta_info)}\n" if meta_info else "")
        + f"\n**Extracted Document Content:**\n\n{full_text}"
    )

    inline_parts = []
    # If file size is under 15MB, also attach PDF as inlineData so Gemini can perform native multimodal visual analysis
    if len(file_bytes) <= 15 * 1024 * 1024:
        try:
            b64_data = base64.b64encode(file_bytes).decode("utf-8")
            inline_parts.append({
                "inlineData": {
                    "mimeType": "application/pdf",
                    "data": b64_data,
                }
            })
        except Exception as e:
            logger.warning("Could not encode PDF to base64: %s", e)

    return {
        "file_type": "pdf",
        "filename": filename,
        "file_size": len(file_bytes),
        "file_size_str": _format_size(len(file_bytes)),
        "duckdb_conn": None,
        "tables": [],
        "summary_prompt": summary_text,
        "inline_parts": inline_parts,
        "meta": {"pages": total_pages},
    }


def _parse_image(file_bytes: bytes, filename: str) -> Dict[str, Any]:
    """
    Parse an uploaded image (PNG, JPG, WebP, GIF, BMP) and prepare multimodal inline data for Gemini Vision.
    Extracts image metadata (dimensions, format) and prepares base64 payload.
    """
    ext = os.path.splitext(filename.lower())[1]
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }
    mime_type = mime_map.get(ext, "image/jpeg")

    width, height, img_format = None, None, None
    try:
        from PIL import Image
        with Image.open(io.BytesIO(file_bytes)) as img:
            width, height = img.size
            img_format = img.format
            # If the image is excessively large (> 15MB), optimize/compress to fit comfortably in LLM payload
            if len(file_bytes) > 15 * 1024 * 1024:
                out_bio = io.BytesIO()
                # If format doesn't support RGB conversion directly
                rgb_img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
                rgb_img.save(out_bio, format="JPEG", quality=85, optimize=True)
                file_bytes = out_bio.getvalue()
                mime_type = "image/jpeg"
                width, height = rgb_img.size
                img_format = "JPEG"
    except Exception as e:
        logger.warning("Could not inspect image metadata for %s: %s", filename, e)

    b64_data = base64.b64encode(file_bytes).decode("utf-8")
    inline_parts = [{
        "inlineData": {
            "mimeType": mime_type,
            "data": b64_data,
        }
    }]

    dim_str = f" ({width}x{height} px)" if width and height else ""
    summary_text = (
        f"**File Profile:** Uploaded Image `{filename}`{dim_str}\n"
        f"- Format: {img_format or ext.lstrip('.').upper()}\n"
        f"- Size: {_format_size(len(file_bytes))}\n"
        f"- Multimodal Vision Analysis: Ready. High-resolution image is attached to Gemini for visual intelligence.\n"
        f"- Supported Operations: Read charts, tables, numbers, receipts, invoices, error screens, and cross-reference with ClickHouse database data."
    )

    return {
        "file_type": "image",
        "filename": filename,
        "file_size": len(file_bytes),
        "file_size_str": _format_size(len(file_bytes)),
        "duckdb_conn": None,
        "tables": [],
        "summary_prompt": summary_text,
        "inline_parts": inline_parts,
        "meta": {"width": width, "height": height, "format": img_format or ext.lstrip('.').upper()},
    }


def analyze_file(file_obj_or_bytes: Any, filename: str) -> Dict[str, Any]:
    """
    Main entry point: analyzes uploaded file (Excel, CSV, PDF, or Image)
    and returns a structured file context dictionary.
    """
    if hasattr(file_obj_or_bytes, "seek"):
        try:
            file_obj_or_bytes.seek(0)
        except Exception:
            pass
    if hasattr(file_obj_or_bytes, "read"):
        file_bytes = file_obj_or_bytes.read()
    elif isinstance(file_obj_or_bytes, bytes):
        file_bytes = file_obj_or_bytes
    else:
        raise ValueError("Invalid file object provided.")

    if len(file_bytes) > MAX_FILE_SIZE:
        raise ValueError(f"File size ({_format_size(len(file_bytes))}) exceeds maximum allowed 50 MB.")

    ext = os.path.splitext(filename.lower())[1]

    if ext in [".xlsx", ".xls"]:
        return _parse_excel(file_bytes, filename)
    elif ext == ".csv":
        return _parse_csv(file_bytes, filename)
    elif ext == ".pdf":
        return _parse_pdf(file_bytes, filename)
    elif ext in [".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"]:
        return _parse_image(file_bytes, filename)
    else:
        raise ValueError(
            f"Unsupported file type '{ext}'. Please upload an Excel (.xlsx, .xls), CSV (.csv), PDF (.pdf), or Image (.png, .jpg, .jpeg, .webp, .gif) file."
        )


def execute_file_query(con: duckdb.DuckDBPyConnection, sql: str, max_rows: int = 500) -> Dict[str, Any]:
    """
    Execute read-only SQL on the in-memory DuckDB connection for the uploaded file.
    Returns standard result dictionary matching ClickHouse format.
    """
    t0 = time.time()
    clean_sql = (sql or "").strip().rstrip(";").strip()
    if not clean_sql:
        return {"error": "Empty SQL query."}

    # Safety checks
    first_word = clean_sql.split(None, 1)[0].upper() if clean_sql else ""
    if first_word not in ("SELECT", "WITH", "DESCRIBE", "DESC", "SHOW", "EXPLAIN"):
        return {"error": f"Only read-only SELECT/WITH/DESCRIBE statements are allowed on uploaded files. Got: {first_word}"}

    forbidden = re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE)\b", clean_sql, re.IGNORECASE)
    if forbidden:
        return {"error": f"Modification query '{forbidden.group(1).upper()}' is not permitted."}

    try:
        # Run DuckDB query
        df = con.execute(clean_sql).fetchdf()
        elapsed_ms = int((time.time() - t0) * 1000)

        # Convert numpy/pandas types to standard python types for JSON serialization
        columns = list(df.columns)
        total_rows = len(df)
        rows_limited = df.head(max_rows).copy()

        formatted_rows = []
        for _, row in rows_limited.iterrows():
            formatted_row = []
            for val in row:
                if pd.isna(val):
                    formatted_row.append(None)
                elif isinstance(val, (datetime, pd.Timestamp)):
                    formatted_row.append(val.isoformat())
                elif hasattr(val, "item"):  # numpy scalars
                    formatted_row.append(val.item())
                else:
                    formatted_row.append(val)
            formatted_rows.append(formatted_row)

        return {
            "columns": columns,
            "rows": formatted_rows,
            "row_count": total_rows,
            "truncated": total_rows > max_rows,
            "elapsed_ms": elapsed_ms,
            "source": "Uploaded File",
        }
    except Exception as e:
        logger.exception("DuckDB file query failed")
        return {"error": f"Query execution error: {str(e)}"}


# ── Session Cache Management ─────────────────────────────────────────────────
def set_cached_file_context(session_id: str, file_ctx: Dict[str, Any]) -> None:
    """Store the file context in session cache."""
    if not session_id:
        return
    with _CACHE_LOCK:
        # Clean expired entries
        now = time.time()
        expired = [sid for sid, item in _FILE_CACHE.items() if now - item.get("ts", 0) > CACHE_TTL_SEC]
        for sid in expired:
            _FILE_CACHE.pop(sid, None)

        _FILE_CACHE[session_id] = {
            "file_ctx": file_ctx,
            "ts": now,
        }


def get_cached_file_context(session_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve active file context for this session."""
    if not session_id:
        return None
    with _CACHE_LOCK:
        item = _FILE_CACHE.get(session_id)
        if not item:
            return None
        if time.time() - item.get("ts", 0) > CACHE_TTL_SEC:
            _FILE_CACHE.pop(session_id, None)
            return None
        # Refresh timestamp on access
        item["ts"] = time.time()
        return item["file_ctx"]


def clear_cached_file_context(session_id: str) -> None:
    """Clear active file context for session."""
    if not session_id:
        return
    with _CACHE_LOCK:
        _FILE_CACHE.pop(session_id, None)
