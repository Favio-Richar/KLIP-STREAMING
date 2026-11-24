import { expect, test } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { mediaWorkerEnabled } from "../support/media-worker";
import { createOrg, login, newUser, register, verifyEmail } from "../support/helpers";

/**
 * F1-05: la biblioteca con un video real ya analizado: miniatura, búsqueda, filtros, detalle con
 * reproductor, renombrar, etiquetas, reanálisis y eliminación. Necesita el worker real.
 */
test.skip(!mediaWorkerEnabled(), "requiere el worker-media real (E2E_MEDIA_WORKER=1)");

test("F1-05: explorar, ver el detalle y gestionar un video de la biblioteca", async ({ page }) => {
  test.setTimeout(240_000);
  const user = newUser("Biblioteca");
  await register(page, user);
  await verifyEmail(page, user);
  await login(page, user);
  await createOrg(page, `Explorar ${user.name.split(" ")[1]}`);

  await page.goto("/library");
  await page.getByLabel("Confirmo que tengo derecho a usar este contenido.").check();
  await page
    .getByTestId("file-input")
    .setInputFiles(resolve(dirname(fileURLToPath(import.meta.url)), "../fixtures/sample.mp4"));
  const card = page.getByTestId("asset-row");
  await expect(card).toContainText("Listo", { timeout: 120_000 });

  // la tarjeta muestra la miniatura generada por el worker (una imagen real, no el ícono)
  const thumb = card.locator("img");
  await expect(thumb).toBeVisible();
  await expect
    .poll(() => thumb.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth))
    .toBeGreaterThan(0);
  await expect(card).toContainText("0:03");

  // búsqueda y filtros
  const search = page.getByRole("searchbox", { name: "Buscar" });
  await search.fill("zzz-no-existe");
  await expect(page.getByText("Sin resultados")).toBeVisible();
  await page.getByRole("button", { name: "Limpiar filtros" }).first().click();
  await expect(card).toHaveCount(1);
  await search.fill("sam");
  await expect(card).toHaveCount(1);
  await page.getByLabel("Tipo", { exact: true }).selectOption("audio");
  await expect(page.getByText("Sin resultados")).toBeVisible();
  await page.getByRole("button", { name: "Limpiar filtros" }).first().click();
  await page.getByLabel("Estado", { exact: true }).selectOption("failed");
  await expect(page.getByText("Sin resultados")).toBeVisible();
  await page.getByRole("button", { name: "Limpiar filtros" }).first().click();

  // vista de lista y de vuelta a cuadrícula (se recuerda la preferencia)
  await page.getByRole("button", { name: "Lista", exact: true }).click();
  await expect(page.getByRole("table")).toBeVisible();
  await page.getByRole("button", { name: "Cuadrícula", exact: true }).click();

  // detalle con reproductor del proxy
  await card.getByRole("link").click();
  await expect(page).toHaveURL(/\/library\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { name: "sample", level: 1 })).toBeVisible();
  const player = page.getByTestId("player");
  await expect(player).toBeVisible();
  await expect
    .poll(() => player.evaluate((v: HTMLVideoElement) => v.readyState))
    .toBeGreaterThan(0);
  expect(await player.evaluate((v: HTMLVideoElement) => v.videoHeight)).toBe(180);
  await expect(page.getByText("320 × 180")).toBeVisible();
  await expect(page.getByText("h264 / aac")).toBeVisible();

  // renombrar
  await page.getByRole("button", { name: "Renombrar" }).click();
  await page.getByLabel("Nombre del archivo").fill("Directo de ajedrez");
  await page.getByRole("button", { name: "Guardar" }).click();
  await expect(page.getByRole("heading", { name: "Directo de ajedrez", level: 1 })).toBeVisible();

  // etiquetas: crear una, queda asignada
  await page.getByLabel("Nueva etiqueta").fill("torneo");
  await page.getByRole("button", { name: "Agregar" }).click();
  await expect(page.getByRole("checkbox", { name: "torneo" })).toBeChecked();

  // volver: el nombre nuevo y el filtro por etiqueta funcionan
  await page.getByRole("link", { name: "Volver a la biblioteca" }).click();
  await expect(page.getByTestId("asset-row")).toContainText("Directo de ajedrez");
  await page.getByLabel("Etiqueta", { exact: true }).selectOption({ label: "torneo" });
  await expect(page.getByTestId("asset-row")).toHaveCount(1);
  await page.getByRole("button", { name: "Limpiar filtros" }).first().click();

  // volver a analizar: pasa por Procesando y regresa a Listo
  await page.getByTestId("asset-row").getByRole("link").click();
  await page.getByRole("button", { name: "Volver a analizar" }).click();
  await expect(page.getByText("Estamos analizando el archivo de nuevo.")).toBeVisible();
  await expect(page.getByTestId("player")).toBeVisible({ timeout: 120_000 });

  // eliminar con confirmación explícita
  await page.getByRole("button", { name: "Eliminar" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("¿Eliminar «Directo de ajedrez»?");
  await dialog.getByRole("button", { name: "Eliminar" }).click();
  await expect(page).toHaveURL(/\/library$/);
  await expect(page.getByText("Todavía no hay archivos")).toBeVisible();

  // un identificador inexistente muestra una pantalla propia, no un error
  await page.goto("/library/00000000-0000-0000-0000-000000000000");
  await expect(page.getByText("No encontramos este archivo")).toBeVisible();
});
