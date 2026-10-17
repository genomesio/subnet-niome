"""Submission storage backed by AWS S3 or Google Drive.

The backend is chosen once per process, the same way the Niome backend does it:
S3 when AWS credentials are configured, otherwise Google Drive when a service
account is configured.

Miners upload by sending a plain HTTP PUT to the URL the validator gives them.
For S3 that is a presigned URL. Google Drive has no presigned URLs, so the
Drive backend opens a resumable upload session instead: the session URL needs
no credentials and only accepts content for the one file it was opened for.
"""
import json
import logging
import time
from datetime import datetime
from functools import lru_cache

import boto3
import requests
from botocore.exceptions import ClientError

import niome_subnet.utils.settings as config

logger = logging.getLogger(__name__)


class S3Storage:
    name = "s3"

    def __init__(self):
        self.bucket = config.AWS_S3_BUCKET
        self.s3 = boto3.client(
            "s3",
            aws_access_key_id=config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
            region_name=config.AWS_REGION,
        )

    def create_upload_url(self, key: str, expires: int) -> str:
        return self.s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires,
        )

    def download_file(self, key: str, path: str) -> None:
        self.s3.download_file(self.bucket, key, path)

    def upload_file(self, path: str, key: str) -> None:
        self.s3.upload_file(Filename=path, Bucket=self.bucket, Key=key)

    def get_bytes(self, key: str) -> bytes:
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise FileNotFoundError(key) from e
            raise


DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
DRIVE_API = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
DRIVE_TIMEOUT = 60  # seconds
# Stored on each submission file so late uploads can be rejected (see below).
DEADLINE_PROPERTY = "upload_deadline"


class SubmissionTooLate(Exception):
    pass


def _escape_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _parse_drive_time(value: str) -> float:
    # Drive returns RFC 3339 UTC timestamps, e.g. "2026-10-17T08:15:30.123Z".
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class GoogleDriveStorage:
    """Maps S3-style keys ("niome/12.json") to a folder tree under GOOGLE_DRIVE_ROOT_FOLDER_ID.

    Upload session URLs stay valid for about a week, much longer than an S3
    presigned URL. To keep the same guarantees as S3:
      - every new upload URL is for a freshly created file, so URLs handed out
        in earlier rounds point at deleted files and stop working;
      - the file records its upload deadline, and download_file() rejects it
        if it was modified after that deadline.
    """
    name = "gdrive"

    def __init__(self):
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(
            config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=DRIVE_SCOPES
        )
        self.session = AuthorizedSession(credentials)
        self.root_folder_id = config.GOOGLE_DRIVE_ROOT_FOLDER_ID
        self._folder_cache: dict[tuple[str, str], str] = {}

    # -- Drive helpers ------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        params = {"supportsAllDrives": "true", **kwargs.pop("params", {})}
        resp = self.session.request(method, url, params=params, timeout=DRIVE_TIMEOUT, **kwargs)
        resp.raise_for_status()
        return resp

    def _find_children(self, name: str, parent_id: str, mime_type: str | None = None) -> list[dict]:
        query = f"name = '{_escape_query(name)}' and '{parent_id}' in parents and trashed = false"
        if mime_type:
            query += f" and mimeType = '{mime_type}'"
        resp = self._request("GET", DRIVE_API, params={
            "q": query,
            "fields": "files(id, modifiedTime, appProperties)",
            "orderBy": "modifiedTime desc",
            "includeItemsFromAllDrives": "true",
        })
        return resp.json().get("files", [])

    def _folder_id(self, parts: list[str]) -> str:
        parent_id = self.root_folder_id
        for name in parts:
            cache_key = (parent_id, name)
            if cache_key not in self._folder_cache:
                existing = self._find_children(name, parent_id, FOLDER_MIME)
                if existing:
                    folder_id = existing[0]["id"]
                else:
                    folder_id = self._request("POST", DRIVE_API, params={"fields": "id"}, json={
                        "name": name, "mimeType": FOLDER_MIME, "parents": [parent_id],
                    }).json()["id"]
                self._folder_cache[cache_key] = folder_id
            parent_id = self._folder_cache[cache_key]
        return parent_id

    def _locate(self, key: str) -> tuple[str, str]:
        parts = [p for p in key.split("/") if p]
        if not parts:
            raise ValueError(f"Invalid storage key: {key!r}")
        return self._folder_id(parts[:-1]), parts[-1]

    def _find_file(self, key: str) -> dict:
        parent_id, name = self._locate(key)
        files = self._find_children(name, parent_id)
        if not files:
            raise FileNotFoundError(key)
        return files[0]

    def _start_update_session(self, file_id: str) -> str:
        resp = self._request(
            "PATCH", f"{DRIVE_UPLOAD_API}/{file_id}",
            params={"uploadType": "resumable"},
            json={},
        )
        return resp.headers["Location"]

    # -- storage operations -------------------------------------------------

    def create_upload_url(self, key: str, expires: int) -> str:
        parent_id, name = self._locate(key)
        for old in self._find_children(name, parent_id):
            self._request("DELETE", f"{DRIVE_API}/{old['id']}")

        deadline = int(time.time()) + int(expires)
        file_id = self._request("POST", DRIVE_API, params={"fields": "id"}, json={
            "name": name,
            "parents": [parent_id],
            "mimeType": "application/json",
            "appProperties": {DEADLINE_PROPERTY: str(deadline)},
        }).json()["id"]
        return self._start_update_session(file_id)

    def download_file(self, key: str, path: str) -> None:
        file = self._find_file(key)
        deadline = (file.get("appProperties") or {}).get(DEADLINE_PROPERTY)
        if deadline and _parse_drive_time(file["modifiedTime"]) > int(deadline):
            raise SubmissionTooLate(f"{key} was uploaded after its deadline")

        resp = self._request("GET", f"{DRIVE_API}/{file['id']}", params={"alt": "media"}, stream=True)
        with open(path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    def upload_file(self, path: str, key: str) -> None:
        """Overwrite `key` with the local file. Clears the upload deadline, since the validator wrote it."""
        file = self._find_file(key)
        self._request("PATCH", f"{DRIVE_API}/{file['id']}", json={
            "appProperties": {DEADLINE_PROPERTY: None},
        })
        with open(path, "rb") as f:
            requests.put(self._start_update_session(file["id"]), data=f, timeout=300).raise_for_status()

    def get_bytes(self, key: str) -> bytes:
        file = self._find_file(key)
        return self._request("GET", f"{DRIVE_API}/{file['id']}", params={"alt": "media"}).content


@lru_cache(maxsize=1)
def get_storage():
    if config.AWS_ACCESS_KEY_ID and config.AWS_SECRET_ACCESS_KEY and config.AWS_S3_BUCKET:
        logger.info("Using AWS S3 storage")
        return S3Storage()
    if config.GOOGLE_SERVICE_ACCOUNT_FILE and config.GOOGLE_DRIVE_ROOT_FOLDER_ID:
        logger.info("Using Google Drive storage")
        return GoogleDriveStorage()
    raise RuntimeError(
        "No storage configured: set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_S3_BUCKET "
        "or GOOGLE_SERVICE_ACCOUNT_FILE/GOOGLE_DRIVE_ROOT_FOLDER_ID"
    )


def submission_key(uid: int) -> str:
    return f"niome/{uid}.json"


def get_json(key: str):
    return json.loads(get_storage().get_bytes(key).decode("utf-8"))
