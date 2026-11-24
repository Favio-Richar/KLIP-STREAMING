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

/** Puertos que el E2E usa en exclusiva. Windows no termina el árbol de procesos de `next dev`:
 * queda un servidor huérfano que, con su caché vieja, responde 404 en la siguiente ejecución. */
const E2E_PORTS = [
  Number(process.env.E2E_WEB_PORT ?? 3100),
  Number(process.env.E2E_API_PORT ?? 8010),
];

function freePorts(): void {
  if (process.platform !== "win32") return;
  const list = E2E_PORTS.join(",");
  spawnSync(
    "powershell",
    [
      "-NoProfile",
      "-Command",
      `Get-NetTCPConnection -State Listen -LocalPort ${list} -ErrorAction SilentlyContinue | ` +
        "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }",
    ],
    { stdio: "ignore" },
  );
}

/**
 * Levanta `worker-media` apuntando al bucket de las pruebas (`klick-e2e`) y, al terminar, lo
 * devuelve a su configuración normal. Si no está habilitado no hace nada.
 */
export default async function globalSetup(): Promise<() => void> {
  // Los servidores web/API ya arrancaron (Playwright los inicia antes): solo se limpian al final.
  if (!mediaWorkerEnabled()) return freePorts;
  docker([...COMPOSE, "up", "-d", "--build", "worker-media"], { S3_BUCKET: "klick-e2e" });
  await waitReady();
  return () => {
    freePorts();
    if (process.env.CI) docker([...COMPOSE, "stop", "worker-media"]);
    else docker([...COMPOSE, "up", "-d", "worker-media"]); // vuelve al bucket de desarrollo
  };
}
