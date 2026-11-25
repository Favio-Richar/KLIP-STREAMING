import { expect, test } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { mediaWorkerEnabled } from "../support/media-worker";
import { createOrg, login, newUser, register, verifyEmail } from "../support/helpers";

/**
 * F1-10: el editor de clips con un video real. Crear el clip desde la biblioteca, dividir con el
 * atajo `C`, guardado automático, persistencia tras recargar, deshacer y exportar hasta tener el
 * archivo listo para descargar. Necesita el worker real (análisis de medios y render).
 */
test.skip(!mediaWorkerEnabled(), "requiere el worker-media real (E2E_MEDIA_WORKER=1)");

test("F1-10: crear un clip, editarlo en la línea de tiempo y exportarlo", async ({ page }) => {
  test.setTimeout(360_000);
  const user = newUser("Editor");
  await register(page, user);
  await verifyEmail(page, user);
  await login(page, user);
  await createOrg(page, `Editor ${user.name.split(" ")[1]}`);

  await page.goto("/library");
  await page.getByLabel("Confirmo que tengo derecho a usar este contenido.").check();
  await page
    .getByTestId("file-input")
    .setInputFiles(resolve(dirname(fileURLToPath(import.meta.url)), "../fixtures/sample.mp4"));
  const card = page.getByTestId("asset-row");
  await expect(card).toContainText("Listo", { timeout: 120_000 });

  // de la biblioteca al editor
  await card.getByRole("link").click();
  await page.getByRole("button", { name: "Crear clip" }).click();
  await expect(page).toHaveURL(/\/clips\/[0-9a-f-]{36}$/);
  const editor = page.getByTestId("clip-editor");
  await expect(editor).toBeVisible();
  await expect(page.getByTestId("save-state")).toHaveText("Guardado");

  const blocks = page.locator('[data-testid^="block-"]');
  await expect(blocks.first()).toBeVisible();
  const initial = await blocks.count();
  expect(initial).toBeGreaterThan(0);

  // la vista previa pinta el video real (el canvas no está vacío)
  const canvas = page.getByRole("img", { name: "Vista previa del clip" });
  await expect(canvas).toBeVisible();

  // mover el cabezal a ~1 s con la regla y comprobar el código de tiempo
  const ruler = page.getByTestId("timeline-ruler");
  await ruler.click({ position: { x: 96 + 80, y: 10 } });
  await expect(page.getByTestId("timecode")).toContainText("00:01.");

  // dividir con el atajo C: aparecen más bloques y el editor queda "sin guardar"
  await page.keyboard.press("c");
  await expect(blocks).toHaveCount(initial * 2);
  await expect(page.getByTestId("save-state")).not.toHaveText("Guardado");

  // guardado automático
  await expect(page.getByTestId("save-state")).toHaveText("Guardado", { timeout: 15_000 });

  // deshacer revierte la división (Ctrl+Z) y se vuelve a guardar; rehacer la recupera
  await page.keyboard.press("Control+z");
  await expect(blocks).toHaveCount(initial);
  await expect(page.getByTestId("save-state")).toHaveText("Guardado", { timeout: 15_000 });
  await page.keyboard.press("Control+Shift+z");
  await expect(blocks).toHaveCount(initial * 2);
  await expect(page.getByTestId("save-state")).toHaveText("Guardado", { timeout: 15_000 });

  // lo guardado persiste tras recargar (el historial local empieza de cero)
  await page.reload();
  await expect(page.getByTestId("clip-editor")).toBeVisible();
  await expect(blocks).toHaveCount(initial * 2);

  // seleccionar un bloque abre el inspector; eliminar con Supr
  await blocks.first().click();
  await expect(page.getByRole("heading", { name: "Video", level: 2 })).toBeVisible();

  // exportar (Ctrl+E): progreso → listo → descarga disponible
  await page.keyboard.press("Control+e");
  await expect(page.getByRole("status").filter({ hasText: /Exportación lista/ })).toBeVisible({
    timeout: 240_000,
  });
  await expect(page.getByRole("button", { name: "Descargar" })).toBeVisible();
});

