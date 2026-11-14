"""Username search engines (Sherlock, WhatsMyName, optional Maigret), merge and verify logic."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable
from urllib.parse import urlparse

import requests

from .models import (CONF_ORDER, Conf, Found, Raw, Stage, StageError, Verify)  # noqa: F401

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


def row(engine, site, status, url, category="other", ms=None, note="", attempts=1):
    return {"engine": engine, "site": site, "status": Raw.parse(status), "url": url,
            "category": category, "ms": ms, "note": note, "attempts": attempts}


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
def wmn_classify(site: dict, status_code: int, text: str) -> Raw:
    """Decision table for one WhatsMyName response. Precedence (first match wins):

    1. WAF        - 403/429/503 that the site's own e_code/m_code do not account for
    2. AVAILABLE  - m_string present, or status == m_code (only when m_code differs from e_code)
    3. CLAIMED    - status == e_code and (no e_string, or e_string present)
    4. UNKNOWN    - anything else
    Absence outranks presence so that a page matching both rules is never reported as a found account.
    """
    e_code, m_code = site.get("e_code"), site.get("m_code")
    e_str, m_str = site.get("e_string"), site.get("m_string")
    if status_code in (403, 429, 503) and status_code not in (e_code, m_code):
        return Raw.WAF
    absent = bool(m_str and m_str in text) or (m_code is not None and m_code != e_code and status_code == m_code)
    if absent:
        return Raw.AVAILABLE
    if status_code == e_code and (not e_str or e_str in text):
        return Raw.CLAIMED
    return Raw.UNKNOWN


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
            return row("WhatsMyName", site["name"], Raw.UNKNOWN, pretty, site.get("cat", "other"),
                       note=type(e).__name__)
        ms = round((time.time() - s) * 1000)
        status = wmn_classify(site, r.status_code, r.text or "")
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
    import importlib.util
    return importlib.util.find_spec("maigret") is not None


def run_maigret(username, timeout=15, progress: ProgressFn | None = None, proxy=None):
    """Run the Maigret CLI and parse its simple JSON report. Parsing is defensive."""
    if not maigret_available():
        return []
    with tempfile.TemporaryDirectory() as d:
        cmd = [sys.executable, "-m", "maigret", username, "--json", "simple", "--folderoutput", d, "--timeout",
               str(timeout), "--no-progressbar", "--no-color", "--no-recursion"]
        if proxy:
            cmd += ["--proxy", proxy]
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
                out.append(row("Maigret", site, Raw.CLAIMED, url, guess_category(site, url)))
    if progress:
        progress(1, 1, "Maigret")
    return out


# ------------------------------------------------------------------ verification
SOFT_404 = ["page not found", "not found", "doesn't exist", "does not exist", "no such user",
            "user not found", "profile not found", "couldn't find", "could not find",
            "this account doesn't exist", "isn't available", "no longer available",
            "404 error", "nothing here", "account suspended", "page isn't available"]


def verify_one(url, timeout=10, proxy=None):
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True, proxies=proxies)
    except requests.RequestException:
        return Verify.UNVERIFIED, "unreachable"
    if r.status_code in (403, 429, 503):
        return Verify.UNVERIFIED, f"HTTP {r.status_code}"
    if r.status_code >= 400:
        return Verify.SUSPECT, f"HTTP {r.status_code}"
    body = (r.text or "")[:30000].lower()
    title = re.search(r"<title[^>]*>(.*?)</title>", body, re.S)
    t = title.group(1) if title else ""
    if any(p in t for p in SOFT_404):
        return Verify.SUSPECT, "error title"
    if any(p in body for p in SOFT_404[:6]) and len(body) < 15000:
        return Verify.SUSPECT, "not-found text"
    if urlparse(url).path.strip("/") and not urlparse(r.url).path.strip("/"):
        return Verify.SUSPECT, "redirected to home"
    return Verify.OK, ""


def verify_many(urls, timeout=10, workers=20, progress: ProgressFn | None = None, proxy=None):
    res, total = {}, len(urls)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(verify_one, u, timeout, proxy): u for u in urls}
        for i, f in enumerate(as_completed(futs), 1):
            res[futs[f]] = f.result()
            if progress:
                progress(i, total, futs[f])
    return res


# ------------------------------------------------------------------ merge -> verify -> score
def merge(rows: list[dict], engines_run: list[str]):
    """Collapse per-engine rows into one record per site. Output stage: MERGED."""
    by = {}
    for r in rows:
        k = norm_key(r["site"])
        m = by.setdefault(k, {"site": r["site"], "category": "other", "url": r["url"],
                              "engines": {}, "ms": r["ms"], "attempts": 1})
        m["engines"][r["engine"]] = Raw.parse(r["status"])
        m["attempts"] = max(m["attempts"], r.get("attempts", 1))
        if r["category"] != "other" or m["category"] == "other":
            m["category"] = r["category"]
        if Raw.parse(r["status"]) is Raw.CLAIMED:
            m["url"] = r["url"]
    out = []
    for m in by.values():
        sts = list(m["engines"].values())
        found_by = sts.count(Raw.CLAIMED)
        if found_by:
            status = Found.FOUND
        elif sts and all(x is Raw.AVAILABLE for x in sts):
            status = Found.NOT_FOUND
        else:
            status = Found.UNKNOWN
        out.append({"site": m["site"], "category": m["category"], "status": status, "url": m["url"],
                    "found_by": found_by, "checked_by": len(sts), "attempts": m["attempts"],
                    "engines": ", ".join(e for e, x in m["engines"].items() if x is Raw.CLAIMED),
                    "verify": Verify.NOT_RUN, "verify_note": "", "confidence": Conf.NONE,
                    "stage": Stage.MERGED})
    return out


def _require(records, stage):
    bad = [r["site"] for r in records if r.get("stage") != stage]
    if bad:
        raise StageError(f"expected stage '{stage}', got a different stage for {len(bad)} record(s), e.g. {bad[0]}")


def apply_verification(records: list[dict], results: dict | None):
    """MERGED -> VERIFIED. results maps url -> (Verify, note); None means verification was switched off.

    Found accounts that have no result (beyond the verification cap) are marked SKIPPED rather than left blank.
    """
    _require(records, Stage.MERGED)
    for r in records:
        if r["status"] != Found.FOUND:
            r["verify"] = Verify.NOT_RUN
        elif results is None:
            r["verify"], r["verify_note"] = Verify.NOT_RUN, ""
        elif r["url"] in results:
            state, note = results[r["url"]]
            r["verify"], r["verify_note"] = Verify(state), note
        else:
            r["verify"], r["verify_note"] = Verify.SKIPPED, "beyond verification cap"
        r["stage"] = Stage.VERIFIED
    return records


def confidence_for(status, found_by: int, verify) -> Conf:
    """Explicit decision table (first match wins):

    not found                   -> NONE
    verify == SUSPECT           -> LOW
    found by 2+ engines         -> HIGH
    verify == OK                -> MEDIUM
    anything else (single engine, verification off / skipped / blocked) -> UNVERIFIED
    """
    if status != Found.FOUND:
        return Conf.NONE
    if verify == Verify.SUSPECT:
        return Conf.LOW
    if found_by >= 2:
        return Conf.HIGH
    if verify == Verify.OK:
        return Conf.MEDIUM
    return Conf.UNVERIFIED


def score(records: list[dict]):
    """VERIFIED -> SCORED."""
    _require(records, Stage.VERIFIED)
    for r in records:
        r["confidence"] = confidence_for(r["status"], r["found_by"], r["verify"])
        r["stage"] = Stage.SCORED
    return records


# ------------------------------------------------------------------ orchestrator
def run_username(username, engines: dict, categories, timeout, retry_unknown=True, verify=True,
                 proxy=None, extra_maigret=False, only=None, progress: ProgressFn | None = None,
                 verify_cap: int = 300):
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
            bad = {r["site"] for r in res if r["status"] in (Raw.UNKNOWN, Raw.WAF)}
            if bad:
                again = {r["site"]: r for r in eng.run(username, sorted(bad), timeout * 2, None, proxy)}
                merged_rows = []
                for r in res:
                    if r["site"] in bad:
                        n = again.get(r["site"])
                        r = n if n is not None and Raw.parse(n["status"]).definitive else r
                        r = {**r, "attempts": 2}
                    merged_rows.append(r)
                res = merged_rows
        rows += res
        ran.append(label)
        step[0] += 1
    if extra_maigret:
        rows += run_maigret(username, timeout, sub("Maigret"), proxy)
        ran.append("Maigret")
        step[0] += 1
    recs = merge(rows, ran)
    results = None
    if verify:
        targets = [r for r in recs if r["status"] == Found.FOUND][:verify_cap]
        results = verify_many([r["url"] for r in targets], progress=sub("Verifying"), proxy=proxy)
    apply_verification(recs, results)
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
