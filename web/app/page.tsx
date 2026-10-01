"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, TodayResponse } from "@/lib/api";
import Reveal from "@/components/Reveal";

const POLL_MS = 5000;
const GREETING_NAME = "Lord Moksh"; // this operator's own Bee display name

const VERDICT_LABEL: Record<string, string> = {
  correct: "Correct",
  partial: "Partial",
  incorrect: "Incorrect",
  not_an_answer: "Not an answer",
  no_answer: "Missed, not counted against you",
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

export default function TodayPage() {
  const [today, setToday] = useState<TodayResponse | null>(null);
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

  return (
    <div className="flex flex-col gap-12">
      <Reveal>
        <p className="text-lg text-muted">Hi {GREETING_NAME},</p>
        <h1 className="text-5xl font-bold tracking-tight">{greeting()}!</h1>
      </Reveal>

      {error && <p className="text-bad text-lg">{error}</p>}
      {!error && !today && <p className="text-muted text-lg">Loading…</p>}

      {today && (
        <Reveal delay={60}>
          <section className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="pop rounded-3xl bg-yellow p-8 text-yellow-foreground shadow-sm">
              <p className="text-sm font-semibold uppercase tracking-wide opacity-80">Daily goal</p>
              <p className="mt-2 text-4xl font-bold">
                {today.answered_today} <span className="text-2xl font-medium opacity-70">of {today.daily_quota}</span>
              </p>
              <p className="mt-2 text-sm opacity-80">{today.goal_met ? "Goal met for today" : "Still a few to go"}</p>
            </div>
            <div className="pop rounded-3xl bg-lavender p-8 text-lavender-foreground shadow-sm" style={{ animationDelay: "60ms" }}>
              <p className="text-sm font-semibold uppercase tracking-wide opacity-80">Streak</p>
              <p className="mt-2 text-4xl font-bold">
                {today.streak_days} <span className="text-2xl font-medium opacity-70">day{today.streak_days === 1 ? "" : "s"}</span>
              </p>
              <p className="mt-2 text-sm opacity-80">Consecutive days the daily goal was met</p>
            </div>
          </section>
        </Reveal>
      )}

      {today && (
        <Reveal delay={120}>
          <section>
            {today.session.state === "in_session" ? (
              <div className="pop rounded-3xl bg-lavender p-12 text-center text-lavender-foreground shadow-sm">
                <span className="mx-auto mb-6 inline-flex items-center gap-2 rounded-full bg-white/70 px-4 py-1.5 text-sm font-medium backdrop-blur-sm">
                  <span className="h-2 w-2 rounded-full bg-accent animate-pulse" />
                  In session: question {today.session.questions_asked} of up to {today.session.daily_quota}
                </span>
                {today.session.current_question?.question_text ? (
                  <p className="mx-auto max-w-2xl text-3xl font-semibold leading-snug">{today.session.current_question.question_text}</p>
                ) : (
                  <p className="mx-auto max-w-2xl text-lg opacity-80">Waiting for Bee to pick up the next question.</p>
                )}
              </div>
            ) : today.report_card ? (
              <div className="pop rounded-3xl bg-yellow p-10 text-yellow-foreground shadow-sm">
                <span className="mb-4 inline-block rounded-full bg-white/60 px-3 py-1 text-xs font-semibold uppercase tracking-wide">
                  Today&apos;s report
                </span>
                <p className="text-2xl font-semibold leading-snug">
                  {today.report_card.questions_answered} of {today.report_card.questions_asked} answered
                </p>
                <p className="mt-2 text-sm opacity-80">
                  Session ended {new Date(today.report_card.ended_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </p>
              </div>
            ) : (
              <div className="rounded-3xl border border-border bg-surface py-20 text-center shadow-sm">
                <p className="text-muted text-lg">Idle. Tick the ready todo or say the start phrase to begin a session.</p>
              </div>
            )}
          </section>
        </Reveal>
      )}

      {today && (
        <Reveal delay={180}>
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
                      <span className="flex-1 truncate">{a.question_text ?? "(question removed)"}</span>
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
