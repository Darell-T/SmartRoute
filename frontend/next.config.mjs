import path from "path"
import { fileURLToPath } from "url"
import { createRequire } from "module"

const require = createRequire(import.meta.url)
const { loadEnvConfig, updateInitialEnv } = require("@next/env")

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.join(__dirname, "..")
const development = process.env.NODE_ENV !== "production"

// Load Next's standard frontend files first so they retain override priority.
// Promote that result to @next/env's baseline, then force a root load that can
// fill only variables the frontend environment did not define.
const { combinedEnv: frontendEnv } = loadEnvConfig(__dirname, development)
updateInitialEnv(frontendEnv)
loadEnvConfig(repoRoot, development, console, true)

if (process.env.NEXT_PUBLIC_APP_KEY?.trim()) {
  throw new Error("APP_KEY must remain server-only; remove NEXT_PUBLIC_APP_KEY")
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  devIndicators: false,
  turbopack: {
    root: __dirname,
  },
  images: {
    unoptimized: true,
  },
}

export default nextConfig
