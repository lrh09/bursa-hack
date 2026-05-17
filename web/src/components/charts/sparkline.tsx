"use client";

import { LineChart, Line, ResponsiveContainer, YAxis, Tooltip as RTooltip } from "recharts";

import { PALETTE } from "@/lib/theme";

interface Props {
  data: number[];
  height?: number;
  color?: string;
  fillColor?: string;
}

export function Sparkline({ data, height = 56, color = PALETTE.navy }: Props) {
  const rows = data.map((v, i) => ({ i, v }));
  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <RTooltip
            cursor={false}
            wrapperStyle={{ display: "none" }}
          />
          <Line
            type="monotone"
            dataKey="v"
            stroke={color}
            strokeWidth={1.6}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
