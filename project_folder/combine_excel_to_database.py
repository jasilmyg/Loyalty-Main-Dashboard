import os
import io
import sqlite3
import pandas as pd
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import time
from concurrent.futures import ThreadPoolExecutor
import queue
import threading

# --- Configuration ---
SERVICE_ACCOUNT_FILE = 'service_account.json'
FOLDER_ID = '1YDtp98SA2HLTbFjkTzOZy0R28fJmz2kL'
DB_NAME = 'combined_data.db'
TABLE_NAME = 'sales_data'
NUM_DOWNLOAD_THREADS = 5

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

def get_credentials():
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"Error: '{SERVICE_ACCOUNT_FILE}' not found!", flush=True)
        return None
    try:
        return service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    except Exception as e:
        print(f"Error loading credentials: {e}", flush=True)
        return None

def download_file_worker(creds, file_meta, download_queue):
    file_id = file_meta['id']
    file_name = file_meta['name']
    try:
        # Create a new service object for this thread (Google API Discovery is not thread-safe)
        service = build('drive', 'v3', credentials=creds, static_discovery=False)
        
        start_dl = time.time()
        request = service.files().get_media(fileId=file_id)
        file_stream = io.BytesIO()
        downloader = MediaIoBaseDownload(file_stream, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        dl_time = time.time() - start_dl
        file_stream.seek(0)
        download_queue.put((file_name, file_stream, dl_time))
    except Exception as e:
        print(f"  - Error downloading '{file_name}': {e}", flush=True)
        download_queue.put((file_name, None, 0))

def main():
    creds = get_credentials()
    if not creds: return
    
    # Discovery service for listing files (single thread)
    service = build('drive', 'v3', credentials=creds, static_discovery=False)
    
    print(f"Fetching file list from folder ID: {FOLDER_ID}...", flush=True)
    files = []
    page_token = None
    try:
        while True:
            query = f"'{FOLDER_ID}' in parents and trashed=false and name contains '.xlsx'"
            response = service.files().list(
                q=query,
                spaces='drive',
                fields='nextPageToken, files(id, name, size)',
                pageToken=page_token
            ).execute()
            for file in response.get('files', []):
                if file.get('name', '').endswith('.xlsx'):
                    files.append(file)
            page_token = response.get('nextPageToken', None)
            if page_token is None: break
        print(f"Found {len(files)} Excel files.", flush=True)
    except Exception as e:
        print(f"Error fetching files: {e}", flush=True)
        return

    if not files: return
    
    total_files = len(files)
    processed_count = 0
    download_queue = queue.Queue(maxsize=10)
    
    print(f"\nStarting thread-safe parallel download and incremental processing ({NUM_DOWNLOAD_THREADS} threads)...", flush=True)
    
    def downloader():
        with ThreadPoolExecutor(max_workers=NUM_DOWNLOAD_THREADS) as executor:
            for file_meta in files:
                executor.submit(download_file_worker, creds, file_meta, download_queue)
        download_queue.put((None, None, None))

    threading.Thread(target=downloader, daemon=True).start()
    
    conn = sqlite3.connect(DB_NAME)
    
    while True:
        file_name, file_stream, dl_t = download_queue.get()
        if file_name is None: break
        
        idx = processed_count + 1
        print(f"[{idx}/{total_files}] Processing: {file_name}", flush=True)
        
        if file_stream is not None:
            try:
                start_read = time.time()
                df = pd.read_excel(file_stream, engine='calamine')
                read_t = time.time() - start_read
                
                if not df.empty:
                    # ── Permanent exclusion filters ──────────────────────────
                    # Remove SMC/EI invoice numbers
                    if 'Invoice Number' in df.columns:
                        before = len(df)
                        df = df[~df['Invoice Number'].astype(str).str.contains('SMC|EI', na=False, regex=True)]
                        removed = before - len(df)
                        if removed:
                            print(f"  - Excluded {removed:,} SMC/EI invoice rows.", flush=True)
                    # Remove HEAD OFFICE and UG SMART CHOICE branches
                    if 'Branch' in df.columns:
                        before = len(df)
                        df = df[~df['Branch'].astype(str).str.upper().str.strip().isin(['HEAD OFFICE', 'UG SMART CHOICE'])]
                        removed = before - len(df)
                        if removed:
                            print(f"  - Excluded {removed:,} HEAD OFFICE/UG SMART CHOICE rows.", flush=True)
                    # ────────────────────────────────────────────────────────
                    df['source_file'] = file_name
                    write_mode = 'replace' if processed_count == 0 else 'append'
                    start_write = time.time()
                    
                    if write_mode == 'append':
                        try:
                            # Faster way to check table exists and get columns
                            db_cols = pd.read_sql(f"SELECT * FROM {TABLE_NAME} LIMIT 0", conn).columns.tolist()
                            df_cols = df.columns.tolist()
                            missing_in_df = [c for c in db_cols if c not in df_cols]
                            for col in missing_in_df:
                                df[col] = None
                            df = df[db_cols]
                        except: pass
                    
                    df.to_sql(TABLE_NAME, conn, if_exists=write_mode, index=False)
                    write_t = time.time() - start_write
                    print(f"  - Saved. [DL: {dl_t:.1f}s, Read: {read_t:.1f}s, Write: {write_t:.1f}s]", flush=True)
                else:
                    print(f"  - Warning: '{file_name}' is empty. Skipping.", flush=True)
                del df
            except Exception as e:
                print(f"  - Error processing '{file_name}': {e}", flush=True)
        
        processed_count += 1
        
    conn.close()
    print(f"\nSuccessfully processed {processed_count} files and saved to '{DB_NAME}'!", flush=True)

if __name__ == '__main__':
    main()
