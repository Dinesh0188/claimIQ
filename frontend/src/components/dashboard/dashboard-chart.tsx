"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { rupees } from "@/lib/utils";
import type { LeakageItem } from "@/lib/types";

export function DashboardChart({ data }: { data: LeakageItem[] }) {
  return (
    <div className="h-[300px] lg:h-[320px]">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          layout="vertical"
          margin={{ left: 120, right: 20, top: 10, bottom: 10 }}
        >
          <XAxis
            type="number"
            tick={{ fill: "#9aa1ac", fontSize: 11 }}
            tickFormatter={(v: number) => `₹${Math.round(v / 1000)}k`}
          />
          <YAxis
            type="category"
            dataKey="cause"
            tick={{ fill: "#9aa1ac", fontSize: 11 }}
            width={110}
            tickFormatter={(v: string) =>
              v.length > 18 ? `${v.slice(0, 18)}…` : v
            }
          />
          <Tooltip
            contentStyle={{
              background: "#14161b",
              border: "1px solid #23262e",
              borderRadius: "8px",
              fontSize: "12px",
            }}
            formatter={(value: number) => [rupees(value), "Amount"]}
          />
          <Bar dataKey="amount" radius={[0, 4, 4, 0]}>
            {data.map((entry, i) => (
              <Cell
                key={i}
                fill={entry.bearer === "HOSPITAL" ? "#ff5c5c" : "#5aa9ff"}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div className="flex items-center gap-4 mt-3 text-xs text-muted">
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-sm bg-[#ff5c5c]" aria-hidden="true" /> Hospital absorbs
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-sm bg-[#5aa9ff]" aria-hidden="true" /> Patient pays
        </span>
      </div>
      {/* Accessible data table for screen readers and as fallback */}
      <table className="sr-only">
        <caption>Leakage by cause</caption>
        <thead>
          <tr>
            <th>Cause</th>
            <th>Bearer</th>
            <th>Amount</th>
          </tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr key={row.cause}>
              <td>{row.cause}</td>
              <td>{row.bearer}</td>
              <td>{rupees(row.amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
