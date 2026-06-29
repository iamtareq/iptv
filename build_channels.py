#!/usr/bin/env python3
"""
Build playlist.m3u from maintained M3U sources, then AGGRESSIVELY health-check every
stream and keep only the ones that respond. Runs entirely in GitHub Actions (no PC needed).

Pruning logic (geo-aware so we don't wrongly drop country-locked channels):
  - removed: connection errors, DNS failures, timeouts, 404, 5xx, or non-stream responses
  - kept:    2xx OR a playlist/manifest body
  - kept:    401/403/451 (access/geo) UNLESS STRICT=1  (these may still work from your country)

Env knobs (set in the workflow if you like):
  CHECK_TIMEOUT (default 8)   per-request seconds
  CHECK_WORKERS (default 50)  concurrent checks
  STRICT       (default 0)    1 = also drop geo-blocked (403/451)
"""
import os, re, ssl, json, urllib.request, urllib.error, concurrent.futures

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = os.path.join(HERE, "sources.txt")
OUTPUT = os.path.join(HERE, "playlist.m3u")

# Toffee (Bangladesh) channels come as JSON with per-channel auth headers (cookie/UA/Host).
# The Edge-Cache-Cookie is short-lived, so we RE-FETCH every run to keep it fresh.
# Toffee CDN is geo-locked to BD, so these are NOT health-checked from CI (would 403) —
# we embed the headers via #EXTHTTP and append them unconditionally.
TOFFEE_JSON_SOURCES = [
    "https://raw.githubusercontent.com/BINOD-XD/Toffee-Auto-Update-Playlist/main/toffee_channel_data.json",
]

# BD-local / BDIX M3U sources: reachable only from inside Bangladesh, so CI's US/EU health-check
# would wrongly mark them dead. We parse + dedupe them like normal M3U but APPEND without checking
# (they work from the user's BD connection). Some may be dead — heart the working ones in-app.
UNCHECKED_M3U_SOURCES = [
    "https://raw.githubusercontent.com/abusaeeidx/Mrgify-BDIX-IPTV/main/playlist.m3u",
    "https://raw.githubusercontent.com/abusaeeidx/CrestSport-IPTV-Collection/main/bd.m3u",
]

# ClubBD — BD sports/entertainment portal. Its numbered webplayer URLs 302-redirect to fresh
# tokenized HLS (ExoPlayer follows the redirect each load, so the stable clubbd URL is what we
# store). Channel list is scraped live each run; streams are BD geo-locked → appended unchecked.
CLUBBD_TV_URL = "http://www.clubbd.com/tv/"
CLUBBD_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

TIMEOUT = int(os.environ.get("CHECK_TIMEOUT", "8"))
WORKERS = int(os.environ.get("CHECK_WORKERS", "50"))
RETRIES = int(os.environ.get("CHECK_RETRIES", "2"))   # tries before marking dead (reduces flapping)
STRICT = os.environ.get("STRICT", "0") == "1"

ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
DEFAULT_UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"


