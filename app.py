"""Sherlock Studio - digital footprint audit (Streamlit)."""
import csv
import io
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import plotly.express as px
import streamlit as st

from studio import engines as E
from studio import netinfo as N
from studio import reports as R
from studio import store
from studio import ui

st.set_page_config(page_title="Sherlock Studio", layout="wide", initial_sidebar_state="expanded")
ui.inject_css()

BRAND_SITES = {E.norm_key(x) for x in [
    "GitHub", "GitLab", "Twitter", "X", "Instagram", "Facebook", "TikTok", "YouTube", "Reddit",
    "Pinterest", "LinkedIn", "Medium", "Twitch", "Steam", "Spotify", "SoundCloud", "Behance",
    "Dribbble", "dev.to", "Patreon", "Tumblr", "Snapchat", "Telegram", "Discord", "ProductHunt",
    "HackerNews", "Kaggle", "DockerHub", "npm", "PyPI", "Replit", "CodePen", "Substack", "Vimeo",
    "Flickr", "Etsy", "Mastodon", "Threads", "Bluesky", "Quora", "Gravatar", "About.me"]}


def secret(name, default=""):
    try:
        return st.secrets.get(name, os.environ.get(name, default))
    except Exception:
        return os.environ.get(name, default)


USER_RE = __import__("re").compile(r"^[\w.\-]{2,40}$")
RATE_LIMIT = int(secret("RATE_LIMIT", 20))

# ------------------------------------------------------------------ cached engines
@st.cache_resource(ttl=3600, show_spinner="Loading site databases...")
def get_engines(nsfw: bool):
    out, warn = {}, []
    try:
        out["Sherlock"] = E.SherlockEngine(nsfw)
    except Exception as e:
        warn.append(f"Sherlock unavailable: {e}")
    try:
        out["WhatsMyName"] = E.WMNEngine(nsfw)
    except Exception as e:
        warn.append(f"WhatsMyName unavailable: {e}")
    return out, warn


MANUAL = [  # platforms that often block automated checks: (name, key, profile URL)
    ("Instagram", "instagram", "https://www.instagram.com/{u}/"),
    ("Facebook", "facebook", "https://www.facebook.com/{u}"),
    ("X / Twitter", "twitter", "https://x.com/{u}"),
    ("TikTok", "tiktok", "https://www.tiktok.com/@{u}"),
    ("LinkedIn", "linkedin", "https://www.linkedin.com/in/{u}"),
    ("Threads", "threads", "https://www.threads.net/@{u}"),
    ("Snapchat", "snapchat", "https://www.snapchat.com/add/{u}"),
    ("YouTube", "youtube", "https://www.youtube.com/@{u}"),
    ("Pinterest", "pinterest", "https://www.pinterest.com/{u}/"),
    ("Reddit", "reddit", "https://www.reddit.com/user/{u}"),
    ("Telegram", "telegram", "https://t.me/{u}"),
]


def manual_panel(user, recs):
    """Major platforms not confirmed by the engines, with one-click profile links."""
    by = {E.norm_key(r["site"]): r for r in recs}
    items = []
    for name, key, tpl in MANUAL:
        hit = by.get(key)
        if hit and hit["status"] == "Found" and hit.get("confidence") != "Low":
            continue
        tag = ("Not confirmed" if hit and hit["status"] == "Found" else
               "Blocked or unreadable" if hit and hit["status"] == "Unknown" else
               "Not detected" if hit else "Not checked")
        items.append((name, tag, tpl.format(u=user), "unk"))
    if items:
        st.markdown('<div class="sec">Major platforms to check manually</div>', unsafe_allow_html=True)
        st.markdown(ui.link_tiles(items), unsafe_allow_html=True)
        st.caption("Instagram, Facebook, X, TikTok and LinkedIn usually block automated requests from cloud servers "
                   "and show login walls, so they often cannot be detected reliably. Open each link to confirm.")


def exposure_index(recs):
    f = [r for r in recs if r["status"] == "Found"]
    hi = sum(r["confidence"] == "High" for r in f)
    md = sum(r["confidence"] == "Medium" for r in f)
    lo = sum(r["confidence"] == "Low" for r in f)
    cats = len({r["category"] for r in f})
    return min(100, round(hi * 4 + md * 2 + lo * 0.5 + cats * 2))


