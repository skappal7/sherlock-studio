"""Theme, CSS and small HTML components."""
from __future__ import annotations

import html
from urllib.parse import urlparse

import streamlit as st

AMBER, TEAL, CORAL, VIOLET, BLUE = "#F5B83D", "#3DD6C3", "#FF6B6B", "#9B8CFF", "#5AA9FF"
MUTED, SURFACE, BORDER = "#8B95B5", "#131A30", "#243056"
CONF_COLOR = {"High": TEAL, "Medium": AMBER, "Low": CORAL, "Unverified": MUTED}
CAT_PALETTE = [AMBER, TEAL, VIOLET, BLUE, CORAL, "#7BD88F", "#F58FD0", "#A0AEC0"]

ICONS = {
    "search": '<path d="M11 4a7 7 0 105.2 11.7l4.1 4.1-1.4 1.4-4.1-4.1A7 7 0 0011 4zm0 2a5 5 0 100 10 5 5 0 000-10z"/>',
    "shield": '<path d="M12 2l8 3v6c0 5-3.4 9.4-8 11-4.6-1.6-8-6-8-11V5l8-3zm0 2.1L6 6.4V11c0 3.9 2.5 7.4 6 8.9 3.5-1.5 6-5 6-8.9V6.4l-6-2.3z"/>',
    "mail": '<path d="M3 5h18v14H3V5zm2 2v.5l7 4.5 7-4.5V7H5zm14 3L12 14.5 5 10v7h14v-7z"/>',
    "globe": '<path d="M12 2a10 10 0 100 20 10 10 0 000-20zm0 2c1.2 1.4 2 3.4 2.3 6H9.7C10 7.4 10.8 5.4 12 4zM4.3 10a8 8 0 015-5.6C8.5 6 8 8 7.7 10H4.3zm0 4h3.4c.3 2 .8 4 1.6 5.6a8 8 0 01-5-5.6zM12 20c-1.2-1.4-2-3.4-2.3-6h4.6c-.3 2.6-1.1 4.6-2.3 6zm2.7-.4c.8-1.6 1.3-3.6 1.6-5.6h3.4a8 8 0 01-5 5.6zM16.3 10c-.3-2-.8-4-1.6-5.6a8 8 0 015 5.6h-3.4z"/>',
}


def icon(name, size=18, color="currentColor"):
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="{color}" '
            f'style="vertical-align:-3px">{ICONS[name]}</svg>')


