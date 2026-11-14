import os, tempfile
os.environ["STUDIO_DB"] = os.path.join(tempfile.mkdtemp(), "h.db")
from streamlit.testing.v1 import AppTest
from studio import engines as E, netinfo as N, store
store.DB = __import__("pathlib").Path(os.environ["STUDIO_DB"])


class Fake:
    def __init__(self, name, found):
        self.name, self.found = name, found
    def site_names(self, cats):
        return ["GitHub", "Reddit", "Etsy", "Nope"]
    def run(self, user, names, timeout, progress=None, proxy=None):
        out = []
        for i, n in enumerate(names):
            st = "Claimed" if n in self.found else "Available"
            out.append(E.row(self.name, n, st, f"https://{n.lower()}.com/{user}", E.guess_category(n)))
            progress and progress(i + 1, len(names), n)
        return out


def patch(m):
    m.setattr(E, "SherlockEngine", lambda nsfw=False: Fake("Sherlock", {"GitHub", "Reddit"}))
    m.setattr(E, "WMNEngine", lambda nsfw=False: Fake("WhatsMyName", {"GitHub", "Etsy"}))
    m.setattr(E, "verify_many", lambda urls, **k: {u: ("ok", "") for u in urls})


def test_flows(monkeypatch):
    patch(monkeypatch)
    monkeypatch.setattr(N, "dns_records", lambda d: {"A": ["1.1.1.1"], "TXT": ["v=spf1 -all"], "DMARC": [], "MX": [], "NS": [], "AAAA": [], "CAA": []})
    monkeypatch.setattr(N, "rdap", lambda d: {"Registrar": "X"})
    monkeypatch.setattr(N, "subdomains", lambda d: ["a." + d])
    monkeypatch.setattr(N, "wayback", lambda d: {"first": "2005-01-01", "last": "2026-01-01", "link": "https://x"})
    monkeypatch.setattr(N, "security_headers", lambda d: {"Strict-Transport-Security": (True, "d")})
    monkeypatch.setattr(N, "email_checks", lambda e: {"valid": True, "mx": ["mx.x"], "gravatar": ""})
    at = AppTest.from_file(os.path.join(os.path.dirname(__file__), "..", "app.py"), default_timeout=60).run()
    assert not at.exception
    # without consent -> warning
    at.text_input(key="u_name").set_value("alice"); at.button(key="go_user").click().run()
    assert any("responsible-use" in w.value for w in at.warning)
    at.checkbox(key="consent").set_value(True).run()
    at.button(key="go_user").click().run()
    assert not at.exception, [e.value for e in at.exception]
    vals = {m.label: m.value for m in at.metric} if at.metric else {}
    md = " ".join(m.value for m in at.markdown)
    assert "GitHub" in md and "High confidence" in md, md[:500]
    # second run produces diff message
    at.button(key="go_user").click().run(); assert not at.exception
    # brand
    at.text_input(key="b_handle").set_value("acme"); at.button(key="go_brand").click().run()
    assert not at.exception and "b_res" in at.session_state
    # email
    at.text_input(key="em").set_value("me@example.com"); at.button(key="go_email").click().run()
    assert not at.exception and "em_res" in at.session_state
    # domain
    at.text_input(key="dm").set_value("https://www.example.com/"); at.button(key="go_dom").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert "dm_res" in at.session_state
    # links
    at.text_input(key="lt").set_value("alice"); at.run(); assert not at.exception
    # rate limit
    at.session_state["rl"] = [__import__("time").time()] * 20
    at.button(key="go_user").click().run()
    assert any("limit" in e.value for e in at.error)
