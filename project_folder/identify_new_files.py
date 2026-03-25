import sys, os, io, sqlite3
from google.oauth2 import service_account
from googleapiclient.discovery import build

SERVICE_ACCOUNT_FILE = 'service_account.json'
FOLDER_ID = '1YDtp98SA2HLTbFjkTzOZy0R28fJmz2kL'
DB_NAME = 'combined_data.db'
TABLE_NAME = 'sales_data'

def get_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=['https://www.googleapis.com/auth/drive.readonly']
    )
    return build('drive', 'v3', credentials=creds, static_discovery=False)

def list_all_files(service):
    files = []
    page_token = None
    while True:
        resp = service.files().list(
            q=f"'{FOLDER_ID}' in parents and trashed=false and name contains '.xlsx'",
            spaces='drive',
            fields='nextPageToken, files(id, name)',
            pageToken=page_token
        ).execute()
        for f in resp.get('files', []):
            files.append(f)
        page_token = resp.get('nextPageToken')
        if not page_token:
            break
    return files

def get_imported_files():
    if not os.path.exists(DB_NAME):
        return set()
    try:
        conn = sqlite3.connect(DB_NAME)
        rows = conn.execute(f"SELECT DISTINCT source_file FROM [{TABLE_NAME}]").fetchall()
        conn.close()
        return {r[0] for r in rows if r[0]}
    except:
        return set()

def main():
    service = get_service()
    drive_files = list_all_files(service)
    imported_files = get_imported_files()
    
    missing = []
    for f in drive_files:
        if f['name'] not in imported_files:
            missing.append(f)
            
    print(f"Total files in Drive: {len(drive_files)}")
    print(f"Files already in DB: {len(imported_files)}")
    print(f"New files to add ({len(missing)}):")
    for m in missing:
        print(f" - {m['name']} (ID: {m['id']})")

if __name__ == '__main__':
    main()
