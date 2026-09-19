import type {
  Conflict,
  DroppedClaim,
  Gap,
  NotePlan,
  ScreeningNote,
  ToolCall,
  Violation,
} from '../types'
import { CitationChip } from './Citations'

/** The plan, shown before and alongside the note. Requirement 1. */
export function PlanView({ plan }: { plan: NotePlan }) {
  if (!plan?.sections?.length) return null
  return (
    <section className="card">
      <h2>Plan</h2>
      <p className="muted small">
        Decided before any retrieval. Each section states what it must answer, which
        tools it expects to need and why, and what would make it unanswerable.
      </p>
      <ol className="plan">
        {plan.sections.map((s) => (
          <li key={s.id}>
            <div className="plan__title">
              <strong>{s.title}</strong>
              <span className="tag">{s.kind}</span>
            </div>
            <ul className="questions">
              {s.questions.map((q) => (
                <li key={q}>{q}</li>
              ))}
            </ul>
            <p className="small">
              <span className="muted">Tools: </span>
              {s.intended_tools.map((t) => (
                <code key={t}>{t}</code>
              ))}
            </p>
            <p className="small muted">{s.tool_rationale}</p>
            <p className="small caution">Unanswerable if: {s.unanswerable_if}</p>
          </li>
        ))}
      </ol>
      {plan.excluded?.length > 0 && (
        <div className="excluded">
          <strong className="small">Excluded from the note</strong>
          {plan.excluded.map((e) => (
            <p key={e.topic} className="small">
              <em>{e.topic}</em> — {e.reason}
            </p>
          ))}
        </div>
      )}
    </section>
  )
}

export function ToolTrace({ calls }: { calls: ToolCall[] }) {
  if (!calls.length) return null
  return (
    <section className="card">
      <h2>Tool calls ({calls.length})</h2>
      <ol className="trace">
        {calls.map((c, i) => (
          <li key={i}>
            <code>{c.tool}</code>
            <span className="mono small muted">{JSON.stringify(c.arguments)}</span>
            <span className={`tag ${c.kind === 'error' ? 'tag--bad' : ''}`}>
              {c.kind}
              {c.n != null ? ` · ${c.n}` : ''}
            </span>
          </li>
        ))}
      </ol>
    </section>
  )
}

/** Disagreements, shown with both positions and no resolution. */
export function ConflictList({
  conflicts,
  onOpen,
}: {
  conflicts: Conflict[]
  onOpen: (token: string) => void
}) {
  if (!conflicts.length) return null
  return (
    <section className="card card--conflict">
      <h2>Conflicts ({conflicts.length})</h2>
      <p className="muted small">
        Sources disagree. Both positions are shown with attribution; the note does not
        pick between them.
      </p>
      {conflicts.map((c) => (
        <article key={c.subject} className="conflict">
          <h3>{c.subject}</h3>
          {c.positions.map((p, i) => (
            <div key={i} className="position">
              <div className="position__head">
                <span className="value">{p.value}</span>
                <span className="muted">per {p.source_label}</span>
              </div>
              {p.stated_reason && <p className="small">{p.stated_reason}</p>}
              <div className="chips">
                {p.citations.map((cit, j) => (
                  <CitationChip key={j} citation={cit} onOpen={onOpen} />
                ))}
              </div>
            </div>
          ))}
          <p className="small caution">{c.note}</p>
        </article>
      ))}
    </section>
  )
}

export function GapList({ gaps, onOpen }: { gaps: Gap[]; onOpen: (t: string) => void }) {
  if (!gaps.length) return null
  return (
    <section className="card card--gap">
      <h2>Gaps ({gaps.length})</h2>
      <p className="muted small">
        Figures the corpus does not establish. Not estimated, not treated as zero.
      </p>
      {gaps.map((g, i) => (
        <article key={i} className="gap">
          <div className="gap__head">
            <strong>{g.subject}</strong>
            <span className="tag">{g.reason.replace(/_/g, ' ')}</span>
          </div>
          <p className="small">{g.detail}</p>
          <div className="chips">
            {g.citations.map((cit, j) => (
              <CitationChip key={j} citation={cit} onOpen={onOpen} />
            ))}
          </div>
        </article>
      ))}
    </section>
  )
}

/** What the verifier removed. Shown rather than hidden. */
export function DroppedList({
  dropped,
  violations,
}: {
  dropped: DroppedClaim[]
  violations: Violation[]
}) {
  if (!dropped.length && !violations.length) return null
  return (
    <section className="card card--dropped">
      <h2>Removed during verification</h2>
      <p className="muted small">
        Claims the model wrote that did not survive checking. Shown so the note's
        coverage can be judged honestly.
      </p>
      {violations.map((v, i) => (
        <div key={`v${i}`} className="dropped">
          <p className="small">{v.claim_text}</p>
          <p className="small bad">
            Cited the other dataset: {v.offending_citations.join(', ')}
          </p>
        </div>
      ))}
      {dropped.map((d, i) => (
        <div key={`d${i}`} className="dropped">
          <p className="small">{d.text}</p>
          {d.unsupported_numbers.length > 0 && (
            <p className="small bad">
              Numbers not found in the retrieved evidence:{' '}
              {d.unsupported_numbers.join(', ')}
            </p>
          )}
          {d.unresolved_citations.length > 0 && (
            <p className="small bad">
              Citations that do not resolve: {d.unresolved_citations.join(', ')}
            </p>
          )}
        </div>
      ))}
    </section>
  )
}

export function NoteView({
  note,
  onOpen,
}: {
  note: ScreeningNote
  onOpen: (token: string) => void
}) {
  if (note.refusal) {
    return (
      <section className="card card--refusal">
        <h2>Declined</h2>
        <p>{note.refusal.detail}</p>
        <p className="small muted">
          Reason code: <code>{note.refusal.reason}</code>
        </p>
      </section>
    )
  }

  return (
    <section className="card">
      <h2>Note</h2>
      {note.sections.map((section) => (
        <article key={section.id} className="section">
          <h3>{section.title}</h3>
          {section.claims.length === 0 && (
            <p className="muted small">No claims survived verification for this section.</p>
          )}
          {section.claims.map((claim, i) => (
            <p key={i} className={`claim claim--${claim.status}`}>
              {claim.text}{' '}
              <span className="chips chips--inline">
                {claim.citations.map((cit, j) => (
                  <CitationChip key={j} citation={cit} onOpen={onOpen} />
                ))}
              </span>
            </p>
          ))}
          {section.coverage_note && (
            <p className="small caution">{section.coverage_note}</p>
          )}
        </article>
      ))}
    </section>
  )
}
