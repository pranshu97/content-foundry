"""Publisher protocol + YouTube adapter + DryRunPublisher (Ch. 13.6). Google libs lazy."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..errors import PublishError


@runtime_checkable
class Publisher(Protocol):
    name: str

    def upload(
        self,
        *,
        video_path: str,
        title: str,
        description: str,
        tags: list[str],
        category_id: str,
        privacy_status: str,
        default_language: str,
    ) -> str:
        """Upload the video; return its YouTube video id."""
        ...

    def set_thumbnail(self, video_id: str, thumbnail_path: str) -> None: ...

    def set_privacy(self, video_id: str, privacy_status: str) -> None: ...

    def try_set_disclosure(self, video_id: str) -> bool:
        """Attempt to set the 'Altered or synthetic content' flag; return True only if confirmed."""
        ...

    def video_url(self, video_id: str) -> str: ...


class DryRunPublisher:
    """Records intended calls; never touches the network (used by ``--dry-run`` and tests)."""

    name = "dryrun"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def upload(self, **kwargs: object) -> str:
        self.calls.append(("upload", dict(kwargs)))
        return "dryrun-video-id"

    def set_thumbnail(self, video_id: str, thumbnail_path: str) -> None:
        self.calls.append(("set_thumbnail", {"video_id": video_id, "thumbnail_path": thumbnail_path}))

    def set_privacy(self, video_id: str, privacy_status: str) -> None:
        self.calls.append(("set_privacy", {"video_id": video_id, "privacy_status": privacy_status}))

    def try_set_disclosure(self, video_id: str) -> bool:
        self.calls.append(("try_set_disclosure", {"video_id": video_id}))
        return True  # simulate confirmed disclosure for a complete rehearsal (Ch. 13.7)

    def video_url(self, video_id: str) -> str:
        return f"https://youtu.be/{video_id}"


class YouTubePublisher:
    """OAuth installed-app flow + Data API v3 ``videos.insert`` (resumable upload)."""

    name = "youtube"
    _SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube",
    ]

    def __init__(self, client_secrets_file: str, token_file: str) -> None:
        self._client_secrets_file = client_secrets_file
        self._token_file = token_file
        self._service = None

    def _build_service(self):
        import os

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if os.path.exists(self._token_file):
            creds = Credentials.from_authorized_user_file(self._token_file, self._SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self._client_secrets_file, self._SCOPES
                )
                creds = flow.run_local_server(port=0)
            with open(self._token_file, "w", encoding="utf-8") as fh:
                fh.write(creds.to_json())
        return build("youtube", "v3", credentials=creds)

    @property
    def service(self):
        if self._service is None:
            self._service = self._build_service()
        return self._service

    def upload(
        self,
        *,
        video_path: str,
        title: str,
        description: str,
        tags: list[str],
        category_id: str,
        privacy_status: str,
        default_language: str,
    ) -> str:
        from googleapiclient.http import MediaFileUpload

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
                "defaultLanguage": default_language,
            },
            "status": {"privacyStatus": privacy_status, "selfDeclaredMadeForKids": False},
        }
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        try:
            request = self.service.videos().insert(
                part="snippet,status", body=body, media_body=media
            )
            response = None
            while response is None:
                _, response = request.next_chunk()
        except Exception as exc:
            raise PublishError(f"YouTube upload failed: {exc}") from exc
        return response["id"]

    def set_thumbnail(self, video_id: str, thumbnail_path: str) -> None:
        from googleapiclient.http import MediaFileUpload

        try:
            self.service.thumbnails().set(
                videoId=video_id, media_body=MediaFileUpload(thumbnail_path)
            ).execute()
        except Exception as exc:
            raise PublishError(f"Thumbnail upload failed: {exc}") from exc

    def set_privacy(self, video_id: str, privacy_status: str) -> None:
        try:
            self.service.videos().update(
                part="status",
                body={"id": video_id, "status": {"privacyStatus": privacy_status}},
            ).execute()
        except Exception as exc:
            raise PublishError(f"Privacy update failed: {exc}") from exc

    def try_set_disclosure(self, video_id: str) -> bool:
        # The Data API does not reliably expose the synthetic-content toggle ⇒ cannot confirm.
        return False

    def video_url(self, video_id: str) -> str:
        return f"https://youtu.be/{video_id}"
