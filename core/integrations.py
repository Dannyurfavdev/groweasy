# core/integrations.py
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from django.conf import settings
import os

# Path to your service account credentials
CREDENTIALS_PATH = os.path.join(settings.BASE_DIR, 'credentials.json')

def get_google_creds():
    """Get Google API credentials"""
    scope = [
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive.readonly',
        'https://www.googleapis.com/auth/drive.metadata.readonly'
    ]
    return ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, scope)


def read_google_sheet(sheet_id, sheet_name='Sheet1'):
    """
    Read data from Google Sheet
    
    Args:
        sheet_id: The Google Sheet ID (from URL)
        sheet_name: Tab name (default: 'Sheet1')
    
    Returns:
        List of dictionaries (each row as dict)
    """
    try:
        creds = get_google_creds()
        client = gspread.authorize(creds)
        
        # Open sheet by ID
        spreadsheet = client.open_by_key(sheet_id)
        worksheet = spreadsheet.worksheet(sheet_name)
        
        # Get all records (returns list of dicts)
        data = worksheet.get_all_records()
        
        print(f"✅ Fetched {len(data)} rows from sheet: {sheet_name}")
        return data
    
    except Exception as e:
        print(f"❌ Error reading Google Sheet: {e}")
        return []


def read_google_drive(folder_id):
    """
    List files in a Google Drive folder
    
    Args:
        folder_id: The folder ID from Google Drive URL
    
    Returns:
        List of file objects with id, name, mimeType, webViewLink
    """
    try:
        creds = get_google_creds()
        service = build('drive', 'v3', credentials=creds)
        
        # Query files in folder
        query = f"'{folder_id}' in parents and trashed=false"
        results = service.files().list(
            q=query,
            fields="files(id, name, mimeType, webViewLink, createdTime, modifiedTime)",
            pageSize=100
        ).execute()
        
        files = results.get('files', [])
        
        print(f"✅ Fetched {len(files)} files from Drive folder")
        return files
    
    except Exception as e:
        print(f"❌ Error reading Google Drive: {e}")
        return []


def get_drive_file_content(file_id):
    """
    Download a specific file from Google Drive
    
    Args:
        file_id: The file ID
    
    Returns:
        File content as bytes
    """
    try:
        creds = get_google_creds()
        service = build('drive', 'v3', credentials=creds)
        
        request = service.files().get_media(fileId=file_id)
        
        # Download file
        import io
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        
        done = False
        while done is False:
            status, done = downloader.next_chunk()
        
        fh.seek(0)
        return fh.read()
    
    except Exception as e:
        print(f"❌ Error downloading file: {e}")
        return None


def search_drive_files(folder_id, query_text):
    """
    Search for files in Drive folder by name/content
    
    Args:
        folder_id: The folder ID
        query_text: Search term
    
    Returns:
        List of matching files
    """
    try:
        creds = get_google_creds()
        service = build('drive', 'v3', credentials=creds)
        
        # Build search query
        query = f"'{folder_id}' in parents and trashed=false and name contains '{query_text}'"
        
        results = service.files().list(
            q=query,
            fields="files(id, name, mimeType, webViewLink)",
            pageSize=20
        ).execute()
        
        files = results.get('files', [])
        
        print(f"✅ Found {len(files)} files matching '{query_text}'")
        return files
    
    except Exception as e:
        print(f"❌ Error searching Drive: {e}")
        return []


# Example usage in Django shell or management command:
# from core.integrations import read_google_sheet, read_google_drive
# 
# # Get sheet data
# data = read_google_sheet('1abc...xyz', 'Bid Tracker')
# 
# # List Drive files
# files = read_google_drive('1folder...id')