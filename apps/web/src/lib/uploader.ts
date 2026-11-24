/**
 * Cargador multipart (F1-02). Sin dependencias de React ni de la red: todo lo externo se inyecta
 * (`UploadApi`, `putPart`, `hash`, `store`, `sleep`) para poder probar cada comportamiento.
 *
 * Garantías:
 * - El navegador sube directo al almacén con URLs firmadas de corta vida; se piden por lotes y se
 *   renuevan si caducan.
 * - Reanudable: la fuente de verdad de lo ya subido es el servidor (`state`), no la memoria.
 * - Pausar cancela las partes en vuelo; reanudar solo repite esas partes.
 * - Reintentos con espera exponencial solo para fallos transitorios (red, 5xx, URL caducada).
 * - Nunca se envía contenido del archivo a la API de Klick: solo metadatos.
 */

export interface StartRequest {
  filename: string;
  size_bytes: number;
  content_type: string | null;
  title?: string | undefined;
  project_id?: string | null | undefined;
  folder_id?: string | null | undefined;
  sha256?: string | undefined;
  rights_confirmed: boolean;
}

export interface UploadInfo {
  id: string;
  asset_id: string;
  status: string;
  part_size: number;
  total_parts: number;
  size_bytes: number;
  uploaded_parts: number[];
  expires_at: string;
}

export interface StartResponse {
  deduplicated: boolean;
  upload: UploadInfo | null;
  asset: { id: string };
}

export interface UploadApi {
  start(body: StartRequest, idempotencyKey: string): Promise<StartResponse>;
  state(uploadId: string): Promise<UploadInfo>;
  partUrls(uploadId: string, partNumbers: number[]): Promise<Record<string, string>>;
  complete(uploadId: string): Promise<{ id: string }>;
  abort(uploadId: string): Promise<void>;
}

/** Fallo de la API de Klick (los códigos son los de `application/problem+json`). */
export class ApiFailure extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly missingParts?: number[],
  ) {
    super(code);
    this.name = "ApiFailure";
  }
}

/** Fallo al subir una parte al almacén. `expired` = la URL firmada caducó (hay que renovarla). */
export class PartFailure extends Error {
  constructor(
    readonly status: number | null,
    readonly retryable: boolean,
    readonly expired = false,
  ) {
    super(status === null ? "network" : `part_${status}`);
    this.name = "PartFailure";
  }
}

export interface PutOptions {
  signal: AbortSignal;
  onProgress: (loadedBytes: number) => void;
}
export type PutPart = (url: string, body: Blob, options: PutOptions) => Promise<void>;
export type HashFile = (
  file: Blob,
  onProgress: (fraction: number) => void,
  signal: AbortSignal,
) => Promise<string>;

export interface ResumeStore {
  get(fingerprint: string): string | null;
  set(fingerprint: string, uploadId: string): void;
  delete(fingerprint: string): void;
}

export type UploadStatus =
  | "queued"
  | "hashing"
  | "starting"
  | "uploading"
  | "paused"
  | "completing"
  | "done"
  | "error"
  | "canceled";

export interface UploadSnapshot {
  id: string;
  fileName: string;
  size: number;
  status: UploadStatus;
  /** Bytes confirmados + en vuelo, 0..size. */
  loaded: number;
  /** Progreso del hash (0..1) mientras `status === "hashing"`. */
  hashProgress: number;
  assetId: string | null;
  uploadId: string | null;
  deduplicated: boolean;
  errorCode: string | null;
  /** Partes subidas / totales (0 antes de iniciar). */
  partsDone: number;
  partsTotal: number;
}

export interface UploadDeps {
  api: UploadApi;
  putPart: PutPart;
  hash?: HashFile | undefined;
  store?: ResumeStore | undefined;
  sleep?: ((ms: number, signal?: AbortSignal) => Promise<void>) | undefined;
  concurrency?: number;
  retries?: number;
  backoffMs?: number;
  /** Por encima de este tamaño no se calcula el hash en el navegador (solo ahorra una subida). */
  hashLimitBytes?: number;
  newId?: () => string;
}

