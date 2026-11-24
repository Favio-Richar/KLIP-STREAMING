import { createHmac, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { expect, type Page } from "@playwright/test";

// Contraseña efímera por ejecución: nunca queda un valor fijo en el repositorio.
export const PASSWORD = `Aa1-${randomBytes(12).toString("hex")}`;

export interface TestUser {
  name: string;
  email: string;
  password: string;
}

export function newUser(label: string): TestUser {
  const id = randomBytes(4).toString("hex");
  return {
    name: `${label} ${id}`,
    email: `${label.toLowerCase()}.${id}@example.com`,
    password: PASSWORD,
  };
}

/** Lee del log de la API el último correo enviado a `to` y devuelve el primer enlace del cuerpo. */
export async function linkFromEmail(to: string, contains: string): Promise<string> {
  const path = process.env.E2E_API_LOG;
  if (!path) throw new Error("E2E_API_LOG no está definido");
  let found: string | null = null;
  await expect
    .poll(
      () => {
        const lines = readFileSync(path, "utf8").split(/\r?\n/);
        for (let i = lines.length - 1; i >= 0; i--) {
          const line = lines[i];
          if (!line?.includes("email_console")) continue;
          try {
            const rec = JSON.parse(line) as { to?: string; email_body?: string };
            if (rec.to !== to) continue;
            const url = rec.email_body?.match(/https?:\/\/\S+/)?.[0];
            if (url?.includes(contains)) {
              found = url;
              return true;
            }
          } catch {
            /* línea que no es JSON */
          }
        }
        return false;
      },
      {
        message: `correo para ${to} con ${contains}`,
        timeout: 15_000,
        intervals: [250, 500, 1000],
      },
    )
    .toBe(true);
  return found as unknown as string;
}

const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
function base32Decode(input: string): Buffer {
  let bits = "";
  for (const ch of input.replace(/=+$/, "").toUpperCase()) {
    const v = B32.indexOf(ch);
    if (v < 0) throw new Error("base32 inválido");
    bits += v.toString(2).padStart(5, "0");
  }
  const bytes: number[] = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

/** Código TOTP (RFC 6238: SHA-1, 6 dígitos, 30 s) para `step` ventanas respecto de ahora. */
export function totp(secret: string, step = 0, now = Date.now()): string {
  const counter = Math.floor(now / 1000 / 30) + step;
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(counter));
  const h = createHmac("sha1", base32Decode(secret)).update(msg).digest();
  const off = h[h.length - 1]! & 0xf;
  const code = (h.readUInt32BE(off) & 0x7fffffff) % 1_000_000;
  return code.toString().padStart(6, "0");
}

export async function register(page: Page, user: TestUser): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Nombre").fill(user.name);
  await page.getByLabel("Correo").fill(user.email);
  await page.getByLabel("Contraseña").fill(user.password);
  await page.getByRole("button", { name: "Crear cuenta" }).click();
  await expect(page.getByRole("heading", { name: "Revisa tu correo" })).toBeVisible();
}

export async function verifyEmail(page: Page, user: TestUser): Promise<void> {
  const link = await linkFromEmail(user.email, "/verify-email");
  await page.goto(link);
  await expect(page.getByText("Tu correo quedó confirmado.")).toBeVisible();
}

/**
 * Ingresa con correo y contraseña. Por defecto espera a salir de /login (sesión abierta); con
 * `wait: "none"` no espera, para los casos en que el ingreso debe fallar o pedir un segundo factor.
 */
export async function login(
  page: Page,
  user: TestUser,
  next?: string,
  opts: { wait?: "app" | "none" } = {},
): Promise<void> {
  await page.goto(next ? `/login?next=${encodeURIComponent(next)}` : "/login");
  await page.getByLabel("Correo").fill(user.email);
  await page.getByLabel("Contraseña").fill(user.password);
  await page.getByRole("button", { name: "Ingresar" }).click();
  if ((opts.wait ?? "app") === "app") {
    await page.waitForURL((u) => !u.pathname.startsWith("/login"));
  }
}

export async function createOrg(page: Page, name: string): Promise<void> {
  await page.goto("/orgs/new");
  await page.getByLabel("Nombre").fill(name);
  await page.getByRole("button", { name: "Crear organización" }).click();
  await expect(page.getByText(`Estás trabajando en ${name}.`)).toBeVisible();
}

export async function logout(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Menú de tu cuenta" }).click();
  await page.getByRole("menuitem", { name: "Cerrar sesión" }).click();
  await expect(page).toHaveURL(/\/login/);
}
