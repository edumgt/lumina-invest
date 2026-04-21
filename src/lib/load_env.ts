import fs from "fs";
import path from "path";
import dotenv from "dotenv";

function resolveEnvFiles(): string[] {
  const cwd = process.cwd();
  const profile = String(process.env.PROFILE || "").trim().toLowerCase();
  const files = [path.join(cwd, ".env")];

  if (profile) {
    files.push(path.join(cwd, `.env.${profile}`));
  }

  return files;
}

for (const file of resolveEnvFiles()) {
  if (!fs.existsSync(file)) continue;
  dotenv.config({
    path: file,
    override: true,
  });
}

