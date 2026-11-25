import { beforeEach, describe, expect, it } from "vitest";

import { moveItem, type Doc } from "@/lib/editor/ops";
import { useEditor } from "@/lib/editor/store";

function makeDoc(): Doc {
  return {
    schema_version: "1.0",
    canvas: { width: 1080, height: 1920, fps: 30, background: "#000000" },
    sources: [{ id: "s1", asset_id: "0190a1b2-c3d4-7e5f-8a6b-7c8d9e0f1a2b", kind: "video" }],
    layout: { mode: "fit", fit: { background: { type: "solid", color: "#0B0D10" } } },
    timeline: {
      duration_ms: 10000,
      tracks: [
        {
          id: "v1",
          kind: "video",
          items: [{ id: "a", source_id: "s1", source_in_ms: 0, source_out_ms: 4000, start_ms: 0 }],
        },
      ],
    },
  } as unknown as Doc;
}

const start = () => {
  const item = useEditor.getState().history.present.timeline.tracks[0]?.items[0] as {
    start_ms: number;
  };
  return item.start_ms;
};

describe("estado del editor", () => {
  beforeEach(() => useEditor.getState().load("clip-1", makeDoc(), 1));

  it("un cambio deja el editor sin guardar y guardar lo vuelve a 'guardado' (sin bucle)", () => {
    const st = useEditor.getState();
    expect(st.save).toBe("saved");
    st.apply((d) => moveItem(d, "a", 1000));
    expect(useEditor.getState().save).toBe("dirty");

    const sent = useEditor.getState().history.present;
    const canonical = { ...sent } as Doc; // el servidor devuelve otra copia del mismo documento
    useEditor.getState().markSaved(sent, 2, canonical);
    const after = useEditor.getState();
    expect(after.save).toBe("saved");
    expect(after.revision).toBe(2);
    expect(after.history.present).toBe(canonical);
    expect(after.history.past.length).toBe(1); // adoptar la copia canónica no crea un paso de historial
  });

  it("si se editó mientras se guardaba, sigue sucio y conserva la edición nueva", () => {
    useEditor.getState().apply((d) => moveItem(d, "a", 1000));
    const sent = useEditor.getState().history.present;
    useEditor.getState().apply((d) => moveItem(d, "a", 2000)); // edición durante el guardado
    useEditor.getState().markSaved(sent, 2, { ...sent } as Doc);
    expect(useEditor.getState().save).toBe("dirty");
    expect(useEditor.getState().revision).toBe(2);
    expect(start()).toBe(2000);
  });

  it("deshacer y rehacer marcan sucio; un conflicto no se pisa con ediciones", () => {
    useEditor.getState().apply((d) => moveItem(d, "a", 1000));
    useEditor
      .getState()
      .markSaved(useEditor.getState().history.present, 2, useEditor.getState().history.present);
    useEditor.getState().undo();
    expect(useEditor.getState().save).toBe("dirty");
    expect(start()).toBe(0);
    useEditor.getState().setSave("conflict");
    useEditor.getState().redo();
    expect(useEditor.getState().save).toBe("conflict");
    useEditor.getState().apply((d) => moveItem(d, "a", 3000));
    expect(useEditor.getState().save).toBe("conflict");
  });

  it("cargar otro clip reinicia cabezal, selección e historial", () => {
    const st = useEditor.getState();
    st.seek(1234);
    st.select("a");
    st.apply((d) => moveItem(d, "a", 500));
    st.load("clip-2", makeDoc(), 7);
    const after = useEditor.getState();
    expect([after.clipId, after.revision, after.playheadMs, after.selectedId]).toEqual([
      "clip-2",
      7,
      0,
      null,
    ]);
    expect(after.history.past).toHaveLength(0);
  });

  it("los marcadores se alternan y se mantienen ordenados", () => {
    const st = useEditor.getState();
    st.toggleMarker(3000);
    st.toggleMarker(1000);
    expect(useEditor.getState().markers).toEqual([1000, 3000]);
    useEditor.getState().toggleMarker(3010); // cerca de uno existente: lo quita
    expect(useEditor.getState().markers).toEqual([1000]);
  });
});
