#!/usr/bin/env python3
"""Upload print-ready deck PDFs to Google Drive under 'Netframe/printing'.

Finds the 'Netframe' folder and 'printing' subfolder on Google Drive,
then uploads or updates in-place all build/*_print*.pdf files (fronts and backs).

Credentials -- matches upload_rules.py:
  1. $RULES_DRIVE_CREDENTIALS, if set
  2. ~/.config/mobilesuit/drive_service_account.json (service account)
  3. ~/.config/mobilesuit/client_secret.json         (OAuth desktop app)

Usage:
  python3 upload_printing_decks.py [--dry-run] [--list] [--force] [--quiet]
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive"]

SCRIPT_DIR = Path(__file__).resolve().parent
BUILD_DIR = SCRIPT_DIR / "build"
STATE_PATH = BUILD_DIR / "printing_upload_state.json"

CONFIG_DIR = Path.home() / ".config" / "mobilesuit"
SERVICE_ACCOUNT_PATH = CONFIG_DIR / "drive_service_account.json"
CLIENT_SECRET_PATH = CONFIG_DIR / "client_secret.json"
TOKEN_PATH = CONFIG_DIR / "drive_token.json"


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_state() -> dict:
    try:
        with open(STATE_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_state(state: dict):
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w") as fh:
        json.dump(state, fh, indent=2)


def find_credentials_file():
    env = os.environ.get("RULES_DRIVE_CREDENTIALS")
    if env:
        path = Path(env).expanduser()
        if not path.is_file():
            sys.exit(f"RULES_DRIVE_CREDENTIALS points at a missing file: {path}")
        return path
    for path in (SERVICE_ACCOUNT_PATH, CLIENT_SECRET_PATH):
        if path.is_file():
            return path
    return None


def build_credentials(path, quiet=False):
    with open(path) as fh:
        blob = json.load(fh)

    if blob.get("type") == "service_account":
        from google.oauth2 import service_account

        if not quiet:
            print(f"auth: service account ({blob.get('client_email', '?')})")
        return service_account.Credentials.from_service_account_file(
            str(path), scopes=SCOPES
        )

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN_PATH.is_file():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        except ValueError:
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None

    if not (creds and creds.valid):
        if not quiet:
            print("auth: opening browser for Google Drive authorisation...")
        flow = InstalledAppFlow.from_client_secrets_file(str(path), SCOPES)
        creds = flow.run_local_server(port=0)

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json())
        TOKEN_PATH.chmod(0o600)
        if not quiet:
            print(f"auth: user account authorised (token cached in {TOKEN_PATH})")

    return creds


def get_drive_service(creds):
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def find_or_create_folder(service, name: str, parent_id: str = None) -> str:
    query_parts = [
        f"name = '{name}'",
        "mimeType = 'application/vnd.google-apps.folder'",
        "trashed = false",
    ]
    if parent_id:
        query_parts.append(f"'{parent_id}' in parents")
    query = " and ".join(query_parts)

    res = (
        service.files()
        .list(q=query, fields="files(id, name, webViewLink)", spaces="drive")
        .execute()
    )
    files = res.get("files", [])
    if files:
        return files[0]["id"]

    # Create folder if not found
    folder_metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        folder_metadata["parents"] = [parent_id]

    folder = (
        service.files()
        .create(body=folder_metadata, fields="id, name, webViewLink")
        .execute()
    )
    return folder["id"]


def list_folder_files(service, folder_id: str) -> list[dict]:
    query = f"'{folder_id}' in parents and trashed = false"
    items = []
    page_token = None
    while True:
        res = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, size, md5Checksum, modifiedTime, webViewLink)",
                pageToken=page_token,
                spaces="drive",
            )
            .execute()
        )
        items.extend(res.get("files", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return items


def upload_or_update_file(
    service, file_path: Path, folder_id: str, existing_files: dict, force=False, quiet=False
):
    from googleapiclient.http import MediaFileUpload

    file_name = file_path.name
    local_md5 = md5_file(file_path)
    file_size = file_path.stat().st_size

    existing = existing_files.get(file_name)
    if existing and not force:
        drive_md5 = existing.get("md5Checksum")
        if drive_md5 == local_md5:
            if not quiet:
                print(f"  [up-to-date] {file_name}")
            return existing["id"], False

    media = MediaFileUpload(
        str(file_path),
        mimetype="application/pdf",
        resumable=True,
        chunksize=8 << 20,
    )

    if existing:
        file_id = existing["id"]
        if not quiet:
            print(f"  [updating] {file_name} ({file_size / 1_048_576:.1f} MB)...", end="", flush=True)
        request = service.files().update(
            fileId=file_id,
            media_body=media,
            fields="id, name, size, modifiedTime, webViewLink, md5Checksum",
        )
    else:
        file_metadata = {
            "name": file_name,
            "parents": [folder_id],
        }
        if not quiet:
            print(f"  [uploading new] {file_name} ({file_size / 1_048_576:.1f} MB)...", end="", flush=True)
        request = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, size, modifiedTime, webViewLink, md5Checksum",
        )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status and not quiet:
            print(f" {int(status.progress() * 100)}%", end="\r", flush=True)

    if not quiet:
        print(f" done.")
    return response["id"], True


def main():
    parser = argparse.ArgumentParser(
        description="Upload print-ready deck PDFs to Google Drive under 'Netframe/printing'."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would be uploaded"
    )
    parser.add_argument(
        "--list", action="store_true", help="list current files in Netframe/printing"
    )
    parser.add_argument(
        "--force", action="store_true", help="upload even if files match Drive MD5"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="only report errors"
    )
    args = parser.parse_args()

    creds_path = find_credentials_file()
    if creds_path is None:
        print("No Google Drive credentials found in ~/.config/mobilesuit/.", file=sys.stderr)
        return 2

    creds = build_credentials(creds_path, quiet=args.quiet)
    service = get_drive_service(creds)

    # 1. Resolve Netframe folder
    if not args.quiet:
        print("Locating 'Netframe/printing' on Google Drive...")
    netframe_id = find_or_create_folder(service, "Netframe")
    printing_id = find_or_create_folder(service, "printing", parent_id=netframe_id)

    # 2. Query existing files in printing folder
    existing = list_folder_files(service, printing_id)
    existing_by_name = {f["name"]: f for f in existing}

    if args.list:
        print(f"\nFiles currently in Netframe/printing ({len(existing)}):")
        for f in sorted(existing, key=lambda x: x["name"]):
            size_mb = int(f.get("size", 0)) / 1_048_576
            print(f"  {f['name']:<35} {size_mb:6.2f} MB  (id: {f['id']})")
        return 0

    # 3. Collect local print files to upload
    local_files = sorted(BUILD_DIR.glob("*_print*.pdf"))
    if not local_files:
        print("No *_print*.pdf files found in build/. Run `python3 generate_all_decks.py --print` first.", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"\nFound {len(local_files)} print PDF(s) to process for Netframe/printing:")

    if args.dry_run:
        for p in local_files:
            ex = existing_by_name.get(p.name)
            status = "would update" if ex else "would upload new"
            print(f"  [dry-run] {p.name:<35} -> {status}")
        return 0

    uploaded_count = 0
    skipped_count = 0
    state = read_state()

    for p in local_files:
        fid, did_upload = upload_or_update_file(
            service, p, printing_id, existing_by_name, force=args.force, quiet=args.quiet
        )
        if did_upload:
            uploaded_count += 1
        else:
            skipped_count += 1
        state[p.name] = {"file_id": fid, "md5": md5_file(p)}

    write_state(state)

    if not args.quiet:
        print(f"\nUpload complete: {uploaded_count} uploaded/updated, {skipped_count} unchanged.")
        print(f"Drive folder: https://drive.google.com/drive/folders/{printing_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
