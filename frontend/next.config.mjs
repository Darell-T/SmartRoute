import path from "path"
import { fileURLToPath } from "url"
import { createRequire } from "module"

const require = createRequire(import.meta.url)
const { loadEnvConfig } = require("@next/env")

const __dirname = path.dirname(fileURLToPath(import.meta.url))
// Next.js only reads .env from the app directory (frontend/) by default.
// Load the monorepo root .env so a single project-root file works.
const repoRoot = path.join(__dirname, "..")
loadEnvConfig(repoRoot)
// Then apply Next's usual env files under frontend/ (e.g. .env.local overrides).
loadEnvConfig(__dirname)

/** @type {import('next').NextConfig} */
const nextConfig = {
  typescript: {
    ignoreBuildErrors: true,
  },
  images: {
    unoptimized: true,
  },
}

export default nextConfig
