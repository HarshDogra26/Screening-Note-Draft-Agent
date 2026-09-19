import { useEffect, useRef, useState } from 'react'
import { followRun, getHealth, startRun } from './api'
import { SourcePanel } from './components/Citations'
import {
  ConflictList,
  DroppedList,
  GapList,
  NoteView,
  PlanView,
  ToolTrace,
} from './components/NoteView'
import type {
  Conflict,
  DroppedClaim,
  Gap,
  Health,
  NotePlan,
  ScreeningNote,
  Stage,
  ToolCall,
  Violation,
} from './types'

const EXAMPLES = [
  'Screening note on Southeast Asia ethylene supply and H1 2026 price moves',
  'What is the capacity of Cilegon Cracker 1?',
  'How has Southeast Asia propylene moved since 2024?',
  'HDPE Southeast Asia prices from December 2025 to March 2026',
  'Outlook for Southeast Asia ethylene prices in 2027',
  'Compare Nusantara Olefins with Reliance Industries',
]

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [brief, setBrief] = useState(EXAMPLES[0])
  const [running, setRunning] = useState(false)
  const [stages, setStages] = useState<Stage[]>([])
  const [plan, setPlan] = useState<NotePlan | null>(null)
  const [toolCalls, setToolCalls] = useState<ToolCall[]>([])
  const [conflicts, setConflicts] = useState<Conflict[]>([])
  const [gaps, setGaps] = useState<Gap[]>([])
  const [note, setNote] = useState<ScreeningNote | null>(null)
  const [dropped, setDropped] = useState<DroppedClaim[]>([])
  const [violations, setViolations] = useState<Violation[]>([])
  const [error, setError] = useState<string | null>(null)
  const [openToken, setOpenToken] = useState<string | null>(null)
  const stopRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null))
    return () => stopRef.current?.()
  }, [])

  function reset() {
    setStages([])
    setPlan(null)
    setToolCalls([])
    setConflicts([])
    setGaps([])
    setNote(null)
    setDropped([])
    setViolations([])
    setError(null)
    setOpenToken(null)
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!brief.trim() || running) return
    stopRef.current?.()
    reset()
    setRunning(true)

    try {
      const runId = await startRun(brief.trim())
      stopRef.current = followRun(runId, (kind, payload) => {
        switch (kind) {
          case 'stage':
            setStages((s) => [...s, payload as Stage])
            break
          case 'plan':
            setPlan(payload as NotePlan)
            break
          case 'tool_call':
            setToolCalls((c) => [...c, payload as ToolCall])
            break
          case 'detected': {
            const d = payload as { conflicts: Conflict[]; gaps: Gap[] }
            setConflicts(d.conflicts ?? [])
            setGaps(d.gaps ?? [])
            break
          }
          case 'note': {
            const p = payload as {
              note: ScreeningNote | null
              dropped_claims: DroppedClaim[]
              violations: Violation[]
            }
            setNote(p.note)
            setDropped(p.dropped_claims ?? [])
            setViolations(p.violations ?? [])
            break
          }
          case 'error':
            setError((payload as { error: string }).error)
            break
          case 'closed':
            setRunning(false)
            break
        }
      })
    } catch (err) {
      setError(String(err))
      setRunning(false)
    }
  }

  const lastStage = stages[stages.length - 1]

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>Screening Note Agent</h1>
          <p className="muted small">
            A one-line brief becomes a structured note in which every claim traces to a
            plant, a price assessment, a document or a filing page.
          </p>
        </div>
        {health && (
          <div className="health">
            <span className={`dot ${health.mcp_reachable ? 'dot--ok' : 'dot--bad'}`} />
            <span className="small mono">
              {health.mcp_tools.length} tools · {health.provider_mode} ·{' '}
              {health.retrieval_mode.replace(/_/g, ' ')} ·{' '}
              {health.tracing ? `tracing → ${health.tracing_project}` : 'no tracing'}
            </span>
            {health.missing_credentials.length > 0 && (
              <span className="small caution">
                missing: {health.missing_credentials.join(', ')}
              </span>
            )}
          </div>
        )}
      </header>

      <form className="brief" onSubmit={submit}>
        <input
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
          placeholder="One line. What should the note cover?"
          disabled={running}
        />
        <button type="submit" disabled={running || !brief.trim()}>
          {running ? 'Working…' : 'Write note'}
        </button>
      </form>

      <div className="examples">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            className="ghost small"
            onClick={() => setBrief(example)}
            disabled={running}
          >
            {example}
          </button>
        ))}
      </div>

      {(running || stages.length > 0) && (
        <div className="stages">
          {stages.map((s, i) => (
            <span key={i} className="stage">
              {s.label}
            </span>
          ))}
          {running && lastStage && <span className="stage stage--live">working…</span>}
        </div>
      )}

      {error && <div className="card card--error">{error}</div>}

      <div className="layout">
        <main>
          {plan && <PlanView plan={plan} />}
          {note && <NoteView note={note} onOpen={setOpenToken} />}
          <ConflictList conflicts={conflicts} onOpen={setOpenToken} />
          <GapList gaps={gaps} onOpen={setOpenToken} />
          <DroppedList dropped={dropped} violations={violations} />
          <ToolTrace calls={toolCalls} />
          {note?.index_manifest && (
            <p className="small muted mono">
              corpus: {JSON.stringify(note.index_manifest)}
            </p>
          )}
        </main>

        {openToken && (
          <SourcePanel token={openToken} onClose={() => setOpenToken(null)} />
        )}
      </div>
    </div>
  )
}
