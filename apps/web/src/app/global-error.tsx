"use client";

/** Falla en el layout raíz: no hay proveedores ni catálogo disponibles, de modo que va autónomo. */
export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="es-CL">
      <body
        style={{
          margin: 0,
          background: "#0B0D10",
          color: "#ECEEF2",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        <main role="alert" style={{ maxWidth: 420, margin: "0 auto", padding: "20vh 20px 0" }}>
          <h1 style={{ fontSize: 24, margin: "0 0 8px" }}>Algo salió mal</h1>
          <p style={{ color: "#A4ADBB", margin: "0 0 16px" }}>
            Tuvimos un problema al cargar Klick. Reintenta en un momento.
          </p>
          <button
            type="button"
            onClick={reset}
            style={{
              height: 40,
              padding: "0 16px",
              border: 0,
              borderRadius: 6,
              background: "#B8F43C",
              color: "#0B0D10",
              font: "600 14px system-ui",
              cursor: "pointer",
            }}
          >
            Reintentar
          </button>
        </main>
      </body>
    </html>
  );
}
