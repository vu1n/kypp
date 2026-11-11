// `npm run deploy:production`: deploys kypp.vu1n.dev, only from a v* tag (release.yml runs it).
import { execFileSync } from "node:child_process";

// Workers Builds still runs this on every push to main; a push to main must not deploy.
if (process.env.WORKERS_CI) {
  console.log("kypp: skipped. Production deploys from v* tags (.github/workflows/release.yml), not from main.");
  process.exit(0);
}
const git = (...a) => execFileSync("git", a, { encoding: "utf8" }).trim();
const tag = git("tag", "--points-at", "HEAD", "--list", "v*").split("\n")[0];
if (!tag) { console.error("kypp: HEAD is not a v* tag; production deploys only a release"); process.exit(1); }
if (git("status", "--porcelain")) { console.error(`kypp: ${tag} has uncommitted or untracked files (wrangler would deploy or migrate them too)`); process.exit(1); }
console.log(`kypp: deploying ${tag}`);
const run = (...a) => execFileSync("npx", ["wrangler", ...a], { stdio: "inherit" });
run("d1", "migrations", "apply", "DB", "--remote", "--env", "production");
run("deploy", "--env", "production");
