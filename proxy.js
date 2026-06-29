/**
 * Toffee Stream Proxy Server — Fixed
 * - Host header: derived from actual target URL (not hardcoded)
 * - SSL: verification disabled (Toffee CDN uses mixed hostnames)
 * - M3U8 rewriting: segments + sub-playlists routed through proxy
 * - ALSO serves the web player at http://localhost:8889 so everything (incl. Toffee)
 *   runs same-origin with no CORS / Private-Network / mixed-content issues.
 * Run: node proxy.js  → open http://localhost:8889
 */

const http  = require('http');
const https = require('https');
const url   = require('url');
const fs    = require('fs');
const path  = require('path');

// The player lives in web/index.html next to this file.
const PLAYER_HTML = path.join(__dirname, 'web', 'index.html');

const PORT = 8889;
const TOFFEE_JSON = 'https://raw.githubusercontent.com/BINOD-XD/Toffee-Auto-Update-Playlist/main/toffee_channel_data.json';

// Resolve relative URLs against a base, collapsing any ../ segments
function resolveUrl(base, relative) {
  if (relative.startsWith('http://') || relative.startsWith('https://')) return relative;
  try {
    return new URL(relative, base).href;
  } catch {
    // Fallback: manual join + normalize
    const baseNoFile = base.substring(0, base.lastIndexOf('/') + 1);
    const parts = (baseNoFile + relative).split('/');
    const resolved = [];
    for (const p of parts) {
      if (p === '..') resolved.pop();
      else if (p !== '.') resolved.push(p);
    }
    return resolved.join('/');
  }
}

// SSL context — ignore cert errors (Toffee CDN hostname mismatch)
const sslCtx = {
  rejectUnauthorized: false,
};

let toffeeCookie   = null;
let toffeeUA       = 'okhttp/4.11.0';
const DEFAULT_UA   = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36';

// ── Fetch cookie from Toffee JSON ──
function fetchToffeeCookie() {
  return new Promise((resolve) => {
    const req = https.get(TOFFEE_JSON, { ...sslCtx, headers: { 'User-Agent': 'curl/7.0' } }, (res) => {
      let data = '';
      res.on('data', d => data += d);
      res.on('end', () => {
        try {
          const json = JSON.parse(data);
          const first = json.channels && json.channels[0];
          if (first && first.headers) {
            if (first.headers.cookie)        { toffeeCookie = first.headers.cookie; }
            if (first.headers['user-agent']) { toffeeUA = first.headers['user-agent']; }
            console.log('✅ Cookie refreshed:', (toffeeCookie || '').slice(0, 70) + '…');
          }
        } catch (e) { console.warn('⚠️  JSON parse error:', e.message); }
        resolve();
      });
    });
    req.on('error', (e) => { console.warn('⚠️  Fetch cookie failed:', e.message); resolve(); });
  });
}

// ── Build upstream headers for a given target URL ──
// Toffee auth (cookie + okhttp UA) is sent ONLY to toffeelive.com; every other host
// gets a plain browser UA so we don't leak the Toffee cookie to third parties.
function buildHeaders(targetHostname, isToffee) {
  const h = {
    'Host':            targetHostname,   // ← use actual hostname, not hardcoded
    'user-agent':      isToffee ? toffeeUA : DEFAULT_UA,
    'accept-encoding': 'identity',
    'connection':      'keep-alive',
  };
  if (isToffee && toffeeCookie) h['cookie'] = toffeeCookie;
  return h;
}