export interface UploadParams {
  file: File;
  orgId: string;
  rightsConfirmed: boolean;
  title?: string;
  projectId?: string | null;
  folderId?: string | null;
}

const URL_BATCH = 100;
const MAX_COMPLETE_ROUNDS = 3;

export const fingerprintOf = (file: File, orgId: string): string =>
  `${orgId}:${file.name}:${file.size}:${file.lastModified}`;

const defaultSleep = (ms: number, signal?: AbortSignal): Promise<void> =>
  new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });

class Interrupted extends Error {}

export class UploadTask {
  readonly id: string;
  private snap: UploadSnapshot;
  private listeners = new Set<() => void>();
  private controller = new AbortController();
  private info: UploadInfo | null = null;
  private done = new Set<number>();
  private inflight = new Map<number, number>(); // parte → bytes en vuelo
  private urls = new Map<number, string>();
  private urlRequests = new Map<number, Promise<void>>();
  private wantPause = false;
  private running: Promise<void> | null = null;
  private lastEmit = 0;
  private readonly fingerprint: string;

  constructor(
    private readonly params: UploadParams,
    private readonly deps: UploadDeps,
  ) {
    this.id = (deps.newId ?? (() => crypto.randomUUID()))();
    this.fingerprint = fingerprintOf(params.file, params.orgId);
    this.snap = {
      id: this.id,
      fileName: params.file.name,
      size: params.file.size,
      status: "queued",
      loaded: 0,
      hashProgress: 0,
      assetId: null,
      uploadId: null,
      deduplicated: false,
      errorCode: null,
      partsDone: 0,
      partsTotal: 0,
    };
  }

  // ------------------------------------------------------------------------- suscripción

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  getSnapshot = (): UploadSnapshot => this.snap;

  private set(patch: Partial<UploadSnapshot>, force = true): void {
    const now = Date.now();
    if (!force && now - this.lastEmit < 100) return; // el progreso no inunda a React
    this.lastEmit = now;
    this.snap = { ...this.snap, ...patch };
    for (const l of this.listeners) l();
  }

  // ------------------------------------------------------------------------------ control

  /** Inicia (o continúa) la subida. Es seguro llamarla más de una vez. */
  start(): Promise<void> {
    if (this.running) return this.running;
    if (["done", "canceled"].includes(this.snap.status)) return Promise.resolve();
    this.wantPause = false;
    this.controller = new AbortController();
    this.running = this.execute().finally(() => {
      this.running = null;
    });
    return this.running;
  }

  pause(): void {
    if (!["hashing", "starting", "uploading"].includes(this.snap.status)) return;
    this.wantPause = true;
    this.controller.abort();
  }

  resume(): Promise<void> {
    if (this.snap.status !== "paused" && this.snap.status !== "error") return Promise.resolve();
    return this.start();
  }

  async cancel(): Promise<void> {
    if (["done", "canceled"].includes(this.snap.status)) return;
    this.wantPause = false;
    this.controller.abort();
    const uploadId = this.info?.id ?? this.snap.uploadId;
    this.deps.store?.delete(this.fingerprint);
    this.set({ status: "canceled" });
    if (uploadId) {
      try {
        await this.deps.api.abort(uploadId);
      } catch {
        // el recolector del servidor limpia las subidas abandonadas
      }
    }
  }

  // ---------------------------------------------------------------------------- ejecución

  private async execute(): Promise<void> {
    try {
      if (!this.info && !(await this.tryResume())) {
        if (!(await this.begin())) return; // deduplicada o interrumpida
      }
      await this.uploadParts();
      await this.finish();
    } catch (err) {
      if (this.snap.status === "canceled") return;
      if (err instanceof Interrupted) {
        if (this.wantPause) this.set({ status: "paused" });
        return;
      }
      this.fail(err);
    }
  }

