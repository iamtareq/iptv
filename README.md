# LiveTV channels — auto-updated playlist

A tiny, **free** pipeline that merges maintained IPTV sources into one `playlist.m3u`
and refreshes it **daily via GitHub Actions** — no server, no cost.

## How it works
- `sources.txt` — the M3U sources to merge (default: iptv-org BD / Bengali / news / sports).
- `build_channels.py` — fetches each source, merges + dedupes, keeps DRM (`#KODIPROP`) and
  header (`#EXTVLCOPT`) lines, writes `playlist.m3u`.
- `.github/workflows/update.yml` — runs the script daily (and on demand) and commits the result.

## One-time setup (free)
1. Create a GitHub repo (public is fine — **no credentials here**) and push these files.
2. GitHub → **Actions** tab → enable workflows. (Optional: click **Run workflow** once.)
3. Use the playlist URL. **jsDelivr CDN is recommended** (faster global CDN, and it works on
   networks that throttle `raw.githubusercontent.com` — e.g. some Bangladeshi ISPs):
   ```
   https://cdn.jsdelivr.net/gh/<you>/<repo>@main/playlist.m3u
   ```
   (The plain GitHub raw URL `https://raw.githubusercontent.com/<you>/<repo>/main/playlist.m3u`
   also works on most networks.)
4. In the app: **⋮ → 🔗 Channel source URL** → paste the URL → **Save & Load**.

That's it — the app caches it and you get a fresh, maintained list every day.

## Customise
Edit `sources.txt` (add any M3U URL, e.g. your country/category from iptv-org, or another
playlist). Commit → the Action rebuilds `playlist.m3u`.

## ⚠️ Do NOT put paid IPTV credentials here
A paid Xtream/IPTV login (server + username + password) must **never** go in a public repo —
it would be stolen and your subscription banned. Enter that in the app instead
(**⋮ → 📡 Xtream / IPTV login**); it stays only on your device.

## Geo note
GitHub's runners are in the US/EU, so this merge reflects what those sources publish, not a
per-device reachability test. Some country-locked channels may still need your local network.
For the most reliable channels, use your paid Xtream subscription.
