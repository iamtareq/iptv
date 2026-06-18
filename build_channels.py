#!/usr/bin/env python3
"""
Merge several maintained M3U sources into one deduped playlist.m3u.
Lossless: keeps #EXTINF / #KODIPROP (DRM) / #EXTVLCOPT (headers) lines per channel,
so the app's parser still gets categories, logos, DRM keys and headers.

Run locally (`python build_channels.py`) or in GitHub Actions (see .github/workflows/update.yml).
"""
import os, re, ssl, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = os.path.join(HERE, "sources.txt")
OUTPUT = os.path.join(HERE, "playlist.m3u")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45, context=ctx) as r:
        return r.read().decode("utf-8", "replace")


def main():
    sources = [l.strip() for l in open(SOURCES, encoding="utf-8")
               if l.strip() and not l.strip().startswith("#")]
    seen = set()
    out = ["#EXTM3U"]
    total = kept = 0

    for src in sources:
        try:
            text = fetch(src)
        except Exception as e:
            print(f"  FAIL {src}: {e}")
            continue
        buf = []  # directive lines (#EXTINF/#KODIPROP/#EXTVLCOPT) for the current entry
        n = 0
        for raw in text.split("\n"):
            line = raw.rstrip("\r")
            s = line.strip()
            if not s or s.startswith("#EXTM3U"):
                continue
            if s.startswith("#"):
                buf.append(line)
            else:
                total += 1
                key = s.split("|")[0].strip()  # ignore pipe headers when deduping
                if key and key not in seen:
                    seen.add(key)
                    out.extend(buf)
                    out.append(line)
                    kept += 1
                    n += 1
                buf = []
        print(f"  {n:6} unique from {src}")

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"\nMerged {kept} unique channels (from {total} entries) -> playlist.m3u")


if __name__ == "__main__":
    main()
