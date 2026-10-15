# Spark Triage

An agentic assistant for the triage desk of a busy public outpatient department. It reads an intake card, scores it on the **South African Triage Scale (adult)**, and puts a **sourced, pending priority** in front of the triage nurse. The nurse approves, overrides or rejects it. Nothing moves in the queue until a named clinician has decided.

It never diagnoses, never prescribes, and never decides who is seen.

Built by SparkBlood Nigeria for the Agentic AI for Africa Innovation Challenge, Healthcare track (The Triage Desk). Theme: **symptom-based priority scoring**, with a deterioration re-check.

## Why this matters

In one Nigerian general hospital, outpatients waited 104 minutes just to have their vital signs taken, and about 220 minutes in total ([study](https://www.academia.edu/68757411/Waiting_for_Health_Care_Clients_Response_to_Waiting_Time_at_the_Outpatient_Department_of_a_General_Hospital_in_Nigeria)). Until a nurse reaches the card, a critically ill walk-in looks like everyone else. SATS has the strongest evidence of the adult triage scales studied in low- and middle-income countries ([systematic review, 2018](https://pubmed.ncbi.nlm.nih.gov/30534518/)), but no Nigerian validation was found, which is what blind mode is built to measure.

## Run it

```bash
./run.sh
```

One command: it creates a virtualenv, installs the project, pulls the open-weights model, writes the synthetic intake cards, runs the tests and opens the nurse's desk in your browser at http://127.0.0.1:8000.

Prerequisites: Python 3.11+, Node.js 18+ (for `npx`) and [Ollama](https://ollama.com), running. On Windows, run it from **Git Bash**. Everything runs on the desk computer; nothing is sent to the internet once the model is downloaded.

Default model: `qwen2.5:3b` (about 1.9 GB), chosen to run on an ordinary 8 GB laptop with no GPU. Pick another with `TRIAGE_MODEL=granite3.3:2b ./run.sh`.

| Model | Download | Licence | Note |
| --- | --- | --- | --- |
| `qwen2.5:3b` (default) | 1.9 GB | Qwen Research Licence, not for commercial use | Fine for this challenge and research |
| `granite3.3:2b` | 1.5 GB | Apache 2.0 | IBM; the clean choice for any later commercial use |

`evals/run_evals.py --models qwen2.5:3b granite3.3:2b` compares both on the same tasks.

### The nurse's desk

* **New arrivals**: intake cards waiting to be scored. Triage one, or all of them.
* **Needs your decision**: each proposal shows the colour, the card with the cited phrases highlighted, and every reason with its SATS rule. Approve it, change the priority (a note is required) or reject it. Flags for missing vitals or a child appear here too.
* **Queue**: only patients a nurse has confirmed, most urgent first, with time waited against the SATS target and an overdue marker. Re-check vitals from here, and **Send to bay** when the patient is called in.
* **Today's numbers**: how many patients have a confirmed priority, the median time from arrival to a confirmed priority, how many were sent to a bay within their SATS target, and how often a nurse lowered the agent's priority.
* **What the agent is doing**: every plan, model step, tool call and error, live.
* **Blind mode** (switch in the top bar): the nurse makes her own call before the agent's proposal is revealed, so her confirmation is an independent check rather than an anchored rubber stamp. The desk counts agreement, and separately counts every case where the agent was *less* urgent than the nurse (possible under-triage). While a call is pending, nothing on screen reveals the agent's answer: not the card order, the activity log or the audit trail.
* **Overdue sweep**: every few minutes (`TRIAGE_SWEEP_MIN`, default 5) the agent checks the queue and asks the nurse for a second look at anyone waiting past their SATS target. It never changes a priority. Code checks the sweep missed nobody, and a nurse who just looked is not nagged again for 30 minutes.
* **Nurse discretion**: the nurse can enter missing vitals, set a priority herself (for a child, or a patient the agent could not score) or change a confirmed one at any time, always with her name and a reason. This follows SATS's senior-clinician discretion and is recorded under rule `SATS-NURSE-DISCRETION`.
* **Audit trail**: click any patient number for a plain-language record of every step, from the card being read to the nurse's decision, with an "Open as text" version.

To run a session with a triage nurse, follow `docs/NURSE_SESSION.md`.

### Use the MCP server without our agent

The `triage-desk` server works with any MCP client, not only ours:

```bash
.venv/Scripts/python scripts/try_server.py      # plain MCP client, no model: lists tools, gets a fake quote refused, proposes, prints the audit log
npx @modelcontextprotocol/inspector .venv/Scripts/python -m triage_mcp.server   # the official visual MCP Inspector
```

Proposals made from any client still wait for the nurse: the server has no approval tool.

### Other commands

```bash
.venv/Scripts/python -m agent.cli web        # the desk (on macOS/Linux: .venv/bin/python)
.venv/Scripts/python -m agent.cli desk       # same workflow in the terminal (not in blind mode)
.venv/Scripts/python -m agent.cli queue
.venv/Scripts/python -m agent.cli audit P002 # every tool call for one patient
.venv/Scripts/python -m pytest -q            # tests, no model needed
.venv/Scripts/python evals/run_evals.py --runs 2 --models qwen2.5:3b granite3.3:2b
```

## What happens for one patient

1. **plan**: the model writes a short plan before calling anything.
2. **read**: it reads the card with the borrowed filesystem MCP server (read-only tools only).
3. **register / score**: it registers the card on our `triage-desk` MCP server and asks for the TEWS. Vitals come from the stored card, never from the model's arguments.
4. **cite**: it picks SATS discriminators and must quote the exact phrase from the card for each. If any quote is not on the card, the server rejects the whole proposal and the model has to repair it.
5. **propose**: the server computes the colour (most urgent of TEWS colour and discriminators). The model cannot set it.
6. **verify**: deterministic code checks a pending proposal or flag really exists and that the card the agent registered is identical to the file on disk. If not, the agent is sent back with the reason.
7. **nurse gate**: LangGraph pauses. The nurse sees the colour, TEWS breakdown, every source with its SATS rule id, and the plan. They approve, override (with a required note) or reject. Only then does the patient get a queue position.

Missing vitals, a child, or a card the agent can't finish all go back to the nurse as a flag or as untriaged, never as a guess.

## Layout

```
triage_mcp/       our MCP server (6 tools) + SATS rules + SQLite store and audit log
agent/            LangGraph agent, the nurse's web desk and the terminal CLI
data/             synthetic card generator, intake cards, SATS reference with rule ids
evals/            answer key and the agent eval runner
tests/            deterministic tests (scoring, sourcing, gate, audit, MCP over stdio)
```

## Honest status

* Deterministic layer (scoring, sourcing checks, gate, audit log, MCP server over stdio) and the desk's web API, including blind mode, the sweep, the audit trail nurse discretion and the waiting-time numbers: 29 tests, passing. The web tests use a labelled stand-in agent, so they check the desk's wiring, not the model.
* Agent layer (LangGraph + Ollama + filesystem server): see EVALS.md for what has and has not been run.
* SATS thresholds were transcribed by hand from the published adult chart and still need a line-by-line check by a clinician.
* Adult chart only. Children are flagged to the nurse, not scored.
* All patient data in this repo is synthetic. Do not put real patient data in it without consent; health data must stay in-country, which is why the model runs locally.

## Licence

Code: Apache-2.0. SATS content (`data/sats_reference.md` and the thresholds in `triage_mcp/sats.py`) is derived from the Adult SATS chart (c) 2012 South African Triage Group / EMSSA, CC BY-NC-SA 3.0.