def exposure_label(v):
    return ("Minimal", ui.TEAL) if v < 15 else ("Moderate", ui.AMBER) if v < 45 else ("Extensive", ui.CORAL)


# ------------------------------------------------------------------ sidebar: guardrails + options
with st.sidebar:
    st.markdown(f"### {ui.icon('shield')} Responsible use", unsafe_allow_html=True)
    consent = st.checkbox("I am checking my own identifier or brand, or I have a lawful, "
                          "legitimate reason to run this search.", key="consent")
    purpose = st.selectbox("Purpose", ["Self-audit", "Brand protection", "Security research",
                                       "Journalism / investigation", "Other lawful use"])
    st.caption(f"Limit: {RATE_LIMIT} searches per hour per session. Searches are logged locally. "
               "Results are automated detections; verify before relying on them.")
    st.divider()
    st.markdown("### Engine settings")
    nsfw = st.toggle("Include NSFW sites", False)
    timeout = st.slider("Timeout per site (s)", 5, 40, 12)
    retry = st.toggle("Retry timeouts / blocks once", True)
    verify = st.toggle("Verify found accounts (second request)", True)
    proxy = st.text_input("Proxy (optional)", placeholder="http://host:port or socks5://host:port")
    st.caption("Tor is only available if you run the app locally with a Tor proxy.")

engines, warns = get_engines(nsfw)
for w in warns:
    st.sidebar.warning(w)


def gate() -> bool:
    if not consent:
        st.warning("Tick the responsible-use box in the sidebar to run searches.")
        return False
    ok, st.session_state["rl"] = store.rate_ok(st.session_state.get("rl", []), RATE_LIMIT)
    if not ok:
        st.error("Hourly search limit reached for this session. Try again later.")
    return ok


def progress_cb():
    bar, txt = st.progress(0.0), st.empty()

    def f(frac, msg):
        bar.progress(min(frac, 1.0))
        txt.caption(msg)

    def done():
        bar.empty(); txt.empty()
    return f, done


# ------------------------------------------------------------------ header
ui.hero()
t_user, t_brand, t_email, t_domain, t_links, t_hist, t_about = st.tabs(
    ["Username", "Brand check", "Email", "Domain", "Search links", "History", "About"])


# ================================================================== USERNAME
def render_username_results(user, recs, ran):
    found = [r for r in recs if r["status"] == "Found"]
    hi = sum(r["confidence"] == "High" for r in found)
    idx = exposure_index(recs)
    lab, col = exposure_label(idx)
    ui.kpi_row([("Accounts found", len(found), f"of {len(recs)} sites checked", ui.AMBER),
                ("High confidence", hi, "confirmed by 2+ engines", ui.TEAL),
                ("Categories", len({r["category"] for r in found}), "platform types"),
                ("Exposure index", f"{idx}/100", lab, col)])
    if not found:
        st.info("No accounts detected for this username on the selected sites.")
        manual_panel(user, recs)
        return
    c1, c2 = st.columns(2)
    cat = pd.Series([r["category"] for r in found]).value_counts().reset_index()
    cat.columns = ["category", "count"]
    fig = px.pie(cat, names="category", values="count", hole=.62, color_discrete_sequence=ui.CAT_PALETTE,
                 title="Accounts by category")
    fig.update_traces(textinfo="value", marker=dict(line=dict(color="#0B1020", width=2)))
    c1.plotly_chart(ui.plotly_layout(fig), width="stretch")
    conf = pd.Series([r["confidence"] for r in found]).value_counts().reindex(
        ["High", "Medium", "Low"]).fillna(0).reset_index()
    conf.columns = ["confidence", "count"]
    fig2 = px.bar(conf, x="count", y="confidence", orientation="h", color="confidence",
                  color_discrete_map=ui.CONF_COLOR, title="Confidence breakdown")
    fig2.update_layout(showlegend=False, xaxis_title=None,
                       yaxis=dict(title=None, categoryorder="array", categoryarray=["High", "Medium", "Low"],
                                  autorange="reversed"))
    c2.plotly_chart(ui.plotly_layout(fig2), width="stretch")
    st.caption("Exposure index is a heuristic: 4 points per high-confidence account, 2 per medium, 0.5 per low, "
               "plus 2 per category, capped at 100. Use it to compare runs, not as a risk rating.")

    f1, f2, f3 = st.columns([2, 2, 1])
    cats = f1.multiselect("Category", sorted({r["category"] for r in found}), key=f"fc_{user}")
    confs = f2.multiselect("Confidence", ["High", "Medium", "Low"], default=["High", "Medium", "Low"],
                           key=f"fcf_{user}")
    view = f3.radio("View", ["Cards", "Table"], horizontal=True, key=f"fv_{user}")
    shown = [r for r in found if (not cats or r["category"] in cats) and r["confidence"] in confs]
    shown.sort(key=lambda r: ({"High": 0, "Medium": 1, "Low": 2}[r["confidence"]], r["site"].lower()))
    if view == "Cards":
        st.markdown(ui.cards(shown), unsafe_allow_html=True)
    else:
        st.dataframe(R.to_df(shown), hide_index=True, width="stretch",
                     column_config={"url": st.column_config.LinkColumn("url")})

    manual_panel(user, recs)
    meta = {"engines": ran}
    st.markdown('<div class="sec">Export</div>', unsafe_allow_html=True)
    d = st.columns(4)
    d[0].download_button("CSV", R.to_df(recs).to_csv(index=False), f"{user}_footprint.csv", "text/csv", width="stretch")
    d[1].download_button("Excel", R.excel_bytes(user, recs, meta), f"{user}_footprint.xlsx", width="stretch")
    d[2].download_button("JSON", R.json_bytes(user, recs, meta), f"{user}_footprint.json", width="stretch")
    d[3].download_button("HTML report", R.html_report(user, recs, meta), f"{user}_report.html",
                         "text/html", width="stretch")
    with st.expander("Not found / unknown sites"):
        st.dataframe(R.to_df([r for r in recs if r["status"] != "Found"]), hide_index=True, width="stretch")


