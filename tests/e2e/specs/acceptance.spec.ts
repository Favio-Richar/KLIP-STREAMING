import { expect, test } from "@playwright/test";
import {
  createOrg,
  linkFromEmail,
  login,
  logout,
  newUser,
  register,
  totp,
  verifyEmail,
} from "../support/helpers";

/**
 * Aceptación de la fase 0 (docs/10): un usuario se registra, activa MFA, crea una organización,
 * invita a otro con rol `viewer` y este no puede ejecutar acciones de escritura; el job
 * `system.ping` muestra progreso en vivo por SSE.
 */
test("F0: registro, MFA, organización, invitación de lector y progreso en vivo", async ({
  browser,
}) => {
  const owner = newUser("Owner");
  const viewer = newUser("Viewer");
  const orgName = `Estudio ${owner.name.split(" ")[1]}`;

  const ownerCtx = await browser.newContext({ locale: "es-CL" });
  const page = await ownerCtx.newPage();

  // 1) registro + verificación de correo + ingreso
  await register(page, owner);
  await verifyEmail(page, owner);
  await login(page, owner);
  await expect(page.getByRole("heading", { name: "Aún no tienes una organización" })).toBeVisible();

  // 2) organización
  await createOrg(page, orgName);

  // 3) MFA: activar, confirmar con un código TOTP real y guardar los códigos de recuperación
  await page.goto("/settings/profile");
  await page.getByRole("button", { name: "Activar" }).click();
  const secret = await page.getByLabel("O ingresa esta clave a mano").inputValue();
  expect(secret).toMatch(/^[A-Z2-7]{16,}$/);
  await expect(page.getByAltText("Código QR para tu app de autenticación")).toBeVisible();
  await page.getByLabel("Código de verificación").fill(totp(secret));
  await page.getByRole("button", { name: "Confirmar y activar" }).click();
  const codes = page.getByRole("dialog").getByRole("listitem");
  await expect(codes).toHaveCount(10);
  await page.getByRole("button", { name: "Ya los guardé" }).click();
  await expect(page.getByText("Activada")).toBeVisible();

  // 4) el siguiente ingreso exige el segundo factor; un código equivocado se rechaza
  await logout(page);
  await login(page, owner, undefined, { wait: "none" });
  await expect(page).toHaveURL(/\/login\/mfa/);
  await page.getByLabel("Código de verificación").fill("000000");
  await page.getByRole("button", { name: "Verificar" }).click();
  await expect(page.locator("#main").getByRole("alert")).toContainText("El código no es correcto");
  // El código de la ventana siguiente es válido y no fue usado al activar (anti-replay).
  await page.getByLabel("Código de verificación").fill(totp(secret, 1));
  await page.getByRole("button", { name: "Verificar" }).click();
  await expect(page.getByText(`Estás trabajando en ${orgName}.`)).toBeVisible();

  // 5) invitar a un lector
  await page.goto("/settings/members");
  await page.getByLabel("Correo").fill(viewer.email);
  await page.getByLabel("Rol", { exact: true }).selectOption("viewer");
  await page.getByRole("button", { name: "Enviar invitación" }).click();
  await expect(page.getByText(`Invitación enviada a ${viewer.email}`)).toBeVisible();
  await expect(
    page.locator("#main").getByRole("listitem").filter({ hasText: viewer.email }),
  ).toBeVisible();

  // 6) el invitado se registra, verifica, acepta y queda como lector
  const viewerCtx = await browser.newContext({ locale: "es-CL" });
  const vpage = await viewerCtx.newPage();
  const invite = await linkFromEmail(viewer.email, "/invitations/accept");
  await register(vpage, viewer);
  await verifyEmail(vpage, viewer);
  const inviteUrl = new URL(invite);
  await login(vpage, viewer, inviteUrl.pathname + inviteUrl.search);
  await expect(vpage.getByRole("heading", { name: "Invitación a una organización" })).toBeVisible();
  await vpage.getByRole("button", { name: "Aceptar invitación" }).click();
  await expect(vpage.getByText(`Estás trabajando en ${orgName}.`)).toBeVisible();
  await expect(vpage.getByText("En esta organización tienes el rol Lector.")).toBeVisible();

  // 7) el lector no puede ejecutar acciones de escritura: ni en la interfaz ni en la API
  await expect(vpage.getByRole("button", { name: "Ejecutar prueba" })).toBeDisabled();
  await vpage.goto("/settings/members");
  await expect(vpage.getByText("solo quienes administran miembros pueden cambiarlo")).toBeVisible();
  await expect(vpage.getByRole("button", { name: "Enviar invitación" })).toHaveCount(0);
  await expect(vpage.getByRole("combobox", { name: /^Rol de/ })).toHaveCount(0);
  const orgs = (await (await vpage.request.get("/api/v1/orgs")).json()) as Array<{ id: string }>;
  const csrf = (await vpage.context().cookies()).find((c) => c.name === "klick_csrf")?.value ?? "";
  const denied = await vpage.request.post("/api/v1/jobs", {
    headers: { "X-CSRF-Token": csrf, "X-Org-ID": orgs[0]!.id },
    data: { kind: "system.ping", payload: { steps: 1 } },
  });
  expect(denied.status()).toBe(403);

  // 8) el propietario ve el progreso en vivo hasta completar
  await page.goto("/");
  await page.getByRole("button", { name: "Ejecutar prueba" }).click();
  await expect(page.getByText("En vivo")).toBeVisible();
  const bar = page.getByRole("progressbar");
  await expect(bar).toBeVisible();
  await expect.poll(async () => Number(await bar.getAttribute("aria-valuenow"))).toBeGreaterThan(0);
  await expect(page.getByText("Completado")).toBeVisible({ timeout: 30_000 });
  await expect(bar).toHaveAttribute("aria-valuenow", "100");

  await ownerCtx.close();
  await viewerCtx.close();
});
