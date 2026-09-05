#!/usr/bin/env python3
"""Publish rules/rules.pdf to Google Drive, updating the shared file in place.

The rules PDF used to be committed to git, which cost ~1.6GB of history for a
file that is pure build output. It now lives only on disk and is published
here instead.

The upload *updates* the existing Drive file rather than creating a new one, so
the link that has already been handed out keeps working and Drive keeps the
older revisions:

    https://drive.google.com/file/d/1tPYJgd-kLqCHa2N3ye0efMUMx0scI4in/view

Nothing is uploaded when the PDF is byte-identical to the last one published
(the hash is remembered in build/), so wiring this into a rebuild is cheap --
only a genuinely changed PDF costs the 60MB transfer. Pass --force to override.

Credentials -- see SETUP below for the one-time steps. First match wins:
  1. $RULES_DRIVE_CREDENTIALS, if set
  2. ~/.config/mobilesuit/drive_service_account.json   (service account)
  3. ~/.config/mobilesuit/client_secret.json           (OAuth desktop app)

Run: python upload_rules.py [--force] [--dry-run] [--quiet]
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

# The Drive file backing the shared link. Updating this ID in place is the whole
# point -- creating a new file would invalidate every link already given out.
FILE_ID = "1tPYJgd-kLqCHa2N3ye0efMUMx0scI4in"
SHARE_LINK = f"https://drive.google.com/file/d/{FILE_ID}/view"

# Full drive scope: the file already exists and was not created by this script,
# so the narrower drive.file scope cannot touch it.
SCOPES = ["https://www.googleapis.com/auth/drive"]

SCRIPT_DIR = Path(__file__).resolve().parent
PDF_PATH = SCRIPT_DIR / "rules" / "rules.pdf"
STATE_PATH = SCRIPT_DIR / "build" / "rules_upload_state.json"

CONFIG_DIR = Path.home() / ".config" / "mobilesuit"
SERVICE_ACCOUNT_PATH = CONFIG_DIR / "drive_service_account.json"
CLIENT_SECRET_PATH = CONFIG_DIR / "client_secret.json"
TOKEN_PATH = CONFIG_DIR / "drive_token.json"

SETUP = f"""
No Drive credentials found. One-time setup -- pick ONE:

  A. Service account (recommended: headless, never expires, no browser)
     1. https://console.cloud.google.com/ -> create/pick a project
     2. APIs & Services -> Library -> enable "Google Drive API"
     3. APIs & Services -> Credentials -> Create credentials -> Service account
     4. On the new account: Keys -> Add key -> JSON -> download
     5. Save it as {SERVICE_ACCOUNT_PATH}
     6. Open {SHARE_LINK}
        -> Share -> paste the service account's e-mail (…@….iam.gserviceaccount.com)
        -> give it *Editor* -> Send. Without this step the upload 404s.

  B. Your own Google account (opens a browser once, then caches a token)
     1-2. as above
     3. APIs & Services -> Credentials -> Create credentials
        -> OAuth client ID -> Desktop app -> download the JSON
     4. Save it as {CLIENT_SECRET_PATH}
     5. If the consent screen is in "Testing", add yourself as a test user.
     The first run opens a browser; the refresh token is cached in
     {TOKEN_PATH}.

Either file can live elsewhere -- point $RULES_DRIVE_CREDENTIALS at it.
"""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_state():
    try:
        with open(STATE_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_state(digest, size):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w") as fh:
        json.dump({"file_id": FILE_ID, "sha256": digest, "size": size}, fh, indent=2)


def find_credentials_file():
    """Return the credentials JSON to use, or None if nothing is configured."""
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


def build_credentials(path, quiet):
    """Build Drive credentials, auto-detecting service account vs OAuth client."""
    with open(path) as fh:
        blob = json.load(fh)

    if blob.get("type") == "service_account":
        from google.oauth2 import service_account

        if not quiet:
            print(f"auth: service account ({blob.get('client_email', '?')})")
        return service_account.Credentials.from_service_account_file(
            str(path), scopes=SCOPES
        )

    # OAuth "Desktop app" client -- reuse the cached refresh token when we can.
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN_PATH.is_file():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        except ValueError:
            creds = None  # cached token predates a scope change; re-authorise

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not (creds and creds.valid):
        if not quiet:
            print("auth: opening a browser for one-time Google authorisation…")
        creds = InstalledAppFlow.from_client_secrets_file(
            str(path), SCOPES
        ).run_local_server(port=0)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())
    TOKEN_PATH.chmod(0o600)
    if not quiet:
        print(f"auth: user account (token cached in {TOKEN_PATH})")
    return creds


def upload(creds, quiet):
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    # Resumable: the PDF is ~60MB, well past the point where a single shot is wise.
    media = MediaFileUpload(
        str(PDF_PATH), mimetype="application/pdf", resumable=True, chunksize=8 << 20
    )
    request = service.files().update(
        fileId=FILE_ID,
        media_body=media,
        keepRevisionForever=False,
        supportsAllDrives=True,
        fields="id,name,size,modifiedTime,webViewLink",
    )

    try:
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status and not quiet:
                print(f"  …{int(status.progress() * 100)}%", end="\r", flush=True)
        if not quiet:
            print("  …100%")
        return response
    except HttpError as err:
        if err.resp.status == 404:
            sys.exit(
                f"\nDrive returned 404 for file {FILE_ID}.\n"
                "The credentials are valid but cannot see that file. If you are "
                "using a service account, share the file with its e-mail address "
                f"and give it Editor:\n  {SHARE_LINK}"
            )
        if err.resp.status == 403:
            sys.exit(
                f"\nDrive returned 403 for file {FILE_ID}.\n"
                "The account can see the file but may not write to it -- check it "
                "has Editor (not Viewer/Commenter), and that the Drive API is "
                "enabled on the Cloud project."
            )
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Update the shared Google Drive copy of rules/rules.pdf."
    )
    parser.add_argument(
        "--force", action="store_true", help="upload even if the PDF is unchanged"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would happen, upload nothing"
    )
    parser.add_argument("--quiet", action="store_true", help="only report problems")
    args = parser.parse_args()

    if not PDF_PATH.is_file():
        sys.exit(
            f"No PDF at {PDF_PATH}.\n"
            "Build it first: run generateCards.py, then pdflatex rules.tex from rules/."
        )

    size = PDF_PATH.stat().st_size
    digest = sha256(PDF_PATH)
    unchanged = read_state().get("sha256") == digest

    if not args.quiet:
        print(f"rules.pdf  {size / 1048576:.1f} MB  sha256 {digest[:12]}…")

    if unchanged and not args.force:
        if not args.quiet:
            print("unchanged since the last upload -- nothing to do (--force overrides)")
        return 0

    if args.dry_run:
        print(f"[dry run] would update Drive file {FILE_ID}")
        return 0

    creds_path = find_credentials_file()
    if creds_path is None:
        print(SETUP, file=sys.stderr)
        return 2

    creds = build_credentials(creds_path, args.quiet)
    if not args.quiet:
        print(f"uploading to Drive file {FILE_ID}…")
    response = upload(creds, args.quiet)

    write_state(digest, size)
    if not args.quiet:
        print(f"published {response.get('name')}  ({response.get('modifiedTime')})")
        print(SHARE_LINK)
    return 0


if __name__ == "__main__":
    sys.exit(main())
