import assert from "node:assert/strict";
import { test } from "node:test";
import { blurredBoxShadow, checkSource } from "./check-design-rules.mjs";

const rules = (src) => checkSource(src).map((v) => v.rule);

test("rechaza backdrop-filter y backdrop-blur", () => {
  assert.deepEqual(rules(".a { backdrop-filter: blur(8px); }").includes("backdrop-filter"), true);
  assert.ok(rules('<div className="backdrop-blur-md p-2" />').includes("backdrop-blur"));
  assert.ok(rules('<div className="md:backdrop-blur" />').includes("backdrop-blur"));
});

test("rechaza filter: blur y clases blur-*", () => {
  assert.ok(rules(".a { filter: blur(4px); }").includes("filter-blur"));
  assert.ok(rules('<i className="blur-sm" />').includes("blur-class"));
  assert.ok(rules('<i className="a blur" />').includes("blur-class"));
  assert.ok(rules('<i className="hover:blur-[3px]" />').includes("blur-class"));
});

test("rechaza sombras de Tailwind salvo shadow-none", () => {
  assert.ok(rules('<i className="shadow" />').includes("shadow-class"));
  assert.ok(rules('<i className="shadow-lg" />').includes("shadow-class"));
  assert.ok(rules('<i className="hover:shadow-md" />').includes("shadow-class"));
  assert.ok(rules('<i className="shadow-[0_0_0_1px_red]" />').includes("shadow-class"));
  assert.deepEqual(rules('<i className="shadow-none" />'), []);
});

test("box-shadow: permite sombras duras y rechaza difusas", () => {
  assert.equal(blurredBoxShadow("box-shadow: 0 0 0 1px #343b48;"), null);
  assert.equal(blurredBoxShadow("box-shadow: none;"), null);
  assert.equal(blurredBoxShadow("box-shadow: 0 0 0 1px rgba(0,0,0,.4), 0 0 0 3px #b8f43c;"), null);
  assert.ok(blurredBoxShadow("box-shadow: 0 4px 12px rgba(0,0,0,.2);"));
  assert.ok(blurredBoxShadow("box-shadow: 0 0 0 1px #000, 0 8px 24px #0006;"));
  assert.ok(blurredBoxShadow('boxShadow: "0 2px 4px 0 black"'));
  assert.ok(rules(".a { box-shadow: 0 1px 2px 0 #000; }").includes("box-shadow-blur"));
  assert.deepEqual(rules(".a { box-shadow: 0 0 0 1px var(--border-2); }"), []);
});

test("rechaza drop-shadow, fondos translúcidos y degradados radiales", () => {
  assert.ok(rules('<i className="drop-shadow-lg" />').includes("drop-shadow"));
  assert.ok(rules('<i className="bg-opacity-50" />').includes("bg-opacity"));
  assert.ok(rules('<i className="bg-bg-1/50" />').includes("bg-alpha-modifier"));
  assert.ok(
    rules(".a { background: radial-gradient(circle, red, blue); }").includes("radial-gradient"),
  );
});

test("no da falsos positivos con código legítimo", () => {
  assert.deepEqual(rules('<i className="bg-bg-1 border border-border-1 rounded-card" />'), []);
  assert.deepEqual(rules("const blurry = true; // sin relación"), []);
  assert.deepEqual(rules('<i className="text-shadow-none" />'), []);
  assert.deepEqual(rules("--scrim: rgba(0, 0, 0, 0.64);"), []);
});

test("las excepciones documentadas se respetan en la misma línea o la anterior", () => {
  assert.deepEqual(
    rules(".a { backdrop-filter: blur(2px); } /* design-rule-exception: contenido del usuario */"),
    [],
  );
  assert.deepEqual(
    rules("/* design-rule-exception: motivo revisado */\n.a { filter: blur(2px); }"),
    [],
  );
  assert.equal(rules("/* design-rule-exception: x */\n\n.a { filter: blur(2px); }").length, 1);
});
