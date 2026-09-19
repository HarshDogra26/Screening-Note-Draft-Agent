export type CitationKind = 'plant' | 'price' | 'document' | 'filing'

export interface Citation {
  kind: CitationKind
  plant_id?: string
  product?: string
  region?: string
  month?: string
  basis?: string
  filename?: string
  company?: string
  page?: number
  quote?: string
}

export interface Claim {
  text: string
  citations: Citation[]
  status: 'asserted' | 'not_publicly_confirmed' | 'disputed'
}

export interface Gap {
  subject: string
  reason: string
  detail: string
  citations: Citation[]
}

export interface AttributedPosition {
  source_label: string
  value: string
  stated_reason: string | null
  citations: Citation[]
}

export interface Conflict {
  subject: string
  positions: AttributedPosition[]
  note: string
}

export interface Section {
  id: string
  kind: string
  title: string
  claims: Claim[]
  gaps: Gap[]
  conflicts: Conflict[]
  coverage_note: string | null
}

export interface SectionPlan {
  id: string
  kind: string
  title: string
  questions: string[]
  intended_tools: string[]
  tool_rationale: string
  unanswerable_if: string
}

export interface NotePlan {
  brief: string
  domain: string
  sections: SectionPlan[]
  excluded: { topic: string; reason: string }[]
}

export interface Refusal {
  reason: string
  detail: string
  citations: Citation[]
}

export interface ScreeningNote {
  run_id: string
  brief: string
  domain: string
  plan: NotePlan | Record<string, never>
  sections: Section[]
  conflicts: Conflict[]
  gaps: Gap[]
  refusal: Refusal | null
  index_manifest: Record<string, unknown>
  tool_calls: ToolCall[]
}

export interface ToolCall {
  tool: string
  arguments: Record<string, unknown>
  kind: string
  n: number | null
  why?: string
}

export interface DroppedClaim {
  section: string
  text: string
  unresolved_citations: string[]
  unsupported_numbers: string[]
}

export interface Violation {
  where: string
  claim_text: string
  offending_citations: string[]
  domain: string
}

export interface SourceResponse {
  token: string
  kind: string
  resolved: boolean
  title: string | null
  subtitle: string | null
  fields: Record<string, unknown>
  text: string | null
  error: string | null
}

export interface Health {
  status: string
  provider_mode: string
  retrieval_mode: string
  mcp_reachable: boolean
  tracing: boolean
  tracing_project: string | null
  mcp_tools: string[]
  index_manifest: Record<string, unknown>
  missing_credentials: string[]
}

export interface Stage {
  node: string
  label: string
}

/** Turns a citation into the token the /api/sources endpoint understands. */
export function citationToken(c: Citation): string {
  switch (c.kind) {
    case 'plant':
      return `plant:${c.plant_id}`
    case 'price':
      return `price:${c.product}|${c.region}|${c.month}|${c.basis}`
    case 'document':
      return `doc:${c.filename}`
    case 'filing':
      return `filing:${c.company}|${c.filename}${c.page ? `|p.${c.page}` : ''}`
  }
}

/** Short label shown on the chip itself. */
export function citationLabel(c: Citation): string {
  switch (c.kind) {
    case 'plant':
      return c.plant_id ?? 'plant'
    case 'price':
      return `${c.product} ${c.month} ${c.basis}`
    case 'document':
      return (c.filename ?? '').replace(/\.md$/, '').replace(/^(\d{3})_/, '$1 ')
    case 'filing':
      return `${c.company} ${c.page ? `p.${c.page}` : ''}`.trim()
  }
}
