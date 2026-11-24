import { execFileSync, spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const COMPOSE = [
  "compose",
  "-f",
  "docker-compose.yml",
  "-f",
  "infra/compose/workers.yml",
  "--profile",
  "core",
];
const CONTAINER = "klick-worker-media-1";

/** El análisis de medios necesita el worker real (FFmpeg vive solo en su imagen). */
export const mediaWorkerEnabled = (): boolean => !!process.env.E2E_MEDIA_WORKER || !!process.env.CI;

const docker = (args: string[], env: Record<string, string> = {}): string =>
  execFileSync("docker", args, {
    cwd: root,
    env: { ...process.env, MSYS_NO_PATHCONV: "1", ...env },
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });

async function waitReady(): Promise<void> {
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    // Dramatiq escribe su registro en stderr: se revisan ambas salidas.
    const r = spawnSync("docker", ["logs", "--tail", "30", CONTAINER], { encoding: "utf8" });
    if (`${r.stdout}${r.stderr}`.includes("ready for action")) return;
    await new Promise((resolveWait) => setTimeout(resolveWait, 1000));
  }
  throw new Error("worker-media no quedó listo en 120 s");
}

/**
 * Levanta `worker-media` apuntando al bucket de las pruebas (`klick-e2e`) y, al terminar, lo
 * devuelve a su configuración normal. Si no está habilitado no hace nada.
 */
export default async function globalSetup(): Promise<(() => void) | void> {
  if (!mediaWorkerEnabled()) return;
  docker([...COMPOSE, "up", "-d", "--build", "worker-media"], { S3_BUCKET: "klick-e2e" });
  await waitReady();
  return () => {
    if (process.env.CI) docker([...COMPOSE, "stop", "worker-media"]);
    else docker([...COMPOSE, "up", "-d", "worker-media"]); // vuelve al bucket de desarrollo
  };
}
