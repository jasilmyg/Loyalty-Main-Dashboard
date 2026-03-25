# Google Drive Excel to SQLite Pipeline

This project contains a high-performance Python data pipeline that reads multiple Excel files stored inside a Google Drive folder and combines them into a single dataset stored in a SQLite database.

## Features
- **Parallel Downloading**: Uses 5 threads to download files from Google Drive simultaneously.
- **Fast Excel Processing**: Uses the `calamine` engine (via `python-calamine`) for 5-10x faster reading of large `.xlsx` files.
- **Incremental Database Writes**: Writes data file-by-file into SQLite to minimize memory (RAM) usage, especially important for large datasets (the current dataset is ~2.2 GB).
- **Column Alignment**: Automatically handles Excel files with slightly different column structures by aligning them with the database schema and padding missing values with nulls.
- **Progress Tracking**: Provides detailed logs showing download, read, and write times for each file.

## Project Structure
- `combine_excel_to_database.py`: The main Python script.
- `requirements.txt`: Python dependencies.
- `service_account.json`: Your Google Service Account key (you must provide this).
- `combined_data.db`: The resulting SQLite database.

## Setup and Installation

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Google Drive API Setup**:
   - Go to the [Google Cloud Console](https://console.cloud.google.com/).
   - Create a project and enable the **Google Drive API**.
   - Create a **Service Account** and download the JSON key.
   - Rename the key to `service_account.json` and place it in the `project_folder`.
   - **Important**: Share the target Google Drive folder with the service account's email address (with at least Viewer permissions).

3. **Configure Folder ID**:
   - Open `combine_excel_to_database.py` and set the `FOLDER_ID` variable to your target Google Drive folder ID.

## Running the Pipeline
```bash
python combine_excel_to_database.py
```

Upon completion, you will find `combined_data.db` in the same directory, containing a table named `sales_data`.
