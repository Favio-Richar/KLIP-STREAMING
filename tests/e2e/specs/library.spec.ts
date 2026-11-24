import { expect, test } from "@playwright/test";
import { createOrg, login, newUser, register, verifyEmail } from "../support/helpers";

/**
 * F1-02: subida multipart directa al almacén desde el navegador, con confirmación de derechos.
 * Un archivo de 11 MiB se divide en dos partes (partes de 8 MiB) y termina en "Procesando".
 */
test("F1-02: subir un archivo por partes exige confirmar derechos y aparece en la biblioteca", async ({
  page,
}) => {
  const user = newUser("Subidas");
  await register(page, user);
  await verifyEmail(page, user);
  await login(page, user);
  await createOrg(page, `Biblioteca ${user.name.split(" ")[1]}`);

  await page.goto("/library");
  await expect(page.getByRole("heading", { name: "Biblioteca", level: 1 })).toBeVisible();
  await expect(page.getByText("Todavía no hay archivos")).toBeVisible();

  // sin confirmar derechos no se puede elegir ningún archivo
  const choose = page.getByRole("button", { name: "Elegir archivos" });
  await expect(choose).toBeDisabled();
  await expect(page.getByText("Marca la confirmación de derechos")).toBeVisible();
  await page.getByLabel("Confirmo que tengo derecho a usar este contenido.").check();
  await expect(choose).toBeEnabled();

  await page.getByTestId("file-input").setInputFiles({
    name: "directo de ajedrez.mp4",
    mimeType: "video/mp4",
    buffer: Buffer.alloc(11 * 1024 * 1024, 7),
  });

  const row = page.getByTestId("upload-row");
  await expect(row).toContainText("directo de ajedrez.mp4");
  await expect(row).toHaveAttribute("data-status", "done", { timeout: 90_000 });

  // el archivo aparece en la biblioteca, ya recibido y a la espera de análisis
  const asset = page.getByTestId("asset-row");
  await expect(asset).toHaveCount(1);
  await expect(asset).toContainText("directo de ajedrez");
  await expect(asset).toContainText("Video");
  await expect(asset).toContainText("Procesando");

  // un tipo no permitido se rechaza con un mensaje claro y no deja nada a medias
  await page.getByTestId("file-input").setInputFiles({
    name: "programa.exe",
    mimeType: "application/x-msdownload",
    buffer: Buffer.alloc(1024, 1),
  });
  const rejected = page.getByTestId("upload-row").filter({ hasText: "programa.exe" });
  await expect(rejected).toHaveAttribute("data-status", "error", { timeout: 30_000 });
  await expect(rejected.getByRole("alert")).toContainText("formato no está permitido");
  await expect(page.getByTestId("asset-row")).toHaveCount(1);
});
