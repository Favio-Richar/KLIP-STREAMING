import { expect, test } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { mediaWorkerEnabled } from "../support/media-worker";
import { createOrg, login, newUser, register, verifyEmail } from "../support/helpers";

/**
 * F1-04: un video real se sube, el worker-media lo analiza por contenido con FFmpeg, genera sus
 * derivados y la biblioteca lo muestra "Listo" con su duración. Necesita el worker real:
 * `E2E_MEDIA_WORKER=1 pnpm test:e2e` (en CI siempre corre).
 */
test.skip(!mediaWorkerEnabled(), "requiere el worker-media real (E2E_MEDIA_WORKER=1)");

test("F1-04: el video subido pasa de Procesando a Listo con su duración", async ({ page }) => {
  test.setTimeout(180_000);
  const user = newUser("Medios");
  await register(page, user);
  await verifyEmail(page, user);
  await login(page, user);
  await createOrg(page, `Medios ${user.name.split(" ")[1]}`);

  await page.goto("/library");
  await page.getByLabel("Confirmo que tengo derecho a usar este contenido.").check();
  await page
    .getByTestId("file-input")
    .setInputFiles(resolve(dirname(fileURLToPath(import.meta.url)), "../fixtures/sample.mp4"));
  await expect(page.getByTestId("upload-row")).toHaveAttribute("data-status", "done", {
    timeout: 60_000,
  });

  // el análisis ocurre en el worker; la lista se refresca sola
  const asset = page.getByTestId("asset-row");
  await expect(asset).toHaveCount(1);
  await expect(asset).toContainText("Listo", { timeout: 120_000 });
  await expect(asset).toContainText("Video");
  await expect(asset).toContainText("0:03");

  // un archivo con extensión de video pero contenido falso termina en error, sin romper nada
  await page.getByTestId("file-input").setInputFiles({
    name: "falso.mp4",
    mimeType: "video/mp4",
    buffer: Buffer.from("esto no es un video\n".repeat(100)),
  });
  const fake = page.getByTestId("asset-row").filter({ hasText: "falso" });
  await expect(fake).toContainText("Con error", { timeout: 120_000 });
  await expect(page.getByTestId("asset-row").filter({ hasText: "sample" })).toContainText("Listo");
});
