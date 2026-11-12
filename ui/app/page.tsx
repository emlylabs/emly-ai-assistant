"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

/**
 * Root: probe `/me` to decide where to send the user.
 *
 * The middleware (and the FastAPI catchall in static-export prod)
 * does NOT gate `/` — it's a public landing. So this client-side
 * probe is the post-login router. If the user has a fresh cookie
 * we land them at `/bots` (or `/accept-invite` if they have pending
 * invites). If `me()` 401s, the user is unauthenticated and we send
 * them to the sign-in page.
 *
 * 200 + pending invites → /accept-invite picker
 * 200 → app shell at `/bots`
 * 401 → sign-in page
 * everything else → bounce to sign-in (safest default)
 */
export default function Home() {
  const router = useRouter();

  useEffect(() => {
    api
      .me()
      .then(async () => {
        try {
          const invites = await api.listMyInvites();
          if (invites.length > 0) {
            router.replace("/accept-invite");
            return;
          }
        } catch {
          /* fall through to workspace */
        }
        router.replace("/bots");
      })
      .catch(() => router.replace("/login"));
  }, [router]);

  return (
    <div className="center">
      <p className="muted">Redirecting…</p>
    </div>
  );
}
