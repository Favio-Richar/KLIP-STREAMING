#!/usr/bin/env node
// Corre las pruebas con FFmpeg real dentro de la imagen `worker-media` (FFmpeg no existe en el
// host). La imagen se une a la red de Compose para alcanzar PostgreSQL, Redis y MinIO por nombre.
// Requiere `pnpm infra:up`. Uso: pnpm test:media [argumentos extra de pytest]
import { spawnSync } from "node:child_process";

const IMAGE = "klick-media-test";
const NETWORK = process.env.KLICK_COMPOSE_NETWORK ?? "klick_default";
const run = (args, opts = {}) =>
  spawnSync("docker", args, {
    stdio: "inherit",
    env: { ...process.env, MSYS_NO_PATHCONV: "1" },
    ...opts,
  });

const build = run([
  "build",
  "-f",
  "infra/docker/api.Dockerfile",
  "--target",
  "media-test",
  "-t",
  IMAGE,
  ".",
]);
if (build.status !== 0) process.exit(build.status ?? 1);

const pg = process.env.POSTGRES_PASSWORD ?? "klick_dev_password";
const user = process.env.POSTGRES_USER ?? "klick_owner";
const args = [
  "run",
  "--rm",
  "--network",
  NETWORK,
  "-e",
  `DATABASE_URL_OWNER=postgresql+asyncpg://${user}:${pg}@postgres:5432/klick`,
  "-e",
  "REDIS_URL=redis://redis:6379/0",
  "-e",
  "S3_ENDPOINT=http://minio:9000",
  "-e",
  "S3_PUBLIC_ENDPOINT=http://minio:9000",
  "-e",
  `S3_ACCESS_KEY=${process.env.S3_ACCESS_KEY ?? "klick_minio"}`,
  "-e",
  `S3_SECRET_KEY=${process.env.S3_SECRET_KEY ?? "klick_minio_secret"}`,
  IMAGE,
  ...(process.argv.length > 2 ? ["pytest", ...process.argv.slice(2)] : []),
];
process.exit(run(args).status ?? 1);
