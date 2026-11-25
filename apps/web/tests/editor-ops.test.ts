import { semanticIssues } from "@klick/video-project-schema";
import { describe, expect, it } from "vitest";

import { canRedo, canUndo, commit, createHistory, redo, seal, undo } from "@/lib/editor/history";
import {
  contentDuration,
  deleteItem,
  find,
  moveItem,
  patchItem,
  snap,
  snapBlock,
  snapPoints,
  splitItem,
  trimEnd,
  trimStart,
  type Doc,
} from "@/lib/editor/ops";

const ASSET = "0190a1b2-c3d4-7e5f-8a6b-7c8d9e0f1a2b";

function makeDoc(): Doc {
  return {
    schema_version: "1.0",
    canvas: { width: 1080, height: 1920, fps: 30, background: "#000000" },
    sources: [
      { id: "s1", asset_id: ASSET, kind: "video" },
      { id: "m1", asset_id: ASSET.replace(/b$/, "d"), kind: "audio" },
    ],
    layout: { mode: "fit", fit: { background: { type: "solid", color: "#0B0D10" } } },
    timeline: {
      duration_ms: 20000,
      tracks: [
        {
          id: "v1",
          kind: "video",
          items: [
            { id: "a", source_id: "s1", source_in_ms: 10000, source_out_ms: 20000, start_ms: 0 },
            {
              id: "b",
              source_id: "s1",
              source_in_ms: 40000,
              source_out_ms: 48000,
              start_ms: 12000,
            },
          ],
        },
        {
          id: "a1",
          kind: "audio",
          items: [
            {
              id: "m",
              source_id: "m1",
              source_in_ms: 0,
              source_out_ms: 20000,
              start_ms: 0,
              fade_in_ms: 500,
              fade_out_ms: 700,
            },
          ],
        },
        {
          id: "t1",
          kind: "text",
          items: [{ id: "x", text: "Hola", start_ms: 1000, end_ms: 4000, style: {}, position: {} }],
        },
      ],
    },
  } as unknown as Doc;
}

const ids = () => {
  let n = 0;
  return () => `n${++n}`;
};
const valid = (doc: Doc) => expect(semanticIssues(doc)).toEqual([]);

describe("split", () => {
  it("divide un clip de video en dos tramos contiguos de la fuente", () => {
    const { doc, rightId } = splitItem(makeDoc(), "a", 4000, ids());
    expect(rightId).toBe("n1");
    const left = find(doc, "a")!.item as { source_in_ms: number; source_out_ms: number };
    const right = find(doc, "n1")!.item as {
      source_in_ms: number;
      source_out_ms: number;
      start_ms: number;
    };
    expect([left.source_in_ms, left.source_out_ms]).toEqual([10000, 14000]);
    expect([right.source_in_ms, right.source_out_ms, right.start_ms]).toEqual([14000, 20000, 4000]);
    valid(doc);
  });

  it("respeta la velocidad al elegir el punto de corte", () => {
    const base = patchItem(makeDoc(), "a", { speed: 2 }); // 10 s de fuente → 5 s en la línea
    expect(contentDuration(base)).toBe(20000);
    const { doc } = splitItem(base, "a", 2000, ids());
    const left = find(doc, "a")!.item as { source_out_ms: number };
    expect(left.source_out_ms).toBe(14000); // 2 s de línea = 4 s de fuente
    valid(doc);
  });

  it("reparte los fundidos: entrada a la izquierda, salida a la derecha", () => {
    const { doc } = splitItem(makeDoc(), "m", 5000, ids());
    const left = find(doc, "m")!.item as { fade_in_ms?: number; fade_out_ms?: number };
    const right = find(doc, "n1")!.item as { fade_in_ms?: number; fade_out_ms?: number };
    expect([left.fade_in_ms, left.fade_out_ms]).toEqual([500, undefined]);
    expect([right.fade_in_ms, right.fade_out_ms]).toEqual([undefined, 700]);
    valid(doc);
  });

  it("divide texto por tiempo y rechaza cortes demasiado cerca de un borde", () => {
    const { doc } = splitItem(makeDoc(), "x", 2500, ids());
    expect((find(doc, "x")!.item as { end_ms: number }).end_ms).toBe(2500);
    expect((find(doc, "n1")!.item as { start_ms: number }).start_ms).toBe(2500);
    const same = splitItem(makeDoc(), "x", 1050, ids());
    expect(same.rightId).toBeNull();
    expect(same.doc).toEqual(makeDoc());
    expect(splitItem(makeDoc(), "nope", 100, ids()).rightId).toBeNull();
  });

  it("reparte los fotogramas clave entre las dos mitades", () => {
    const withKf = patchItem(makeDoc(), "a", {
      keyframes: [
        { t_ms: 1000, x: 0.1 },
        { t_ms: 6000, x: 0.5 },
      ],
    });
    const { doc } = splitItem(withKf, "a", 4000, ids());
    const left = find(doc, "a")!.item as { keyframes: Array<{ t_ms: number }> };
    const right = find(doc, "n1")!.item as { keyframes: Array<{ t_ms: number }> };
    expect(left.keyframes.map((k) => k.t_ms)).toEqual([1000]);
    expect(right.keyframes.map((k) => k.t_ms)).toEqual([2000]);
  });
});

