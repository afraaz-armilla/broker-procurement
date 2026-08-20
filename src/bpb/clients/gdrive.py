"""Google Drive archive for raw sanctions-list downloads — the legally defensible
artifact (government lists aren't retrievable retroactively, so keeping the exact
bytes is the only way to reproduce a past screening). Same Workspace as the Sheets
store, so no new vendor.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class DriveArchiveClient:
    def __init__(self, folder_id: str, *, credentials=None, dry_run: bool = False) -> None:
        self.folder_id = folder_id
        self.dry_run = dry_run
        self._service = None
        if not dry_run and credentials is not None:
            from googleapiclient.discovery import build

            self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    def upload_raw_list(
        self, filename: str, content: bytes, mime_type: str = "text/plain"
    ) -> str | None:
        """Upload one raw list file, return its Drive file id. Returns None in
        dry-run or when no credentials are configured (SanctionsSnapshot.drive_file_id
        stays null — refresh still proceeds using the in-memory parsed entries)."""
        if self.dry_run or self._service is None:
            logger.info(
                "dry_run_drive_upload",
                extra={"drive_filename": filename, "num_bytes": len(content)},
            )
            return None

        from googleapiclient.http import MediaInMemoryUpload

        media = MediaInMemoryUpload(content, mimetype=mime_type)
        metadata = {"name": filename, "parents": [self.folder_id]}
        result = (
            self._service.files()
            .create(body=metadata, media_body=media, fields="id")
            .execute()
        )
        return result.get("id")
