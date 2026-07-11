const path = require("path");

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export — FastAPI serves the contents of `out/` at /.
  output: "export",
  // SPA-style routing: every page becomes /<route>.html so a URL like
  // /admins resolves to admins.html in the export.
  trailingSlash: false,
  // No <Image/> optimisation server in static export.
  images: { unoptimized: true },
  // Pin the workspace root so Next doesn't get confused by an unrelated
  // package-lock.json that may exist higher up in $HOME.
  outputFileTracingRoot: path.resolve(__dirname),
};

module.exports = nextConfig;
