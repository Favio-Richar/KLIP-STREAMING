import { mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { createOrg, login, newUser, register, verifyEmail } from "../support/helpers";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const SHOTS = join(root, "docs", "entregas", "F0");
const THEMES = ["dark", "light"] as const;
const WIDTHS = [375, 1024, 1440] as const;

const PUBLIC = ["/login", "/register", "/forgot-password"] as const;
const PRIVATE = ["/", "/library", "/settings/members", "/settings/profile", "/orgs/new"] as const;

async function setTheme(page: Page, theme: (typeof THEMES)[number]) {
  await page.evaluate((t) => {
    localStorage.setItem("klick.theme", t);
    document.documentElement.setAttribute("data-theme", t);
  }, theme);
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  return r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
}

test.describe("pantallas públicas", () => {
  for (const theme of THEMES) {
    for (const path of PUBLIC) {
      test(`${path} sin violaciones de accesibilidad (${theme})`, async ({ page }) => {
        await page.goto(path);
        await setTheme(page, theme);
        await expect(page.locator("h1")).toBeVisible();
        expect(await axe(page)).toEqual([]);
      });
    }
  }

  test("sin desplazamiento horizontal en móvil", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    for (const path of PUBLIC) {
      await page.goto(path);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      );
      expect(overflow, path).toBeLessThanOrEqual(0);
    }
  });

  test("el foco del teclado es visible y los controles táctiles miden al menos 40 px en móvil", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto("/login");
    await page.keyboard.press("Tab"); // enlace "Saltar al contenido"
    const skip = page.getByRole("link", { name: "Saltar al contenido" });
    await expect(skip).toBeFocused();
    await page.getByLabel("Correo").focus();
    const outline = await page
      .getByLabel("Correo")
      .evaluate((el) => getComputedStyle(el).outlineStyle);
    expect(outline).toBe("solid");
    const targets = [
      page.getByRole("button", { name: "Ingresar" }),
      page.getByRole("link", { name: "Continuar con Google" }),
    ];
    for (const target of targets) {
      const box = await target.boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(40);
    }
  });

  test("las páginas inexistentes y los errores usan pantallas propias", async ({ page }) => {
    const res = await page.goto("/esta-ruta-no-existe");
    expect(res?.status()).toBe(404);
    await expect(page.getByRole("heading", { name: "No encontramos esta página" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Ir al inicio" })).toBeVisible();
  });
});

test.describe("sin efectos prohibidos en la interfaz renderizada", () => {
  test("ningún elemento usa blur, sombras difusas ni fondos translúcidos", async ({ page }) => {
    await page.goto("/login");
    const offenders = await page.evaluate(() => {
      const bad: string[] = [];
      for (const el of Array.from(document.querySelectorAll("body *"))) {
        const s = getComputedStyle(el);
        const backdrop = s.getPropertyValue("backdrop-filter");
        if (backdrop && backdrop !== "none") bad.push(`${el.tagName} backdrop-filter`);
        if (s.filter && s.filter !== "none") bad.push(`${el.tagName} filter ${s.filter}`);
        const shadow = s.boxShadow;
        if (shadow && shadow !== "none") {
          // Solo se permiten sombras duras: el tercer valor numérico (desenfoque) debe ser 0.
          for (const layer of shadow.split(/,(?![^(]*\))/)) {
            const nums = layer.replace(/rgba?\([^)]*\)/g, "").match(/-?\d+(?:\.\d+)?px/g) ?? [];
            if (nums.length >= 3 && parseFloat(nums[2]!) > 0)
              bad.push(`${el.tagName} box-shadow ${layer.trim()}`);
          }
        }
      }
      return bad;
    });
    expect(offenders).toEqual([]);
  });
});

test.describe("instalable como PWA", () => {
  test("manifiesto, iconos y service worker válidos", async ({ page, request }) => {
    await page.goto("/login");
    const href = await page.locator('link[rel="manifest"]').getAttribute("href");
    expect(href).toBe("/manifest.webmanifest");
    const manifest = (await (await request.get("/manifest.webmanifest")).json()) as {
      display: string;
      start_url: string;
      icons: Array<{ src: string; sizes: string; purpose?: string }>;
    };
    expect(manifest.display).toBe("standalone");
    expect(manifest.icons.some((i) => i.sizes === "192x192")).toBe(true);
    expect(manifest.icons.some((i) => i.sizes === "512x512" && i.purpose === "maskable")).toBe(
      true,
    );
    for (const icon of manifest.icons) {
      const res = await request.get(icon.src);
      expect(res.status(), icon.src).toBe(200);
    }
    const sw = await request.get("/sw.js");
    expect(sw.status()).toBe(200);
    expect(sw.headers()["content-type"]).toContain("javascript");
    expect(sw.headers()["cache-control"]).toContain("no-cache");
    expect((await request.get("/offline.html")).status()).toBe(200);
  });

  test("el service worker nunca guarda respuestas de la API", async ({ request }) => {
    const source = await (await request.get("/sw.js")).text();
    expect(source).toContain('url.pathname.startsWith("/api/")');
    expect(source).not.toMatch(/cache\.put\([^)]*api/i);
  });
});

test.describe("pantallas autenticadas", () => {
  test("accesibilidad en ambos temas, sin desbordes y con capturas de entrega", async ({
    page,
  }) => {
    mkdirSync(SHOTS, { recursive: true });
    const user = newUser("Calidad");
    await register(page, user);
    await verifyEmail(page, user);
    await login(page, user);
    await createOrg(page, `Calidad ${user.name.split(" ")[1]}`);

    // Capturas de las pantallas públicas (cada una en sus dos temas y tres anchos)
    for (const theme of THEMES) {
      for (const width of WIDTHS) {
        await page.setViewportSize({ width, height: 900 });
        for (const path of PRIVATE) {
          await page.goto(path);
          await setTheme(page, theme);
          await expect(page.locator("h1").first()).toBeVisible();
          await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
          const overflow = await page.evaluate(
            () => document.documentElement.scrollWidth - window.innerWidth,
          );
          expect(overflow, `${path} @${width}`).toBeLessThanOrEqual(0);
          if (width === 1440 || width === 375) {
            const slug = path === "/" ? "panel" : path.replace(/^\//, "").replace(/\//g, "-");
            await page.screenshot({ path: join(SHOTS, `${slug}-${theme}-${width}.png`) });
          }
        }
      }
    }

    // Accesibilidad de cada pantalla autenticada en ambos temas
    await page.setViewportSize({ width: 1440, height: 900 });
    for (const theme of THEMES) {
      for (const path of PRIVATE) {
        await page.goto(path);
        await setTheme(page, theme);
        await expect(page.locator("h1").first()).toBeVisible();
        await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
        expect(await axe(page), `${path} (${theme})`).toEqual([]);
      }
    }
  });
});