  private fail(err: unknown): void {
    const code =
      err instanceof ApiFailure
        ? err.code
        : err instanceof PartFailure
          ? err.status === null
            ? "network"
            : "part_failed"
          : "unexpected";
    this.set({ status: "error", errorCode: code });
  }

  private get signal(): AbortSignal {
    return this.controller.signal;
  }

  private checkInterrupted(): void {
    if (this.signal.aborted) throw new Interrupted();
  }

  /** ¿Hay una subida abierta de este mismo archivo? Se retoma desde lo que el servidor tiene. */
  private async tryResume(): Promise<boolean> {
    const stored = this.deps.store?.get(this.fingerprint);
    if (!stored) return false;
    try {
      const info = await this.deps.api.state(stored);
      if (info.status !== "open") throw new ApiFailure(409, "upload_not_open");
      this.adopt(info);
      return true;
    } catch {
      // la subida guardada ya no existe, expiró o no se pudo consultar: se empieza una nueva
      this.deps.store?.delete(this.fingerprint);
      return false;
    }
  }

  private adopt(info: UploadInfo): void {
    this.info = info;
    this.done = new Set(info.uploaded_parts);
    this.set({
      uploadId: info.id,
      assetId: info.asset_id,
      partsTotal: info.total_parts,
      partsDone: this.done.size,
      loaded: this.confirmedBytes(),
    });
  }

  /** Hash opcional + creación de la subida. Devuelve `false` si ya terminó (deduplicada). */
  private async begin(): Promise<boolean> {
    const { file } = this.params;
    let sha256: string | undefined;
    const limit = this.deps.hashLimitBytes ?? 4 * 1024 ** 3;
    if (this.deps.hash && file.size <= limit) {
      this.set({ status: "hashing", hashProgress: 0 });
      try {
        sha256 = await this.deps.hash(
          file,
          (fraction) => this.set({ hashProgress: fraction }, false),
          this.signal,
        );
      } catch {
        this.checkInterrupted();
        sha256 = undefined; // sin hash solo se pierde la deduplicación
      }
      this.checkInterrupted();
    }
    this.set({ status: "starting" });
    const res = await this.deps.api.start(
      {
        filename: file.name,
        size_bytes: file.size,
        content_type: file.type || null,
        title: this.params.title,
        project_id: this.params.projectId ?? null,
        folder_id: this.params.folderId ?? null,
        sha256,
        rights_confirmed: this.params.rightsConfirmed,
      },
      `up-${this.id}`,
    );
    this.checkInterrupted();
    if (res.deduplicated || !res.upload) {
      this.set({
        status: "done",
        deduplicated: true,
        assetId: res.asset.id,
        loaded: file.size,
      });
      return false;
    }
    this.adopt(res.upload);
    this.deps.store?.set(this.fingerprint, res.upload.id);
    return true;
  }

  private confirmedBytes(): number {
    const info = this.info;
    if (!info) return 0;
    let total = 0;
    for (const n of this.done) total += this.partLength(n);
    return Math.min(total, info.size_bytes);
  }

  private partLength(n: number): number {
    const info = this.info as UploadInfo;
    const start = (n - 1) * info.part_size;
    return Math.max(0, Math.min(info.part_size, info.size_bytes - start));
  }

  private emitProgress(force = false): void {
    let loaded = this.confirmedBytes();
    for (const bytes of this.inflight.values()) loaded += bytes;
    this.set({ loaded: Math.min(loaded, this.params.file.size), partsDone: this.done.size }, force);
  }

  // ------------------------------------------------------------------------------ partes

  private async uploadParts(): Promise<void> {
    const info = this.info as UploadInfo;
    this.set({ status: "uploading", errorCode: null });
    const pending: number[] = [];
    for (let n = 1; n <= info.total_parts; n++) if (!this.done.has(n)) pending.push(n);
    const queue = [...pending];
    const workers = Math.max(1, Math.min(this.deps.concurrency ?? 4, queue.length || 1));
    let failure: unknown = null;

    const worker = async (): Promise<void> => {
      while (!failure) {
        this.checkInterrupted();
        const n = queue.shift();
        if (n === undefined) return;
        try {
          await this.uploadOne(n, queue);
        } catch (err) {
          failure ??= err;
          this.controller.abort(); // detiene a los demás workers
          return;
        }
      }
    };
    await Promise.all(Array.from({ length: workers }, worker));
    if (failure) throw this.wantPause ? new Interrupted() : failure;
    this.checkInterrupted();
  }

