# LiveTV channels — auto-updated playlist

A tiny, **free** pipeline that merges maintained IPTV sources into one `playlist.m3u`
and refreshes it **daily via GitHub Actions** — no server, no cost.

## 🌐 Web player (open in a browser)

A browser-based player lives in [`web/`](web/) and is auto-deployed to GitHub Pages:

> **https://iamtareq.github.io/iptv/**

Open the link and start watching — it loads this same `playlist.m3u`. **Most channels just play**
in the hosted page: they send permissive CORS headers, so the browser streams them directly with
zero setup. (Dead/geo-locked channels and ones that lack CORS won't play — that's normal.)

### Want Toffee + the CORS-blocked channels too? Run it locally

A browser can't let the **hosted HTTPS** page talk to a proxy on your own machine
(Private-Network / mixed-content security). So for full coverage, run the player **locally**,
where `proxy.js` also serves the player — one origin, no CORS issues:

```bash
node proxy.js          # then open http://localhost:8889
```

Now Toffee and the CORS-blocked channels route through the proxy automatically. (Or just use the
**Android app** — native players ignore CORS, so everything works there.)

- **Settings (`⋮`)** — set a custom **Channel source URL** (any `.m3u`) or a different **Toffee proxy origin**. Saved on your device.

### Make the *hosted* link play Toffee + CORS channels (point it at a proxy)

The hosted page can use any HTTPS proxy you give it — wire it with a link (remembered after):

```
https://iamtareq.github.io/iptv/?proxy=https://YOUR-PROXY
```

Two ways to get that `YOUR-PROXY`:

| Option | Toffee? | CORS-blocked? | PC on? | Setup |
|---|---|---|---|---|
| **A. Cloudflare Worker** ([`worker.js`](worker.js)) | ⚠️ geo gamble | ✅ yes | ❌ no | deploy once, free |
| **B. Tunnel to your local `proxy.js`** | ✅ **reliable** | ✅ yes | ✅ yes | run 2 commands |

**A — Cloudflare Worker** (always-on, no PC; but Toffee may 403). At dash.cloudflare.com →
*Workers & Pages → Create → Worker*, paste [`worker.js`](worker.js), Deploy. You get
`https://<name>.workers.dev`. Open `…/iptv/?proxy=https://<name>.workers.dev`. This always fixes
CORS-blocked channels; Toffee works only if Cloudflare's egress is seen as Bangladesh (not guaranteed —
its egress is a Cloudflare IP, not a BD one).

**B — Tunnel** (guaranteed Toffee, because the fetch happens on your BD machine). Run the local
proxy, then expose it over HTTPS with a free [cloudflared](https://developers.cloudflare.com/cloudflare-tunnel/)
quick tunnel (no account):

```bash
node proxy.js
cloudflared tunnel --url http://localhost:8889      # prints https://<random>.trycloudflare.com
```

Then open `…/iptv/?proxy=https://<random>.trycloudflare.com`. Now the hosted link plays everything,
including Toffee — as long as your PC + tunnel are running. (Use a **named** tunnel for a stable URL.)

> Why no fully-hosted Toffee: Toffee's CDN is geo-locked to Bangladesh (403 to non-BD IPs), so the
> proxy must sit on a BD IP. No free cloud host has one — only your own BD machine does.

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
