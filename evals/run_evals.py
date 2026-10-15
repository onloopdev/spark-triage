"""Agent evals: run every synthetic card through the full agent N times.

Each run uses a fresh database and a different model seed so run-to-run
variation is measured, not hidden. The agent is stopped at the nurse gate;
the eval reads what it proposed and never approves on a clinician's behalf.

  python evals/run_evals.py              # 3 runs per task (EVAL_RUNS to change)
  python evals/run_evals.py --runs 2 --models qwen2.5:3b granite3.3:2b   # compare two models
  python evals/run_evals.py --only P008 P013

Writes evals/results.json (every run) and evals/results.md (summary table).
Requires Ollama with TRIAGE_MODEL pulled, and Node (npx) for the filesystem server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from triage_mcp import desk, store  # noqa: E402

KEY = json.loads((ROOT / "evals" / "cases.json").read_text())


def outcome_label(run: dict) -> str:
    o = run["state"].get("outcome") or {}
    if o.get("type") == "proposal":
        return o["proposed_colour"]
    if o.get("type") == "flag":
        rp = o.get("retriage_proposal")
        return f"retriage:{rp['proposed_colour']}" if rp else f"flag:{o['kind']}"
    return "failed"


def summarise(run: dict, expected: str, task_id: str, seed: int) -> dict:
    s = run["state"]
    log = s.get("tool_log", [])
    errors = [e for e in log if not e["ok"]]
    got = outcome_label(run)
    q = desk.get_queue()
    return {
        "task": task_id, "seed": seed, "expected": expected, "got": got, "pass": got == expected,
        "gate_held": q["queue"] == [] if task_id != "RECHECK-P002" else True,
        "transcription_ok": not any("MISMATCH" in n for n in s.get("verify_notes") or []),
        "tool_calls": len(log), "tool_errors": len(errors),
        "recovered": bool(errors) and got != "failed",
        "verify_retries": s.get("verify_attempts", 0),
        "seconds": run["seconds"], "usage": s.get("usage"),
        "error_samples": [e["result"][:200] for e in errors[:3]],
    }


COLOUR_RANK = {"green": 0, "yellow": 1, "orange": 2, "red": 3}


def triage_errors(rows: list[dict]) -> str:
    """Under- and over-triage on the colour cases, the measures SATS studies report."""
    graded = [r for r in rows if r["expected"] in COLOUR_RANK]
    if not graded:
        return ""
    under = sum(1 for r in graded if r["got"] in COLOUR_RANK and COLOUR_RANK[r["got"]] < COLOUR_RANK[r["expected"]])
    over = sum(1 for r in graded if r["got"] in COLOUR_RANK and COLOUR_RANK[r["got"]] > COLOUR_RANK[r["expected"]])
    none = sum(1 for r in graded if r["got"] not in COLOUR_RANK)
    pct = lambda n: f"{100 * n / len(graded):.0f}%"
    return (f"Under-triage {under}/{len(graded)} ({pct(under)}), over-triage {over}/{len(graded)} ({pct(over)}), "
            f"no proposal {none}/{len(graded)}. For reference only (our set is small and synthetic): published SATS "
            "studies report under-triage of about 10% (South African validation) and 7.8% (Kenyatta National "
            "Hospital, Nairobi), and over-triage of 15% to 62%.")


async def run_model(model: str, runs: int, only: list[str] | None) -> list[dict]:
    from agent.graph import INTAKE_DIR, TriageAgent, intake_task, recheck_task

    rows = []
    cases = [c for c in KEY["cases"] if not only or c["patient_id"] in only]
    for seed in range(1, runs + 1):
        for case in cases:
            db = tempfile.mktemp(suffix=".db")
            async with TriageAgent(db, model) as agent:
                card = INTAKE_DIR / f"{case['patient_id']}.json"
                run = await agent.start(intake_task(card), card_path=str(card), seed=seed)
                rows.append(summarise(run, case["expected"], case["patient_id"], seed))
                agent.save_run(run)
                print(rows[-1]["task"], seed, rows[-1]["got"], "PASS" if rows[-1]["pass"] else "FAIL", flush=True)
        if not only or "P002" in only:
            rc = KEY["rechecks"]["P002"]
            db = tempfile.mktemp(suffix=".db")
            async with TriageAgent(db, model) as agent:
                # Harness setup, not a clinical decision: P002 starts with a confirmed orange.
                desk.register_patient(json.loads((INTAKE_DIR / "P002.json").read_text()))
                p = desk.propose_priority("P002", [{"code": "CHEST_PAIN", "evidence_quote": "chest pain"}], "setup")
                desk.record_clinician_decision(p["proposal_id"], "EVAL HARNESS (synthetic)", "approve")
                run = await agent.start(recheck_task("P002", rc["reason"], rc["new_vitals"]),
                                        patient_id="P002", seed=seed, mode="recheck")
                row = summarise(run, f"retriage:{rc['expected_colour']}", "RECHECK-P002", seed)
                row["gate_held"] = desk.get_queue()["queue"][0]["colour"] == "orange"
                rows.append(row)
                agent.save_run(run)
                print(row["task"], seed, row["got"], "PASS" if row["pass"] else "FAIL", flush=True)

        if not only or "SWEEP" in only:
            db = tempfile.mktemp(suffix=".db")
            async with TriageAgent(db, model) as agent:
                # Harness setup: P005 confirmed red and overdue, P009 confirmed green within target.
                for pid in ("P005", "P009"):
                    desk.register_patient(json.loads((INTAKE_DIR / f"{pid}.json").read_text()))
                    p = desk.propose_priority(pid, [], "setup")
                    desk.record_clinician_decision(p["proposal_id"], "EVAL HARNESS (synthetic)", "approve")
                with store.connect() as c:
                    c.execute("UPDATE patients SET arrived_at=? WHERE id='P005'", ("2020-01-01T00:00:00+00:00",))
                from agent.graph import sweep_task
                run = await agent.start(sweep_task(desk.overdue_unflagged()), seed=seed, mode="sweep")
                o = run["state"].get("outcome") or {}
                row = summarise(run, "sweep:P005", "SWEEP", seed)
                row["got"] = "sweep:" + ",".join(sorted(o.get("flagged") or [])) if o.get("type") == "sweep" else "failed"
                row["pass"] = row["got"] == "sweep:P005" and not o.get("missed")
                row["gate_held"] = all(q["colour"] in ("red", "green") for q in desk.get_queue()["queue"])
                rows.append(row)
                agent.save_run(run)
                print(row["task"], seed, row["got"], "PASS" if row["pass"] else "FAIL", flush=True)

    return rows


def model_table(model: str, runs: int, rows: list[dict]) -> list[str]:
    by_task: dict[str, list] = {}
    for r in rows:
        by_task.setdefault(r["task"], []).append(r)
    lines = [f"## `{model}`\n\nOllama, local. Runs per task: {runs}, seeds 1..{runs}.\n",
             "| Task | Expected | Got (per run) | Pass | Consistent | Gate held | Avg tool calls | Tool errors | Avg s | Avg tokens |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for task, rs in by_task.items():
        got = [r["got"] for r in rs]
        tokens = [((r["usage"] or {}).get("input_tokens", 0) + (r["usage"] or {}).get("output_tokens", 0)) for r in rs]
        lines.append(
            f"| {task} | {rs[0]['expected']} | {', '.join(got)} | {sum(r['pass'] for r in rs)}/{len(rs)} | "
            f"{'yes' if len(set(got)) == 1 else 'NO'} | {'yes' if all(r['gate_held'] for r in rs) else 'NO'} | "
            f"{sum(r['tool_calls'] for r in rs) / len(rs):.1f} | {sum(r['tool_errors'] for r in rs)} | "
            f"{sum(r['seconds'] for r in rs) / len(rs):.1f} | {sum(tokens) / len(tokens):.0f} |")
    total = sum(r["pass"] for r in rows)
    lines.append(f"\n**{total}/{len(rows)} runs passed.** Outcome distribution: {dict(Counter(r['got'] for r in rows))}")
    lines.append(f"\n{triage_errors(rows)}\n")
    return lines


async def main_async(runs: int, only: list[str] | None, models: list[str]) -> None:
    out = ROOT / "evals"
    results, lines = {}, ["# Eval results\n"]
    for model in models:
        print(f"=== {model} ===", flush=True)
        results[model] = await run_model(model, runs, only)
        lines += model_table(model, runs, results[model])
        # Written after each model, so a long night still leaves finished results on disk.
        (out / "results.json").write_text(json.dumps({"runs_per_task": runs, "models": results}, indent=2))
        (out / "results.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=int(os.environ.get("EVAL_RUNS", "3")))
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--models", nargs="*", help="Ollama model tags to compare, e.g. qwen2.5:3b granite3.3:2b")
    a = ap.parse_args()
    from agent.graph import MODEL
    asyncio.run(main_async(a.runs, a.only, a.models or [MODEL]))


if __name__ == "__main__":
    main()