test("F1-10: otra sesión modificó el clip → conflicto sin pisar su trabajo", async ({ page }) => {
  test.setTimeout(240_000);
  const user = newUser("Conflicto");
  await register(page, user);
  await verifyEmail(page, user);
  await login(page, user);
  await createOrg(page, `Conflicto ${user.name.split(" ")[1]}`);

  await page.goto("/library");
  await page.getByLabel("Confirmo que tengo derecho a usar este contenido.").check();
  await page
    .getByTestId("file-input")
    .setInputFiles(resolve(dirname(fileURLToPath(import.meta.url)), "../fixtures/sample.mp4"));
  await expect(page.getByTestId("asset-row")).toContainText("Listo", { timeout: 120_000 });
  await page.getByTestId("asset-row").getByRole("link").click();
  await page.getByRole("button", { name: "Crear clip" }).click();
  await expect(page.getByTestId("clip-editor")).toBeVisible();

  // otra pestaña (misma sesión) guarda una revisión nueva por detrás
  const clipId = page.url().split("/").pop() as string;
  const other = await page.context().newPage();
  await other.goto(`/clips/${clipId}`);
  await expect(other.getByTestId("clip-editor")).toBeVisible();
  await other.getByTestId("timeline-ruler").click({ position: { x: 96 + 80, y: 10 } });
  await other.keyboard.press("c");
  await expect(other.getByTestId("save-state")).toHaveText("Guardado", { timeout: 15_000 });
  await other.close();

  // esta pestaña ya está desactualizada: su guardado recibe 412 y avisa en vez de pisar
  await page.getByTestId("timeline-ruler").click({ position: { x: 96 + 160, y: 10 } });
  await page.keyboard.press("c");
  await expect(page.getByTestId("save-state")).toHaveText("Conflicto", { timeout: 15_000 });
  await expect(page.getByTestId("clip-editor").getByRole("alert")).toContainText(
    "Otra sesión modificó este clip",
  );
});

test("F1-10: recortar por las asas y mover bloques con el mouse", async ({ page }) => {
  test.setTimeout(240_000);
  const user = newUser("Arrastre");
  await register(page, user);
  await verifyEmail(page, user);
  await login(page, user);
  await createOrg(page, `Arrastre ${user.name.split(" ")[1]}`);

  await page.goto("/library");
  await page.getByLabel("Confirmo que tengo derecho a usar este contenido.").check();
  await page
    .getByTestId("file-input")
    .setInputFiles(resolve(dirname(fileURLToPath(import.meta.url)), "../fixtures/sample.mp4"));
  await expect(page.getByTestId("asset-row")).toContainText("Listo", { timeout: 120_000 });
  await page.getByTestId("asset-row").getByRole("link").click();
  await page.getByRole("button", { name: "Crear clip" }).click();
  await expect(page.getByTestId("clip-editor")).toBeVisible();

  // el clip dura 3 s; a 0,08 px/ms, 80 px = 1 s
  const block = page.locator('[data-testid^="block-"]').first();
  await expect(block).toBeVisible();
  await block.click({ position: { x: 60, y: 10 } });
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  await expect(inspector).toContainText("0:03");

  // arrastrar el asa derecha 1 s hacia la izquierda: el clip pasa a durar 2 s
  const handle = page.locator('[data-testid^="trimR-"]').first();
  const box = await handle.boundingBox();
  if (!box) throw new Error("sin asa");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 - 80, box.y + box.height / 2, { steps: 8 });
  await page.mouse.up();
  await expect(inspector).toContainText("0:02");
  await expect(page.getByTestId("save-state")).toHaveText("Guardado", { timeout: 15_000 });

  // todo el arrastre es UN solo paso de historial: un Ctrl+Z devuelve los 3 s
  await page.keyboard.press("Control+z");
  await expect(inspector).toContainText("0:03");
});

// PNG de 1×1 px: suficiente para que el análisis lo reconozca como imagen
const PNG_1X1 = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64",
);

