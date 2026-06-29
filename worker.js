/**
 * IPTV stream proxy for Cloudflare Workers (free).
 * Lets the HOSTED player (https://iamtareq.github.io/iptv/) play channels that a browser
 * can't fetch directly — CORS-blocked streams, and (geo permitting) Toffee.
 *
 * Deploy (free, ~3 min, no card):
 *   dash.cloudflare.com → Workers & Pages → Create → Worker → paste this → Deploy.
 *   You'll get https://<name>.<acct>.workers.dev — set that as the player's proxy:
 *   open  https://iamtareq.github.io/iptv/?proxy=https://<name>.<acct>.workers.dev
 *   (or ⋮ Settings → Toffee proxy origin). It's remembered after the first time.
 *
 * Reality check: Toffee's CDN is geo-locked to Bangladesh. A Worker egresses from a
 * Cloudflare IP (not a BD residential IP), so Toffee may still 403. CORS-blocked
 * non-Toffee channels always work through this. For GUARANTEED Toffee, expose your local
 * proxy.js via a tunnel instead (see README).
 */

const TOFFEE_JSON = 'https://raw.githubusercontent.com/BINOD-XD/Toffee-Auto-Update-Playlist/main/toffee_channel_data.json';
const DEFAULT_UA  = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36';

// Best-effort in-isolate cache for the short-lived Toffee cookie.
let auth = { cookie: null, ua: 'okhttp/4.11.0', ts: 0 };

const cors = (extra = {}) => ({
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, OPTIONS',
  'Access-Control-Allow-Headers': '*',
  ...extra,
});

async function toffeeAuth() {
  if (auth.cookie && Date.now() - auth.ts < 30 * 60 * 1000) return auth;   // refresh ≤ every 30 min
  try {
    const j = await (await fetch(TOFFEE_JSON, { headers: { 'User-Agent': 'curl/7' } })).json();
    const h = (j.channels && j.channels[0] && j.channels[0].headers) || {};
    auth = { cookie: h.cookie || auth.cookie, ua: h['user-agent'] || auth.ua, ts: Date.now() };
  } catch (_) { /* keep previous */ }
  return auth;
}

export default {
  async fetch(request) {
    const url = new URL(request.url);

    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors() });
    if (url.pathname === '/health') return new Response(JSON.stringify({ ok: true }), { headers: cors({ 'Content-Type': 'application/json' }) });

    const target = url.searchParams.get('url');
    if (!target) return new Response('Missing ?url=', { status: 400, headers: cors() });
    if (!/^https?:\/\//i.test(target)) return new Response('Only http(s) targets', { status: 400, headers: cors() });

    const isToffee = target.includes('toffeelive.com');
    const headers = { 'User-Agent': DEFAULT_UA, 'Accept-Encoding': 'identity' };
    if (isToffee) {
      const a = await toffeeAuth();
      headers['User-Agent'] = a.ua || DEFAULT_UA;
      if (a.cookie) headers['Cookie'] = a.cookie;
    }

    let upstream;
    try {
      upstream = await fetch(target, { headers, redirect: 'follow', cf: { cacheTtl: 0 } });
    } catch (e) {
      return new Response('Upstream error: ' + e, { status: 502, headers: cors() });
    }
    if (upstream.status >= 400) return new Response('Upstream ' + upstream.status, { status: upstream.status, headers: cors() });

    const ct = upstream.headers.get('content-type') || '';
    const isM3U8 = /\.m3u8|playlist|master/i.test(target) || ct.includes('mpegurl');

    if (isM3U8) {
      const body = await upstream.text();
      const self = `${url.origin}/proxy?url=`;
      const viaProxy = (uri) => { try { return self + encodeURIComponent(new URL(uri, target).href); } catch { return null; } };
      const rewritten = body.split('\n').map(line => {
        const l = line.trim();
        if (!l) return line;
        if (l.startsWith('#')) {
          // Also proxy URI="..." in tag lines (esp. #EXT-X-KEY / #EXT-X-MAP) so encrypted
          // streams can fetch their AES key through us instead of off-proxy (→ decrypt OK).
          return l.includes('URI="')
            ? line.replace(/URI="([^"]+)"/g, (m, uri) => { const p = uri.startsWith('data:') ? null : viaProxy(uri); return p ? `URI="${p}"` : m; })
            : line;
        }
        const p = viaProxy(l);
        return p || line;
      }).join('\n');
      return new Response(rewritten, { headers: cors({ 'Content-Type': 'application/vnd.apple.mpegurl', 'Cache-Control': 'no-cache, no-store, must-revalidate' }) });
    }

    // segment / key / other → stream straight through
    return new Response(upstream.body, { headers: cors({ 'Content-Type': ct || 'video/mp2t' }) });
  },
};