with t_user:
    if not engines:
        st.error("No search engines could be loaded. Check your network connection.")
    else:
        c1, c2 = st.columns([3, 2])
        with c1:
            username = st.text_input("Username", placeholder="e.g. johndoe", key="u_name").strip()
            up = st.file_uploader("Or upload a CSV with a 'username' column (max 5)", type="csv")
        with c2:
            preset = st.selectbox("Site preset", list(E.PRESETS))
            chosen_cats = st.multiselect("Categories", E.CATEGORIES, default=E.PRESETS[preset],
                                         key=f"cats_{preset}")
            avail = list(engines)
            sel_eng = st.multiselect("Engines", avail + (["Maigret"] if E.maigret_available() else []),
                                     default=avail,
                                     help="Maigret adds ~500 more sites but is slower (a few minutes).")
        with st.expander("Name to username variants"):
            nm = st.text_input("Full name", placeholder="Jane Doe", key="v_name")
            if nm:
                st.code(", ".join(E.variants(nm)) or "-")
                st.caption("Copy any variant into the username box.")

        if st.button("Search username", type="primary", key="go_user"):
            names = [username] if username else []
            if up is not None:
                try:
                    names += [r["username"].strip() for r in csv.DictReader(io.StringIO(up.getvalue().decode()))
                              if r.get("username", "").strip()]
                except Exception:
                    st.error("Could not read CSV. Include a header row with 'username'.")
            names = list(dict.fromkeys(names))[:5]
            bad = [n for n in names if not USER_RE.match(n)]
            if not names:
                st.warning("Enter a username.")
            elif bad:
                st.warning(f"Invalid username(s): {', '.join(bad)}")
            elif gate():
                eng_sel = {k: v for k, v in engines.items() if k in sel_eng}
                results = {}
                for n in names:
                    f, done = progress_cb()
                    prev = store.list_runs("username", n, 1)
                    prev_data = store.get_run(prev[0]["id"]) if prev else []
                    recs, ran = E.run_username(n, eng_sel, set(chosen_cats), timeout, retry, verify,
                                               proxy, "Maigret" in sel_eng, progress=f)
                    done()
                    fnd = [r for r in recs if r["status"] == "Found"]
                    store.save_run("username", n, {"found": len(fnd), "checked": len(recs)}, recs)
                    store.audit("username", n, purpose)
                    results[n] = (recs, ran, store.diff_runs(prev_data, recs) if prev else None)
                st.session_state["u_res"] = results

    for n, (recs, ran, diff) in st.session_state.get("u_res", {}).items():
        st.markdown(f'<div class="sec">Results for <span style="color:{ui.AMBER}">{n}</span> '
                    f'<span class="chip">{", ".join(ran)}</span></div>', unsafe_allow_html=True)
        if diff and (diff[0] or diff[1]):
            st.info(f"Since the last run: {len(diff[0])} new ({', '.join(diff[0][:8])}"
                    f"{'...' if len(diff[0]) > 8 else ''}), {len(diff[1])} gone.")
        render_username_results(n, recs, ran)

