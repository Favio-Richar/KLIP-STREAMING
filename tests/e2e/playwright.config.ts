import { mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const API_PORT = Number(process.env.E2E_API_PORT ?? 8010);
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3100);
const WEB_URL = `http://localhost:${WEB_PORT}`;
const logDir = join(root, ".e2e");
mkdirSync(logDir, { recursive: true });

// Los correos de desarrollo salen por la consola de la API: se guardan en un archivo para leerlos.
process.env.E2E_API_LOG = join(logDir, "api.log");

const apiEnv = {
  LOG_JSON: "true",
  // Las pruebas crean varias cuentas desde la misma IP: se sube el tope de tasa solo aquí.
  RATE_LIMIT_REGISTER_IP: "1000",
  RATE_LIMIT_LOGIN_IP: "1000",
  // Bucket propio: las subidas de prueba no ensucian el de desarrollo.
  S3_BUCKET: "klick-e2e",
  PUBLIC_WEB_URL: WEB_URL,
  PUBLIC_API_URL: WEB_URL, // las redirecciones de Google pasan por el proxy del web
};

export default defineConfig({
  testDir: "./specs",
  outputDir: join(root, "test-results"),
  fullyParallel: false, // comparten un único servidor y base de datos
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: WEB_URL,
    locale: "es-CL",
    timezoneId: "America/Santiago",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  // Usa el Chrome instalado (en CI: `playwright install chrome`): evita descargar un navegador aparte.
  projects: [{ name: "chrome", use: { ...devices["Desktop Chrome"], channel: "chrome" } }],
  webServer: [
    {
      command: `uv run --package klick uvicorn klick.main:app --port ${API_PORT} > "${join(
        logDir,
        "api.log",
      )}" 2>&1`,
      cwd: root,
      url: `http://127.0.0.1:${API_PORT}/readyz`,
      env: apiEnv,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `uv run --package klick dramatiq klick.worker_main --queues default --processes 1 --threads 2`,
      cwd: root,
      // Dramatiq escribe su registro en stderr; "ready for action" indica que ya consume la cola.
      wait: { stderr: /ready for action/ },
      env: { LOG_JSON: "true" },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `pnpm --filter web exec next dev --port ${WEB_PORT}`,
      cwd: root,
      url: `${WEB_URL}/login`,
      env: { KLICK_API_URL: `http://127.0.0.1:${API_PORT}` },
      reuseExistingServer: false,
      timeout: 180_000,
    },
  ],
});
