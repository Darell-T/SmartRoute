import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { relative, resolve } from "node:path";

type ArtifactFingerprint = {
  file: string;
  sha256: string;
  bytes: number;
  featureCount?: number;
};

const ARTIFACTS = [
  "public/subway-network.canonical.geojson",
  "public/subway-network.visual.geojson",
  "public/subway-network.station-anchors.geojson",
  "public/subway-network.artifacts.json",
];

const frontendRoot = resolve(process.cwd());

function toPosixPath(path: string): string {
  return path.replace(/\\/g, "/");
}

function parseOutputArg(): string {
  const outIndex = process.argv.indexOf("--out");
  if (outIndex === -1) {
    return resolve(frontendRoot, "scripts", "artifact-fingerprint.json");
  }

  const outValue = process.argv[outIndex + 1];
  if (!outValue) {
    throw new Error("Missing value after --out");
  }

  return resolve(frontendRoot, outValue);
}

function featureCountForJson(bytes: Buffer): number | undefined {
  try {
    const parsed = JSON.parse(bytes.toString("utf8")) as { features?: unknown };
    return Array.isArray(parsed.features) ? parsed.features.length : undefined;
  } catch {
    return undefined;
  }
}

function fingerprintArtifact(relativeFile: string): ArtifactFingerprint | null {
  const absolutePath = resolve(frontendRoot, relativeFile);
  if (!existsSync(absolutePath)) {
    return null;
  }

  const bytes = readFileSync(absolutePath);
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  const featureCount = relativeFile.endsWith(".geojson") ? featureCountForJson(bytes) : undefined;

  return {
    file: toPosixPath(relativeFile),
    sha256,
    bytes: bytes.length,
    ...(featureCount === undefined ? {} : { featureCount }),
  };
}

const outputPath = parseOutputArg();
const fingerprints = ARTIFACTS.map(fingerprintArtifact).filter(
  (entry): entry is ArtifactFingerprint => entry !== null,
);

writeFileSync(outputPath, `${JSON.stringify(fingerprints, null, 2)}\n`);

console.log(
  JSON.stringify(
    {
      output: toPosixPath(relative(frontendRoot, outputPath)),
      artifactCount: fingerprints.length,
      artifacts: fingerprints,
    },
    null,
    2,
  ),
);