# ================================================================== BRAND CHECK
with t_brand:
    st.markdown("Check whether your brand handle is **taken or free** on the major platforms, "
                "and what to register before someone else does.")
    handle = st.text_input("Brand handle", placeholder="acmecorp", key="b_handle").strip()
    if st.button("Check handle", type="primary", key="go_brand"):
        if not USER_RE.match(handle):
            st.warning("Enter a valid handle.")
        elif gate():
            f, done = progress_cb()
            recs, ran = E.run_username(handle, engines, set(E.CATEGORIES), timeout, retry, False,
                                       proxy, only=BRAND_SITES, progress=f)
            done()
            store.save_run("brand", handle, {"checked": len(recs)}, recs)
            store.audit("brand", handle, purpose)
            st.session_state["b_res"] = (handle, recs)
    if "b_res" in st.session_state:
        h, recs = st.session_state["b_res"]
        taken = [r for r in recs if r["status"] == "Found"]
        free = [r for r in recs if r["status"] == "Not found"]
        unk = [r for r in recs if r["status"] == "Unknown"]
        ui.kpi_row([("Taken", len(taken), "handle in use", ui.CORAL), ("Free", len(free), "appears available", ui.TEAL),
                    ("Unknown", len(unk), "blocked or timed out", ui.MUTED)])
        st.markdown('<div class="sec">Platform status</div>', unsafe_allow_html=True)
        items = [(r["site"], "Taken - review the account" if r["status"] == "Found" else
                  "Free - consider registering" if r["status"] == "Not found" else "Could not determine",
                  "taken" if r["status"] == "Found" else "free" if r["status"] == "Not found" else "unk")
                 for r in sorted(recs, key=lambda x: (x["status"], x["site"].lower()))]
        st.markdown(ui.tiles(items), unsafe_allow_html=True)
        st.caption("'Free' reflects detection logic, not a reservation. Platform rules may still block registration "
                   "(reserved or banned names).")

# ================================================================== EMAIL
with t_email:
    st.markdown("Email exposure check. Intended for **your own** address.")
    em = st.text_input("Email address", placeholder="you@example.com", key="em").strip()
    hk = st.text_input("Have I Been Pwned API key (optional)", type="password",
                       value=secret("HIBP_API_KEY"), help="Required by HIBP for breach lookups.")
    use_holehe = st.toggle("Check site registrations with Holehe", False, disabled=not N.holehe_available(),
                           help="Available if the optional 'holehe' package is installed.")
    if st.button("Run email checks", type="primary", key="go_email"):
        if not N.EMAIL_RE.match(em):
            st.warning("Enter a valid email address.")
        elif gate():
            with st.spinner("Checking..."):
                basic = N.email_checks(em)
                br, br_err = N.hibp(em, hk)
                sites = N.run_holehe(em) if use_holehe else []
            store.audit("email", em.split("@")[1], purpose)
            st.session_state["em_res"] = (em, basic, br, br_err, sites, bool(hk), use_holehe)
    if "em_res" in st.session_state:
        em, basic, br, br_err, sites, had_key, used_h = st.session_state["em_res"]
        ui.kpi_row([("Mail servers", len(basic.get("mx", [])), "MX records", ui.TEAL if basic.get("mx") else ui.CORAL),
                    ("Gravatar", "Yes" if basic.get("gravatar") else "No", "public avatar linked"),
                    ("Known breaches", len(br) if had_key and not br_err else "n/a",
                     br_err or ("from HIBP" if had_key else "add API key"),
                     ui.CORAL if br else ui.TEAL),
                    ("Registered sites", len(sites) if used_h else "n/a", "via Holehe" if used_h else "not run")])
        if basic.get("gravatar"):
            st.image(basic["gravatar"], width=90, caption="Public Gravatar avatar")
        if basic.get("mx"):
            st.caption("MX: " + ", ".join(basic["mx"]))
        if br:
            st.markdown('<div class="sec">Breaches</div>', unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(br), hide_index=True, width="stretch")
            st.caption("Change passwords for affected services, enable 2FA, and avoid password reuse.")
        if sites:
            st.markdown('<div class="sec">Sites where this email is registered</div>', unsafe_allow_html=True)
            st.markdown(" ".join(f'<span class="chip">{s}</span>' for s in sites), unsafe_allow_html=True)