  private async urlFor(n: number, upcoming: number[], refresh: boolean): Promise<string> {
    if (refresh) this.urls.delete(n);
    let request = this.urlRequests.get(n);
    if (!this.urls.has(n) && !request) {
      // un solo lote para todos los workers: las partes del lote comparten la misma petición
      const batch = [n, ...upcoming.filter((p) => !this.urls.has(p) && !this.urlRequests.has(p))];
      const numbers = batch.slice(0, URL_BATCH);
      request = this.deps.api
        .partUrls((this.info as UploadInfo).id, numbers)
        .then((urls) => {
          for (const [k, v] of Object.entries(urls)) this.urls.set(Number(k), v);
        })
        .finally(() => {
          for (const p of numbers) this.urlRequests.delete(p);
        });
      for (const p of numbers) this.urlRequests.set(p, request);
    }
    await request;
    const url = this.urls.get(n);
    if (!url) throw new ApiFailure(500, "missing_part_url");
    return url;
  }

  private async uploadOne(n: number, upcoming: number[]): Promise<void> {
    const info = this.info as UploadInfo;
    const retries = this.deps.retries ?? 3;
    const sleep = this.deps.sleep ?? defaultSleep;
    const start = (n - 1) * info.part_size;
    const blob = this.params.file.slice(start, start + this.partLength(n));
    let refresh = false;
    for (let attempt = 0; ; attempt++) {
      this.checkInterrupted();
      this.inflight.set(n, 0);
      try {
        const url = await this.urlFor(n, upcoming, refresh);
        await this.deps.putPart(url, blob, {
          signal: this.signal,
          onProgress: (bytes) => {
            this.inflight.set(n, Math.min(bytes, blob.size));
            this.emitProgress();
          },
        });
        this.inflight.delete(n);
        this.done.add(n);
        this.urls.delete(n);
        this.emitProgress(true);
        return;
      } catch (err) {
        this.inflight.delete(n);
        this.emitProgress();
        if (this.signal.aborted) throw new Interrupted();
        const transient =
          err instanceof PartFailure
            ? err.retryable || err.expired
            : err instanceof ApiFailure && (err.status >= 500 || err.status === 0);
        if (!transient || attempt >= retries) throw err;
        refresh = err instanceof PartFailure && err.expired;
        await sleep((this.deps.backoffMs ?? 500) * 2 ** attempt, this.signal);
      }
    }
  }

  // ----------------------------------------------------------------------------- completar

  private async finish(): Promise<void> {
    const sleep = this.deps.sleep ?? defaultSleep;
    this.set({ status: "completing" });
    for (let round = 0; ; round++) {
      this.checkInterrupted();
      try {
        const asset = await this.deps.api.complete((this.info as UploadInfo).id);
        this.deps.store?.delete(this.fingerprint);
        this.set({ status: "done", assetId: asset.id, loaded: this.params.file.size });
        return;
      } catch (err) {
        if (err instanceof ApiFailure && err.code === "upload_incomplete" && err.missingParts) {
          // el servidor dice qué partes faltan realmente: se vuelven a subir
          if (round >= MAX_COMPLETE_ROUNDS) throw err;
          for (const n of err.missingParts) this.done.delete(n);
          await this.uploadParts();
          this.set({ status: "completing" });
          continue;
        }
        const transient = err instanceof ApiFailure && (err.status >= 500 || err.status === 0);
        if (!transient || round >= (this.deps.retries ?? 3)) throw err;
        await sleep((this.deps.backoffMs ?? 500) * 2 ** round, this.signal);
      }
    }
  }
}
