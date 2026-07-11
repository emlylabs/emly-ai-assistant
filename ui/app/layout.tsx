import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Emly Admin",
  description: "Admin console for the Emly AI Agents service",
};

// Inline pre-paint script: read theme/density from localStorage before
// React mounts so the page never flashes the wrong palette. Mirrors the
// initialization in nextjs-starter-preview.html. The default is "ink"
// (dark) to match the existing admin look; users can flip to paper from
// the sidebar footer.
const THEME_INIT = `
(function(){
  try {
    var t = localStorage.getItem("bf-theme") || "ink";
    var d = localStorage.getItem("bf-density") || "comfortable";
    document.documentElement.setAttribute("data-theme", t);
    document.documentElement.setAttribute("data-density", d);
  } catch (_) {}
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="ink" data-density="comfortable">
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
      </head>
      <body>
        {children}
      </body>
    </html>
  );
}
