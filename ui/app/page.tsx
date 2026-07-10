"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

/**
 * Root: probe `/me` to decide where to send the user.
 *
 * 200 → app shell at `/bots`
 * 401 → sign-in page
 * everything else → log + bounce to sign-in (safest default)
 */
export default function Home() {
  const router = useRouter();

  useEffect(() => {
    api
      .me()
      .then(() => router.replace("/bots"))
      .catch(() => router.replace("/login"));
  }, [router]);

  return (
    <div className="center">
      <p className="muted">Redirecting…</p>
    </div>
  );
}
