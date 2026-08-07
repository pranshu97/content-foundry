"""Image API errors must stay DIAGNOSTIC, not just short.

The old code did `resp.text[:200]`, which cut Google's 429 body off mid-sentence and discarded the
quota ids. Both a free-tier exhaustion and a genuine paid rate-limit say "check your plan and
billing details" in the human-readable message -- only the `-FreeTier` quota id distinguishes them,
and that is the difference between "wait for the daily reset" and "billing is broken, fix it".
"""

from __future__ import annotations

import json

import pytest

from content_foundry.providers.image import _error_detail, _raise_for_image_status


class _Resp:
    def __init__(self, status_code: int, payload, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        raise AssertionError("should not be called for a 4xx")


def _quota_body(*quota_ids: str, retry: str = "27s"):
    return {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "message": "You exceeded your current quota, please check your plan and billing details.",
            "details": [
                {"violations": [{"quotaId": q} for q in quota_ids]},
                {"retryDelay": retry},
            ],
        }
    }


def test_free_tier_quotas_are_named_outright():
    """The real run-0024 case: every quota is FreeTier, so waiting will never help."""
    detail = _error_detail(
        _Resp(429, _quota_body("GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
    )
    assert "RESOURCE_EXHAUSTED" in detail
    assert "GenerateRequestsPerDayPerProjectPerModel-FreeTier" in detail
    assert "FREE TIER" in detail
    assert "billing is NOT active" in detail
    assert "retry_after=27s" in detail


def test_a_paid_quota_is_not_reported_as_free_tier():
    """A genuine paid rate-limit must NOT be mislabelled -- that would send you to fix billing that
    is already fine."""
    detail = _error_detail(_Resp(429, _quota_body("GenerateRequestsPerMinutePerProjectPerModel")))
    assert "FREE TIER" not in detail
    assert "billing is NOT active" not in detail
    assert "GenerateRequestsPerMinutePerProjectPerModel" in detail


def test_a_mix_of_paid_and_free_quotas_is_not_called_free_tier():
    detail = _error_detail(_Resp(429, _quota_body("SomePaidQuota", "SomeQuota-FreeTier")))
    assert "FREE TIER" not in detail


def test_the_quota_ids_survive_where_the_old_200_char_cut_lost_them():
    """Google puts the quota ids AFTER a long boilerplate message, which is why a blind prefix
    truncation dropped exactly the useful part."""
    body = _quota_body("GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    body["error"]["message"] = "You exceeded your current quota. " + ("blah " * 80)
    raw = json.dumps(body)
    assert "FreeTier" not in raw[:200], "precondition: the old cut really did lose the quota id"
    assert "FreeTier" in _error_detail(_Resp(429, body, text=raw))


@pytest.mark.parametrize(
    "payload", [None, [1, 2, 3], {}, {"error": {}}], ids=["not-json", "list", "empty", "no-details"]
)
def test_a_malformed_body_never_raises(payload):
    detail = _error_detail(_Resp(429, payload, text="raw body here"))
    assert isinstance(detail, str)


def test_4xx_raises_with_the_detail_attached():
    with pytest.raises(Exception) as excinfo:
        _raise_for_image_status(_Resp(429, _quota_body("Foo-FreeTier")))
    assert "image API 429" in str(excinfo.value)
    assert "Foo-FreeTier" in str(excinfo.value)
