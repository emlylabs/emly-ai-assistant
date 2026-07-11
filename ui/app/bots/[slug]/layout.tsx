import type { ReactNode } from "react";
import BotShell from "@/components/BotShell";

// Static export + dynamic [slug] route gotcha: `dynamicParams: true`
// is forbidden under `output: "export"`, so we have to tell Next.js
// the slug list at build time. Slugs are runtime data — we emit one
// placeholder slug ("_") so Next produces the HTML scaffold for each
// nested page (dashboard/config/files/...). At runtime, FastAPI's
// SPA-fallback (`main.py:_serve_ui_path`) serves the same scaffold
// for any concrete slug, and the client-side router populates
// `useParams().slug` from the URL.
export const dynamic = "force-static";

export function generateStaticParams() {
  return [{ slug: "_" }];
}

export default function BotLayout({ children }: { children: ReactNode }) {
  return <BotShell>{children}</BotShell>;
}
