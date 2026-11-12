import type { ReactNode } from "react";

// Same static-export gotcha as bots/[slug]/layout: the admin_id is runtime
// data. Emit one placeholder so Next produces the HTML scaffold; the SPA
// fallback in main.py serves it for any concrete id.
export const dynamic = "force-static";

export function generateStaticParams() {
  return [{ admin_id: "_" }];
}

export default function AdminIdLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
