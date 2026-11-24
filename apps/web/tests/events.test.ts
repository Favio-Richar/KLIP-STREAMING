import { describe, expect, it } from "vitest";
import { parseEvent } from "@/lib/events";
import { describeAgent } from "@/app/(app)/settings/profile/sessions";

describe("parseEvent", () => {
  it("lee el sobre estándar de la API", () => {
    const ev = parseEvent(
      JSON.stringify({
        id: "1-0",
        type: "job.progress",
        occurred_at: "2026-11-23T10:00:00Z",
        payload: { job_id: "j", progress: 40 },
      }),
    );
    expect(ev).toMatchObject({ type: "job.progress", payload: { job_id: "j", progress: 40 } });
  });
  it.each([
    "no es json",
    "null",
    "[]",
    '{"type":1,"payload":{}}',
    '{"type":"x"}',
    '{"type":"x","payload":null}',
  ])("ignora datos mal formados: %s", (raw) => expect(parseEvent(raw)).toBeNull());
});

describe("describeAgent", () => {
  it("resume navegador y sistema", () => {
    expect(
      describeAgent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36 Edg/120",
      ),
    ).toBe("Edge · Windows");
    expect(
      describeAgent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Gecko/20100101 Firefox/121.0"),
    ).toBe("Firefox · macOS");
  });
  it("devuelve null sin agente o irreconocible", () => {
    expect(describeAgent(null)).toBeNull();
    expect(describeAgent("curl/8")).toBeNull();
  });
});
