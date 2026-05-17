// Cross-platform wrapper that picks the right Python interpreter, then runs
// scripts/build_data.py. Used as pnpm prebuild / predev so the web/data/
// bundles stay fresh against results/.
import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "..");

const candidates = process.platform === "win32"
  ? [resolve(repo, ".venv/Scripts/python.exe"), "python", "python3"]
  : [resolve(repo, ".venv/bin/python"), "python3", "python"];

let py = null;
for (const c of candidates) {
  try {
    if (c.includes(".venv") && !existsSync(c)) continue;
    execSync(`"${c}" --version`, { stdio: "ignore" });
    py = c;
    break;
  } catch { /* keep looking */ }
}

if (!py) {
  console.error("[prebuild] no Python interpreter found");
  process.exit(1);
}

const script = resolve(repo, "scripts/build_data.py");
console.log(`[prebuild] ${py} ${script}`);
execSync(`"${py}" "${script}"`, { stdio: "inherit", cwd: repo });
