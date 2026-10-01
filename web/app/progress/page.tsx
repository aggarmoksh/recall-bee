"use client";

import { useEffect, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, MasteryResponse } from "@/lib/api";
import Reveal from "@/components/Reveal";

const LINE_COLORS = ["#7c5cff", "#16a34a", "#d97706", "#dc2626", "#0891b2", "#db2777"];

function buildChartData(series: MasteryResponse["retention_series"]) {
  const byReview = new Map<number, Record<string, number | string>>();
  for (const day of series) {
    for (const point of day.points) {
      const row = byReview.get(point.review_number) ?? { review_number: point.review_number };
      row[day.day] = Math.round(point.average_quality * 100) / 100;
      byReview.set(point.review_number, row);
    }
  }
  return Array.from(byReview.values()).sort((a, b) => (a.review_number as number) - (b.review_number as number));
}

function statusPillClass(label: string): string {
  if (label === "Missed, not counted against you") return "bg-zinc-500/15 text-muted";
  if (label === "Capturing...") return "bg-accent/15 text-accent";
  return "bg-zinc-500/15 text-muted";
}

export default function ProgressPage() {
  const [data, setData] = useState<MasteryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .mastery()
      .then(setData)
      .catch(() => setError("Could not reach the Recall API. Is it running on localhost:8000?"));
  }, []);

  return (
    <div className="flex flex-col gap-10">
      <Reveal>
        <h1 className="text-5xl font-bold tracking-tight">Progress</h1>
      </Reveal>

      {error && <p className="text-bad text-lg">{error}</p>}
      {!error && !data && <p className="text-muted text-lg">Loading…</p>}

      {data && (
        <>
          <Reveal delay={80}>
            <section>
              <h2 className="mb-4 text-xl font-semibold">Mastery by session</h2>
              {data.sessions.length === 0 ? (
                <div className="rounded-3xl border border-border bg-surface py-20 text-center shadow-sm">
                  <p className="text-muted text-lg">No graded answers yet -- sessions will appear here once questions have been answered.</p>
                </div>
              ) : (
                <ul className="flex flex-col gap-3">
                  {data.sessions.map((s) => (
                    <li key={s.session_id} className="bounce rounded-2xl bg-surface px-6 py-5 shadow-sm hover:shadow-lg">
                      <div className="flex items-center justify-between gap-4">
                        <span className="text-lg font-medium">
                          {s.label}, {s.question_count} question{s.question_count === 1 ? "" : "s"}
                        </span>
                        {s.average_quality === null ? (
                          <span className="shrink-0 rounded-full bg-zinc-500/15 px-3 py-1 text-sm font-medium text-muted">not yet answered</span>
                        ) : (
                          <span className="shrink-0 rounded-full bg-accent/15 px-3 py-1 text-sm font-medium text-accent">
                            {s.average_quality.toFixed(1)}/5
                          </span>
                        )}
                      </div>
                      <ul className="mt-3 flex flex-col gap-2 border-t border-border pt-3">
                        {s.questions.map((q) => (
                          <li key={q.question_id} className="flex items-center justify-between gap-4 text-sm">
                            <span className="flex-1 truncate text-muted">{q.text}</span>
                            {q.status_label ? (
                              <span className={`shrink-0 rounded-full px-2.5 py-0.5 font-medium ${statusPillClass(q.status_label)}`}>
                                {q.status_label}
                              </span>
                            ) : q.latest_quality !== null ? (
                              <span className="shrink-0 rounded-full bg-accent/10 px-2.5 py-0.5 font-medium text-accent">{q.latest_quality}/5</span>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </Reveal>

          <Reveal delay={160}>
            <section>
              <h2 className="mb-4 text-xl font-semibold">Retention (average quality by review number)</h2>
              {(() => {
                const days = data.retention_series.map((d) => d.day);
                const chartData = buildChartData(data.retention_series);
                if (chartData.length === 0) {
                  return (
                    <div className="rounded-3xl border border-border bg-surface py-20 text-center shadow-sm">
                      <p className="text-muted text-lg">No repeated reviews yet -- the retention curve fills in as questions get re-asked.</p>
                    </div>
                  );
                }
                return (
                  <div className="h-96 rounded-3xl bg-surface p-6 shadow-sm">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={chartData}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                        <XAxis dataKey="review_number" stroke="var(--muted)" label={{ value: "Review #", position: "insideBottom", offset: -5 }} />
                        <YAxis domain={[0, 5]} stroke="var(--muted)" label={{ value: "Avg quality", angle: -90, position: "insideLeft" }} />
                        <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 12 }} />
                        <Legend />
                        {days.map((day, i) => (
                          <Line key={day} type="monotone" dataKey={day} stroke={LINE_COLORS[i % LINE_COLORS.length]} strokeWidth={2} connectNulls />
                        ))}
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                );
              })()}
            </section>
          </Reveal>
        </>
      )}
    </div>
  );
}
