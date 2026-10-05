// Minimal typing for the one Node API the tests use (avoids adding @types/node).
declare module "node:fs" {
  export function readFileSync(path: string, encoding: "utf8"): string;
}
