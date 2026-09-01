import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import {
  assertOriginalLineHit,
  istanbulFromBrowserRawDirectory,
  mergeIstanbulCoverage,
  readJson,
  writeJson,
} from "./browser-source-coverage.mjs";

const scriptPath = fileURLToPath(import.meta.url);
const frontendRoot = path.resolve(path.dirname(scriptPath), "..");
const repoRoot = path.resolve(frontendRoot, "..");
const scope = JSON.parse(
  fs.readFileSync(
    path.join(repoRoot, "scripts", "frontend_quality_scope.json"),
    "utf8",
  ),
);

function skippedRelative(rel) {
  if (scope.unitTestExclude?.[rel]) return true;
  for (const prefix of scope.skipPathPrefixes || []) {
    const trimmed = String(prefix).replace(/\/$/, "");
    if (rel === trimmed || rel.startsWith(`${trimmed}/`)) return true;
  }
  return false;
}

function listUnitTests() {
  const testRe = new RegExp(scope.unitTestPattern);
  const skip = new Set(scope.skipDirNames);
  const files = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      const rel = path.relative(frontendRoot, full).replaceAll("\\", "/");
      if (skippedRelative(rel)) continue;
      if (entry.isDirectory()) {
        if (!skip.has(entry.name)) walk(full);
        continue;
      }
      if (testRe.test(rel)) files.push(full);
    }
  };
  for (const root of scope.unitTestRoots) {
    const dir = path.join(frontendRoot, root);
    if (fs.existsSync(dir)) walk(dir);
  }
  return files.sort();
}

function tsxCli() {
  const cli = path.join(frontendRoot, "node_modules", "tsx", "dist", "cli.mjs");
  if (!fs.existsSync(cli)) {
    throw new Error("tsx is not installed in frontend/node_modules");
  }
  return cli;
}

function c8Cli() {
  const cli = path.join(frontendRoot, "node_modules", "c8", "bin", "c8.js");
  if (!fs.existsSync(cli)) {
    throw new Error("c8 is not installed. Run npm install in frontend/");
  }
  return cli;
}

function playwrightCli() {
  const cli = path.join(frontendRoot, "node_modules", "@playwright", "test", "cli.js");
  if (!fs.existsSync(cli)) {
    throw new Error("@playwright/test is not installed in frontend/node_modules");
  }
  return cli;
}

async function runCoverage() {
  const markerPath = path.join(
    frontendRoot,
    "coverage",
    "browser-source-mapped.json",
  );
  fs.rmSync(markerPath, { force: true });

  const args = [
    c8Cli(),
    "--all",
    "--exclude-after-remap",
    "--reporter",
    "text-summary",
    "--reporter",
    "json",
    "--reports-dir",
    "coverage",
    "--temp-directory",
    "coverage/v8",
  ];
  for (const root of scope.productionRoots) {
    args.push("--src", root, "--include", `${root}/**`);
  }
  for (const glob of scope.coverageExcludeGlobs) {
    args.push("--exclude", glob);
  }
  args.push(process.execPath, tsxCli(), scriptPath);
  const unit = spawnSync(process.execPath, args, {
    cwd: frontendRoot,
    env: process.env,
    stdio: "inherit",
  });
  if (unit.status) process.exit(unit.status);

  const rawDirectory = path.join(frontendRoot, "coverage", "browser-v8");
  fs.rmSync(rawDirectory, { recursive: true, force: true });
  const browser = spawnSync(
    process.execPath,
    [playwrightCli(), "test", "--grep-invert", "@visual"],
    {
      cwd: frontendRoot,
      env: { ...process.env, SMARTROUTE_BROWSER_COVERAGE: "1" },
      stdio: "inherit",
    },
  );
  if (browser.status) process.exit(browser.status);

  const proofFile = "components/smart-route/chat/chat-sidebar.tsx";
  const unitCoveragePath = path.join(frontendRoot, "coverage", "coverage-final.json");
  const browserCoverage = await istanbulFromBrowserRawDirectory({
    frontendRoot,
    rawDirectory,
  });
  const hits = assertOriginalLineHit(browserCoverage, frontendRoot, proofFile, 143);
  writeJson(
    unitCoveragePath,
    mergeIstanbulCoverage(readJson(unitCoveragePath), browserCoverage),
  );
  process.stderr.write(
    `browser source coverage: ${proofFile} original lines ${hits[0]}-${hits.at(-1)} (${hits.length} hit)\n`,
  );
  writeJson(markerPath, {
    file: proofFile,
    first: hits[0],
    last: hits.at(-1),
    hit: hits.length,
  });
  process.exit(0);
}

function runTests(files) {
  const result = spawnSync(
    process.execPath,
    [tsxCli(), "--test", ...files],
    {
      cwd: frontendRoot,
      env: process.env,
      stdio: "inherit",
    },
  );
  process.exit(result.status ?? 1);
}

const files = listUnitTests();
if (process.argv.includes("--list")) {
  process.stdout.write(
    `${files
      .map((file) => path.relative(frontendRoot, file).replaceAll("\\", "/"))
      .join("\n")}\n`,
  );
  process.exit(0);
}
if (process.argv.includes("--coverage")) {
  await runCoverage();
}
if (files.length === 0) {
  throw new Error("frontend unit-test discovery found no files");
}
runTests(files);
