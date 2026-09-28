"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, AnswerDetail, TodayResponse, WindowStatus } from "@/lib/api";
import Reveal from "@/components/Reveal";

const POLL_MS = 5000;
const GRADED_DISPLAY_WINDOW_MS = 3 * 60 * 1000; // show a just-graded answer in the hero for 3 min
const GREETING_NAME = "Lord Moksh"; // this operator's own Bee display name

const STATUS_LABEL: Record<WindowStatus, string> = {
  waiting: "Waiting for the prompt to buzz",
  listening: "Listening for your answer",
  grading: "Grading your answer",
};

const STATUS_DOT: Record<WindowStatus, string> = {
  waiting: "bg-zinc-400",
  listening: "bg-accent animate-pulse",
  grading: "bg-[color:var(--good)] animate-pulse",
};

const VERDICT_LABEL: Record<string, string> = {
  correct: "Correct",
  partial: "Partial",
  incorrect: "Incorrect",
  not_an_answer: "Not an answer",
  no_answer: "No answer captured",
  incomplete_capture: "Only caught a few words",
  code_truncated: "Capture truncated (tooling issue)",
};

function verdictPillClass(verdict: string): string {
  if (verdict === "correct" || verdict === "partial") return "bg-[color:var(--good)]/15 text-[color:var(--good)]";
  if (verdict === "no_answer" || verdict === "incomplete_capture" || verdict === "code_truncated") return "bg-zinc-500/15 text-muted";
  return "bg-[color:var(--bad)]/15 text-[color:var(--bad)]";
}

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

function useCountdown(target: string | null): string | null {
  const [label, setLabel] = useState<string | null>(null);

  useEffect(() => {
    if (!target) {
      setLabel(null);
      return;
    }
    const targetMs = new Date(target).getTime();
    const tick = () => {
      const diff = targetMs - Date.now();
      if (diff <= 0) {
        setLabel("any moment now");
        return;
      }
      const s = Math.floor(diff / 1000);
      const mm = Math.floor(s / 60);
      const ss = s % 60;
      setLabel(`${mm}:${ss.toString().padStart(2, "0")}`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [target]);

  return label;
}

export default function TodayPage() {
  const [today, setToday] = useState<TodayResponse | null>(null);
  const [gradedDetail, setGradedDetail] = useState<AnswerDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await api.today();
        if (!cancelled) setToday(data);
      } catch {
        if (!cancelled) setError("Could not reach the Recall API. Is it running on localhost:8000?");
      }
    };
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!today || today.open_dispatch) {
      setGradedDetail(null);
      return;
    }
    const latest = today.recent_answers[0];
    if (!latest) return;
    const age = Date.now() - new Date(latest.matched_at).getTime();
    if (age > GRADED_DISPLAY_WINDOW_MS) return;
    let cancelled = false;
    api
      .answer(latest.answer_id)
      .then((detail) => {
        if (!cancelled) setGradedDetail(detail);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [today]);

  const countdown = useCountdown(today?.open_dispatch?.window_status === "waiting" ? today.open_dispatch.alarm_at : null);

  return (
    <div className="flex flex-col gap-12">
      <Reveal>
        <p className="text-lg text-muted">Hi {GREETING_NAME},</p>
        <h1 className="text-5xl font-bold tracking-tight">{greeting()}!</h1>
        {today && (
          <p className="mt-3 text-sm text-muted">
            Profile: <span className="font-medium text-foreground">{today.profile}</span> · {today.dispatches_today}{" "}
            dispatched today
          </p>
        )}
      </Reveal>

      {error && <p className="text-bad text-lg">{error}</p>}
      {!error && !today && <p className="text-muted text-lg">Loading…</p>}

      {today && (
        <Reveal delay={80}>
          <section>
            {today.open_dispatch ? (
              <div key={today.open_dispatch.dispatch_id} className="pop rounded-3xl bg-lavender p-12 text-center text-lavender-foreground shadow-sm">
                <span className="mx-auto mb-6 inline-flex items-center gap-2 rounded-full bg-white/70 px-4 py-1.5 text-sm font-medium backdrop-blur-sm">
                  <span className={`h-2 w-2 rounded-full ${STATUS_DOT[today.open_dispatch.window_status]}`} />
                  {STATUS_LABEL[today.open_dispatch.window_status]}
                </span>
                <p className="mx-auto max-w-2xl text-3xl font-semibold leading-snug">{today.open_dispatch.question_text}</p>
                {countdown && <p className="mt-4 text-lavender-foreground/70">Buzzes in {countdown}</p>}
              </div>
            ) : gradedDetail ? (
              <div key={gradedDetail.answer_id} className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="pop rounded-3xl bg-yellow p-10 text-yellow-foreground shadow-sm">
                  <span className={`mb-4 inline-block rounded-full bg-white/60 px-3 py-1 text-xs font-semibold uppercase tracking-wide`}>
                    Just graded
                  </span>
                  <p className="text-2xl font-semibold leading-snug">{gradedDetail.question?.text}</p>
                  <p className="mt-4 text-sm">{gradedDetail.feedback_text}</p>
                  <Link href={`/answers/${gradedDetail.answer_id}`} className="bounce mt-4 inline-block text-sm font-medium underline">
                    View full answer →
                  </Link>
                </div>
                <div className="pop rounded-3xl bg-lavender p-10 text-lavender-foreground shadow-sm" style={{ animationDelay: "80ms" }}>
                  <p className="mb-3 text-sm font-semibold uppercase tracking-wide">Rubric</p>
                  <ul className="flex flex-col gap-2">
                    {gradedDetail.rubric.map((r) => (
                      <li key={r.point} className="flex items-center gap-2">
                        <span className={r.hit ? "text-[color:var(--good)]" : "text-[color:var(--bad)]"}>{r.hit ? "✓" : "✗"}</span>
                        <span>{r.point}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            ) : (
              <div className="rounded-3xl border border-border bg-surface py-20 text-center shadow-sm">
                <p className="text-muted text-lg">No prompt open right now.</p>
              </div>
            )}
          </section>
        </Reveal>
      )}

      {today && (
        <Reveal delay={160}>
          <section>
            <h2 className="mb-4 text-xl font-semibold">Today&apos;s answers</h2>
            {today.recent_answers.length === 0 ? (
              <p className="text-muted text-lg">No answers graded yet today.</p>
            ) : (
              <ul className="flex flex-col gap-3">
                {today.recent_answers.map((a) => (
                  <li key={a.answer_id}>
                    <Link
                      href={`/answers/${a.answer_id}`}
                      className="bounce flex items-center justify-between gap-4 rounded-2xl bg-surface px-6 py-4 shadow-sm hover:shadow-lg"
                    >
                      <span className="flex-1 truncate">{a.question_text}</span>
                      <span className={`shrink-0 rounded-full px-3 py-1 text-sm font-medium ${verdictPillClass(a.verdict)}`}>
                        {VERDICT_LABEL[a.verdict] ?? a.verdict}
                      </span>
                      <span className="text-muted shrink-0 text-sm">
                        {new Date(a.matched_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </Reveal>
      )}
    </div>
  );
}