describe("trim", () => {
  it("recorta el borde izquierdo avanzando la entrada de la fuente", () => {
    const doc = trimStart(makeDoc(), "a", 3000);
    const item = find(doc, "a")!.item as { start_ms: number; source_in_ms: number };
    expect([item.start_ms, item.source_in_ms]).toEqual([3000, 13000]);
    valid(doc);
  });

  it("recorta el borde derecho y respeta el largo de la fuente", () => {
    const shorter = trimEnd(makeDoc(), "a", 6000);
    expect((find(shorter, "a")!.item as { source_out_ms: number }).source_out_ms).toBe(16000);
    const longer = trimEnd(makeDoc(), "a", 11000, { sourceDurationMs: 25000 });
    expect((find(longer, "a")!.item as { source_out_ms: number }).source_out_ms).toBe(21000);
    // sin pasarse del archivo ni de la entrada siguiente (b empieza en 12000)
    const capped = trimEnd(makeDoc(), "a", 12000, { sourceDurationMs: 15000 });
    expect((find(capped, "a")!.item as { source_out_ms: number }).source_out_ms).toBe(15000);
    valid(longer);
  });

  it("no deja pasar el borde izquierdo antes del inicio del archivo ni sobre el vecino", () => {
    const doc = trimStart(makeDoc(), "b", -500); // vecino 'a' termina en 10000
    expect((find(doc, "b")!.item as { start_ms: number }).start_ms).toBe(10000);
    const fromZero = trimStart(makeDoc(), "m", -100);
    expect((find(fromZero, "m")!.item as { start_ms: number }).start_ms).toBe(0);
    valid(doc);
  });

  it("mantiene un largo mínimo", () => {
    const doc = trimEnd(makeDoc(), "x", 1010);
    expect((find(doc, "x")!.item as { end_ms: number }).end_ms).toBe(1100);
  });

  it("recorta con velocidad: la línea de tiempo y la fuente crecen en proporción", () => {
    const fast = patchItem(makeDoc(), "a", { speed: 2 });
    const doc = trimEnd(fast, "a", 3000);
    expect((find(doc, "a")!.item as { source_out_ms: number }).source_out_ms).toBe(16000);
    valid(doc);
  });
});

describe("move", () => {
  it("se detiene en el vecino en vez de solaparse", () => {
    const doc = moveItem(makeDoc(), "a", 5000); // 'a' dura 10 s; 'b' empieza en 12000
    expect((find(doc, "a")!.item as { start_ms: number }).start_ms).toBe(2000);
    valid(doc);
    const back = moveItem(makeDoc(), "b", 3000); // no puede entrar en 'a'
    expect((find(back, "b")!.item as { start_ms: number }).start_ms).toBe(10000);
    expect((find(moveItem(makeDoc(), "a", -500), "a")!.item as { start_ms: number }).start_ms).toBe(
      0,
    );
  });

  it("mueve texto conservando su duración y amplía la duración total", () => {
    const doc = moveItem(makeDoc(), "x", 19000);
    const text = find(doc, "x")!.item as { start_ms: number; end_ms: number };
    expect([text.start_ms, text.end_ms]).toEqual([19000, 22000]);
    expect(doc.timeline.duration_ms).toBe(22000);
    valid(doc);
  });

  it("devuelve el mismo objeto si no hay cambio", () => {
    const doc = makeDoc();
    expect(moveItem(doc, "a", 0)).toBe(doc);
  });
});

describe("delete", () => {
  it("elimina y recalcula la duración", () => {
    const doc = deleteItem(makeDoc(), "b");
    expect(find(doc, "b")).toBeNull();
    expect(doc.timeline.duration_ms).toBe(20000); // el audio sigue en 20 s
  });

  it("ripple cierra el hueco de la pista", () => {
    const doc = deleteItem(makeDoc(), "a", true);
    expect((find(doc, "b")!.item as { start_ms: number }).start_ms).toBe(2000);
    valid(doc);
  });
});

describe("snap", () => {
  const doc = makeDoc();
  it("reúne bordes, cabezal y cero", () => {
    expect(snapPoints(doc, "a", 7000)).toEqual([0, 1000, 4000, 7000, 12000, 20000]);
  });
  it("se pega solo dentro del umbral", () => {
    expect(snap(4090, [0, 4000, 9000], 120)).toBe(4000);
    expect(snap(4300, [0, 4000, 9000], 120)).toBe(4300);
  });
  it("ajusta el borde de un bloque que se arrastra", () => {
    expect(snapBlock(3950, 2000, [0, 4000, 6000], 100)).toBe(4000);
    expect(snapBlock(3000, 2950, [0, 6000], 100)).toBe(3050); // pega el FIN
  });
});

describe("historial", () => {
  it("deshace y rehace, y un cambio nuevo descarta el futuro", () => {
    const a = makeDoc();
    const b = moveItem(a, "x", 2000);
    const c = deleteItem(b, "b");
    let h = commit(commit(createHistory(a), b), c);
    expect(canUndo(h)).toBe(true);
    h = undo(h);
    expect(h.present).toBe(b);
    h = undo(h);
    expect(h.present).toBe(a);
    expect(canUndo(h)).toBe(false);
    h = redo(h);
    expect(h.present).toBe(b);
    expect(canRedo(h)).toBe(true);
    h = commit(h, deleteItem(b, "a"));
    expect(canRedo(h)).toBe(false);
  });

  it("funde un arrastre en un solo paso y lo separa al sellar", () => {
    const a = makeDoc();
    let h = createHistory(a);
    for (const t of [1100, 1200, 1300]) h = commit(h, moveItem(h.present, "x", t), "drag:x");
    expect(h.past.length).toBe(1); // un solo paso para todo el gesto
    expect(undo(h).present).toBe(a);
    h = seal(h);
    h = commit(h, moveItem(h.present, "x", 2000), "drag:x");
    expect(h.past.length).toBe(2);
  });

  it("ignora cambios que no cambian nada y limita su tamaño", () => {
    const a = makeDoc();
    const h = createHistory(a);
    expect(commit(h, a)).toBe(h);
    expect(undo(h)).toBe(h);
    expect(redo(h)).toBe(h);
  });
});
