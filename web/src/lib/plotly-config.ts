// Shared Plotly layout/config primitives. Keeps look consistent across charts.

import type { Layout, Config } from "plotly.js";

import { PALETTE } from "./theme";

export const PLOTLY_FONT = {
  family: "var(--font-inter), ui-sans-serif, system-ui",
  size: 12,
  color: PALETTE.ink,
};

export const PLOTLY_BASE_LAYOUT: Partial<Layout> = {
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  font: PLOTLY_FONT,
  margin: { l: 56, r: 24, t: 24, b: 40 },
  hovermode: "x unified",
  xaxis: {
    gridcolor: PALETTE.grid,
    linecolor: PALETTE.grid,
    zeroline: false,
    tickfont: { size: 11, color: PALETTE.muted, family: PLOTLY_FONT.family },
    showspikes: false,
  },
  yaxis: {
    gridcolor: PALETTE.grid,
    linecolor: PALETTE.grid,
    zeroline: false,
    tickfont: { size: 11, color: PALETTE.muted, family: PLOTLY_FONT.family },
  },
  showlegend: false,
  modebar: { color: PALETTE.muted, activecolor: PALETTE.navy, bgcolor: "rgba(0,0,0,0)" },
};

export const PLOTLY_BASE_CONFIG: Partial<Config> = {
  responsive: true,
  displaylogo: false,
  modeBarButtonsToRemove: [
    "select2d",
    "lasso2d",
    "autoScale2d",
    "toggleSpikelines",
  ],
  toImageButtonOptions: {
    format: "png",
    scale: 2,
    filename: "bursahack-chart",
  },
};
