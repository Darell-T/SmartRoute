import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");
const frontendRoot = path.join(repoRoot, "frontend");
const require = createRequire(path.join(frontendRoot, "package.json"));
const ts = require("typescript");
const scope = JSON.parse(
  fs.readFileSync(path.join(scriptDir, "frontend_quality_scope.json"), "utf8"),
);
const SKIP_DIRS = new Set(scope.skipDirNames);
const SKIP_PATH_PREFIXES = (scope.skipPathPrefixes || []).map((prefix) =>
  prefix.replace(/\/$/, ""),
);

function skippedRelative(relativePath) {
  const parts = relativePath.split("/");
  if (parts.some((part) => SKIP_DIRS.has(part))) {
    return true;
  }
  return SKIP_PATH_PREFIXES.some(
    (prefix) => relativePath === prefix || relativePath.startsWith(`${prefix}/`),
  );
}

const LOGICAL_OPERATORS = new Set([
  ts.SyntaxKind.AmpersandAmpersandToken,
  ts.SyntaxKind.BarBarToken,
  ts.SyntaxKind.QuestionQuestionToken,
  ts.SyntaxKind.AmpersandAmpersandEqualsToken,
  ts.SyntaxKind.BarBarEqualsToken,
  ts.SyntaxKind.QuestionQuestionEqualsToken,
]);

function isFunctionLike(node) {
  return (
    ts.isFunctionDeclaration(node) ||
    ts.isFunctionExpression(node) ||
    ts.isArrowFunction(node) ||
    ts.isMethodDeclaration(node) ||
    ts.isConstructorDeclaration(node) ||
    ts.isGetAccessorDeclaration(node) ||
    ts.isSetAccessorDeclaration(node) ||
    ts.isClassStaticBlockDeclaration(node)
  );
}

function hasBody(node) {
  if (ts.isClassStaticBlockDeclaration(node)) {
    return Boolean(node.body);
  }
  return Boolean(node.body);
}

function increment(node) {
  switch (node.kind) {
    case ts.SyntaxKind.IfStatement:
    case ts.SyntaxKind.ForStatement:
    case ts.SyntaxKind.ForInStatement:
    case ts.SyntaxKind.ForOfStatement:
    case ts.SyntaxKind.WhileStatement:
    case ts.SyntaxKind.DoStatement:
    case ts.SyntaxKind.CatchClause:
    case ts.SyntaxKind.ConditionalExpression:
    case ts.SyntaxKind.CaseClause:
      return 1;
    case ts.SyntaxKind.BinaryExpression:
      return LOGICAL_OPERATORS.has(node.operatorToken.kind) ? 1 : 0;
    case ts.SyntaxKind.Parameter:
    case ts.SyntaxKind.BindingElement:
      return node.initializer ? 1 : 0;
    case ts.SyntaxKind.PropertyAccessExpression:
    case ts.SyntaxKind.ElementAccessExpression:
    case ts.SyntaxKind.CallExpression:
      return node.questionDotToken ? 1 : 0;
    default:
      return 0;
  }
}

function complexityOf(fnNode) {
  let complexity = 1;
  const visit = (node) => {
    if (node !== fnNode && isFunctionLike(node)) {
      return;
    }
    if (node !== fnNode) {
      complexity += increment(node);
    }
    ts.forEachChild(node, visit);
  };
  visit(fnNode);
  return complexity;
}

function enclosingClassName(node) {
  let current = node.parent;
  while (current) {
    if (ts.isClassDeclaration(current) || ts.isClassExpression(current)) {
      return current.name ? current.name.text : "";
    }
    current = current.parent;
  }
  return "";
}

function assignedName(node) {
  const parent = node.parent;
  if (!parent) {
    return "";
  }
  if (ts.isVariableDeclaration(parent) && ts.isIdentifier(parent.name)) {
    return parent.name.text;
  }
  if (
    ts.isPropertyAssignment(parent) &&
    ts.isIdentifier(parent.name)
  ) {
    return parent.name.text;
  }
  if (
    ts.isBinaryExpression(parent) &&
    parent.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
    ts.isIdentifier(parent.left)
  ) {
    return parent.left.text;
  }
  return "";
}

function functionName(node) {
  if (ts.isClassStaticBlockDeclaration(node)) {
    const className = enclosingClassName(node);
    return className ? `${className}.[static block]` : "[static block]";
  }
  if (ts.isConstructorDeclaration(node)) {
    const className = enclosingClassName(node);
    return className ? `${className}.constructor` : "constructor";
  }
  if (node.name && ts.isIdentifier(node.name)) {
    const className = enclosingClassName(node);
    return className ? `${className}.${node.name.text}` : node.name.text;
  }
  if (node.name && ts.isStringLiteral(node.name)) {
    const className = enclosingClassName(node);
    const name = node.name.text;
    return className ? `${className}.${name}` : name;
  }
  const inferred = assignedName(node);
  if (inferred) {
    return inferred;
  }
  return "anonymous";
}

function collectFunctions(sourceFile) {
  const functions = [];
  const visit = (node) => {
    if (isFunctionLike(node) && hasBody(node)) {
      const start = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile, false));
      const end = sourceFile.getLineAndCharacterOfPosition(node.end);
      const name = functionName(node);
      functions.push({
        function: name,
        anonymous: name === "anonymous",
        line: start.line + 1,
        endLine: end.line + 1,
        lines: end.line - start.line + 1,
        complexity: complexityOf(node),
      });
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return functions;
}

function batchFor(relativePath) {
  const match = scope.batchRules
    .filter((rule) => relativePath.startsWith(rule.prefix))
    .sort((left, right) => right.prefix.length - left.prefix.length)[0];
  return match ? match.batch : "unassigned";
}

