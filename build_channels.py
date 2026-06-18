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
import os, re, ssl, urllib.request, urllib.error, concurrent.futures

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = os.path.join(HERE, "sources.txt")
OUTPUT = os.path.join(HERE, "playlist.m3u")

TIMEOUT = int(os.environ.get("CHECK_TIMEOUT", "8"))
WORKERS = int(os.environ.get("CHECK_WORKERS", "50"))
STRICT = os.environ.get("STRICT", "0") == "1"

ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
DEFAULT_UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"


def fetch(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read().decode("utf-8", "replace")


def load_entries():
    """Merge + dedupe sources. Each entry keeps its raw directive lines + extracted test headers."""
    sources = [l.strip() for l in open(SOURCES, encoding="utf-8")
               if l.strip() and not l.strip().startswith("#")]
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


def check(entry):
    h = {"User-Agent": entry["headers"].get("User-Agent", DEFAULT_UA)}
    if "Referer" in entry["headers"]:
        h["Referer"] = entry["headers"]["Referer"]
    try:
        req = urllib.request.Request(entry["url"], headers=h)
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            data = r.read(2048).decode("utf-8", "replace")
            if any(t in data for t in ("#EXTM3U", "#EXTINF", "#EXT-X", "<MPD", "<?xml")):
                return "alive"
            return "alive" if 200 <= r.status < 400 else "dead"
    except urllib.error.HTTPError as e:
        return "geo" if e.code in (401, 403, 451) else "dead"
    except Exception:
        return "dead"


def main():
    entries = load_entries()
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

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"\nalive={alive}  geo/access={geo}  dead={dead}")
    print(f"KEPT {kept} of {len(entries)} channels -> playlist.m3u")


if __name__ == "__main__":
    main()
