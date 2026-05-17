import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  experimental: {
    optimizePackageImports: ["lucide-react", "recharts"],
  },
  // Allow the server build to read JSON from ../web/data and ../results at request time.
  outputFileTracingIncludes: {
    "/strategies/**": ["./data/**", "../results/**"],
    "/search/**": ["./data/**", "../results/**"],
    "/folds/**": ["./data/**"],
    "/methodology/**": ["./data/**", "../DEPLOYMENT_FRAMEWORK.md"],
    "/reports/**": ["./data/**", "../results/**.md"],
    "/api/**": ["./data/**"],
  },
};

export default nextConfig;
