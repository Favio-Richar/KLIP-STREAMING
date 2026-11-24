"use client";

import { create } from "zustand";
import { hashFile, localResumeStore, uploadApi, xhrPutPart } from "./upload-adapters";
import { UploadTask } from "./uploader";

/** Cuántos archivos se suben a la vez (cada uno ya sube varias partes en paralelo). */
export const MAX_ACTIVE_UPLOADS = 2;

const FINAL = new Set(["done", "error", "canceled"]);
const ACTIVE = new Set(["hashing", "starting", "uploading", "completing"]);

interface UploadsState {
  tasks: UploadTask[];
  /** Encola archivos. `rightsConfirmed` debe ser verdadero: la API lo exige y lo registra. */
  add: (files: File[], opts: { orgId: string; rightsConfirmed: boolean }) => void;
  remove: (id: string) => void;
  clearFinished: () => void;
}

export const useUploads = create<UploadsState>()((set, get) => {
  /** Arranca tareas en espera mientras haya cupo. */
  const pump = () => {
    const { tasks } = get();
    let active = tasks.filter((t) => ACTIVE.has(t.getSnapshot().status)).length;
    for (const task of tasks) {
      if (active >= MAX_ACTIVE_UPLOADS) break;
      if (task.getSnapshot().status === "queued") {
        active++;
        void task.start();
      }
    }
  };

  return {
    tasks: [],
    add: (files, { orgId, rightsConfirmed }) => {
      if (!rightsConfirmed) return;
      const created = files.map((file) => {
        const task = new UploadTask(
          { file, orgId, rightsConfirmed },
          {
            api: uploadApi,
            putPart: xhrPutPart,
            hash: hashFile,
            store: localResumeStore,
          },
        );
        // cuando una subida termina, libera su cupo para la siguiente
        let last = task.getSnapshot().status;
        task.subscribe(() => {
          const now = task.getSnapshot().status;
          if (now !== last && (FINAL.has(now) || now === "paused")) pump();
          last = now;
        });
        return task;
      });
      set((s) => ({ tasks: [...s.tasks, ...created] }));
      pump();
    },
    remove: (id) => {
      const task = get().tasks.find((t) => t.id === id);
      if (task && !FINAL.has(task.getSnapshot().status)) void task.cancel();
      set((s) => ({ tasks: s.tasks.filter((t) => t.id !== id) }));
    },
    clearFinished: () =>
      set((s) => ({ tasks: s.tasks.filter((t) => !FINAL.has(t.getSnapshot().status)) })),
  };
});
