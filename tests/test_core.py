import time
from studio import engines as E, netinfo as N, store, reports as R


def rows():
    return [E.row("Sherlock", "GitHub", "Claimed", "https://github.com/x", "coding"),
            E.row("WhatsMyName", "github", "Claimed", "https://github.com/x", "coding"),
            E.row("Sherlock", "Foo", "Claimed", "https://foo.com/x"),
            E.row("WhatsMyName", "Foo", "Available", "https://foo.com/x", "blog"),
            E.row("Sherlock", "Bar", "Available", "https://bar.com/x"),
            E.row("Sherlock", "Baz", "Unknown", "https://baz.com/x")]


def test_merge_and_score():
    recs = E.merge(rows(), ["Sherlock", "WhatsMyName"])
    by = {r["site"].lower(): r for r in recs}
    assert by["github"]["found_by"] == 2 and by["foo"]["found_by"] == 1
    assert by["bar"]["status"] == "Not found" and by["baz"]["status"] == "Unknown"
    assert by["foo"]["category"] == "blog"  # engine with category wins over 'other'
    E.apply_verification(recs, {"https://foo.com/x": ("ok", ""), "https://github.com/x": ("ok", "")})
    E.score(recs)
    assert by["github"]["confidence"] == "High" and by["foo"]["confidence"] == "Medium"


def test_variants_and_category():
    v = E.variants("Jane Doe")
    assert "janedoe" in v and "jane.doe" in v and "jdoe" in v and len(v) == len(set(v))
    assert E.guess_category("GitHub") == "coding" and E.guess_category("zzz") == "other"


def test_wmn_check_logic(monkeypatch):
    class R_:
        def __init__(s, c, t): s.status_code, s.text = c, t
    site = {"name": "T", "uri_check": "https://t/{account}", "e_code": 200, "e_string": "profile",
            "m_code": 404, "m_string": "nope", "cat": "social"}
    for code, text, exp in [(200, "profile page", "Claimed"), (404, "x", "Available"),
                            (200, "nope here", "Available"), (200, "other", "Unknown"), (429, "", "WAF")]:
        monkeypatch.setattr(E.requests, "get", lambda *a, **k: R_(code, text))
        assert E.WMNEngine._check(site, "u", 5, None)["status"] == exp


def test_verify(monkeypatch):
    class R_:
        def __init__(s, c, t, u): s.status_code, s.text, s.url = c, t, u
    u = "https://s.com/user"
    cases = [(R_(200, "<title>Alice</title>" + "x" * 20000, u), "ok"),
             (R_(404, "", u), "suspect"),
             (R_(200, "<title>Page not found</title>", u), "suspect"),
             (R_(200, "<title>Home</title>", "https://s.com/"), "suspect"),
             (R_(429, "", u), "unverified")]
    for resp, exp in cases:
        monkeypatch.setattr(E.requests, "get", lambda *a, **k: resp)
        assert E.verify_one(u)[0] == exp


def test_dns_grades():
    g = N.email_auth_grade({"TXT": ["v=spf1 include:x -all"], "DMARC": ["v=DMARC1; p=reject"], "CAA": []})
    assert [x[2] for x in g] == ["good", "good", "warn"]
    assert N.email_auth_grade({})[0][2] == "bad"
    assert N.clean_domain("https://www.Example.com/a?b") == "example.com"
    assert N.DOMAIN_RE.match("example.co.uk") and not N.DOMAIN_RE.match("nodot")


def test_store_and_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", tmp_path / "h.db")
    recs = E.score(E.apply_verification(E.merge(rows(), ["Sherlock"]), None))
    store.save_run("username", "x", {"found": 2}, recs)
    r = store.list_runs("username", "x")
    assert r and store.get_run(r[0]["id"])[0]["site"]
    new, gone = store.diff_runs([], recs)
    assert new and not gone
    ok, ts = store.rate_ok([], 1); assert ok
    ok, ts = store.rate_ok(ts, 1); assert not ok
    assert R.excel_bytes("x", recs, {"engines": ["Sherlock"]})[:2] == b"PK"
    assert "<table>" in R.html_report("x<script>", recs, {"engines": []}) and "<script>" not in R.html_report("x<script>", recs, {"engines": []})
    assert N.EMAIL_RE.match("a.b@c.io") and not N.EMAIL_RE.match("a@b")


# ---------------------------------------------------------------- state-machine regressions
import pytest
from studio.models import Conf, Found, Raw, Stage, StageError, Verify


def test_lifecycle_order_enforced():
    recs = E.merge(rows(), ["Sherlock"])
    with pytest.raises(StageError):
        E.score(recs)                      # score before verification
    E.apply_verification(recs, None)
    with pytest.raises(StageError):
        E.apply_verification(recs, None)   # verification twice
    E.score(recs)
    assert {r["stage"] for r in recs} == {Stage.SCORED}


def test_confidence_table():
    c = E.confidence_for
    assert c(Found.NOT_FOUND, 0, Verify.NOT_RUN) == Conf.NONE
    assert c(Found.FOUND, 3, Verify.SUSPECT) == Conf.LOW          # suspect outranks multi-engine
    assert c(Found.FOUND, 2, Verify.NOT_RUN) == Conf.HIGH
    assert c(Found.FOUND, 1, Verify.OK) == Conf.MEDIUM
    for v in (Verify.NOT_RUN, Verify.SKIPPED, Verify.UNVERIFIED):
        assert c(Found.FOUND, 1, v) == Conf.UNVERIFIED


