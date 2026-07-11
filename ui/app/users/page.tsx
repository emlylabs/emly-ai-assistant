"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function LegacyUsersRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/bots");
  }, [router]);
  return (
    <div className="center">
      <p className="muted">Redirecting…</p>
    </div>
  );
}
