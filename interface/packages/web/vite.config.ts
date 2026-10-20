import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import { phoneBridge } from './bridge-plugin';

/**
 * The dev/preview server configuration. Everything about how the bridge answers
 * a request lives in `bridge-plugin.ts`; what is left here is the server itself
 * and the document policy it serves the page under.
 */

/**
 * The development server has to run Vite's own inline bootstrap (the client
 * script and the React Fast Refresh preamble), so the development policy is the
 * relaxed one. A production build ships no inline script at all, so the strict
 * policy is what a deployed bundle gets.
 */
const DEVELOPMENT_CSP = "default-src 'self'; base-uri 'self'; object-src 'none'; form-action 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' https: http://localhost:* http://*.localhost:* http://127.0.0.1:* ws: wss:";
const PRODUCTION_CSP = "default-src 'self'; base-uri 'self'; object-src 'none'; form-action 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' https: http://localhost:* http://*.localhost:* http://127.0.0.1:*";

/**
 * The loopback sources are listed one at a time, and deliberately without the
 * bracketed IPv6 form: browsers reject `http://[::1]:*` outright, log it as an
 * invalid source, and then ignore it — so it buys nothing and costs a console
 * error on every load. A page served from `::1` is covered by `'self'` anyway.
 *
/**
 * Response headers for the dev and preview servers. `Referrer-Policy` and
 * `Cache-Control` are load-bearing rather than cosmetic: the bridge hands the
 * page a per-process token, and that token travels in a query string on the
 * event stream, so it must never reach a third party through a `Referer` or
 * sit in a disk cache next to a stale document.
 */
function securityHeaders(csp: string): Record<string, string> {
  return {
    // `frame-ancestors` is added here rather than kept in the policy string: a
    // browser ignores the directive when it arrives in a <meta> element, so
    // carrying it there only earns a console error on every load. As a header it
    // is enforced, which is the whole point of setting it.
    'Content-Security-Policy': `${csp}; frame-ancestors 'none'`,
    'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
    'Cross-Origin-Opener-Policy': 'same-origin',
    'Cache-Control': 'no-store',
  };
}

/** Rewrites the document policy in `index.html` for the mode being served. */
function cspPlugin(): Plugin {
  return {
    name: 'project-phone-csp',
    transformIndexHtml(html, context) {
      const policy = context.server ? DEVELOPMENT_CSP : PRODUCTION_CSP;
      const tag = `<meta http-equiv="Content-Security-Policy" content="${policy}" />`;
      if (html.includes('<meta http-equiv="Content-Security-Policy"')) {
        return html.replace(/<meta http-equiv="Content-Security-Policy"[^>]*\/?>/, tag);
      }
      return html.replace('</head>', `${tag}</head>`);
    },
  };
}

// Re-exported so the bridge's rules can be tested without starting a server.
export {
  isLoopbackAddress,
  isLoopbackAuthority,
  isLoopbackRemoteAddress,
  isTrustedOrigin,
  mergeIncoming,
} from './bridge-plugin';

export default defineConfig({
  plugins: [react(), cspPlugin(), phoneBridge()],
  server: {
    host: '127.0.0.1',
    port: 3000,
    strictPort: true,
    headers: securityHeaders(DEVELOPMENT_CSP),
  },
  preview: {
    host: '127.0.0.1',
    port: 3000,
    strictPort: true,
    headers: securityHeaders(PRODUCTION_CSP),
  },
  build: {
    target: 'es2020',
  },
});
