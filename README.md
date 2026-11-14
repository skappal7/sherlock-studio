# Sherlock Studio

Digital footprint audit app built with Streamlit on top of
[Sherlock](https://github.com/sherlock-project/sherlock) and
[WhatsMyName](https://github.com/WebBreacher/WhatsMyName).

## Features
- **Username**: Sherlock + WhatsMyName (+ Maigret if installed), merged per site; category presets and filters;
  second-request verification; High/Medium/Low confidence; retry of timeouts; batch CSV (max 5);
  name-to-username variants; exposure index; card/table views; CSV, Excel, JSON and HTML report export.
- **Brand check**: taken / free grid across ~40 major platforms.
- **Email**: MX, Gravatar, Have I Been Pwned breaches (API key), optional Holehe site registrations.
- **Domain**: RDAP, DNS, SPF/DMARC/CAA grading, security headers, subdomains (crt.sh), Wayback history.
- **Search links**: ready-made search and reverse-image links (nothing scraped).
- **History**: local SQLite, run-to-run diff, audit log.
- **Guardrails**: consent gate, purpose log, per-session rate limit. No people-search, phone-to-address or geolocation.

Requires Python 3.11 or newer (3.12 recommended).

## Run locally
    pip install -r requirements.txt
    streamlit run app.py
    # optional engines
    pip install -r requirements-optional.txt

## Deploy on Streamlit Community Cloud
1. Push this repo to GitHub.
2. share.streamlit.io > New app > pick the repo, branch `main`, main file `app.py`.
3. Optional secrets (App settings > Secrets):

       HIBP_API_KEY = "..."
       RATE_LIMIT = 20

Notes: history storage is ephemeral on Cloud; Tor is unavailable; shared hosts get blocked or rate-limited by more
sites than a home connection. Optional engines (Maigret, Holehe) add heavy dependencies; install them only if needed.

## Tests
    pip install pytest && python -m pytest -q tests

## Responsible use
For self-audit, brand protection and security review. Detections are automated and can be wrong. Do not use this
to harass, stalk or profile private individuals. Check the Sherlock and WhatsMyName licenses before redistributing.