// ── Proxy handler ──
const server = http.createServer(async (req, res) => {
  // A client (HLS.js) routinely aborts in-flight segment requests when switching quality/
  // channel — swallow the resulting socket errors so one abort can't crash the whole proxy.
  res.on('error', () => {});
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  // Allow a public HTTPS page (e.g. GitHub Pages) to reach this local proxy.
  // Chrome's Private Network Access blocks HTTPS→localhost without this header.
  res.setHeader('Access-Control-Allow-Private-Network', 'true');

  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }

  if (req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, cookie: !!toffeeCookie }));
    return;
  }

  // Serve the player itself so http://localhost:8889 is a complete, same-origin app.
  if (req.url === '/' || req.url === '/index.html') {
    fs.readFile(PLAYER_HTML, (err, html) => {
      if (err) {
        res.writeHead(404, { 'Content-Type': 'text/plain' });
        res.end('web/index.html not found — run from the repo root.');
      } else {
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end(html);
      }
    });
    return;
  }

  const parsed = url.parse(req.url, true);
  const target = parsed.query.url;

  if (!target) { res.writeHead(400); res.end('Missing ?url='); return; }
  // Allow any http(s) stream so the player can route CORS-blocked channels through us too
  // (not just Toffee). NOTE: this is an open relay — safe for a LOCAL proxy, but if you ever
  // host it publicly, restrict `target` to an allowlist of your playlist's stream domains.
  if (!/^https?:\/\//i.test(target)) { res.writeHead(400); res.end('Only http(s) targets'); return; }
  const isToffee = target.includes('toffeelive.com');

  const targetUrl = url.parse(target);
  const isHttps   = targetUrl.protocol !== 'http:';
  const lib       = isHttps ? https : http;
  const isM3U8 = target.includes('.m3u8') || target.includes('playlist') || target.includes('master');

  console.log(`→ [${isM3U8 ? 'M3U8' : 'SEG '}] ${target.slice(0, 90)}`);

  const opts = {
    hostname:           targetUrl.hostname,
    port:               targetUrl.port || (isHttps ? 443 : 80),
    path:               targetUrl.path,
    method:             'GET',
    headers:            buildHeaders(targetUrl.hostname, isToffee),
    rejectUnauthorized: false,   // ← disable SSL verification
  };

  function doRequest(attemptsLeft) {
  const upReq = lib.request(opts, (upRes) => {
    const status = upRes.statusCode;
    console.log(`   ← ${status} from ${targetUrl.hostname}`);

    if (status >= 400) {
      if (!res.headersSent) {
        res.writeHead(status, { 'Access-Control-Allow-Origin': '*', 'Content-Type': 'text/plain' });
        res.end(`Upstream returned ${status}`);
      }
      upRes.resume();
      return;
    }

    const ct = upRes.headers['content-type'] || '';
    const fwdHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Content-Type': ct || (isM3U8 ? 'application/vnd.apple.mpegurl' : 'video/mp2t'),
    };
    // Force HLS.js to always re-fetch M3U8 — prevents stale hdntl token caching
    if (isM3U8) {
      fwdHeaders['Cache-Control'] = 'no-cache, no-store, must-revalidate';
      fwdHeaders['Pragma'] = 'no-cache';
      fwdHeaders['Expires'] = '0';
    }
    if (upRes.headers['content-length']) fwdHeaders['Content-Length'] = upRes.headers['content-length'];

    // M3U8 → rewrite all URLs through proxy
    if (isM3U8 || ct.includes('mpegurl')) {
      let body = '';
      upRes.on('data', d => body += d.toString());
      upRes.on('end', () => {
        // Route rewritten URLs back through THIS proxy — derived from the incoming
        // request so it works whether we run on localhost or a hosted origin.
        const proto    = req.headers['x-forwarded-proto'] || 'http';
        const selfBase = `${proto}://${req.headers.host}/proxy?url=`;
        const viaProxy = (uri) => `${selfBase}${encodeURIComponent(resolveUrl(target, uri))}`;
        const rewritten = body.split('\n').map(line => {
          const l = line.trim();
          if (!l) return line;
          if (l.startsWith('#')) {
            // Tag lines can carry a URI="..." that ALSO needs proxying — most importantly
            // #EXT-X-KEY (AES-128 decryption key) and #EXT-X-MAP. Without this, encrypted
            // streams (e.g. Toffee) fetch the key off-proxy and fail to decrypt → black screen.
            return l.includes('URI="')
              ? line.replace(/URI="([^"]+)"/g, (m, uri) => uri.startsWith('data:') ? m : `URI="${viaProxy(uri)}"`)
              : line;
          }
          return viaProxy(l);   // segment / sub-playlist (resolveUrl handles ../)
        }).join('\n');
        if (!res.headersSent) { res.writeHead(status, fwdHeaders); res.end(rewritten); }
      });
    } else if (!res.headersSent) {
      // TS segment → pipe directly
      res.writeHead(status, fwdHeaders);
      upRes.on('error', () => { try { res.end(); } catch {} });
      upRes.pipe(res);
    } else {
      upRes.resume();   // response already started (retry/timeout race) — drain & drop
    }
  });

  upReq.on('error', (e) => {
    const retryable = e.code === 'ECONNRESET' || e.code === 'ETIMEDOUT' || e.code === 'ECONNREFUSED';
    if (retryable && attemptsLeft > 1) {
      console.warn(`   ↺ Retry (${attemptsLeft - 1} left): ${e.code}`);
      setTimeout(() => doRequest(attemptsLeft - 1), 300);
      return;
    }
    console.error('   ✗ Upstream error:', e.message);
    if (!res.headersSent) {
      res.writeHead(502, { 'Access-Control-Allow-Origin': '*' });
      res.end('Upstream error: ' + e.message);
    }
  });

  upReq.setTimeout(15000, () => {
    upReq.destroy(new Error('Request timeout'));
  });

  upReq.end();
  } // end doRequest
  doRequest(3); // up to 3 attempts
});

// Last-resort safety net: a long-running streaming proxy must never die from one stray
// socket/stream error (EPIPE, ECONNRESET, late writeHead). Log and keep serving.
process.on('uncaughtException',  (e) => console.error('⚠️  uncaught:', e.code || e.message));
process.on('unhandledRejection', (e) => console.error('⚠️  unhandled:', (e && e.message) || e));

// ── Start ──
(async () => {
  console.log('🔄 Fetching Toffee cookie…');
  await fetchToffeeCookie();

  server.listen(PORT, () => {
    console.log(`\n🚀 Toffee Proxy → http://localhost:${PORT}`);
    console.log(`   Player      → http://localhost:8888`);
    console.log(`   Health      → http://localhost:${PORT}/health\n`);
  });

  // Auto-refresh cookie every 4 hours
  setInterval(fetchToffeeCookie, 4 * 60 * 60 * 1000);
})();
