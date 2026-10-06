import { api, ExternalAccessEntry } from "@/lib/api";
import Reveal from "@/components/Reveal";

function _entryLabel(entry: ExternalAccessEntry): string {
  const name = entry.name ?? entry.service ?? entry.service_name ?? entry.id;
  return typeof name === "string" || typeof name === "number" ? String(name) : "Unknown entry";
}

export default async function PrivacyPage() {
  const data = await api.privacy();
  const skippedTotal = Object.values(data.sessions_skipped_by_type).reduce((a, b) => a + b, 0);

  let externalAccess: ExternalAccessEntry[] | null = null;
  try {
    externalAccess = (await api.externalAccess()).external_access;
  } catch {
    externalAccess = null; // Bee unreachable -- section renders a fallback message instead of failing the page
  }

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

      {/* Phase 5F Step 5: hidden entirely when the list is empty -- an
          empty "What Recall read from your Bee" section with nothing in
          it is just noise, and developer-mode CLI/proxy reads don't show
          up here anyway (see FRICTION_LOG), so an empty list isn't a
          meaningful "nothing has accessed your data" claim either. */}
      {externalAccess === null ? (
        <Reveal delay={220}>
          <section>
            <h2 className="mb-4 text-xl font-semibold">What Recall read from your Bee</h2>
            <div className="rounded-3xl border border-border bg-surface py-10 text-center shadow-sm">
              <p className="text-muted text-lg">Could not reach Bee to check this right now.</p>
            </div>
          </section>
        </Reveal>
      ) : externalAccess.length > 0 ? (
        <Reveal delay={220}>
          <section>
            <h2 className="mb-4 text-xl font-semibold">What Recall read from your Bee</h2>
            <ul className="flex flex-col gap-3">
              {externalAccess.map((entry, i) => (
                <li key={i} className="bounce rounded-2xl bg-surface p-5 shadow-sm hover:shadow-lg">
                  <p className="text-lg font-medium">{_entryLabel(entry)}</p>
                  <pre className="text-muted mt-2 overflow-x-auto text-sm">{JSON.stringify(entry, null, 2)}</pre>
                </li>
              ))}
            </ul>
          </section>
        </Reveal>
      ) : null}
    </div>
  );
}
