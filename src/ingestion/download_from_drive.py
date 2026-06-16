"""
GeoAI-TKO: Download Sentinel-2 from Google Drive
==================================================
Downloads the exported GeoTIFF from Google Drive after GEE task completes.
Uses the same OAuth credentials as earthengine.
"""

import os
import io
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def get_drive_service():
    """Authenticate and return Google Drive service."""
    # Reuse existing GEE credentials
    creds_path = Path.home() / ".config" / "earthengine" / "credentials"
    if creds_path.exists():
        import json
        with open(creds_path) as f:
            creds_data = json.load(f)
        creds = Credentials(
            token=None,
            refresh_token=creds_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=creds_data.get("client_id"),
            client_secret=creds_data.get("client_secret"),
            scopes=creds_data.get("scopes", SCOPES),
        )
        creds.refresh(Request())
    else:
        raise FileNotFoundError("No GEE credentials found. Run earthengine authenticate first.")

    return build("drive", "v3", credentials=creds)


def find_file(service, filename="sentinel2_day1.tif", folder="GeoAI_TKO"):
    """Search for file in Google Drive."""
    # Find folder first
    folder_query = f"name='{folder}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    folders = service.files().list(q=folder_query, fields="files(id,name)").execute()
    folder_id = None
    for f in folders.get("files", []):
        folder_id = f["id"]
        print(f"[Drive] Found folder: {f['name']} ({folder_id})")
        break

    if folder_id:
        query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    else:
        query = f"name='{filename}' and trashed=false"

    results = service.files().list(q=query, fields="files(id,name,size)").execute()
    files = results.get("files", [])
    if not files:
        print(f"[Drive] File '{filename}' not found in '{folder}'")
        return None

    f = files[0]
    print(f"[Drive] Found: {f['name']} ({f['id']}), {int(f.get('size',0))/1024/1024:.1f} MB")
    return f["id"]


def download_file(service, file_id, output_path):
    """Download file from Google Drive."""
    request = service.files().get_media(fileId=file_id)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=32 * 1024 * 1024)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"  Download {status.progress() * 100:.0f}%")

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"[Drive] Downloaded: {output_path} ({size_mb:.1f} MB)")
    return output_path


if __name__ == "__main__":
    import sys
    output = sys.argv[1] if len(sys.argv) > 1 else "data/raw/sentinel2_day1.tif"

    service = get_drive_service()
    file_id = find_file(service)
    if file_id:
        download_file(service, file_id, output)
    else:
        print("[Drive] Nothing to download yet. Wait for GEE task to complete.")
