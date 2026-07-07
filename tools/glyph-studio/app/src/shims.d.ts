// Minimal ambient declarations for the few Node builtins used by the vite
// config and the vitest fixture loader, so `tsc --noEmit` passes without
// pulling in @types/node (kept out of deps deliberately).

declare module "node:fs" {
  export function readFileSync(path: string, encoding: "utf-8" | "utf8"): string;
}
declare module "node:url" {
  export function fileURLToPath(url: string | URL): string;
}
declare module "node:path" {
  interface PathApi {
    dirname(p: string): string;
    resolve(...segments: string[]): string;
    join(...segments: string[]): string;
  }
  const path: PathApi;
  export default path;
}
