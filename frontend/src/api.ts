import type { Health, SourceResponse } from './types'

export async function getHealth(): Promise<Health> {
  const response = await fetch('/api/health')
  if (!response.ok) throw new Error(`health failed: ${response.status}`)
  return response.json()
}

export async function startRun(brief: string): Promise<string> {
  const response = await fetch('/api/notes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ brief }),
  })
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(`could not start run (${response.status}): ${detail}`)
  }
  const body = await response.json()
  return body.run_id as string
}

export async function resolveSource(token: string): Promise<SourceResponse> {
  const response = await fetch(`/api/sources?token=${encodeURIComponent(token)}`)
  if (!response.ok) throw new Error(`source lookup failed: ${response.status}`)
  return response.json()
}

export type StreamHandler = (kind: string, payload: unknown) => void

/**
 * Follow a run's server-sent events.
 *
 * The store replays everything buffered before following live, so subscribing late
 * still yields the full sequence from the start.
 */
export function followRun(runId: string, onEvent: StreamHandler): () => void {
  const source = new EventSource(`/api/notes/${runId}/stream`)
  const kinds = [
    'run_started',
    'stage',
    'plan',
    'tool_call',
    'detected',
    'refusal',
    'note',
    'error',
    'closed',
  ]

  for (const kind of kinds) {
    source.addEventListener(kind, (event) => {
      let payload: unknown = null
      try {
        payload = JSON.parse((event as MessageEvent).data)
      } catch {
        payload = (event as MessageEvent).data
      }
      onEvent(kind, payload)
      if (kind === 'closed') source.close()
    })
  }

  source.onerror = () => {
    // EventSource retries on its own; a closed stream after 'closed' is expected.
    if (source.readyState === EventSource.CLOSED) return
  }

  return () => source.close()
}
