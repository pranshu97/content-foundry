"""Sound-effects provider: a local library first, then an optional Freesound download (Ch. 11.6).

Pixabay has no public SFX/audio API, so network fetch uses Freesound; any Pixabay downloads can be
dropped straight into the local ``data/sounds`` folder.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol, runtime_checkable

_AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".flac")


@runtime_checkable
class SfxClient(Protocol):
    enabled: bool

    def resolve(self, keyword: str) -> str | None:
        """Return a local path to an audio clip matching ``keyword``, or None."""
        ...


class NullSfxClient:
    """Used when sound effects are disabled — every cue resolves to nothing."""

    enabled = False

    def resolve(self, keyword: str) -> str | None:
        return None


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


class SfxLibrary:
    """A local folder of sound files, with an optional Freesound network fallback (cached locally)."""

    enabled = True

    def __init__(self, sounds_dir: str, *, freesound_api_key: str = "") -> None:
        self._dir = Path(sounds_dir)
        self._key = freesound_api_key

    def resolve(self, keyword: str) -> str | None:
        keyword = (keyword or "").strip()
        if not keyword:
            return None
        local = self._match_local(keyword)
        if local is not None:
            return str(local)
        if self._key:
            return self._fetch_freesound(keyword)
        return None

    # ------------------------------------------------------------------ local
    def _files(self) -> list[Path]:
        if not self._dir.exists():
            return []
        return sorted(p for p in self._dir.iterdir() if p.suffix.lower() in _AUDIO_EXTS)

    def _match_local(self, keyword: str) -> Path | None:
        """Best filename match: score by shared tokens + substring, e.g. 'notification' matches
        'apple_notification.mp3' and 'cash register' matches 'cash_register.mp3'."""
        kw = _tokens(keyword)
        if not kw:
            return None
        best: Path | None = None
        best_score = 0
        for f in self._files():
            stem = f.stem.lower()
            stem_tokens = set(_tokens(stem))
            score = sum(1 for t in kw if t in stem_tokens)
            if any(t in stem for t in kw):
                score += 1
            if score > best_score:
                best, best_score = f, score
        return best if best_score > 0 else None

    # -------------------------------------------------------------- freesound
    def _fetch_freesound(self, keyword: str) -> str | None:  # pragma: no cover - network
        """Download a short matching clip's HQ preview from Freesound and cache it in the folder."""
        import httpx

        try:
            resp = httpx.get(
                "https://freesound.org/apiv2/search/text/",
                params={
                    "query": keyword, "fields": "id,previews", "page_size": 1,
                    "filter": "duration:[0.2 TO 6]", "sort": "score", "token": self._key,
                },
                timeout=20,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            preview = results[0].get("previews", {}).get("preview-hq-mp3") if results else None
            if not preview:
                return None
            data = httpx.get(preview, timeout=30, follow_redirects=True).content
            self._dir.mkdir(parents=True, exist_ok=True)
            out = self._dir / f"_fs_{re.sub(r'[^a-z0-9]+', '_', keyword.lower())[:40]}.mp3"
            out.write_bytes(data)
            return str(out)
        except Exception:
            return None