CSS = f"""
<style>
.block-container{{padding-top:3.2rem;max-width:1250px}}
#MainMenu,footer{{visibility:hidden}}
.hero{{background:radial-gradient(1200px 300px at 0% 0%,rgba(245,184,61,.18),transparent 60%),
 radial-gradient(900px 300px at 100% 0%,rgba(61,214,195,.14),transparent 60%),{SURFACE};
 border:1px solid {BORDER};border-radius:18px;padding:26px 30px;margin-bottom:18px}}
.hero h1{{margin:0;font-size:2rem;letter-spacing:-.02em}}
.hero h1 span{{color:{AMBER}}}
.hero p{{margin:.35rem 0 0;color:{MUTED};font-size:1rem}}
.kpi{{margin-bottom:12px;background:{SURFACE};border:1px solid {BORDER};border-radius:14px;padding:14px 16px;height:100%}}
.kpi .l{{color:{MUTED};font-size:.78rem;text-transform:uppercase;letter-spacing:.08em}}
.kpi .v{{font-size:1.9rem;font-weight:700;line-height:1.15;margin-top:2px}}
.kpi .s{{color:{MUTED};font-size:.8rem;margin-top:2px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px;margin-top:6px}}
.card{{background:{SURFACE};border:1px solid {BORDER};border-radius:14px;padding:14px;transition:.15s;display:block;
 text-decoration:none!important;color:inherit!important}}
.card:hover{{border-color:{AMBER};transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.35)}}
.card .top{{display:flex;align-items:center;gap:10px}}
.card img{{width:28px;height:28px;border-radius:7px;background:#0B1020}}
.card .n{{font-weight:600;font-size:.98rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.card .meta{{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}}
.card .by{{color:{MUTED};font-size:.76rem;margin-top:8px}}
.pill{{display:inline-block;padding:2px 9px;border-radius:999px;font-size:.72rem;font-weight:600;border:1px solid}}
.chip{{display:inline-block;padding:2px 9px;border-radius:999px;font-size:.72rem;background:#1B2444;color:{MUTED}}}
.tile{{border-radius:12px;padding:10px 12px;border:1px solid {BORDER};background:{SURFACE};font-size:.9rem}}
.tile b{{display:block;margin-bottom:2px}}
.tile.free{{border-color:{TEAL}}} .tile.taken{{border-color:{CORAL}}} .tile.unk{{border-color:{MUTED}}}
.sec{{font-size:1.05rem;font-weight:600;margin:18px 0 6px}}
.good{{color:{TEAL}}} .warn{{color:{AMBER}}} .bad{{color:{CORAL}}}
.stTabs [data-baseweb="tab-list"]{{gap:6px}}
.stTabs [data-baseweb="tab"]{{background:{SURFACE};border-radius:10px 10px 0 0;padding:8px 16px}}
.stTabs [aria-selected="true"]{{border-bottom:2px solid {AMBER}}}
div.stButton>button[kind="primary"],div.stFormSubmitButton>button[kind="primaryFormSubmit"]{{font-weight:700;color:#0B1020}}
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def hero():
    st.markdown(
        f'<div class="hero"><h1>{icon("search", 30, AMBER)} Sherlock <span>Studio</span></h1>'
        f'<p>Digital footprint audit: usernames, brand handles, email exposure and domain posture, '
        f'in one place.</p></div>', unsafe_allow_html=True)


def kpi(label, value, sub="", color=None):
    c = f' style="color:{color}"' if color else ""
    return (f'<div class="kpi"><div class="l">{html.escape(label)}</div>'
            f'<div class="v"{c}>{html.escape(str(value))}</div><div class="s">{html.escape(sub)}</div></div>')


def kpi_row(items):
    cols = st.columns(len(items))
    for col, it in zip(cols, items):
        col.markdown(kpi(*it), unsafe_allow_html=True)


def pill(text, color):
    return (f'<span class="pill" style="color:{color};border-color:{color};'
            f'background:{color}1A">{html.escape(text)}</span>')


def cards(records):
    esc = html.escape
    out = ['<div class="grid">']
    for r in records:
        host = urlparse(r["url"]).netloc or "example.com"
        conf = r.get("confidence", "")
        out.append(
            f'<a class="card" href="{esc(r["url"])}" target="_blank" rel="noopener noreferrer">'
            f'<div class="top"><img loading="lazy" src="https://www.google.com/s2/favicons?domain={esc(host)}&sz=64">'
            f'<div class="n">{esc(r["site"])}</div></div>'
            f'<div class="meta"><span class="chip">{esc(r["category"])}</span>'
            f'{pill(conf if conf == "Unverified" else conf + " confidence", CONF_COLOR.get(conf, MUTED)) if conf else ""}</div>'
            f'<div class="by">Detected by {esc(r.get("engines") or "-")}'
            f'{" &middot; verified" if r.get("verify") == "ok" else ""}'
            f'{" &middot; check manually" if r.get("verify") == "suspect" else ""}</div></a>')
    out.append("</div>")
    return "".join(out)


def tiles(items):
    """items: (title, subtitle, kind) kind in free/taken/unk"""
    esc = html.escape
    return '<div class="grid">' + "".join(
        f'<div class="tile {k}"><b>{esc(t)}</b><span style="color:{MUTED}">{esc(s)}</span></div>'
        for t, s, k in items) + "</div>"


def plotly_layout(fig, height=300):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(color="#E8ECF6"), legend=dict(font=dict(size=11)))
    return fig


def link_tiles(items):
    """items: (name, tag, url, kind) - clickable tiles for manual checks."""
    esc = html.escape
    return '<div class="grid">' + "".join(
        f'<a class="tile {k}" href="{esc(u)}" target="_blank" rel="noopener noreferrer" '
        f'style="text-decoration:none;color:inherit"><b>{esc(n)}</b>'
        f'<span style="color:{MUTED}">{esc(t)} &middot; open profile</span></a>'
        for n, t, u, k in items) + "</div>"
