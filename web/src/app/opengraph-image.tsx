import { ImageResponse } from "next/og";

export const runtime = "edge";
export const alt = "BursaHack — Bursa Malaysia Quant Research Portal";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default async function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          background: "linear-gradient(135deg, #0B2349 0%, #0F2D5B 60%, #0A1B36 100%)",
          color: "#F4F8FB",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: "72px",
          fontFamily: "Inter, system-ui",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{
            color: "#C49A2A",
            fontSize: 22,
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: 6,
          }}>
            FatFyre Research
          </div>
          <div style={{
            fontSize: 96,
            fontWeight: 800,
            lineHeight: 1.05,
            letterSpacing: -2,
            fontFamily: "Georgia, serif",
          }}>
            BursaHack
          </div>
          <div style={{ fontSize: 32, color: "#7B91B2", marginTop: 4 }}>
            Bursa Malaysia momentum &amp; trend research portal
          </div>
        </div>

        <div style={{ display: "flex", gap: 64, alignItems: "baseline" }}>
          <div>
            <div style={{ color: "#7B91B2", fontSize: 16, textTransform: "uppercase", letterSpacing: 3 }}>
              Variants
            </div>
            <div style={{ fontSize: 64, fontWeight: 700 }}>102</div>
          </div>
          <div>
            <div style={{ color: "#7B91B2", fontSize: 16, textTransform: "uppercase", letterSpacing: 3 }}>
              Fold backtests
            </div>
            <div style={{ fontSize: 64, fontWeight: 700 }}>2,965</div>
          </div>
          <div>
            <div style={{ color: "#7B91B2", fontSize: 16, textTransform: "uppercase", letterSpacing: 3 }}>
              Panel
            </div>
            <div style={{ fontSize: 64, fontWeight: 700 }}>2007-2022</div>
          </div>
          <div style={{ marginLeft: "auto", textAlign: "right" }}>
            <div style={{
              padding: "8px 20px",
              borderRadius: 999,
              background: "rgba(180, 83, 9, 0.18)",
              color: "#F59E0B",
              fontSize: 18,
              fontWeight: 600,
              textTransform: "uppercase",
              letterSpacing: 3,
              display: "flex",
            }}>
              Research stage
            </div>
          </div>
        </div>
      </div>
    ),
    { ...size },
  );
}