def fetch(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read().decode("utf-8", "replace")


def file_sources():
    return [l.strip() for l in open(SOURCES, encoding="utf-8")
            if l.strip() and not l.strip().startswith("#")]


def load_entries(sources, seen=None):
    """Merge + dedupe sources. Each entry keeps its raw directive lines + extracted test headers.
    Pass a shared `seen` set to dedupe across multiple load_entries() calls."""
    if seen is None:
        seen = set()
    entries = []
    for src in sources:
        try:
            text = fetch(src)
        except Exception as e:
            print(f"  FAIL fetch {src}: {e}")
            continue
        buf, n = [], 0
        for raw in text.split("\n"):
            line = raw.rstrip("\r")
            s = line.strip()
            if not s or s.startswith("#EXTM3U"):
                continue
            if s.startswith("#"):
                buf.append(line)
            else:
                key = s.split("|")[0].strip()
                if key and key not in seen:
                    seen.add(key)
                    headers = {}
                    for d in buf:
                        ds = d.strip().lower()
                        if ds.startswith("#extvlcopt:http-user-agent="):
                            headers["User-Agent"] = d.split("=", 1)[1]
                        elif ds.startswith("#extvlcopt:http-referrer="):
                            headers["Referer"] = d.split("=", 1)[1]
                    if "|" in s:
                        for kv in s.split("|", 1)[1].split("&"):
                            if "=" in kv:
                                k, v = kv.split("=", 1)
                                if k.lower() == "user-agent":
                                    headers["User-Agent"] = v
                                elif k.lower() in ("referer", "referrer"):
                                    headers["Referer"] = v
                    entries.append({"lines": buf, "urlline": line, "url": key, "headers": headers})
                    n += 1
                buf = []
        print(f"  {n:6} unique from {src}")
    return entries


def load_toffee_entries():
    """Fetch Toffee JSON sources and turn each channel into an M3U entry whose auth
    headers (cookie/user-agent/Host) are embedded via #EXTHTTP so the app can play it."""
    entries, seen = [], set()
    for src in TOFFEE_JSON_SOURCES:
        try:
            data = json.loads(fetch(src))
        except Exception as e:
            print(f"  FAIL toffee {src}: {e}")
            continue
        n = 0
        for ch in data.get("channels", []):
            link = (ch.get("link") or "").strip()
            if not link or link in seen:
                continue
            out_h = {}
            for k, v in (ch.get("headers") or {}).items():
                if not v or v == "YOUR_CLIENT_API_HEADER":
                    continue
                if k.lower() in ("cookie", "user-agent"):  # cookie+UA suffice; Host is restricted in ExoPlayer
                    out_h[k.lower()] = v
            seen.add(link)
            name = (ch.get("name") or "Channel").strip()
            logo = (ch.get("logo") or "").strip()
            lines = [f'#EXTINF:-1 tvg-logo="{logo}" group-title="Toffee",{name}']
            if out_h:
                lines.append("#EXTHTTP:" + json.dumps(out_h, ensure_ascii=False))
            entries.append({"lines": lines, "urlline": link, "url": link, "headers": {}})
            n += 1
        print(f"  {n:6} toffee channels from {src}")
    return entries


def load_clubbd_entries():
    """Scrape clubbd.com/tv/'s JS channel array (name/url pairs) into M3U entries with a
    browser User-Agent embedded via #EXTHTTP. Appended without health-check (BD geo-locked)."""
    entries, seen = [], set()
    try:
        html = fetch(CLUBBD_TV_URL)
    except Exception as e:
        print(f"  FAIL clubbd {CLUBBD_TV_URL}: {e}")
        return entries
    ua = "#EXTHTTP:" + json.dumps({"user-agent": CLUBBD_UA}, ensure_ascii=False)
    pat = re.compile(r"""name:\s*['"]([^'"]+)['"]\s*,\s*url:\s*['"]([^'"]+)['"]""", re.I)
    n = 0
    for m in pat.finditer(html):
        name, url = m.group(1).strip(), m.group(2).strip()
        if not url or ".m3u8" not in url.lower() or url in seen:
            continue
        seen.add(url)
        entries.append({"lines": [f'#EXTINF:-1 group-title="ClubBD",{name}', ua],
                        "urlline": url, "url": url, "headers": {}})
        n += 1
    print(f"  {n:6} clubbd channels from {CLUBBD_TV_URL}")
    return entries


def check(entry):
    h = {"User-Agent": entry["headers"].get("User-Agent", DEFAULT_UA)}
    if "Referer" in entry["headers"]:
        h["Referer"] = entry["headers"]["Referer"]
    # Retry before declaring dead, so a transient blip doesn't drop a working channel (anti-flap).
    for _ in range(RETRIES):
        try:
            req = urllib.request.Request(entry["url"], headers=h)
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
                data = r.read(2048).decode("utf-8", "replace")
                if any(t in data for t in ("#EXTM3U", "#EXTINF", "#EXT-X", "<MPD", "<?xml")):
                    return "alive"
                return "alive" if 200 <= r.status < 400 else "dead"
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 451):
                return "geo"
            # 404 / 5xx — could be transient, so retry
        except Exception:
            pass  # timeout / connection error — retry
    return "dead"


def main():
    seen = set()
    entries = load_entries(file_sources(), seen)
    print(f"\nHealth-checking {len(entries)} channels (timeout={TIMEOUT}s, workers={WORKERS}, strict={STRICT})...")
    statuses = [None] * len(entries)
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(check, e): i for i, e in enumerate(entries)}
        for f in concurrent.futures.as_completed(futs):
            statuses[futs[f]] = f.result()

    alive = statuses.count("alive")
    geo = statuses.count("geo")
    dead = statuses.count("dead")

    out, kept = ["#EXTM3U"], 0
    for e, s in zip(entries, statuses):
        if s == "alive" or (s == "geo" and not STRICT):
            out.extend(e["lines"]); out.append(e["urlline"]); kept += 1

    # BD-local / BDIX M3U (unreachable from CI) — appended without health-check.
    unchecked = load_entries(UNCHECKED_M3U_SOURCES, seen)
    for e in unchecked:
        out.extend(e["lines"]); out.append(e["urlline"])

    # Toffee (BD geo-locked, auth via #EXTHTTP) — appended without CI health-check.
    toffee = load_toffee_entries()
    for e in toffee:
        out.extend(e["lines"]); out.append(e["urlline"])

    # ClubBD (BD geo-locked sports/entertainment, scraped live) — appended without CI health-check.
    clubbd = load_clubbd_entries()
    for e in clubbd:
        out.extend(e["lines"]); out.append(e["urlline"])

    extra = len(unchecked) + len(toffee) + len(clubbd)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"\nalive={alive}  geo/access={geo}  dead={dead}")
    print(f"KEPT {kept} health-checked + {len(unchecked)} bd-local + {len(toffee)} toffee "
          f"+ {len(clubbd)} clubbd -> {kept + extra} total -> playlist.m3u")


if __name__ == "__main__":
    main()
