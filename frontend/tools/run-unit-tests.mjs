import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const scriptPath = fileURLToPath(import.meta.url);
const frontendRoot = path.resolve(path.dirname(scriptPath), "..");
const repoRoot = path.resolve(frontendRoot, "..");
const scope = JSON.parse(
  fs.readFileSync(
    path.join(repoRoot, "scripts", "frontend_quality_scope.json"),
    "utf8",
  ),
);

function listUnitTests() {
  const testRe = new RegExp(scope.unitTestPattern);
  const skip = new Set(scope.skipDirNames);
  const files = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (!skip.has(entry.name)) walk(full);
        continue;
      }
      const rel = path.relative(frontendRoot, full).replaceAll("\\", "/");
      if (scope.unitTestExclude?.[rel]) continue;
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

function runCoverage() {
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
  const result = spawnSync(process.execPath, args, {
    cwd: frontendRoot,
    env: process.env,
    stdio: "inherit",
  });
  process.exit(result.status ?? 1);
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
  runCoverage();
}
if (files.length === 0) {
  throw new Error("frontend unit-test discovery found no files");
}
runTests(files);
