from tradebot import netcheck


def test_egress_check_matches_and_mismatches(monkeypatch):
    monkeypatch.setattr(netcheck, "public_ipv4", lambda timeout=4.0: "168.144.86.125")
    ok = netcheck.egress_check("168.144.86.125", enforce=True)
    assert ok["ok"] and "matches" in ok["detail"]
    bad = netcheck.egress_check("10.0.0.9", enforce=True)
    assert not bad["ok"] and "reject" in bad["detail"]
    info = netcheck.egress_check("10.0.0.9", enforce=False)       # research sandbox: informational only
    assert info["ok"] and "not an executor" in info["detail"]
    unset = netcheck.egress_check(None, enforce=True)
    assert unset["ok"] and "not set" in unset["detail"]


def test_egress_check_when_no_echo_service(monkeypatch):
    monkeypatch.setattr(netcheck, "public_ipv4", lambda timeout=4.0: None)
    assert netcheck.egress_check("1.2.3.4", enforce=False)["ok"]
    assert not netcheck.egress_check("1.2.3.4", enforce=True)["ok"]


def test_public_ipv4_skips_bad_answers(monkeypatch):
    answers = iter([("garbage", 200), ("2400:6180::1", 200), ("203.0.113.7", 200)])

    class R:
        def __init__(self, text, code):
            self.text, self.status_code = text, code

    monkeypatch.setattr(netcheck.httpx, "get", lambda url, **kw: R(*next(answers)))
    assert netcheck.public_ipv4() == "203.0.113.7"
