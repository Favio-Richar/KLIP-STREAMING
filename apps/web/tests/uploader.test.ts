import { describe, expect, it, vi } from "vitest";
import {
  ApiFailure,
  PartFailure,
  UploadTask,
  fingerprintOf,
  type PutPart,
  type ResumeStore,
  type UploadApi,
  type UploadDeps,
  type UploadInfo,
} from "../src/lib/uploader";

const PART = 10;

function info(over: Partial<UploadInfo> = {}): UploadInfo {
  return {
    id: "up1",
    asset_id: "as1",
    status: "open",
    part_size: PART,
    total_parts: 4,
    size_bytes: 35,
    uploaded_parts: [],
    expires_at: "2099-01-01T00:00:00Z",
    ...over,
  };
}

function file(size = 35): File {
  return new File([new Uint8Array(size)], "video.mp4", { type: "video/mp4", lastModified: 1 });
}

class MemoryStore implements ResumeStore {
  data = new Map<string, string>();
  get = (k: string) => this.data.get(k) ?? null;
  set = (k: string, v: string) => void this.data.set(k, v);
  delete = (k: string) => void this.data.delete(k);
}

function setup(over: { api?: Partial<UploadApi>; put?: PutPart; deps?: Partial<UploadDeps> } = {}) {
  const uploaded: number[] = [];
  const api: UploadApi = {
    start: vi.fn(async () => ({ deduplicated: false, upload: info(), asset: { id: "as1" } })),
    state: vi.fn(async () => info()),
    partUrls: vi.fn(async (_id: string, numbers: number[]) =>
      Object.fromEntries(numbers.map((n) => [String(n), `https://s3/part/${n}`])),
    ),
    complete: vi.fn(async () => ({ id: "as1" })),
    abort: vi.fn(async () => undefined),
    ...over.api,
  };
  const put: PutPart =
    over.put ??
    (async (url, blob, o) => {
      o.onProgress(blob.size);
      uploaded.push(Number(url.split("/").pop()));
    });
  const store = new MemoryStore();
  const sleep = vi.fn(async (_ms: number) => undefined);
  const deps: UploadDeps = {
    api,
    putPart: put,
    store,
    sleep,
    backoffMs: 1,
    newId: () => "task1",
    ...over.deps,
  };
  const task = new UploadTask(
    { file: file(), orgId: "org1", rightsConfirmed: true, title: "Mi video" },
    deps,
  );
  return { task, api, store, sleep, uploaded };
}

const sorted = (xs: number[]) => [...xs].sort((a, b) => a - b);

