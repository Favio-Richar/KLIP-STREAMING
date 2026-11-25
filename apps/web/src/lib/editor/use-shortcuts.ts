"use client";

import { useEffect } from "react";
import { contentDuration, deleteItem, find, span, splitItem } from "./ops";
import { uniqueId } from "./sources";
import { useEditor } from "./store";

/** Atajos de docs/08 §3. No se activan al escribir en un campo de texto. */
export interface ShortcutActions {
  save: () => void;
  exportClip: () => void;
}

export function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

export function useShortcuts({ save, exportClip }: ShortcutActions): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target) || e.altKey) return;
      const st = useEditor.getState();
      const doc = st.history.present;
      if (!doc) return;
      const mod = e.ctrlKey || e.metaKey;
      const frame = 1000 / doc.canvas.fps;
      const key = e.key.toLowerCase();
      const stop = () => e.preventDefault();

      if (mod) {
        if (key === "z") {
          stop();
          if (e.shiftKey) st.redo();
          else st.undo();
        } else if (key === "y") {
          stop();
          st.redo();
        } else if (key === "s") {
          stop();
          save();
        } else if (key === "e") {
          stop();
          exportClip();
        }
        return;
      }

      switch (key) {
        case " ":
          stop();
          st.setPlaying(!st.playing);
          break;
        case "k":
          st.setPlaying(false);
          break;
        case "l":
          st.setPlaying(true);
          break;
        case "j":
          // sin reproducción inversa: retrocede un segundo (ADR de ejecución F1-10)
          st.setPlaying(false);
          st.seek(Math.max(0, st.playheadMs - 1000));
          break;
        case "i":
          st.setIn(st.playheadMs);
          break;
        case "o":
          st.setOut(st.playheadMs);
          break;
        case "m":
          st.toggleMarker(st.playheadMs);
          break;
        case "c": {
          // divide el elemento seleccionado, o todos los que cruza el cabezal si no hay selección
          const at = st.playheadMs;
          const targets = st.selectedId
            ? [st.selectedId]
            : doc.timeline.tracks.flatMap((track) =>
                track.items
                  .filter((item) => {
                    const s = span(track, item);
                    return s.start < at && at < s.end;
                  })
                  .map((item) => (item as { id: string }).id),
              );
          if (!targets.length) break;
          stop();
          st.apply((d) => {
            let next = d;
            for (const target of targets) {
              const current = next;
              next = splitItem(current, target, at, () => uniqueId(current)).doc;
            }
            return next;
          });
          st.sealHistory();
          break;
        }
        case "delete":
        case "backspace": {
          const id = st.selectedId;
          if (id && find(doc, id)) {
            stop();
            st.apply((d) => deleteItem(d, id, e.shiftKey));
            st.sealHistory();
            st.select(null);
          }
          break;
        }
        case "arrowleft":
        case "arrowright": {
          stop();
          const dir = key === "arrowleft" ? -1 : 1;
          const step = e.shiftKey ? 1000 : frame;
          st.setPlaying(false);
          st.seek(Math.min(Math.max(0, st.playheadMs + dir * step), contentDuration(doc)));
          break;
        }
        case "+":
        case "=":
          st.setZoom(st.zoom * 1.25);
          break;
        case "-":
          st.setZoom(st.zoom / 1.25);
          break;
        case "escape":
          st.select(null);
          break;
        default:
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save, exportClip]);
}
