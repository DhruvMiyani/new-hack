import pytest

from blindspot import muse, referrals


def _response(text):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": text},
    ]}]}


def test_muse_claim_requires_the_exact_signed_page(monkeypatch):
    monkeypatch.setattr(referrals, "scanned_text", lambda: "Status: signed\nRouting instruction: CRITICAL")
    claim = muse._extract(_response('{"priority":"CRITICAL","page":2,"quote":"Routing instruction: CRITICAL"}'))
    assert muse._verified("R101", *claim)
    assert not muse._verified("R101", claim[0], 1, claim[2])
    assert not muse._verified("R101", "high", 2, "Routing instruction: HIGH")


def test_muse_cannot_use_conflicting_quote():
    with pytest.raises(ValueError, match="matching"):
        muse._extract(_response('{"priority":"LOW","page":2,"quote":"Routing instruction: CRITICAL"}'))
