"""Triage desk command line: what the nurse sees.

  python -m agent.cli web                  open the nurse's desk in the browser (default)
  python -m agent.cli desk                 same workflow in the terminal
  python -m agent.cli recheck P002 --rr 31 --hr 132 --sbp 86 --avpu C --reason "more breathless"
  python -m agent.cli reported P006 --reason "says the pain is much worse"
  python -m agent.cli queue                current queue, awaiting confirmation, overdue
  python -m agent.cli audit P002           full audit trail for one patient
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from triage_mcp import desk, sats, store  # noqa: E402

COL = {"red": "\033[41;97m", "orange": "\033[48;5;208;30m", "yellow": "\033[43;30m",
       "green": "\033[42;30m"}
RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"


def chip(colour: str | None) -> str:
    if not colour:
        return "  -   "
    return f"{COL.get(colour, '')} {colour.upper():^6} {RESET}"


def show_pause(pause: dict) -> None:
    o = pause["outcome"]
    print()
    print(f"{BOLD}Patient {o.get('patient_id')}{RESET}")
    for note in pause.get("verify_notes") or []:
        print(f"  \033[91m! {note}{RESET}")
    if o.get("proposed_colour"):
        print(f"  Proposed priority {chip(o['proposed_colour'])}  TEWS {o['tews']} "
              f"(TEWS alone: {o['tews_colour']})  target: see within {o['target_minutes']} min")
        print(f"  {DIM}Sources:{RESET}")
        for s in o.get("sources", []):
            if s["type"] == "tews_total":
                print(f"    - TEWS total {s['tews']} -> {s['colour']}  [{s['rule_id']}] from {s['entry']}")
            elif s["type"] == "vital":
                print(f"    - {s['param']} = {s['value']} (+{s['points']})  [{s['rule_id']}]")
            else:
                print(f"    - {s['code']} -> {s['colour']}: \"{s['evidence']}\"  [{s['rule_id']}] "
                      f"from {s['evidence_source']}")
    if o.get("type") == "flag":
        print(f"  Re-check flag: {o['kind']}  {DIM}{o.get('status', '')}{RESET}")
        if "new_tews" in o:
            print(f"  New TEWS {o['new_tews']} -> {o['new_tews_colour']}  (confirmed: {o.get('current_confirmed')})")
        rp = o.get("retriage_proposal")
        if rp:
            print(f"  Re-triage proposed {chip(rp['proposed_colour'])}")
    print(f"  {DIM}Agent plan:{RESET} {DIM}{pause.get('plan', '').strip()[:400]}{RESET}")


def ask_decision(pause: dict, clinician: str) -> dict:
    o = pause["outcome"]
    if not o.get("proposal_id"):
        input("  Flag only. Press Enter to acknowledge (you will act on it yourself) ")
        return {"clinician": clinician, "action": "acknowledge", "note": "acknowledged at desk"}
    while True:
        a = input("  [a]pprove  [o]verride  [r]eject > ").strip().lower()
        if a in {"a", "approve"}:
            return {"clinician": clinician, "action": "approve"}
        if a in {"r", "reject"}:
            return {"clinician": clinician, "action": "reject", "note": input("  reason: ")}
        if a in {"o", "override"}:
            c = input("  colour (red/orange/yellow/green): ").strip().lower()
            if c in sats.RANK:
                return {"clinician": clinician, "action": "override", "override_colour": c,
                        "note": input("  note (required): ")}


def get_clinician() -> str:
    name = os.environ.get("TRIAGE_NURSE") or input("Triage nurse name: ").strip()
    while len(name) < 2:
        name = input("A named clinician is required. Name: ").strip()
    return name


def print_queue() -> None:
    q = desk.get_queue()
    print(f"\n{BOLD}Queue (confirmed by a nurse){RESET}")
    for r in q["queue"]:
        late = " \033[91mOVERDUE\033[0m" if r["overdue"] else ""
        flags = f"  flags: {[f['kind'] for f in r['open_flags']]}" if r["open_flags"] else ""
        print(f"  {r['position']:>2}. {r['patient_id']}  {chip(r['colour'])}  waited {r['waited_min']:>5} min "
              f"/ target {r['target_min']}{late}  by {r['confirmed_by']}{flags}")
    if q["awaiting_nurse"]:
        print(f"{BOLD}Awaiting nurse confirmation{RESET}")
        for r in q["awaiting_nurse"]:
            print(f"      {r['patient_id']}  proposed {chip(r['proposed_colour'])}")
    if q["untriaged"]:
        print(f"{BOLD}Not yet triaged / needs the nurse{RESET}")
        for r in q["untriaged"]:
            print(f"      {r['patient_id']}  waited {r['waited_min']} min  "
                  f"{[f['kind'] + ': ' + f['reason'] for f in r['open_flags']]}")


async def run_one(agent, task: str, clinician: str, **kw) -> None:
    run = await agent.start(task, **kw)
    if run["pause"]:
        show_pause(run["pause"])
        decision = ask_decision(run["pause"], clinician)
        run = await agent.resume(run, decision)
        print(f"  -> {run['state'].get('decision')}")
    else:
        print(f"\n  \033[91mAgent could not finish: {run['state'].get('outcome')}. "
              f"Patient left for the nurse untriaged.{RESET}")
    u = run["state"].get("usage") or {}
    path = agent.save_run(run)
    print(f"  {DIM}{run['seconds']}s, {u.get('llm_calls')} model calls, "
          f"{u.get('input_tokens')}+{u.get('output_tokens')} tokens, run log {path.relative_to(ROOT)}{RESET}")


async def main_async(args) -> None:
    from agent.graph import INTAKE_DIR, TriageAgent, intake_task, recheck_task

    if desk.blind_mode() and args.cmd == "desk":
        print("Blind mode is on, and blind calls are made on the web desk. Run: python -m agent.cli web")
        return
    clinician = get_clinician()
    async with TriageAgent() as agent:
        if args.cmd == "desk":
            cards = sorted(INTAKE_DIR.glob("*.json"))
            if args.only:
                cards = [c for c in cards if c.stem in set(args.only)]
            for card in cards:
                if store.get_patient(card.stem):
                    continue
                print(f"\n{DIM}--- {card.name} ---{RESET}")
                await run_one(agent, intake_task(card), clinician, card_path=str(card))
        elif args.cmd == "recheck":
            nv = {k: v for k, v in {"rr": args.rr, "hr": args.hr, "sbp": args.sbp, "temp": args.temp,
                                    "avpu": args.avpu, "mobility": args.mobility}.items() if v is not None}
            await run_one(agent, recheck_task(args.patient_id, args.reason, nv), clinician,
                          patient_id=args.patient_id)
        elif args.cmd == "reported":
            await run_one(agent, recheck_task(args.patient_id, args.reason, None), clinician,
                          patient_id=args.patient_id)
    print_queue()


def main() -> None:
    p = argparse.ArgumentParser(prog="spark-triage")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("web")
    w.add_argument("--port", type=int, default=8000)
    w.add_argument("--no-browser", action="store_true")
    d = sub.add_parser("desk")
    d.add_argument("--only", nargs="*")
    r = sub.add_parser("recheck")
    r.add_argument("patient_id")
    r.add_argument("--reason", required=True)
    for k in ("rr", "hr", "sbp"):
        r.add_argument(f"--{k}", type=int)
    r.add_argument("--temp", type=float)
    r.add_argument("--avpu", choices=sorted(sats.AVPU_VALUES))
    r.add_argument("--mobility", choices=sorted(sats.MOBILITY_VALUES))
    rp = sub.add_parser("reported")
    rp.add_argument("patient_id")
    rp.add_argument("--reason", required=True)
    sub.add_parser("queue")
    a = sub.add_parser("audit")
    a.add_argument("patient_id")
    args = p.parse_args()

    if args.cmd == "web":
        from agent.webdesk import serve
        serve(port=args.port, open_browser=not args.no_browser)
    elif args.cmd == "queue":
        print_queue()
    elif args.cmd == "audit":
        for row in store.audit_for(args.patient_id):
            ok = "ok " if row["ok"] else "ERR"
            print(f"{row['ts']}  {ok}  {row['actor']:<22} {row['tool']:<26} {row['input_json'][:90]}")
            print(f"{'':>30}-> {row['output_json'][:160]}")
    else:
        asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