describe("UploadTask", () => {
  it("sube todas las partes, completa una vez y reporta el 100 %", async () => {
    const { task, api, uploaded, store } = setup();
    await task.start();
    const s = task.getSnapshot();
    expect(s.status).toBe("done");
    expect(sorted(uploaded)).toEqual([1, 2, 3, 4]);
    expect(s).toMatchObject({ loaded: 35, partsDone: 4, partsTotal: 4, assetId: "as1" });
    expect(api.complete).toHaveBeenCalledTimes(1);
    expect(api.partUrls).toHaveBeenCalledTimes(1); // un solo lote de URLs
    expect(store.get(fingerprintOf(file(), "org1"))).toBeNull(); // ya no hay nada que reanudar
    expect(api.start).toHaveBeenCalledWith(
      expect.objectContaining({ filename: "video.mp4", size_bytes: 35, rights_confirmed: true }),
      "up-task1",
    );
  });

  it("respeta el límite de partes simultáneas", async () => {
    let active = 0;
    let peak = 0;
    const put: PutPart = async (_u, blob, o) => {
      active++;
      peak = Math.max(peak, active);
      await new Promise((r) => setTimeout(r, 5));
      o.onProgress(blob.size);
      active--;
    };
    const { task } = setup({ put, deps: { concurrency: 2 } });
    await task.start();
    expect(task.getSnapshot().status).toBe("done");
    expect(peak).toBe(2);
  });

  it("sube cada parte con su rango exacto de bytes", async () => {
    const sizes = new Map<number, number>();
    const put: PutPart = async (url, blob) =>
      void sizes.set(Number(url.split("/").pop()), blob.size);
    const { task } = setup({ put });
    await task.start();
    expect([...sizes.entries()].sort()).toEqual([
      [1, 10],
      [2, 10],
      [3, 10],
      [4, 5], // la última parte lleva el resto
    ]);
  });

  it("deduplica: con hash conocido por el servidor no se sube nada", async () => {
    const start = vi.fn(async () => ({
      deduplicated: true,
      upload: null,
      asset: { id: "copia" },
    }));
    const hash = vi.fn(async () => "ab".repeat(32));
    const { task, uploaded } = setup({ api: { start }, deps: { hash } });
    await task.start();
    expect(task.getSnapshot()).toMatchObject({
      status: "done",
      deduplicated: true,
      assetId: "copia",
    });
    expect(uploaded).toEqual([]);
    expect(start).toHaveBeenCalledWith(
      expect.objectContaining({ sha256: "ab".repeat(32) }),
      expect.any(String),
    );
  });

  it("si el hash falla sigue sin deduplicación", async () => {
    const hash = vi.fn(async () => {
      throw new Error("sin memoria");
    });
    const { task, api } = setup({ deps: { hash } });
    await task.start();
    expect(task.getSnapshot().status).toBe("done");
    expect(api.start).toHaveBeenCalledWith(
      expect.objectContaining({ sha256: undefined }),
      expect.any(String),
    );
  });

  it("no calcula el hash de archivos por encima del límite", async () => {
    const hash = vi.fn(async () => "x");
    const { task } = setup({ deps: { hash, hashLimitBytes: 10 } });
    await task.start();
    expect(hash).not.toHaveBeenCalled();
  });

  it("reintenta fallos transitorios con espera exponencial", async () => {
    let failures = 0;
    const put: PutPart = async (url, blob, o) => {
      if (url.endsWith("/2") && failures < 2) {
        failures++;
        throw new PartFailure(null, true);
      }
      o.onProgress(blob.size);
    };
    const { task, sleep } = setup({ put, deps: { concurrency: 1 } });
    await task.start();
    expect(task.getSnapshot().status).toBe("done");
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([1, 2]);
  });

  it("renueva la URL firmada cuando caduca", async () => {
    let expiredOnce = false;
    const put: PutPart = async (url, _b, o) => {
      if (url === "https://s3/part/3" && !expiredOnce) {
        expiredOnce = true;
        throw new PartFailure(403, false, true);
      }
      o.onProgress(1);
    };
    const partUrls = vi.fn(async (_id: string, numbers: number[]) =>
      Object.fromEntries(numbers.map((n) => [String(n), `https://s3/part/${n}`])),
    );
    const { task } = setup({ put, api: { partUrls }, deps: { concurrency: 1 } });
    await task.start();
    expect(task.getSnapshot().status).toBe("done");
    expect(partUrls.mock.calls.some(([, nums]) => nums[0] === 3 && nums.length === 1)).toBe(true);
  });

  it("un error no reintentable detiene la subida y resume() conserva lo ya subido", async () => {
    let broken = true;
    const done: number[] = [];
    const put: PutPart = async (url, blob, o) => {
      const n = Number(url.split("/").pop());
      if (n === 3 && broken) throw new PartFailure(400, false);
      o.onProgress(blob.size);
      done.push(n);
    };
    const { task, api } = setup({ put, deps: { concurrency: 1 } });
    await task.start();
    expect(task.getSnapshot()).toMatchObject({ status: "error", errorCode: "part_failed" });
    expect(api.complete).not.toHaveBeenCalled();

    broken = false;
    done.length = 0;
    await task.resume();
    expect(task.getSnapshot().status).toBe("done");
    expect(sorted(done)).toEqual([3, 4]); // 1 y 2 no se repitieron
  });

  it("pausa cancelando las partes en vuelo y reanuda solo con lo que falta", async () => {
    const release: Array<() => void> = [];
    const done: number[] = [];
    let gate = true;
    const put: PutPart = (url, blob, o) =>
      new Promise((resolve, reject) => {
        const n = Number(url.split("/").pop());
        if (gate && n >= 3) {
          o.signal.addEventListener("abort", () => reject(new PartFailure(null, true)));
          release.push(() => resolve());
          return;
        }
        o.onProgress(blob.size);
        done.push(n);
        resolve();
      });
    const { task } = setup({ put, deps: { concurrency: 2 } });
    const running = task.start();
    await vi.waitFor(() => expect(release.length).toBe(2)); // partes 3 y 4 colgadas
    task.pause();
    await running;
    expect(task.getSnapshot().status).toBe("paused");
    expect(sorted(done)).toEqual([1, 2]);

    gate = false;
    done.length = 0;
    await task.resume();
    expect(task.getSnapshot().status).toBe("done");
    expect(sorted(done)).toEqual([3, 4]);
  });

  it("se reanuda tras recargar la página usando lo que el servidor ya tiene", async () => {
    const { task, api, uploaded, store } = setup({
      api: { state: vi.fn(async () => info({ uploaded_parts: [1, 2] })) },
    });
    store.set(fingerprintOf(file(), "org1"), "up1");
    await task.start();
    expect(api.start).not.toHaveBeenCalled();
    expect(sorted(uploaded)).toEqual([3, 4]);
    expect(task.getSnapshot().status).toBe("done");
  });

  it("si la subida guardada ya no está abierta empieza una nueva", async () => {
    const { task, api, store } = setup({
      api: { state: vi.fn(async () => info({ status: "completed" })) },
    });
    store.set(fingerprintOf(file(), "org1"), "vieja");
    await task.start();
    expect(api.start).toHaveBeenCalledTimes(1);
    expect(task.getSnapshot().status).toBe("done");
  });

  it("vuelve a subir las partes que el servidor reporta faltantes al completar", async () => {
    const complete = vi
      .fn()
      .mockRejectedValueOnce(new ApiFailure(409, "upload_incomplete", [2]))
      .mockResolvedValue({ id: "as1" });
    const { task, uploaded } = setup({ api: { complete } });
    await task.start();
    expect(task.getSnapshot().status).toBe("done");
    expect(uploaded.filter((n) => n === 2)).toHaveLength(2);
    expect(complete).toHaveBeenCalledTimes(2);
  });

  it("reintenta completar ante un 5xx pero no ante un 4xx", async () => {
    const flaky = vi
      .fn()
      .mockRejectedValueOnce(new ApiFailure(503, "unavailable"))
      .mockResolvedValue({ id: "as1" });
    const a = setup({ api: { complete: flaky } });
    await a.task.start();
    expect(a.task.getSnapshot().status).toBe("done");

    const hard = vi.fn().mockRejectedValue(new ApiFailure(422, "upload_size_mismatch"));
    const b = setup({ api: { complete: hard } });
    await b.task.start();
    expect(b.task.getSnapshot()).toMatchObject({
      status: "error",
      errorCode: "upload_size_mismatch",
    });
    expect(hard).toHaveBeenCalledTimes(1);
  });

  it("muestra el código de la API cuando rechaza el inicio", async () => {
    const start = vi.fn().mockRejectedValue(new ApiFailure(422, "rights_not_confirmed"));
    const { task, uploaded } = setup({ api: { start } });
    await task.start();
    expect(task.getSnapshot()).toMatchObject({
      status: "error",
      errorCode: "rights_not_confirmed",
    });
    expect(uploaded).toEqual([]);
  });

  it("cancelar aborta la subida en el servidor y olvida el progreso guardado", async () => {
    const put: PutPart = (_u, _b, o) =>
      new Promise((_resolve, reject) => {
        o.signal.addEventListener("abort", () => reject(new PartFailure(null, true)));
      });
    const { task, api, store } = setup({ put });
    const running = task.start();
    await vi.waitFor(() => expect(task.getSnapshot().status).toBe("uploading"));
    await vi.waitFor(() => expect(api.partUrls).toHaveBeenCalled());
    await task.cancel();
    await running;
    expect(task.getSnapshot().status).toBe("canceled");
    expect(api.abort).toHaveBeenCalledWith("up1");
    expect(store.get(fingerprintOf(file(), "org1"))).toBeNull();
    await task.start(); // una subida cancelada no se reactiva
    expect(task.getSnapshot().status).toBe("canceled");
  });

  it("notifica a los suscriptores y entrega instantáneas inmutables", async () => {
    const { task } = setup();
    const seen: string[] = [];
    const first = task.getSnapshot();
    task.subscribe(() => seen.push(task.getSnapshot().status));
    await task.start();
    expect(seen).toContain("uploading");
    expect(seen.at(-1)).toBe("done");
    expect(first.status).toBe("queued"); // la instantánea anterior no mutó
    expect(task.getSnapshot()).not.toBe(first);
  });
});

describe("fingerprintOf", () => {
  it("distingue organización, nombre, tamaño y fecha", () => {
    const a = fingerprintOf(file(), "o1");
    expect(a).toBe(fingerprintOf(file(), "o1"));
    expect(a).not.toBe(fingerprintOf(file(), "o2"));
    expect(a).not.toBe(fingerprintOf(file(36), "o1"));
  });
});
