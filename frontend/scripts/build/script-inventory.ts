import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, extname, join, relative, resolve } from "node:path";

type ScriptExtension = ".mjs" | ".js" | ".ts" | ".tsx";

type ScriptClassification =
  | "active_entrypoint"
  | "active_helper"
  | "test"
  | "qa_candidate"
  | "orphan_candidate";

type ScriptInventoryEntry = {
  path: string;
  extension: ScriptExtension;
  bytes: number;
  importedBy: string[];
  imports: string[];
  npmEntrypoint: boolean;
  classification: ScriptClassification;
};

const SCRIPT_EXTENSIONS = new Set<ScriptExtension>([".mjs", ".js", ".ts", ".tsx"]);
const MAINTENANCE_ENTRYPOINTS = new Set([
  "scripts/build/script-inventory.ts",
  "scripts/build/artifact-fingerprint.ts",
]);

const frontendRoot = resolve(process.cwd());
const scriptsRoot = resolve(frontendRoot, "scripts");
const packageJsonPath = resolve(frontendRoot, "package.json");
const outputPath = resolve(scriptsRoot, "script-inventory.json");

function toPosixPath(path: string): string {
  return path.replace(/\\/g, "/");
}

function toFrontendRelativePath(path: string): string {
  return toPosixPath(relative(frontendRoot, path));
}

function walkScripts(dir: string, files: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const fullPath = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === ".tmp" || entry.name === "tmp") {
        continue;
      }
      walkScripts(fullPath, files);
      continue;
    }

    const extension = extname(entry.name) as ScriptExtension;
    if (SCRIPT_EXTENSIONS.has(extension)) {
      files.push(fullPath);
    }
  }

  return files;
}

function readPackageEntrypoints(): Set<string> {
  const packageJson = JSON.parse(readFileSync(packageJsonPath, "utf8")) as {
    scripts?: Record<string, string>;
  };
  const entrypoints = new Set<string>();
  const scriptValues = Object.values(packageJson.scripts ?? {});
  const scriptReferencePattern = /scripts[\\/][^\s"'`]+?\.(?:mjs|js|ts|tsx)/g;

  for (const scriptValue of scriptValues) {
    for (const match of scriptValue.matchAll(scriptReferencePattern)) {
      entrypoints.add(toPosixPath(match[0]));
    }
  }

  return entrypoints;
}

function extractImportSpecifiers(source: string): string[] {
  const specifiers = new Set<string>();
  const patterns = [
    /\bimport\s+(?:[^"'`]*?\s+from\s+)?["']([^"']+)["']/g,
    /\bexport\s+[^"'`]*?\s+from\s+["']([^"']+)["']/g,
    /\bimport\s*\(\s*["']([^"']+)["']\s*\)/g,
  ];

  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) {
      specifiers.add(match[1]);
    }
  }

  return [...specifiers].sort((a, b) => a.localeCompare(b));
}

function resolveRelativeImport(importerPath: string, specifier: string, knownFiles: Set<string>): string | null {
  if (!specifier.startsWith(".")) {
    return null;
  }

  const base = resolve(dirname(importerPath), specifier);
  const candidates = [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    `${base}.mjs`,
    `${base}.js`,
    join(base, "index.ts"),
    join(base, "index.mjs"),
    join(base, "index.js"),
  ];

  for (const candidate of candidates) {
    if (knownFiles.has(candidate)) {
      return candidate;
    }
  }

  return null;
}

function classifyEntry(path: string, npmEntrypoint: boolean, importedBy: string[]): ScriptClassification {
  if (MAINTENANCE_ENTRYPOINTS.has(path)) {
    return "active_entrypoint";
  }
  if (path.endsWith(".test.mjs") || path.endsWith(".test.ts") || path.endsWith(".test.tsx")) {
    return "test";
  }
  if (npmEntrypoint) {
    return "active_entrypoint";
  }
  if (importedBy.length > 0) {
    return "active_helper";
  }
  if (path.startsWith("scripts/qa/")) {
    return "qa_candidate";
  }
  return "orphan_candidate";
}

function buildInventory(): ScriptInventoryEntry[] {
  const files = walkScripts(scriptsRoot).sort((a, b) => a.localeCompare(b));
  const knownFiles = new Set(files);
  const npmEntrypoints = readPackageEntrypoints();
  const importsByFile = new Map<string, string[]>();
  const importedByFile = new Map<string, string[]>();

  for (const file of files) {
    const source = readFileSync(file, "utf8");
    const importSpecifiers = extractImportSpecifiers(source);
    const resolvedImports = importSpecifiers
      .map((specifier) => resolveRelativeImport(file, specifier, knownFiles))
      .filter((value): value is string => value !== null);

    importsByFile.set(file, resolvedImports.map(toFrontendRelativePath).sort((a, b) => a.localeCompare(b)));

    for (const imported of resolvedImports) {
      const importers = importedByFile.get(imported) ?? [];
      importers.push(toFrontendRelativePath(file));
      importedByFile.set(imported, importers);
    }
  }

  return files.map((file) => {
    const frontendRelativePath = toFrontendRelativePath(file);
    const extension = extname(file) as ScriptExtension;
    const importedBy = (importedByFile.get(file) ?? []).sort((a, b) => a.localeCompare(b));
    const npmEntrypoint = npmEntrypoints.has(frontendRelativePath);

    return {
      path: frontendRelativePath,
      extension,
      bytes: statSync(file).size,
      importedBy,
      imports: importsByFile.get(file) ?? [],
      npmEntrypoint,
      classification: classifyEntry(frontendRelativePath, npmEntrypoint, importedBy),
    };
  });
}

const inventory = buildInventory();
writeFileSync(outputPath, `${JSON.stringify(inventory, null, 2)}\n`);

const summary = inventory.reduce<Record<ScriptClassification, number>>(
  (acc, entry) => {
    acc[entry.classification] += 1;
    return acc;
  },
  {
    active_entrypoint: 0,
    active_helper: 0,
    test: 0,
    qa_candidate: 0,
    orphan_candidate: 0,
  },
);

console.log(
  JSON.stringify(
    {
      output: toFrontendRelativePath(outputPath),
      total: inventory.length,
      summary,
    },
    null,
    2,
  ),
);
