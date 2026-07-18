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

    def add_comment(self, video_id: str, text: str) -> None:
        self.calls.append(("add_comment", {"video_id": video_id, "text": text}))


class YouTubePublisher:
    """OAuth installed-app flow + Data API v3 ``videos.insert`` (resumable upload)."""

    name = "youtube"
    _SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube",
    ]
    # Posting a comment needs the broader force-ssl scope. It is requested ONLY when comments are
    # enabled, because adding a scope invalidates a previously saved token (forcing re-consent) —
    # upload-only users keep their existing token untouched.
    _COMMENT_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"

    def __init__(
        self, client_secrets_file: str, token_file: str, *, comment_enabled: bool = False
    ) -> None:
        self._client_secrets_file = client_secrets_file
        self._token_file = token_file
        self._scopes = [*self._SCOPES, *([self._COMMENT_SCOPE] if comment_enabled else [])]
        self._service = None

    def _build_service(self):
        import os

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if os.path.exists(self._token_file):
            creds = Credentials.from_authorized_user_file(self._token_file, self._scopes)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self._client_secrets_file, self._scopes
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

    def add_to_playlist(self, video_id: str, playlist_id: str) -> None:  # pragma: no cover - network
        """File the uploaded video into a playlist (playlistItems.insert), so a niche series keeps
        viewers watching one video after another (session watch time)."""
        try:
            self.service.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            ).execute()
        except Exception as exc:
            raise PublishError(f"Add to playlist failed: {exc}") from exc

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

    def add_comment(self, video_id: str, text: str) -> None:  # pragma: no cover - network
        """Post a top-level comment (commentThreads.insert) nudging viewers to subscribe/explore.
        Needs the force-ssl scope (present only when comments are enabled). The API can post but
        CANNOT pin — pin it once in YouTube Studio."""
        try:
            self.service.commentThreads().insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "topLevelComment": {"snippet": {"textOriginal": text}},
                    }
                },
            ).execute()
        except Exception as exc:
            raise PublishError(f"Add comment failed: {exc}") from exc
