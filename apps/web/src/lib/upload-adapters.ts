"use client";

import { createSHA256 } from "hash-wasm";
import { ApiProblem, api, unwrap } from "./api";
import {
  ApiFailure,
  PartFailure,
  type HashFile,
  type PutPart,
  type ResumeStore,
  type UploadApi,
  type UploadInfo,
} from "./uploader";

/** Traduce los errores de la API de Klick al tipo que entiende el cargador. */
async function call<T>(promise: Promise<T>): Promise<T> {
  try {
    return await promise;
  } catch (err) {
    if (err instanceof ApiProblem) {
      const missing = err.errors.find((e) => Array.isArray(e.missing_parts))?.missing_parts as
        | number[]
        | undefined;
      throw new ApiFailure(err.status, err.code, missing);
    }
    throw new ApiFailure(0, "network"); // fetch lanzó: sin conexión o petición cortada
  }
}

const toInfo = (u: UploadInfo): UploadInfo => u;

export const uploadApi: UploadApi = {
  start: (body, idempotencyKey) =>
    call(
      unwrap(
        api.POST("/api/v1/assets/uploads", {
          body,
          params: { header: { "Idempotency-Key": idempotencyKey } },
        }),
      ),
    ),
  state: (uploadId) =>
    call(
      unwrap(
        api.GET("/api/v1/assets/uploads/{upload_id}", {
          params: { path: { upload_id: uploadId } },
        }),
      ).then(toInfo),
    ),
  partUrls: (uploadId, partNumbers) =>
    call(
      unwrap(
        api.POST("/api/v1/assets/uploads/{upload_id}/parts", {
          params: { path: { upload_id: uploadId } },
          body: { part_numbers: partNumbers },
        }),
      ).then((r) => Object.fromEntries(Object.entries(r.urls).map(([k, v]) => [k, v]))),
    ),
  complete: (uploadId) =>
    call(
      unwrap(
        api.POST("/api/v1/assets/uploads/{upload_id}/complete", {
          params: { path: { upload_id: uploadId } },
        }),
      ),
    ),
  abort: async (uploadId) => {
    await call(
      unwrap(
        api.POST("/api/v1/assets/uploads/{upload_id}/abort", {
          params: { path: { upload_id: uploadId } },
        }),
      ),
    );
  },
};

/** PUT directo al almacén con progreso real (fetch aún no expone el progreso de subida). */
export const xhrPutPart: PutPart = (url, body, { signal, onProgress }) =>
  new Promise<void>((resolve, reject) => {
    if (signal.aborted) return reject(new PartFailure(null, true));
    const xhr = new XMLHttpRequest();
    const onAbort = () => xhr.abort();
    signal.addEventListener("abort", onAbort, { once: true });
    const done = () => signal.removeEventListener("abort", onAbort);
    xhr.open("PUT", url);
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => {
      done();
      if (xhr.status >= 200 && xhr.status < 300) return resolve();
      // 403 = URL firmada caducada o reloj desfasado: se renueva y se reintenta
      reject(
        new PartFailure(
          xhr.status,
          xhr.status >= 500 || xhr.status === 408 || xhr.status === 429,
          xhr.status === 403,
        ),
      );
    };
    xhr.onerror =
      xhr.ontimeout =
      xhr.onabort =
        () => {
          done();
          reject(new PartFailure(null, true));
        };
    xhr.send(body);
  });

const HASH_CHUNK = 8 * 1024 * 1024;

/** SHA-256 incremental: nunca carga el archivo entero en memoria. */
export const hashFile: HashFile = async (file, onProgress, signal) => {
  const hasher = await createSHA256();
  hasher.init();
  for (let offset = 0; offset < file.size; offset += HASH_CHUNK) {
    if (signal.aborted) throw new DOMException("aborted", "AbortError");
    hasher.update(new Uint8Array(await file.slice(offset, offset + HASH_CHUNK).arrayBuffer()));
    onProgress(Math.min(1, (offset + HASH_CHUNK) / file.size));
  }
  return hasher.digest("hex");
};

const PREFIX = "klick.upload.";

/** Qué subida abierta corresponde a cada archivo, para reanudar tras recargar la página. */
export const localResumeStore: ResumeStore = {
  get: (k) => {
    try {
      return localStorage.getItem(PREFIX + k);
    } catch {
      return null;
    }
  },
  set: (k, v) => {
    try {
      localStorage.setItem(PREFIX + k, v);
    } catch {
      /* sin almacenamiento local: no hay reanudación entre recargas, solo eso */
    }
  },
  delete: (k) => {
    try {
      localStorage.removeItem(PREFIX + k);
    } catch {
      /* ver arriba */
    }
  },
};
