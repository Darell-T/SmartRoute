import path from "path"
import { copyFileSync, mkdirSync } from "node:fs"
import { fileURLToPath } from "url"
import { createRequire } from "module"

const require = createRequire(import.meta.url)
const { loadEnvConfig, updateInitialEnv } = require("@next/env")

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.join(__dirname, "..")
const development = process.env.NODE_ENV !== "production"

// Next emits worker URLs as assets without their relative module dependencies.
// Keep MapLibre's worker and shared module together, keyed by installed version.
const maplibrePackage = require.resolve("maplibre-gl/package.json")
const maplibreVersion = require(maplibrePackage).version
const maplibreDist = path.join(path.dirname(maplibrePackage), "dist")
const maplibrePublic = path.join(__dirname, "public", "maplibre", maplibreVersion)
mkdirSync(maplibrePublic, { recursive: true })
for (const file of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) {
  copyFileSync(path.join(maplibreDist, file), path.join(maplibrePublic, file))
}


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
