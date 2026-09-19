import { useEffect, useState } from 'react'
import { resolveSource } from '../api'
import type { Citation, SourceResponse } from '../types'
import { citationLabel, citationToken } from '../types'

/**
 * A clickable citation. Claim-level grounding is only worth something if a reader
 * can check it, so every chip opens the actual row, document or filing page behind
 * the claim.
 */
export function CitationChip({
  citation,
  onOpen,
}: {
  citation: Citation
  onOpen: (token: string) => void
}) {
  const token = citationToken(citation)
  return (
    <button
      className={`chip chip--${citation.kind}`}
      onClick={() => onOpen(token)}
      title={token}
    >
      {citationLabel(citation)}
    </button>
  )
}

export function SourcePanel({ token, onClose }: { token: string; onClose: () => void }) {
  const [source, setSource] = useState<SourceResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setSource(null)
    setError(null)
    resolveSource(token)
      .then((s) => !cancelled && setSource(s))
      .catch((e) => !cancelled && setError(String(e)))
    return () => {
      cancelled = true
    }
  }, [token])

  return (
    <aside className="source-panel">
      <div className="source-panel__head">
        <span className="mono small">{token}</span>
        <button className="ghost" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>

      {error && <p className="bad">{error}</p>}
      {!source && !error && <p className="muted">Resolving…</p>}

      {source && !source.resolved && (
        <div className="unresolved">
          <strong>This citation does not resolve.</strong>
          <p>{source.error}</p>
        </div>
      )}

      {source?.resolved && (
        <>
          <h3>{source.title}</h3>
          {source.subtitle && <p className="muted">{source.subtitle}</p>}
          <dl className="fields">
            {Object.entries(source.fields).map(([key, value]) => (
              <div key={key} className={key === 'warning' ? 'field field--warn' : 'field'}>
                <dt>{key.replace(/_/g, ' ')}</dt>
                <dd>{String(value)}</dd>
              </div>
            ))}
          </dl>
          {source.text && <pre className="source-text">{source.text}</pre>}
        </>
      )}
    </aside>
  )
}
