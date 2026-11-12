// Next.js middleware — server-side auth gate.
//
// Companion to the FastAPI catchall check in `main.py::_serve_ui_path`,
// which is the production runtime enforcement (the static export in
// `ui/next.config.js` excludes middleware from the build, so this file
// only runs in `next dev` and any future server-side deployment).
//
// What it does:
// 1. Inspects the `emly_admin_session` cookie on incoming requests to
//    any of the protected UI paths.
// 2. If the cookie is missing, 302-redirects to
//    `/api/admin/auth/login?return_to=<original-path>` so the IdP flow
//    can put the user back where they started after the round-trip.
// 3. Public paths (`/login`, `/auth/callback`, `/request-access`, static
//    assets, the public root) pass through untouched.
//
// Security notes:
// - We only check the cookie's *presence*, not its JWT signature. The
//   signature is verified by the API endpoints (`get_admin` dep) on
//   each request — verifying twice per page load would be wasteful
//   since the BFF can hand the same id_token to the API and the
//   proactive-refresh interceptor keeps the cookie fresh.
// - The redirect is always same-origin (the `return_to` is just a path
//   component, never a full URL) so an attacker can't open-redirect
//   through this guard.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const SESSION_COOKIE = process.env.NEXT_PUBLIC_AUTH_COOKIE_NAME || "emly_admin_session";

const PROTECTED_PREFIXES = [
  "/bots",
  "/admins",
  "/workspace",
  "/accept-invite",
  "/users",
  "/conversations",
  "/dashboard",
  "/config",
] as const;

const PUBLIC_EXACT = new Set<string>([
  "/login",
  "/auth/callback",
  "/request-access",
]);

function isProtectedPath(pathname: string): boolean {
  if (PUBLIC_EXACT.has(pathname)) return false;
  return PROTECTED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
  );
}

function buildLoginRedirect(req: NextRequest): URL {
  const url = req.nextUrl.clone();
  url.pathname = "/api/admin/auth/login";
  url.search = "";
  // return_to must stay a same-origin path; never trust any user-controlled
  // absolute URL here.
  url.searchParams.set("return_to", req.nextUrl.pathname + req.nextUrl.search);
  return url;
}

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  if (!isProtectedPath(pathname)) {
    return NextResponse.next();
  }

  const hasSession = req.cookies.has(SESSION_COOKIE);
  if (hasSession) {
    return NextResponse.next();
  }

  return NextResponse.redirect(buildLoginRedirect(req));
}

// Skip the middleware on the Next.js internals + static asset paths so
// bundle requests don't pay the cookie-read cost.
export const config = {
  matcher: [
    /*
     * Match all request paths except for:
     *  - _next/static (static files)
     *  - _next/image (image optimization)
     *  - favicon.ico, file.svg, etc. (static files in /public)
     *  - /api/* (the backend handles its own auth)
     */
    "/((?!_next/static|_next/image|_next/data|favicon.ico|file.svg|api/).*)",
  ],
};
