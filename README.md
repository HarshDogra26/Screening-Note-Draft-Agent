# Screening Note Agent

Turns a one-line brief into a structured screening note in which **every factual claim
traces to a source** — a `plant_id`, a price assessment (product, region, month,
basis), a document filename, or a filing page.

The system is built around one principle: **comparability, nullity and conflict are
decided by deterministic Python, never by the model.** The LLM plans, selects tools and
writes prose. It is never the component that decides whether two numbers may be
subtracted, whether a blank means zero, or which of two disagreeing sources is right.

---

## 1. Prerequisites

- Python 3.11+
- Node 18+
- An Azure OpenAI resource with a chat deployment and an embeddings deployment

## 2. Setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy .env.example .env
```

Fill in `backend/.env`:

```
PROVIDER_MODE=live
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=<key>
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4.1
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large
```

Optional, for tracing — key from https://smith.langchain.com → Settings → API Keys:

```
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_pt_...
LANGCHAIN_PROJECT=screening-note-agent
```

## 3. Build the index

```powershell
.\.venv\Scripts\python.exe -m ingestion.cli all      # validate corpus + write manifest
.\.venv\Scripts\python.exe -m ingestion.cli embed    # embed Part B chunks (~$0.07, cached)
```

`all` refuses to write a manifest if the corpus does not match its expected shape —
52 plants, 190 price rows, 27 documents and the exact 7-series price inventory.

`embed` is optional. Without it retrieval runs lexical-only and **says so** in every
tool response, so a note can never present reduced recall as a complete answer.

## 4. Run

```powershell
# terminal 1 — backend
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000

# terminal 2 — frontend
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**.

Confirm the backend is healthy:

```powershell
curl http://127.0.0.1:8000/api/health
```

Expect `provider_mode: live`, `retrieval_mode: hybrid`, `mcp_reachable: true`,
`missing_credentials: []`.

### Running the MCP server as a separate process

`MCP_INPROCESS=true` (the default in `.env.example` is `false`) mounts the tool server
inside the API. To run it as a genuine separate process over HTTP — which is the
intended architecture — set `MCP_INPROCESS=false` and start it first:

```powershell
.\.venv\Scripts\python.exe -m mcp_servers.screening_server --port 8766
```

---

## 5. Using it

Enter a one-line brief. The UI shows, in order:

1. **The plan** — decided before any retrieval: sections, the questions each must
   answer, which tools it expects to need and why, and what would make it unanswerable.
2. **The note** — claims with clickable citations. Clicking one resolves it against the
   real source, so any claim can be checked in one click.
3. **Conflicts** — both positions, attributed, explicitly unresolved.
4. **Gaps** — what the corpus does not establish, and why.
5. **Removed during verification** — claims the model wrote that failed checking, and
   the reason each was dropped.
6. **Tool calls** — what the agent chose to call, with arguments.


## 6. How the requirements are met

| # | Requirement | Where |
|---|---|---|
| 1 | **Planning** | Plan emitted before any retrieval, in the API response, the logs (`plan.emitted`) and the UI |
| 2 | **Tools** | Five over MCP: corpus description, plant register, price series, Part A documents, Part B filings. The agent chooses which, in what order, and when it has enough |
| 3 | **Grounding** | Claim-level, enforced by type — `Claim.citations` has `min_length=1`. Every number must appear in retrieved evidence or be a declared calculation with cited inputs; claims that fail are dropped |
| 4 | **Gaps and conflicts** | `Conflict` has **no** `resolution` field, so silently picking a side is unrepresentable. Blank values are never estimated; missing months and withheld values are reported as different facts |
| 5 | **Reproducibility** | Temperature 0, fixed seed, structured outputs, fixed section catalogue, deterministic tool layer, content-hash embedding cache, exact vector search, stable ordering. **Measured metrics implemented but not yet run** — see below |
| 6 | **Evaluation** | 21 cases including 5 where the correct answer is a refusal, plus 43 tests proving the assertions can actually fail |

### What the system refuses to do

Enforced in Python, not requested in a prompt:

- Compute a change across a basis break (propylene switches Spot→Contract at 2026-01)
- Sum capacity without an explicit status filter
- Treat a blank capacity or a withheld price as zero
- Resolve a disagreement between sources
- State a number that is not in the retrieved evidence
- Combine the synthetic dataset and the real filings in one note
- Use the other dataset's tools during a note

---

## 7. Layout

```
data-part a/     plants.csv, prices.csv, documents/ (27 md)    [synthetic]
data-part b/     7 PDFs + 2 XLSX for three real groups          [real filings]

backend/
  app/domain/    models that make fabrication unrepresentable
  app/services/  deterministic engines: prices, register, grounding,
                 conflicts, domain guard, retrieval
  app/graph/     LangGraph orchestrator, nodes, MCP client
  app/prompts/   versioned prompt files
  app/api/       FastAPI routes, SSE streaming, citation resolver
  mcp_servers/   the five tools, served over streamable HTTP
  ingestion/     parsing, chunking, supersession, manifest, CLI
  eval/          21 cases, assertions, reproducibility, findings
  tests/         234 tests
frontend/        Vite + React + TypeScript
```

**Stack:** Python · FastAPI · LangGraph · Azure OpenAI · MCP (FastMCP) · ChromaDB ·
LangSmith · React + TypeScript