test("F1-11: añadir texto, una imagen y subtítulos manuales, y exportar", async ({ page }) => {
  test.setTimeout(420_000);
  const user = newUser("Capas");
  await register(page, user);
  await verifyEmail(page, user);
  await login(page, user);
  await createOrg(page, `Capas ${user.name.split(" ")[1]}`);

  // biblioteca: un video y una imagen
  await page.goto("/library");
  await page.getByLabel("Confirmo que tengo derecho a usar este contenido.").check();
  await page
    .getByTestId("file-input")
    .setInputFiles(resolve(dirname(fileURLToPath(import.meta.url)), "../fixtures/sample.mp4"));
  await expect(page.getByTestId("asset-row").filter({ hasText: "sample" })).toContainText("Listo", {
    timeout: 120_000,
  });
  await page
    .getByTestId("file-input")
    .setInputFiles({ name: "logo.png", mimeType: "image/png", buffer: PNG_1X1 });
  await expect(page.getByTestId("asset-row").filter({ hasText: "logo" })).toContainText("Listo", {
    timeout: 120_000,
  });

  await page.getByTestId("asset-row").filter({ hasText: "sample" }).getByRole("link").click();
  await page.getByRole("button", { name: "Crear clip" }).click();
  await expect(page.getByTestId("clip-editor")).toBeVisible();
  const blocks = page.locator('[data-testid^="block-"]');
  await expect(blocks.first()).toBeVisible();
  const initial = await blocks.count();
  const inspector = page.getByRole("complementary", { name: "Inspector" });

  // --- texto: se añade en el cabezal y se edita en el inspector
  await page.getByRole("button", { name: "Añadir texto" }).click();
  await expect(blocks).toHaveCount(initial + 1);
  await expect(inspector.getByRole("heading", { name: "Texto", level: 2 })).toBeVisible();
  await inspector.getByRole("textbox", { name: "Texto" }).fill("Hola Klick");
  await expect(page.getByRole("button", { name: /Hola Klick/ }).first()).toBeVisible();
  await inspector.getByLabel("Posición vertical").fill("80");
  await expect(inspector).toContainText("Posición vertical: 80 %");

  // --- imagen de la biblioteca como capa
  await page.getByRole("tab", { name: "Imágenes" }).click();
  await page.getByRole("button", { name: "Añadir imagen logo" }).click();
  await expect(blocks).toHaveCount(initial + 2);
  await expect(inspector.getByRole("heading", { name: "Imagen", level: 2 })).toBeVisible();

  // --- subtítulos: crear la pista y escribir dos
  await page.getByRole("tab", { name: "Subtítulos" }).click();
  await page.getByRole("button", { name: "Crear subtítulos" }).click();
  await page.getByRole("button", { name: "Añadir en el cabezal" }).click();
  await page.getByRole("textbox", { name: "Texto del subtítulo 1" }).fill("Primer subtítulo");
  await page.getByRole("button", { name: "Añadir en el cabezal" }).click();
  await expect(page.getByTestId("cue-1")).toBeVisible();
  await page.getByRole("textbox", { name: "Texto del subtítulo 2" }).fill("Segundo subtítulo");

  // solapar el segundo con el primero marca error y no se guarda hasta corregirlo
  const start2 = page.getByRole("textbox", { name: "Inicio del subtítulo 2 (s)" });
  await start2.fill("0.1");
  await start2.press("Enter");
  await expect(page.getByTestId("cue-1").getByRole("alert")).toContainText("Se solapa");
  await expect(page.getByTestId("captions-state")).toHaveText("Hay errores");
  // con errores no se puede exportar ni guardar versión: nada se envía a medias
  await expect(page.getByRole("button", { name: "Exportar" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Guardar versión" })).toBeDisabled();
  await start2.fill("2.2");
  await start2.press("Enter");
  await expect(page.getByTestId("cue-1").getByRole("alert")).toHaveCount(0);

  // la línea de tiempo muestra el carril de subtítulos y todo se guarda solo
  await expect(page.getByTestId("cue-block-0")).toBeVisible();
  await expect(page.getByTestId("captions-state")).toHaveText("Guardado", { timeout: 15_000 });
  await expect(page.getByTestId("save-state")).toHaveText("Guardado", { timeout: 15_000 });

  // --- persiste tras recargar: texto, imagen y subtítulos
  await page.reload();
  await expect(page.getByTestId("clip-editor")).toBeVisible();
  await expect(blocks).toHaveCount(initial + 2);
  await page.getByRole("tab", { name: "Subtítulos" }).click();
  await expect(page.getByRole("textbox", { name: "Texto del subtítulo 1" })).toHaveValue(
    "Primer subtítulo",
  );
  await expect(page.getByRole("textbox", { name: "Texto del subtítulo 2" })).toHaveValue(
    "Segundo subtítulo",
  );

  // --- exportar: el worker quema los subtítulos y entrega el archivo
  await page.keyboard.press("Control+e");
  await expect(page.getByRole("status").filter({ hasText: /Exportación lista/ })).toBeVisible({
    timeout: 300_000,
  });
  await expect(page.getByRole("button", { name: "Descargar" })).toBeVisible();
});
