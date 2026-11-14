"""Username search engines (Sherlock, WhatsMyName, optional Maigret), merge and verify logic."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable
from urllib.parse import urlparse

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
WMN_URL = "https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json"
NSFW_CAT = "xx NSFW xx"

CATEGORIES = ["social", "coding", "tech", "business", "gaming", "art", "music", "video",
              "images", "blog", "news", "shopping", "finance", "dating", "health",
              "hobby", "political", "archived", "misc", "other"]

PRESETS = {
    "Everything": CATEGORIES,
    "Professional": ["business", "coding", "tech", "blog", "news"],
    "Social": ["social", "blog", "images", "video", "dating"],
    "Creative": ["art", "music", "images", "video", "blog"],
    "Gaming": ["gaming", "video", "social"],
}

# Keyword -> category for Sherlock sites (Sherlock has no categories of its own).
_KW = {
    "social": ["facebook", "twitter", "instagram", "tiktok", "snapchat", "reddit", "pinterest",
               "tumblr", "mastodon", "threads", "vk", "telegram", "discord", "quora", "myspace",
               "flickr", "weibo", "ok.ru", "clubhouse", "bluesky"],
    "coding": ["github", "gitlab", "bitbucket", "codepen", "replit", "stackoverflow", "leetcode",
               "hackerrank", "kaggle", "npm", "pypi", "dockerhub", "codeforces", "gitee",
               "sourceforge", "hackerone", "bugcrowd", "codewars"],
    "tech": ["hackernews", "producthunt", "habr", "slashdot", "dev.to", "hashnode"],
    "business": ["linkedin", "behance", "dribbble", "angel", "crunchbase", "upwork", "fiverr",
                 "freelancer", "about.me", "xing"],
    "gaming": ["steam", "twitch", "xbox", "playstation", "chess", "roblox", "minecraft", "itch",
               "gaming", "speedrun", "osu", "faceit", "epicgames", "gog"],
    "art": ["deviantart", "artstation", "500px", "pixiv", "cgsociety"],
    "music": ["spotify", "soundcloud", "bandcamp", "last.fm", "lastfm", "mixcloud", "genius",
              "audiojungle", "reverbnation"],
    "video": ["youtube", "vimeo", "dailymotion", "rumble", "bitchute"],
    "images": ["imgur", "unsplash", "gravatar", "giphy", "pexels"],
    "blog": ["medium", "wordpress", "blogger", "substack", "livejournal", "ghost", "hashnode",
             "wattpad", "blogspot"],
    "shopping": ["etsy", "ebay", "amazon", "poshmark", "depop", "gumroad"],
    "finance": ["patreon", "paypal", "venmo", "cashapp", "kofi", "ko-fi", "buymeacoffee", "tradingview"],
    "dating": ["tinder", "okcupid", "badoo", "bumble", "match.com"],
}


def norm_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def guess_category(name: str, url: str = "") -> str:
    hay = f"{name} {url}".lower()
    for cat, kws in _KW.items():
        if any(k in hay for k in kws):
            return cat
    return "other"


def row(engine, site, status, url, category="other", ms=None, note=""):
    return {"engine": engine, "site": site, "status": status, "url": url,
            "category": category, "ms": ms, "note": note}


ProgressFn = Callable[[int, int, str], None]


# ------------------------------------------------------------------ Sherlock
class SherlockEngine:
    name = "Sherlock"

    def __init__(self, include_nsfw=False):
        from sherlock_project.sites import SitesInformation
        self.sites = SitesInformation()
        if not include_nsfw:
            self.sites.remove_nsfw_sites()
        self.cat = {n: guess_category(n, i.url_home) for n, i in self.sites.sites.items()}

    def site_names(self, categories):
        return [n for n, c in self.cat.items() if c in categories]

    def run(self, username, names, timeout, progress: ProgressFn | None = None, proxy=None):
        from sherlock_project.notify import QueryNotify
        from sherlock_project.sherlock import sherlock

        data = {n: self.sites.sites[n].information for n in names if n in self.sites.sites}
        out, total = [], len(data)

        class N(QueryNotify):
            def update(s, r):
                out.append(row("Sherlock", r.site_name, str(r.status), r.site_url_user,
                               self.cat.get(r.site_name, "other"),
                               round(r.query_time * 1000) if r.query_time else None,
                               r.context or ""))
                if progress:
                    progress(len(out), total, r.site_name)

        if data:
            sherlock(username, data, N(), timeout=timeout, proxy=proxy or None)
        return out


# ------------------------------------------------------------------ WhatsMyName
class WMNEngine:
    name = "WhatsMyName"

    def __init__(self, include_nsfw=False):
        r = requests.get(WMN_URL, timeout=20, headers=UA)
        r.raise_for_status()
        sites = r.json()["sites"]
        self.sites = {s["name"]: s for s in sites if include_nsfw or s.get("cat") != NSFW_CAT}

    def site_names(self, categories):
        return [n for n, s in self.sites.items() if s.get("cat", "other") in categories]

    @staticmethod
    def _check(site, username, timeout, proxy):
        url = site["uri_check"].replace("{account}", username)
        pretty = site.get("uri_pretty", site["uri_check"]).replace("{account}", username)
        headers = {**UA, **(site.get("headers") or {})}
        body = site.get("post_body")
        proxies = {"http": proxy, "https": proxy} if proxy else None
        s = time.time()
        try:
            if body:
                r = requests.post(url, data=body.replace("{account}", username), headers=headers,
                                  timeout=timeout, proxies=proxies, allow_redirects=False)
            else:
                r = requests.get(url, headers=headers, timeout=timeout, proxies=proxies,
                                 allow_redirects=False)
        except requests.RequestException as e:
            return row("WhatsMyName", site["name"], "Unknown", pretty, site.get("cat", "other"),
                       note=type(e).__name__)
        ms = round((time.time() - s) * 1000)
        text = r.text or ""
        e_code, m_code = site.get("e_code"), site.get("m_code")
        e_str, m_str = site.get("e_string"), site.get("m_string")
        if r.status_code in (403, 429, 503) and r.status_code not in (e_code, m_code):
            status = "WAF"
        elif (m_str and m_str in text) or r.status_code == m_code:
            status = "Available"
        elif r.status_code == e_code and (not e_str or e_str in text):
            status = "Claimed"
        else:
            status = "Unknown"
        return row("WhatsMyName", site["name"], status, pretty, site.get("cat", "other"), ms)

    def run(self, username, names, timeout, progress: ProgressFn | None = None, proxy=None):
        out, total = [], len(names)
        with ThreadPoolExecutor(max_workers=40) as ex:
            futs = [ex.submit(self._check, self.sites[n], username, timeout, proxy)
                    for n in names if n in self.sites]
            for f in as_completed(futs):
                out.append(f.result())
                if progress:
                    progress(len(out), total, out[-1]["site"])
        return out


# ------------------------------------------------------------------ Maigret (optional, experimental)
def maigret_available() -> bool:
    return shutil.which("maigret") is not None


def run_maigret(username, timeout=15, progress: ProgressFn | None = None):
    """Run the Maigret CLI and parse its simple JSON report. Parsing is defensive."""
    if not maigret_available():
        return []
    with tempfile.TemporaryDirectory() as d:
        cmd = ["maigret", username, "--json", "simple", "--folderoutput", d, "--timeout",
               str(timeout), "--no-progressbar", "--no-color", "--no-recursion"]
        try:
            subprocess.run(cmd, capture_output=True, timeout=600, check=False)
        except Exception:
            return []
        out = []
        for fn in os.listdir(d):
            if not fn.endswith(".json"):
                continue
            try:
                data = json.load(open(os.path.join(d, fn), encoding="utf-8"))
            except Exception:
                continue
            for site, v in data.items():
                if not isinstance(v, dict):
                    continue
                st = v.get("status")
                st = st.get("status") if isinstance(st, dict) else st
                if st and "claim" not in str(st).lower():
                    continue
                url = v.get("url_user") or v.get("url_main", "")
                out.append(row("Maigret", site, "Claimed", url, guess_category(site, url)))
    if progress:
        progress(1, 1, "Maigret")
    return out


# ------------------------------------------------------------------ verification
SOFT_404 = ["page not found", "not found", "doesn't exist", "does not exist", "no such user",
            "user not found", "profile not found", "couldn't find", "could not find",
            "this account doesn't exist", "isn't available", "no longer available",
            "404 error", "nothing here", "account suspended", "page isn't available"]


def verify_one(url, timeout=10):
    try:
        r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return "unverified", "unreachable"
    if r.status_code in (403, 429, 503):
        return "unverified", f"HTTP {r.status_code}"
    if r.status_code >= 400:
        return "suspect", f"HTTP {r.status_code}"
    body = (r.text or "")[:30000].lower()
    title = re.search(r"<title[^>]*>(.*?)</title>", body, re.S)
    t = title.group(1) if title else ""
    if any(p in t for p in SOFT_404):
        return "suspect", "error title"
    if any(p in body for p in SOFT_404[:6]) and len(body) < 15000:
        return "suspect", "not-found text"
    if urlparse(url).path.strip("/") and not urlparse(r.url).path.strip("/"):
        return "suspect", "redirected to home"
    return "ok", ""


def verify_many(urls, timeout=10, workers=20, progress: ProgressFn | None = None):
    res, total = {}, len(urls)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(verify_one, u, timeout): u for u in urls}
        for i, f in enumerate(as_completed(futs), 1):
            res[futs[f]] = f.result()
            if progress:
                progress(i, total, futs[f])
    return res


# ------------------------------------------------------------------ merge + confidence
def merge(rows: list[dict], engines_run: list[str]):
    """Collapse per-engine rows into one record per site."""
    by = {}
    for r in rows:
        k = norm_key(r["site"])
        m = by.setdefault(k, {"site": r["site"], "category": "other", "url": r["url"],
                              "engines": {}, "ms": r["ms"]})
        m["engines"][r["engine"]] = r["status"]
        if r["category"] != "other" or m["category"] == "other":
            m["category"] = r["category"]
        if r["status"] == "Claimed":
            m["url"] = r["url"]
    out = []
    for m in by.values():
        sts = list(m["engines"].values())
        found_by = sts.count("Claimed")
        if found_by:
            status = "Found"
        elif sts and all(s == "Available" for s in sts):
            status = "Not found"
        else:
            status = "Unknown"
        out.append({"site": m["site"], "category": m["category"], "status": status, "url": m["url"],
                    "found_by": found_by, "checked_by": len(sts),
                    "engines": ", ".join(e for e, s in m["engines"].items() if s == "Claimed"),
                    "verify": "", "confidence": ""})
    return out


def score(records: list[dict]):
    for r in records:
        if r["status"] != "Found":
            r["confidence"] = ""
            continue
        if r["verify"] == "suspect":
            r["confidence"] = "Low"
        elif r["found_by"] >= 2:
            r["confidence"] = "High"
        elif r["verify"] == "ok":
            r["confidence"] = "Medium"
        else:
            r["confidence"] = "Low"
    return records


# ------------------------------------------------------------------ orchestrator
def run_username(username, engines: dict, categories, timeout, retry_unknown=True, verify=True,
                 proxy=None, extra_maigret=False, only=None, progress: ProgressFn | None = None):
    """engines: {'Sherlock': SherlockEngine, 'WhatsMyName': WMNEngine}. Returns merged records."""
    rows, ran = [], []
    n_steps = len(engines) + (1 if extra_maigret else 0) + (1 if verify else 0)
    step = [0]

    def sub(label):
        def f(done, total, name):
            if progress:
                progress((step[0] + done / max(total, 1)) / n_steps, f"{label}: {done}/{total} - {name}")
        return f

    for label, eng in engines.items():
        names = eng.site_names(categories)
        if only:
            names = [n for n in names if norm_key(n) in only]
        res = eng.run(username, names, timeout, sub(label), proxy)
        if retry_unknown:
            bad = [r["site"] for r in res if r["status"] in ("Unknown", "WAF")]
            if bad:
                again = eng.run(username, bad, timeout * 2, None, proxy)
                keep = {r["site"]: r for r in again}
                res = [keep.get(r["site"], r) if r["site"] in bad else r for r in res]
        rows += res
        ran.append(label)
        step[0] += 1
    if extra_maigret:
        rows += run_maigret(username, timeout, sub("Maigret"))
        ran.append("Maigret")
        step[0] += 1
    recs = merge(rows, ran)
    if verify:
        found = [r for r in recs if r["status"] == "Found"][:200]
        res = verify_many([r["url"] for r in found], progress=sub("Verifying"))
        for r in found:
            r["verify"], r["verify_note"] = res.get(r["url"], ("unverified", ""))
    return score(recs), ran


# ------------------------------------------------------------------ username variants
def variants(full_name: str):
    parts = [p for p in re.split(r"[\s._-]+", full_name.lower().strip()) if p]
    if not parts:
        return []
    if len(parts) == 1:
        return [parts[0]]
    f, l = parts[0], parts[-1]
    cands = [f + l, f + "." + l, f + "_" + l, f + "-" + l, f[0] + l, f + l[0], l + f,
             l + "." + f, f[0] + "." + l, f + l + "1", f + "_" + l + "_"]
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out
