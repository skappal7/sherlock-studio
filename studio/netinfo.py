"""Email, domain and link-generation helpers. All lookups use public APIs / DNS."""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from urllib.parse import quote

import requests

UA = {"User-Agent": "SherlockStudio/1.0 (+self-audit tool)"}
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def _get(url, timeout=15, **kw):
    try:
        return requests.get(url, headers=UA, timeout=timeout, **kw)
    except requests.RequestException:
        return None


# ------------------------------------------------------------------ domain
def clean_domain(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"^https?://", "", s).split("/")[0].split(":")[0]
    return s[4:] if s.startswith("www.") else s


def dns_records(domain: str) -> dict:
    import dns.resolver
    res = dns.resolver.Resolver()
    res.lifetime = 6
    out = {}
    for t in ["A", "AAAA", "MX", "NS", "TXT", "CAA"]:
        try:
            ans = res.resolve(domain, t)
            out[t] = sorted(a.to_text().strip('"') for a in ans)
        except Exception:
            out[t] = []
    try:
        out["DMARC"] = [a.to_text().strip('"') for a in res.resolve("_dmarc." + domain, "TXT")]
    except Exception:
        out["DMARC"] = []
    return out


def email_auth_grade(rec: dict) -> list[tuple[str, str, str]]:
    """Return (check, result, level) with level in good/warn/bad."""
    items = []
    spf = [t for t in rec.get("TXT", []) if t.lower().startswith("v=spf1")]
    if not spf:
        items.append(("SPF", "No SPF record", "bad"))
    elif spf[0].rstrip().endswith("-all"):
        items.append(("SPF", "Strict (-all)", "good"))
    elif spf[0].rstrip().endswith("~all"):
        items.append(("SPF", "Soft fail (~all)", "warn"))
    else:
        items.append(("SPF", "Permissive or unclear", "warn"))
    dm = [t for t in rec.get("DMARC", []) if t.lower().startswith("v=dmarc1")]
    if not dm:
        items.append(("DMARC", "No DMARC record", "bad"))
    else:
        m = re.search(r"p=(\w+)", dm[0])
        pol = m.group(1).lower() if m else "none"
        items.append(("DMARC", f"Policy: {pol}", "good" if pol in ("reject", "quarantine") else "warn"))
    items.append(("CAA", "Present" if rec.get("CAA") else "Absent", "good" if rec.get("CAA") else "warn"))
    return items


def rdap(domain: str) -> dict:
    r = _get(f"https://rdap.org/domain/{domain}", timeout=20)
    if not r or r.status_code != 200:
        return {}
    d = r.json()
    ev = {e.get("eventAction"): e.get("eventDate", "")[:10] for e in d.get("events", [])}
    registrar = ""
    for ent in d.get("entities", []):
        if "registrar" in ent.get("roles", []):
            for item in ent.get("vcardArray", [None, []])[1]:
                if item[0] == "fn":
                    registrar = item[3]
    return {"Registered": ev.get("registration", ""), "Updated": ev.get("last changed", ""),
            "Expires": ev.get("expiration", ""), "Registrar": registrar,
            "Status": ", ".join(d.get("status", [])), "Nameservers": ", ".join(
                n.get("ldhName", "") for n in d.get("nameservers", []))}


def subdomains(domain: str, limit=300) -> list[str]:
    r = _get(f"https://crt.sh/?q=%25.{domain}&output=json", timeout=40)
    if not r or r.status_code != 200:
        return []
    try:
        names = set()
        for row in r.json():
            for n in row.get("name_value", "").split("\n"):
                n = n.strip().lower().lstrip("*.")
                if n.endswith(domain):
                    names.add(n)
        return sorted(names)[:limit]
    except ValueError:
        return []


def wayback(domain: str) -> dict:
    base = "https://web.archive.org/cdx/search/cdx"
    first = _get(f"{base}?url={domain}&output=json&limit=1&fl=timestamp")
    last = _get(f"{base}?url={domain}&output=json&limit=-1&fl=timestamp")

    def ts(r):
        try:
            rows = r.json()
            return rows[1][0] if len(rows) > 1 else ""
        except Exception:
            return ""

    f, l = ts(first) if first else "", ts(last) if last else ""
    fmt = lambda t: f"{t[:4]}-{t[4:6]}-{t[6:8]}" if t else ""
    return {"first": fmt(f), "last": fmt(l),
            "link": f"https://web.archive.org/web/*/{domain}"}