def _fake_engine(found, unknown_first=()):
    class F:
        calls = 0
        def site_names(self, cats): return list(found)
        def run(self, user, names, timeout, progress=None, proxy=None):
            F.calls += 1
            out = []
            for n in names:
                st = Raw.UNKNOWN if (n in unknown_first and F.calls == 1) else Raw.CLAIMED
                out.append(E.row("Fake", n, st, f"https://{n}.com/{user}"))
            return out
    return F()


def test_verification_off_and_cap(monkeypatch):
    eng = {"Fake": _fake_engine(["a", "b", "c"])}
    recs, _ = E.run_username("u", eng, {"other"}, 5, retry_unknown=False, verify=False)
    assert {r["verify"] for r in recs} == {Verify.NOT_RUN}
    assert {r["confidence"] for r in recs} == {Conf.UNVERIFIED}      # Medium unreachable without a check
    monkeypatch.setattr(E, "verify_many", lambda urls, **k: {u: (Verify.OK, "") for u in urls})
    recs, _ = E.run_username("u", eng, {"other"}, 5, retry_unknown=False, verify=True, verify_cap=2)
    by = {r["site"]: r for r in recs}
    assert [by[x]["confidence"] for x in "ab"] == [Conf.MEDIUM, Conf.MEDIUM]
    assert by["c"]["verify"] == Verify.SKIPPED and by["c"]["confidence"] == Conf.UNVERIFIED   # no silent Low


def test_retry_records_attempts():
    eng = {"Fake": _fake_engine(["a", "b"], unknown_first={"a"})}
    recs, _ = E.run_username("u", eng, {"other"}, 5, retry_unknown=True, verify=False)
    by = {r["site"]: r for r in recs}
    assert by["a"]["status"] == Found.FOUND and by["a"]["attempts"] == 2
    assert by["b"]["attempts"] == 1

    stuck = _fake_engine(["a"])
    stuck.run = lambda *a, **k: [E.row("Fake", "a", Raw.UNKNOWN, "https://a.com/u")]
    recs, _ = E.run_username("u", {"Fake": stuck}, {"other"}, 5, retry_unknown=True, verify=False)
    assert recs[0]["status"] == Found.UNKNOWN and recs[0]["attempts"] == 2


def test_wmn_decision_table():
    site = {"e_code": 200, "e_string": "profile", "m_code": 404, "m_string": "nope"}
    f = E.wmn_classify
    assert f(site, 200, "profile") == Raw.CLAIMED
    assert f(site, 404, "") == Raw.AVAILABLE
    assert f(site, 200, "profile nope") == Raw.AVAILABLE      # absence outranks presence
    assert f(site, 200, "other") == Raw.UNKNOWN
    assert f(site, 429, "") == Raw.WAF
    same = {"e_code": 200, "e_string": "profile", "m_code": 200, "m_string": "gone"}
    assert f(same, 200, "profile") == Raw.CLAIMED             # equal codes must not imply absence
    assert f(same, 200, "gone") == Raw.AVAILABLE
    assert f({"e_code": 403, "m_code": 404}, 403, "") == Raw.CLAIMED   # site-defined 403 is not a WAF


def test_rate_limit_charges_per_search():
    ok, ts = store.rate_ok([], 5, cost=3); assert ok and len(ts) == 3
    ok2, ts2 = store.rate_ok(ts, 5, cost=3)
    assert not ok2 and len(ts2) == 3                              # denied request consumes nothing
    ok3, ts3 = store.rate_ok(ts2, 5, cost=2); assert ok3 and len(ts3) == 5


def test_scoped_history_and_closed_connections(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", tmp_path / "h.db")
    recs = E.score(E.apply_verification(E.merge(rows(), ["Sherlock"]), None))
    s1 = {"engines": ["Sherlock"], "categories": ["coding"], "verify": True}
    s2 = {**s1, "engines": ["Sherlock", "WhatsMyName"]}
    store.save_run("username", "u", {"found": 1}, recs, s1)
    store.save_run("username", "u", {"found": 1}, recs, s2)
    assert store.last_run("username", "u", s1)["summary"]["scope"] == s1   # skips the newer, different-scope run
    assert store.last_run("username", "u", {**s1, "verify": False}) is None
    assert store.last_run("username", "u")["summary"]["scope"] == s2
    with store.conn() as c:
        pass
    with pytest.raises(Exception):
        c.execute("select 1")                                              # connection really closed


def test_records_serialise_as_plain_strings(tmp_path, monkeypatch):
    import json
    recs = E.score(E.apply_verification(E.merge(rows(), ["Sherlock"]), None))
    d = json.loads(json.dumps(recs))
    assert d[0]["status"] in ("Found", "Not found", "Unknown") and d[0]["stage"] == "scored"
    assert "Conf." not in R.to_df(recs).to_csv() and "Found." not in R.to_df(recs).to_csv()
    assert R.excel_bytes("x", recs, {"engines": []})[:2] == b"PK"