# ================================================================== DOMAIN
with t_domain:
    st.markdown("Domain posture: registration, DNS, email authentication, security headers, subdomains, archive history.")
    dm = st.text_input("Domain", placeholder="example.com", key="dm")
    if st.button("Analyze domain", type="primary", key="go_dom"):
        d = N.clean_domain(dm)
        if not N.DOMAIN_RE.match(d):
            st.warning("Enter a valid domain, e.g. example.com")
        elif gate():
            with st.spinner("Collecting data (certificate search can take ~30s)..."):
                with ThreadPoolExecutor(6) as ex:
                    fs = {"dns": ex.submit(N.dns_records, d), "rdap": ex.submit(N.rdap, d),
                          "subs": ex.submit(N.subdomains, d), "wb": ex.submit(N.wayback, d),
                          "hdr": ex.submit(N.security_headers, d)}
                    res = {k: f.result() for k, f in fs.items()}
            store.audit("domain", d, purpose)
            st.session_state["dm_res"] = (d, res)
    if "dm_res" in st.session_state:
        d, r = st.session_state["dm_res"]
        grades = N.email_auth_grade(r["dns"])
        hdr = r["hdr"]
        hdr_ok = sum(v[0] for v in hdr.values()) if hdr else 0
        ui.kpi_row([("Subdomains", len(r["subs"]), "from certificates", ui.AMBER),
                    ("Security headers", f"{hdr_ok}/{len(N.SEC_HEADERS)}" if hdr else "n/a", "present",
                     ui.TEAL if hdr_ok >= 4 else ui.AMBER if hdr_ok >= 2 else ui.CORAL),
                    ("Email auth", sum(g[2] == "good" for g in grades), f"of {len(grades)} checks good"),
                    ("Archive", r["wb"].get("first", "")[:4] or "n/a", "first snapshot year")])
        a, b = st.columns(2)
        with a:
            st.markdown('<div class="sec">Registration (RDAP)</div>', unsafe_allow_html=True)
            if r["rdap"]:
                st.dataframe(pd.DataFrame(r["rdap"].items(), columns=["Field", "Value"]), hide_index=True, width="stretch")
            else:
                st.caption("No RDAP data returned for this domain.")
            st.markdown('<div class="sec">Email authentication</div>', unsafe_allow_html=True)
            for chk, msg, lvl in grades:
                st.markdown(f'<span class="{lvl}">&#9679;</span> **{chk}** &nbsp; {msg}', unsafe_allow_html=True)
        with b:
            st.markdown('<div class="sec">Security headers</div>', unsafe_allow_html=True)
            if hdr:
                for k, (ok, desc) in hdr.items():
                    st.markdown(f'<span class="{"good" if ok else "bad"}">&#9679;</span> **{k}** '
                                f'<span style="color:{ui.MUTED}">{desc}</span>', unsafe_allow_html=True)
            else:
                st.caption("Could not fetch the site over HTTPS.")
            st.markdown('<div class="sec">Archive history</div>', unsafe_allow_html=True)
            wb = r["wb"]
            st.markdown(f"First: **{wb.get('first') or 'n/a'}** &nbsp; Last: **{wb.get('last') or 'n/a'}** &nbsp; "
                        f"[Open Wayback]({wb['link']})")
        st.markdown('<div class="sec">DNS records</div>', unsafe_allow_html=True)
        rows = [(t, v) for t, vals in r["dns"].items() for v in vals]
        st.dataframe(pd.DataFrame(rows, columns=["Type", "Value"]), hide_index=True, width="stretch")
        st.markdown('<div class="sec">Subdomains seen in certificate logs</div>', unsafe_allow_html=True)
        if r["subs"]:
            st.dataframe(pd.DataFrame({"Subdomain": r["subs"]}), hide_index=True, width="stretch")
            st.download_button("Download subdomains (CSV)", pd.DataFrame({"subdomain": r["subs"]}).to_csv(index=False),
                               f"{d}_subdomains.csv", "text/csv")
        else:
            st.caption("None returned (crt.sh may be slow or rate-limiting).")

