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
    by["foo"]["verify"] = "ok"
    E.score(recs)
    assert by["github"]["confidence"] == "High" and by["foo"]["confidence"] == "Medium"
    by["foo"]["verify"] = "suspect"; E.score(recs)
    assert by["foo"]["confidence"] == "Low"


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
    recs = E.score(E.merge(rows(), ["Sherlock"]))
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