SEC_HEADERS = {
    "Strict-Transport-Security": "Forces HTTPS",
    "Content-Security-Policy": "Limits script/content sources",
    "X-Frame-Options": "Blocks clickjacking",
    "X-Content-Type-Options": "Stops MIME sniffing",
    "Referrer-Policy": "Controls referrer leakage",
    "Permissions-Policy": "Restricts browser features",
}


def security_headers(domain: str) -> dict:
    r = _get(f"https://{domain}", timeout=12, allow_redirects=True)
    if not r:
        return {}
    h = {k.lower(): v for k, v in r.headers.items()}
    return {k: (k.lower() in h, desc) for k, desc in SEC_HEADERS.items()}


# ------------------------------------------------------------------ email
def email_checks(email: str) -> dict:
    out = {"valid": bool(EMAIL_RE.match(email))}
    if not out["valid"]:
        return out
    dom = email.split("@")[1].lower()
    try:
        import dns.resolver
        mx = dns.resolver.resolve(dom, "MX", lifetime=6)
        out["mx"] = sorted(m.exchange.to_text().rstrip(".") for m in mx)
    except Exception:
        out["mx"] = []
    h = hashlib.md5(email.strip().lower().encode()).hexdigest()
    r = _get(f"https://www.gravatar.com/avatar/{h}?d=404&s=120", timeout=8)
    out["gravatar"] = f"https://www.gravatar.com/avatar/{h}?s=120" if r is not None and r.status_code == 200 else ""
    return out


def hibp(email: str, api_key: str) -> tuple[list[dict], str]:
    if not api_key:
        return [], "No API key set"
    r = _get(f"https://haveibeenpwned.com/api/v3/breachedaccount/{quote(email)}?truncateResponse=false",
             timeout=15, headers={**UA, "hibp-api-key": api_key})
    if r is None:
        return [], "Request failed"
    if r.status_code == 404:
        return [], ""
    if r.status_code == 401:
        return [], "Invalid API key"
    if r.status_code == 429:
        return [], "Rate limited, retry shortly"
    if r.status_code != 200:
        return [], f"HTTP {r.status_code}"
    return [{"Breach": b["Name"], "Date": b.get("BreachDate", ""),
             "Data exposed": ", ".join(b.get("DataClasses", [])[:6])} for b in r.json()], ""


def holehe_available() -> bool:
    return shutil.which("holehe") is not None


def run_holehe(email: str) -> list[str]:
    if not holehe_available():
        return []
    try:
        p = subprocess.run(["holehe", "--only-used", "--no-color", email], capture_output=True,
                           text=True, timeout=240)
    except Exception:
        return []
    return [re.sub(r"\x1b\[[0-9;]*m", "", l)[3:].strip() for l in p.stdout.splitlines()
            if l.strip().startswith("[+]")]


# ------------------------------------------------------------------ search links
def dork_links(term: str) -> list[tuple[str, str, str]]:
    q = quote(f'"{term}"')
    return [
        ("Web", "Exact match", f"https://www.google.com/search?q={q}"),
        ("Web", "Bing exact match", f"https://www.bing.com/search?q={q}"),
        ("Web", "DuckDuckGo", f"https://duckduckgo.com/?q={q}"),
        ("Code", "GitHub code", f"https://github.com/search?q={q}&type=code"),
        ("Code", "GitHub users", f"https://github.com/search?q={quote(term)}&type=users"),
        ("Code", "Gist", f"https://gist.github.com/search?q={q}"),
        ("Leaks", "Pastebin (via Google)", f"https://www.google.com/search?q={quote('site:pastebin.com ' + chr(34) + term + chr(34))}"),
        ("Social", "Reddit", f"https://www.reddit.com/search/?q={q}"),
        ("Social", "X / Twitter", f"https://x.com/search?q={q}&f=user"),
        ("Docs", "PDF mentions", f"https://www.google.com/search?q={quote(chr(34) + term + chr(34) + ' filetype:pdf')}"),
        ("Archive", "Wayback", f"https://web.archive.org/web/*/{quote(term)}"),
    ]


def image_links(image_url: str) -> list[tuple[str, str]]:
    u = quote(image_url, safe="")
    return [("Google Lens", f"https://lens.google.com/uploadbyurl?url={u}"),
            ("TinEye", f"https://tineye.com/search?url={u}"),
            ("Yandex Images", f"https://yandex.com/images/search?rpt=imageview&url={u}"),
            ("Bing Visual Search", f"https://www.bing.com/images/search?view=detailv2&iss=sbi&q=imgurl:{u}")]
