# Architecture

## Agent shape

A LangGraph state machine on a local open-weights model (Ollama, default `qwen2.5:3b`, sized for an 8 GB desk laptop with no GPU; `granite3.3:2b` is the Apache-2.0 alternative). The SATS score is a fixed rule engine in code, and the model only reads free text and cites what it found, so the agent can never score a patient below what the vitals say.

```
plan -> act <-> tools -> verify --retry (max 2)--> act
                            |
                            +--> nurse_gate (interrupt, named clinician) -> END
                            +--> END: failed, patient shown to nurse as untriaged
```

* **plan** (model): numbered plan naming tools and likely failure points.
* **act / tools** (model + MCP): tool-calling loop, max 14 steps. Tool errors are returned to the model as text to repair, not swallowed.
* **verify** (code, not model): confirms a pending proposal or flag exists in the store, and that the registered card equals the file on disk (catches altered vitals). Sends the agent back with the reason if it stopped early.
* **nurse_gate** (human): `interrupt()`. The decision tool lives outside every MCP server, so no model can confirm its own proposal.

## The desk

`agent/webdesk.py` serves one offline page on the desk computer. One agent job runs at a time (the model is CPU-bound). Runs paused at the nurse gate are held in memory and resumed with the nurse's decision; a proposal left over from an earlier session is decided directly through the same gate function, with the same audit row.

**Blind mode.** When on, a pending proposal is served to the page with no colour, sources or score, the list is ordered by arrival instead of urgency, colour-bearing tool results in the live log are masked, and the audit trail is locked until the nurse records her own call. The server, not the page, refuses an approval without a blind call. Agreement and under-triage counts come from `blind_calls` joined to `proposals`.

**Overdue sweep.** A third agent mode on the same graph: the model calls `get_queue` and raises `nurse_review` flags; the verify node then recomputes in code who is overdue without a flag and sends the model back if it missed anyone. Flags are idempotent, and a 30-minute cooldown follows a nurse closing one.

## Division of labour

The model reads free text and chooses which SATS discriminators apply, with an exact quote for each. Everything with a number is code: TEWS, measured discriminators (glucose), colour, target time, queue order, overdue flags. A priority that cannot be traced to a vital or a quote cannot be created.

## MCP servers

| Server | Built or borrowed | Tools used | Why |
|---|---|---|---|
| `triage-desk` (`triage_mcp/server.py`) | **Built** | `register_patient`*, `compute_tews`, `list_discriminators`, `propose_priority`*, `raise_recheck_flag`*, `get_queue` (* = writes) | The scoring, sourcing rules and audit are the product. Tool boundaries are reusable by any MCP client: another facility's agent can call the same server. |
| `@modelcontextprotocol/server-filesystem` | **Borrowed** (official reference server) | `read_text_file`, `list_directory` only | Intake cards arrive as files exported from the desk tablet. Its path sandboxing is maintained and tested by the MCP project; rewriting that is risk with no benefit. We bind only its read-only tools. |

## Logging and gates

* Every `triage-desk` call is written to the SQLite `audit` table (timestamp, actor, tool, inputs, outputs, success) by the server itself, including failed calls. Calls to the borrowed filesystem server are logged into the same table by the agent.
* Each agent run also writes `runs/<time>_<patient>_<id>.json`: plan, every tool call with latency, tokens, outcome and the nurse decision.
* Irreversible step = a patient getting or changing a queue position. Gate: `record_clinician_decision` requires a clinician name; overrides require a note. Re-check flags with worse vitals create a **pending** re-triage, the confirmed colour stays until the nurse acts.

## Data and cost

* Synthetic patients only (`data/make_synthetic.py`). The model runs on the facility's machine, so patient data never leaves the country.
* Cost per run is local compute: the CLI prints model calls, tokens and seconds for every patient; `evals/results.md` reports the averages.

## Known limits

Adult SATS only; SpO2 recorded but not scored (not part of adult TEWS); thresholds hand-transcribed and awaiting clinical check; single desk, single SQLite file (no concurrency handling beyond SQLite's own).
