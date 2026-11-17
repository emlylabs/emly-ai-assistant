import { api } from "@/lib/api";

type Router = { replace: (href: string) => void };

/**
 * Sign out, and say so when it didn't work.
 *
 * A refused sign-out (a CSRF origin mismatch, a network error) used to
 * fall through to /login as if it had worked, while the session cookies
 * stayed and the next page signed the admin straight back in (found in
 * the G8b restore drill). Now: if the server refused and the session is
 * still valid, return a message for the caller to show instead.
 */
export async function signOut(router: Router): Promise<string | null> {
  try {
    const res = await api.logout();
    if (res?.provider_logout_url) {
      window.location.href = res.provider_logout_url;
      return null;
    }
    router.replace("/login");
    return null;
  } catch {
    try {
      await api.me();
    } catch {
      router.replace("/login"); // the session is gone anyway
      return null;
    }
    return "Sign-out didn't go through, so you are still signed in. Reload the page and try again.";
  }
}
