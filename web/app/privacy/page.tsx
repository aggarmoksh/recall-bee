import { api } from "@/lib/api";
import Reveal from "@/components/Reveal";

export default async function PrivacyPage() {
  const data = await api.privacy();
  const skippedTotal = Object.values(data.sessions_skipped_by_type).reduce((a, b) => a + b, 0);

  return (
    <div className="flex flex-col gap-10">
      <Reveal>
        <h1 className="text-5xl font-bold tracking-tight">Privacy</h1>
        <p className="text-muted mt-2 text-lg">What Recall ignored, and why.</p>
      </Reveal>

      <Reveal delay={80}>
        <section className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="bounce rounded-3xl bg-yellow p-8 text-center text-yellow-foreground shadow-sm">
            <p className="text-5xl font-bold">{skippedTotal}</p>
            <p className="mt-2 text-sm opacity-80">
              casual/personal sessions skipped
              {data.sessions_skipped_by_type.casual !== undefined && (
                <> ({data.sessions_skipped_by_type.casual ?? 0} casual, {data.sessions_skipped_by_type.personal ?? 0} personal)</>
              )}
            </p>
          </div>
          <div className="bounce rounded-3xl bg-lavender p-8 text-center text-lavender-foreground shadow-sm">
            <p className="text-5xl font-bold">{data.excluded_sessions_count}</p>
            <p className="mt-2 text-sm opacity-80">sessions permanently excluded</p>
          </div>
        </section>
      </Reveal>

      <Reveal delay={160}>
        <section>
          <h2 className="mb-4 text-xl font-semibold">The rules</h2>
          <ul className="flex flex-col gap-3">
            {data.rules.map((rule) => (
              <li key={rule} className="bounce flex items-start gap-3 rounded-2xl bg-surface p-5 text-lg leading-relaxed shadow-sm hover:shadow-lg">
                <span className="text-accent">•</span>
                <span>{rule}</span>
              </li>
            ))}
          </ul>
        </section>
      </Reveal>
    </div>
  );
}