# ================================================================== LINKS
with t_links:
    st.markdown("Ready-made search links. Nothing is scraped; you open each search yourself.")
    term = st.text_input("Search term (username, email, brand)", key="lt").strip()
    if term:
        by_group = {}
        for g, label, url in N.dork_links(term):
            by_group.setdefault(g, []).append((label, url))
        cols = st.columns(3)
        for i, (g, items) in enumerate(by_group.items()):
            with cols[i % 3]:
                st.markdown(f'<div class="sec">{g}</div>', unsafe_allow_html=True)
                for label, url in items:
                    st.markdown(f"[{label}]({url})")
    st.markdown('<div class="sec">Reverse image search</div>', unsafe_allow_html=True)
    img = st.text_input("Public image URL (e.g. your own profile photo)", key="img").strip()
    if img.startswith("http"):
        st.markdown(" &nbsp;|&nbsp; ".join(f"[{n}]({u})" for n, u in N.image_links(img)))

# ================================================================== HISTORY
with t_hist:
    runs = store.list_runs(limit=200)
    if not runs:
        st.info("No saved runs yet. Results are saved automatically after each search.")
    else:
        df = pd.DataFrame([{"id": r["id"], "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"])),
                            "type": r["kind"], "query": r["query"],
                            "found": r["summary"].get("found", ""), "checked": r["summary"].get("checked", "")}
                           for r in runs])
        st.dataframe(df, hide_index=True, width="stretch")
        st.markdown('<div class="sec">Compare two runs</div>', unsafe_allow_html=True)
        uname_runs = [r for r in runs if r["kind"] == "username"]
        if len(uname_runs) >= 2:
            lab = {r["id"]: f"#{r['id']} {r['query']} ({time.strftime('%m-%d %H:%M', time.localtime(r['ts']))})"
                   for r in uname_runs}
            c1, c2 = st.columns(2)
            a = c1.selectbox("Older run", list(lab), format_func=lab.get, index=min(1, len(lab) - 1))
            b = c2.selectbox("Newer run", list(lab), format_func=lab.get, index=0)
            new, gone = store.diff_runs(store.get_run(a), store.get_run(b))
            c1.success(f"New accounts ({len(new)}): " + (", ".join(new) or "none"))
            c2.warning(f"No longer found ({len(gone)}): " + (", ".join(gone) or "none"))
        else:
            st.caption("Run at least two username searches to compare them.")
    with st.expander("Audit log"):
        log = store.audit_log()
        st.dataframe(pd.DataFrame([{"when": time.strftime("%Y-%m-%d %H:%M", time.localtime(x["ts"])),
                                    "type": x["kind"], "query": x["query"], "purpose": x["purpose"]}
                                   for x in log]), hide_index=True, width="stretch")
    if st.button("Clear all history and audit log"):
        store.clear_all()
        st.rerun()
    st.caption("History is stored in a local SQLite file. On Streamlit Community Cloud this storage is "
               "temporary and resets when the app restarts.")

# ================================================================== ABOUT
with t_about:
    st.markdown(f"""
**How it works.** Username searches run Sherlock and WhatsMyName side by side (Maigret too, if installed). Results are
merged by site. Each found account is re-requested once and checked for soft-404 pages and redirects to the home page.

**Confidence**
- {ui.pill("High", ui.TEAL)} detected by two or more engines
- {ui.pill("Medium", ui.AMBER)} detected by one engine and passed the second-request check
- {ui.pill("Low", ui.CORAL)} single engine, or the verification looked suspicious or could not run

**Limits.** Sites change often and some block automated requests, so expect false positives and gaps. On a shared host
(e.g. Streamlit Community Cloud) more sites will rate-limit or block you than on your own machine.

**Scope.** This tool is built for self-audit, brand protection and security review. It deliberately has no people-search,
phone-to-address, geolocation or third-party account profiling features. Do not use it to harass, stalk or profile private
individuals.

Built on [Sherlock](https://github.com/sherlock-project/sherlock) and
[WhatsMyName](https://github.com/WebBreacher/WhatsMyName). Both are open source; check their licenses before redistribution.
""", unsafe_allow_html=True)