function classify(relativePath) {
  const parts = relativePath.split("/");
  const base = path.basename(relativePath);
  if (skippedRelative(relativePath) || base.endsWith(".d.ts")) {
    return { role: "excluded", batch: "unassigned" };
  }
  const testFile =
    base.includes(".test.") ||
    base.includes(".spec.") ||
    parts.includes("tests");
  const toolFile = base.includes(".check.") || relativePath.startsWith("tools/");
  let role = "excluded";
  if (testFile) role = "test";
  else if (toolFile) role = "tool";
  else if (
    scope.productionRoots.some((root) => relativePath.startsWith(`${root}/`))
  ) {
    role = "production";
  }
  return { role, batch: batchFor(relativePath) };
}

function walkSourceFiles(kind) {
  const roots = kind === "authored" ? scope.authoredRoots : scope.productionRoots;
  const files = [];
  const visitDir = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        const relativeDir = path.relative(frontendRoot, full).replaceAll("\\", "/");
        if (!SKIP_DIRS.has(entry.name) && !skippedRelative(relativeDir)) {
          visitDir(full);
        }
        continue;
      }
      if (!/\.(ts|tsx|js|mjs)$/.test(entry.name)) {
        continue;
      }
      const relative = path.relative(frontendRoot, full).replaceAll("\\", "/");
      const { role } = classify(relative);
      if (kind === "authored") {
        if (role !== "excluded") files.push(full);
        continue;
      }
      if (role === "production") files.push(full);
    }
  };
  for (const root of roots) {
    const dir = path.join(frontendRoot, root);
    if (fs.existsSync(dir)) {
      visitDir(dir);
    }
  }
  return files.sort();
}

function scriptKindFor(filePath) {
  if (filePath.endsWith(".tsx")) {
    return ts.ScriptKind.TSX;
  }
  if (filePath.endsWith(".ts")) {
    return ts.ScriptKind.TS;
  }
  if (filePath.endsWith(".mjs") || filePath.endsWith(".js")) {
    return ts.ScriptKind.JS;
  }
  return ts.ScriptKind.Unknown;
}

function inventoryFromSource(relative, text) {
  const { role, batch } = classify(relative);
  const sourceFile = ts.createSourceFile(
    relative,
    text,
    ts.ScriptTarget.Latest,
    true,
    scriptKindFor(relative),
  );
  return {
    files: [{ file: relative, role, batch }],
    functions: collectFunctions(sourceFile).map((row) => ({
      file: relative,
      language: "TypeScript",
      role,
      batch,
      ...row,
    })),
  };
}

function inventory(kind) {
  const functions = [];
  const files = [];
  for (const filePath of walkSourceFiles(kind)) {
    const relative = path.relative(frontendRoot, filePath).replaceAll("\\", "/");
    const { role, batch } = classify(relative);
    files.push({ file: relative, role, batch });
    const text = fs.readFileSync(filePath, "utf8");
    const sourceFile = ts.createSourceFile(
      relative,
      text,
      ts.ScriptTarget.Latest,
      true,
      scriptKindFor(filePath),
    );
    for (const row of collectFunctions(sourceFile)) {
      functions.push({
        file: relative,
        language: "TypeScript",
        role,
        batch,
        ...row,
      });
    }
  }
  return { files, functions };
}

function selfTest() {
  const source = `
export function formatNycRouteClock(value) {
  if (value == null || value === "") return null;
  const date = typeof value === "number" ? new Date(value) : new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return formatter.format(date);
}
`.trim();
  const sourceFile = ts.createSourceFile(
    "self-test.js",
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.JS,
  );
  const [fn] = collectFunctions(sourceFile);
  if (!fn || fn.function !== "formatNycRouteClock" || fn.complexity !== 5) {
    throw new Error(
      `self-test failed: expected formatNycRouteClock complexity 5, got ${JSON.stringify(fn)}`,
    );
  }
  const cases = [
    ["app/page.tsx", "production", "7"],
    ["lib/nyc-route-clock.ts", "production", "7"],
    ["lib/nyc-route-clock.test.mjs", "test", "7"],
    ["components/map/smart-route-map.tsx", "production", "8"],
    ["tests/release/smartroute-chat.spec.ts", "test", "8"],
    ["scripts/build/bundle-stage.ts", "production", "9"],
    ["scripts/build/spine.test.ts", "test", "9"],
    ["components/map/subway-renderer.check.mjs", "tool", "8"],
    ["types/api.ts", "excluded", "unassigned"],
    ["next-env.d.ts", "excluded", "unassigned"],
    ["public/generated.json", "excluded", "unassigned"],
    ["tools/run-unit-tests.mjs", "tool", "unassigned"],
    ["tools/oxlint/anti-slop/index.ts", "excluded", "unassigned"],
  ];
  for (const [relative, role, batch] of cases) {
    const actual = classify(relative);
    if (actual.role !== role || actual.batch !== batch) {
      throw new Error(
        `classify(${relative}) => ${JSON.stringify(actual)}, expected ${role}/${batch}`,
      );
    }
  }
}

const sourceIdx = process.argv.indexOf("--source");
if (process.argv.includes("--self-test")) {
  selfTest();
  process.stderr.write("js_function_metrics self-test passed\n");
} else if (sourceIdx !== -1) {
  const relative = process.argv[sourceIdx + 1];
  const text = fs.readFileSync(0, "utf8");
  process.stdout.write(`${JSON.stringify(inventoryFromSource(relative, text))}\n`);
} else {
  const kind = process.argv.includes("--authored") ? "authored" : "production";
  process.stdout.write(`${JSON.stringify(inventory(kind), null, 2)}\n`);
}
