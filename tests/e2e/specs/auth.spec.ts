import { expect, test } from "@playwright/test";
import {
  PASSWORD,
  createOrg,
  linkFromEmail,
  login,
  logout,
  newUser,
  register,
  verifyEmail,
} from "../support/helpers";

test.describe("acceso", () => {
  test("una ruta protegida redirige al ingreso y conserva el destino", async ({ page }) => {
    await page.goto("/settings/members");
    await expect(page).toHaveURL(/\/login\?next=%2Fsettings%2Fmembers/);
  });

  test("credenciales incorrectas muestran un error claro sin revelar si la cuenta existe", async ({
    page,
  }) => {
    await page.goto("/login");
    // Correo distinto en cada ejecución: el limitador de intentos fallidos es por correo y persiste en Redis.
    await page.getByLabel("Correo").fill(newUser("Nadie").email);
    await page.getByLabel("Contraseña").fill("una-clave-cualquiera");
    await page.getByRole("button", { name: "Ingresar" }).click();
    await expect(page.locator("#main").getByRole("alert")).toContainText(
      "Correo o contraseña incorrectos.",
    );
    await expect(page).toHaveURL(/\/login/);
  });

  test("el registro valida la contraseña corta antes de enviar", async ({ page }) => {
    await page.goto("/register");
    await page.getByLabel("Nombre").fill("Ana");
    await page.getByLabel("Correo").fill("ana@example.com");
    await page.getByLabel("Contraseña").fill("corta");
    await page.getByRole("button", { name: "Crear cuenta" }).click();
    await expect(page.getByText("al menos 10 caracteres.")).toBeVisible();
  });

  test("el destino posterior al ingreso rechaza redirecciones a otros sitios", async ({ page }) => {
    const user = newUser("Redir");
    await register(page, user);
    await verifyEmail(page, user);
    await login(page, user, "//evil.example.com");
    await expect(page).toHaveURL(/localhost:\d+\/$/);
  });

  test("recuperar contraseña: enlace de un solo uso y las sesiones anteriores se cierran", async ({
    page,
    browser,
  }) => {
    const user = newUser("Reset");
    await register(page, user);
    await verifyEmail(page, user);
    await login(page, user);
    await expect(
      page.getByRole("heading", { name: "Aún no tienes una organización" }),
    ).toBeVisible();

    // Otra sesión abierta que debe cerrarse al cambiar la contraseña.
    const other = await browser.newContext({ locale: "es-CL" });
    const opage = await other.newPage();
    await login(opage, user);
    await expect(
      opage.getByRole("heading", { name: "Aún no tienes una organización" }),
    ).toBeVisible();

    const anon = await browser.newContext({ locale: "es-CL" });
    const apage = await anon.newPage();
    await apage.goto("/forgot-password");
    await apage.getByLabel("Correo").fill(user.email);
    await apage.getByRole("button", { name: "Enviar enlace" }).click();
    await expect(apage.getByRole("heading", { name: "Revisa tu correo" })).toBeVisible();

    const link = await linkFromEmail(user.email, "/reset-password");
    await apage.goto(link);
    const next = `${PASSWORD}-nueva`;
    await apage.getByLabel("Nueva contraseña").fill(next);
    await apage.getByLabel("Repite la contraseña").fill(next);
    await apage.getByRole("button", { name: "Guardar contraseña" }).click();
    await expect(apage).toHaveURL(/\/login\?reset=1/);
    await expect(apage.getByText("Contraseña actualizada.")).toBeVisible();

    // El mismo enlace ya no sirve.
    await apage.goto(link);
    await apage.getByLabel("Nueva contraseña").fill(`${next}-x`);
    await apage.getByLabel("Repite la contraseña").fill(`${next}-x`);
    await apage.getByRole("button", { name: "Guardar contraseña" }).click();
    await expect(apage.locator("#main").getByRole("alert")).toContainText("El enlace no es válido");

    // La contraseña vieja no entra; la nueva sí.
    await login(apage, user, undefined, { wait: "none" });
    await expect(apage.locator("#main").getByRole("alert")).toContainText(
      "Correo o contraseña incorrectos.",
    );
    await login(apage, { ...user, password: next });
    await expect(
      apage.getByRole("heading", { name: "Aún no tienes una organización" }),
    ).toBeVisible();

    // La sesión paralela quedó cerrada.
    await opage.goto("/");
    await expect(opage).toHaveURL(/\/login/);
    await other.close();
    await anon.close();
  });

  test("cerrar sesión devuelve al ingreso y el panel deja de ser accesible", async ({ page }) => {
    const user = newUser("Salida");
    await register(page, user);
    await verifyEmail(page, user);
    await login(page, user);
    await createOrg(page, `Org ${user.name.split(" ")[1]}`);
    await logout(page);
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);
  });
});
