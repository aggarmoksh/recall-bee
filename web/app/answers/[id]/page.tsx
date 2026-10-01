import Link from "next/link";
import { api } from "@/lib/api";
import Reveal from "@/components/Reveal";

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

export default async function AnswerPage({ params }: PageProps<"/answers/[id]">) {
  const { id } = await params;
  let detail;
  try {
    detail = await api.answer(Number(id));
  } catch {
    return (
      <div className="flex flex-col gap-4">
        <Link href="/" className="bounce text-accent text-sm hover:underline">
          ← Back to Today
        </Link>
        <p className="text-muted text-lg">Answer not found.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <Link href="/" className="bounce inline-block text-accent text-sm hover:underline">
        ← Back to Today
      </Link>

      <Reveal>
        <h1 className="text-4xl font-bold tracking-tight">{detail.question?.text}</h1>
        <div className="mt-3 flex items-center gap-3">
          <span className={`rounded-full px-3 py-1 text-sm font-medium ${verdictPillClass(detail.verdict)}`}>
            {VERDICT_LABEL[detail.verdict] ?? detail.verdict}
          </span>
          {detail.quality !== null && <span className="text-muted text-sm">quality {detail.quality}/5</span>}
        </div>
      </Reveal>

      <Reveal delay={80}>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="bounce rounded-3xl bg-yellow p-10 text-yellow-foreground shadow-sm">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide">Your spoken answer</h2>
            <p className="text-lg leading-relaxed">{detail.answer_transcript || <span className="opacity-60">(nothing captured)</span>}</p>
          </div>

          <div className="bounce rounded-3xl bg-lavender p-10 text-lavender-foreground shadow-sm">
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide">Rubric</h2>
            <ul className="flex flex-col gap-2">
              {detail.rubric.map((r) => (
                <li key={r.point} className="flex items-center gap-2 text-lg">
                  <span className={r.hit ? "text-[color:var(--good)]" : "text-[color:var(--bad)]"}>{r.hit ? "✓" : "✗"}</span>
                  <span>{r.point}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </Reveal>

      {detail.feedback && (
        <Reveal delay={140}>
          <section className="bounce rounded-3xl bg-surface p-6 shadow-sm">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-muted text-sm font-semibold uppercase tracking-wide">Sent to your wrist</h2>
              {detail.hint_in_notification !== null && (
                <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${detail.hint_in_notification ? "bg-accent/15 text-accent" : "bg-zinc-500/15 text-muted"}`}>
                  {detail.hint_in_notification ? "hint included" : "no hint"}
                </span>
              )}
            </div>
            <p className="text-lg">{detail.feedback}</p>
          </section>
        </Reveal>
      )}

      <Reveal delay={200}>
        <section className="bounce rounded-3xl bg-surface p-6 shadow-sm">
          <h2 className="text-muted mb-2 text-sm font-semibold uppercase tracking-wide">
            Source{detail.session_datetime && <>: {new Date(detail.session_datetime).toLocaleString()}</>}
          </h2>
          <p className="text-lg leading-relaxed italic">&ldquo;{detail.source_excerpt}&rdquo;</p>
        </section>
      </Reveal>
    </div>
  );
}
