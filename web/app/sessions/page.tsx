"use client";

import { useEffect, useState } from "react";
import { api, SessionRow } from "@/lib/api";
import Reveal from "@/components/Reveal";

const STATUS_STYLE: Record<string, string> = {
  approved: "bg-[color:var(--good)]/15 text-[color:var(--good)]",
  pending: "bg-accent/15 text-accent",
  rejected: "bg-[color:var(--bad)]/15 text-[color:var(--bad)]",
  excluded: "bg-zinc-500/15 text-muted",
  none: "bg-zinc-500/15 text-muted",
};

function formatType(type: string | null): string {
  if (!type) return "unclassified";
  return type.replace("_", " ");
}

export default function SessionsPage() {
  const [sessions, setSessions] = useState<SessionRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<number | null>(null);

  const load = () => {
    api
      .sessions()
      .then((data) => setSessions(data.sessions))
      .catch(() => setError("Could not reach the Recall API. Is it running on localhost:8000?"));
  };

  useEffect(load, []);

  const act = async (id: number, action: "approve" | "reject") => {
    setPendingAction(id);
    try {
      if (action === "approve") await api.approveSession(id);
      else await api.rejectSession(id);
      load();
    } finally {
      setPendingAction(null);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <Reveal>
        <h1 className="text-5xl font-bold tracking-tight">Sessions</h1>
      </Reveal>

      {error && <p className="text-bad text-lg">{error}</p>}
      {!error && !sessions && <p className="text-muted text-lg">Loading…</p>}

      {sessions && (
        <Reveal delay={80}>
          {sessions.length === 0 ? (
            <div className="rounded-3xl border border-border bg-surface py-20 text-center shadow-sm">
              <p className="text-muted text-lg">No sessions ingested yet.</p>
            </div>
          ) : (
            <ul className="flex flex-col gap-3">
              {sessions.map((s) => (
                <li key={s.session_id} className="bounce flex flex-col gap-3 rounded-2xl bg-surface px-6 py-5 shadow-sm hover:shadow-lg">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                      <span className="text-lg font-medium">{s.date ? new Date(s.date).toLocaleString() : "unknown date"}</span>
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-wide ${STATUS_STYLE[s.status] ?? STATUS_STYLE.none}`}>
                        {s.status}
                      </span>
                      <span className="text-muted text-sm">{formatType(s.session_type)}</span>
                    </div>
                    <span className="text-muted text-sm">
                      {s.duration_minutes ?? "?"} min · {s.question_count} question{s.question_count === 1 ? "" : "s"}
                    </span>
                  </div>
                  {s.session_type_reason && <p className="text-muted text-sm">{s.session_type_reason}</p>}
                  {s.status === "pending" && (
                    <div className="flex gap-3">
                      <button
                        onClick={() => act(s.session_id, "approve")}
                        disabled={pendingAction === s.session_id}
                        className="press rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-accent-foreground hover:opacity-90 disabled:opacity-50"
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => act(s.session_id, "reject")}
                        disabled={pendingAction === s.session_id}
                        className="press rounded-full bg-zinc-500/15 px-4 py-1.5 text-sm font-medium text-muted hover:bg-zinc-500/25 disabled:opacity-50"
                      >
                        Reject
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Reveal>
      )}
    </div>
  );
}
