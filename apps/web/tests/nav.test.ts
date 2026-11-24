import { describe, expect, it } from "vitest";
import { safeNext, slugify } from "@/lib/nav";

describe("safeNext", () => {
  it("acepta rutas internas con query", () => {
    expect(safeNext("/settings/members?x=1")).toBe("/settings/members?x=1");
  });
  it.each([
    "//evil.com",
    "https://evil.com",
    "/\\evil.com",
    "javascript:alert(1)",
    "",
    null,
    undefined,
  ])("rechaza %s", (v) => expect(safeNext(v as string | null | undefined)).toBe("/"));
  it("usa el respaldo indicado", () => expect(safeNext("x", "/login")).toBe("/login"));
});

describe("slugify", () => {
  it("quita tildes, símbolos y bordes", () => {
    expect(slugify("  Visionary AI Group! ")).toBe("visionary-ai-group");
    expect(slugify("Niño Ñandú")).toBe("nino-nandu");
  });
  it("respeta el máximo de 40 sin terminar en guion", () => {
    const s = slugify("a".repeat(39) + " b" + "c".repeat(10));
    expect(s.length).toBeLessThanOrEqual(40);
    expect(s.endsWith("-")).toBe(false);
  });
  it("devuelve vacío si no queda nada válido", () => expect(slugify("¡¡¡")).toBe(""));
});
