"""Exports: Excel, JSON and a self-contained HTML report (print to PDF from the browser)."""
from __future__ import annotations

import html
import io
import json
import time

import pandas as pd

COLS = ["site", "category", "status", "confidence", "found_by", "checked_by", "engines", "verify", "url"]


def to_df(records):
    df = pd.DataFrame(records)
    for c in COLS:
        if c not in df:
            df[c] = ""
    return df[COLS]


def excel_bytes(username, records, meta: dict):
    df = to_df(records)
    found = df[df["status"] == "Found"]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        summ = pd.DataFrame({"Item": ["Username", "Generated", "Engines", "Sites checked", "Found",
                                      "High confidence", "Medium", "Low", "Unverified"],
                             "Value": [username, time.strftime("%Y-%m-%d %H:%M"), ", ".join(meta.get("engines", [])),
                                       len(df), len(found), int((found.confidence == "High").sum()),
                                       int((found.confidence == "Medium").sum()),
                                       int((found.confidence == "Low").sum()),
                                       int((found.confidence == "Unverified").sum())]})
        summ.to_excel(xw, sheet_name="Summary", index=False)
        found.to_excel(xw, sheet_name="Found", index=False)
        df.to_excel(xw, sheet_name="All", index=False)
        for ws in xw.book.worksheets:
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(
                    60, max(len(str(c.value or "")) for c in col) + 2)
    return buf.getvalue()


def json_bytes(username, records, meta):
    return json.dumps({"username": username, "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       **meta, "results": records}, indent=2).encode()


def html_report(username, records, meta):
    df = to_df(records)
    found = df[df["status"] == "Found"]
    esc = html.escape
    rows = "".join(
        f"<tr><td>{esc(r.site)}</td><td>{esc(r.category)}</td><td class='c-{esc(r.confidence)}'>{esc(r.confidence)}</td>"
        f"<td>{esc(r.engines)}</td><td><a href='{esc(r.url)}'>{esc(r.url)}</a></td></tr>"
        for r in found.itertuples())
    return f"""<!doctype html><meta charset=utf-8><title>Footprint report - {esc(username)}</title>
<style>body{{font:14px system-ui;margin:40px;color:#111}}h1{{margin:0}}table{{border-collapse:collapse;width:100%;margin-top:16px}}
td,th{{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left}}th{{background:#f4f4f4}}.c-High{{color:#0a8f7f;font-weight:600}}
.c-Medium{{color:#b7791f;font-weight:600}}.c-Low{{color:#c53030;font-weight:600}}.c-Unverified{{color:#666;font-weight:600}}.m{{color:#666}}</style>
<h1>Footprint report: {esc(username)}</h1>
<p class=m>Generated {time.strftime('%Y-%m-%d %H:%M')} &middot; Engines: {esc(', '.join(meta.get('engines', [])))} &middot;
{len(df)} sites checked &middot; {len(found)} found</p>
<p class=m>Matches are automated detections and may include false positives. Verify each manually.</p>
<table><tr><th>Site</th><th>Category</th><th>Confidence</th><th>Detected by</th><th>URL</th></tr>{rows}</table>"""
