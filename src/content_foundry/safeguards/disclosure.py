"""Synthetic-content disclosure injector + the hard publish gate (Ch. 13.4, 19.4)."""

from __future__ import annotations

import re

DISCLOSURE_SENTENCE = "Note: this video uses AI-altered/synthetic content."
_DISCLOSURE_RE = re.compile(
    r"(altered or synthetic|synthetic content|ai[-\s]?(?:generated|altered))", re.IGNORECASE
)


def description_has_disclosure(description: str) -> bool:
    return bool(_DISCLOSURE_RE.search(description or ""))


def ensure_description_discloses(description: str) -> str:
    """Append the synthetic-content sentence if the description does not already disclose."""
    description = description or ""
    if description_has_disclosure(description):
        return description
    sep = " " if description and not description.endswith(("\n", " ")) else ""
    return f"{description}{sep}{DISCLOSURE_SENTENCE}".strip()


def resolve_publish_outcome(
    *,
    publish_mode: str,
    requested_privacy: str,
    disclosure_set: bool,
    require_manual_disclosure_before_public: bool,
) -> tuple[str, str]:
    """The non-negotiable disclosure gate (Ch. 13.4).

    Returns ``(effective_privacy_status, upload_status)``. A video can **never** become
    ``public`` while ``disclosure_set`` is False and the manual-disclosure gate is on.
    """
    if disclosure_set:
        if publish_mode == "auto":
            return requested_privacy, "uploaded"
        privacy = requested_privacy if requested_privacy in ("private", "unlisted") else "private"
        return privacy, "uploaded"

    # Disclosure unconfirmed.
    wants_public = publish_mode == "auto" and requested_privacy == "public"
    if require_manual_disclosure_before_public or wants_public:
        return "private", "pending_manual_disclosure"
    privacy = requested_privacy if requested_privacy in ("private", "unlisted") else "private"
    return privacy, "pending_manual_disclosure"


def disclosure_checklist(disclosure_set: bool) -> str:
    """The mandatory checklist rendered into ``package.md`` (Ch. 19.3)."""
    blocking = "" if disclosure_set else " ⛔ BLOCKING — video stays Private until done"
    return (
        f"## ⚠️ MANDATORY DISCLOSURE CHECKLIST{blocking}\n"
        f"- [ ] In YouTube Studio, set **\"Altered or synthetic content\" = Yes** "
        f"(disclosure_set: {str(disclosure_set).lower()})\n"
        "- [ ] Confirm thumbnail uploaded\n"
        "- [ ] Confirm description includes the synthetic-content note\n"
        "- [ ] Spot-check the Judge report for drift\n"
    )
