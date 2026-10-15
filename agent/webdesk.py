"""Local web desk for the triage nurse.

  python -m agent.cli web          -> http://127.0.0.1:8000

Runs entirely on the desk machine: no internet needed once the model is
pulled. One agent job runs at a time (the model is CPU-bound anyway); the page
polls /api/state and shows every tool call as it happens.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

from triage_mcp import desk, sats, store

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
INTAKE_DIR = Path(os.environ.get("TRIAGE_INTAKE_DIR", ROOT / "data" / "intake")).resolve()
SWEEP_MIN = float(os.environ.get("TRIAGE_SWEEP_MIN", "5"))  # 0 turns the automatic sweep off

# Tool results that would reveal the agent's colour before the nurse's blind call.
BLIND_HIDDEN_TOOLS = {"compute_tews", "propose_priority", "raise_recheck_flag"}


class DeskRuntime:
    """Owns the agent, the job queue and the runs paused at the nurse gate."""

    def __init__(self, agent, tasks_module):
        self.agent = agent
        self.t = tasks_module  # provides intake_task / recheck_task
        self.jobs: asyncio.Queue = asyncio.Queue()
        self.waiting: list[dict] = []
        self.current: dict | None = None
        self.paused: dict[tuple, dict] = {}
        self.worker: asyncio.Task | None = None
        self.sweeper: asyncio.Task | None = None
        self.last_sweep: str | None = None

    def busy(self) -> bool:
        return bool(self.current or self.waiting)

    def submit_sweep(self) -> list[str]:
        """Queue an overdue sweep if anyone is overdue without a flag. Returns their ids."""
        if any(j.get("mode") == "sweep" for j in self.waiting) or (self.current or {}).get("mode") == "sweep":
            return []
        overdue = desk.overdue_unflagged()
        if not overdue:
            return []
        self.submit({"patient_id": "queue", "label": "Overdue sweep", "mode": "sweep",
                     "task": self.t.sweep_task(overdue)})
        return [o["patient_id"] for o in overdue]

    async def sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(SWEEP_MIN * 60)
            if not self.busy():
                self.submit_sweep()

    def submit(self, job: dict) -> None:
        self.waiting.append(job)
        self.jobs.put_nowait(job)

    async def work(self) -> None:
        while True:
            job = await self.jobs.get()
            self.waiting = [j for j in self.waiting if j is not job]
            self.current = job
            self.agent.emit({"kind": "start", "patient": job["patient_id"], "text": job["label"]})
            try:
                run = await self.agent.start(job["task"], card_path=job.get("card_path"),
                                             patient_id=None if job.get("mode") == "sweep" else job.get("patient_id"),
                                             mode=job.get("mode", "intake"))
                pause = run.get("pause")
                outcome = (run.get("state") or {}).get("outcome") or {}
                if job.get("mode") == "sweep":
                    self.last_sweep = store.now()
                    self.agent.emit({"kind": "gate", "patient": "queue",
                                     "text": f"sweep finished after {run['seconds']}s: flagged "
                                             f"{', '.join(outcome.get('flagged') or []) or 'nobody new'}"})
                elif pause:
                    o = pause["outcome"]
                    key = ("p", o["proposal_id"]) if o.get("proposal_id") else ("f", o.get("flag_id"))
                    self.paused[key] = run
                    self.agent.emit({"kind": "gate", "patient": o.get("patient_id"),
                                     "text": f"paused for the nurse after {run['seconds']}s"})
                else:
                    self.agent.emit({"kind": "error", "ok": False, "patient": job["patient_id"],
                                     "text": "agent could not finish; patient left for the nurse"})
                self.agent.save_run(run)
            except Exception as e:  # keep the desk alive, show the failure
                self.agent.emit({"kind": "error", "ok": False, "patient": job["patient_id"],
                                 "text": f"run failed: {e!r}"[:300]})
            finally:
                self.current = None


def _cards() -> list[dict]:
    out = []
    for f in sorted(INTAKE_DIR.glob("*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except ValueError:
            continue
    return out


def _card(pid: str) -> dict:
    p = store.get_patient(pid)
    if p:
        return p["card"]
    f = INTAKE_DIR / f"{pid}.json"
    return json.loads(f.read_text()) if f.exists() else {}


def _brief(card: dict) -> dict:
    return {k: card.get(k) for k in ("patient_id", "age_years", "sex", "complaint", "nurse_notes",
                                     "arrived_at", "vitals")}


def state_payload(rt: DeskRuntime, after: int) -> dict:
    q = desk.get_queue()
    busy = {j["patient_id"] for j in rt.waiting} | ({rt.current["patient_id"]} if rt.current else set())
    arrivals = []
    for c in _cards():
        pid = c.get("patient_id")
        if pid and not store.get_patient(pid):
            arrivals.append({**_brief(c), "waited_min": desk._minutes_since(c.get("arrived_at", store.now())),
                             "busy": pid in busy})
    blind = desk.blind_mode()
    decisions = []
    for p in store.pending_all():
        card = _brief(_card(p["patient_id"]))
        card["vitals"] = (store.latest_vitals(p["patient_id"]) or {}).get("vitals", card.get("vitals"))
        call = store.blind_call(p["id"])
        base = {"type": "proposal", "proposal_id": p["id"], "patient_id": p["patient_id"], "kind": p["kind"],
                "created_at": p["created_at"], "card": card,
                "confirmed": (store.current_confirmed(p["patient_id"]) or {}).get("final_colour"),
                "blind_call": call and {"colour": call["colour"], "clinician": call["clinician"]}}
        if blind and not call:
            decisions.append({**base, "hidden": True})  # nothing that reveals the agent's answer
        else:
            decisions.append({**base, "hidden": False, "colour": p["colour"], "tews": p["tews"],
                              "target_min": sats.TARGET_MINUTES[p["colour"]], "sources": p["sources"],
                              "rationale": p["rationale"]})
    for f in store.open_flags():
        if f["proposal_id"]:
            continue  # shown with its proposal
        decisions.append({"type": "flag", "flag_id": f["id"], "patient_id": f["patient_id"],
                          "flag_kind": f["kind"], "reason": f["reason"], "created_at": f["created_at"],
                          "card": _brief(_card(f["patient_id"]))})
    # Most urgent first, flags after proposals. In blind mode, arrival order only:
    # sorting by colour would leak the agent's answer.
    if blind:
        decisions.sort(key=lambda d: (d["type"] != "proposal", d["created_at"]))
    else:
        decisions.sort(key=lambda d: (-sats.RANK.get(d.get("colour"), -1), d["created_at"]))
    events = [e for e in rt.agent.events if e["n"] > after][-200:]
    if blind:
        events = [{**e, "text": "result hidden in blind mode; it appears on the card after your call"}
                  if e.get("kind") == "tool" and e.get("tool") in BLIND_HIDDEN_TOOLS and e.get("ok") else e
                  for e in events]
    queue = [{**r, "card": _brief(_card(r["patient_id"])), "busy": r["patient_id"] in busy}
             for r in q["queue"]]
    counts = {c: sum(1 for r in q["queue"] if r["colour"] == c) for c in sats.COLOURS}
    try:
        from agent.graph import MODEL
    except Exception:
        MODEL = os.environ.get("TRIAGE_MODEL", "unknown")
    return {
        "model": MODEL, "arrivals": arrivals, "decisions": decisions, "queue": queue,
        "untriaged": [r for r in q["untriaged"] if not any(d["patient_id"] == r["patient_id"] for d in decisions)],
        "counts": counts,
        "running": rt.current and {"patient_id": rt.current["patient_id"], "label": rt.current["label"]},
        "waiting": [{"patient_id": j["patient_id"], "label": j["label"]} for j in rt.waiting],
        "events": events,
        "blind_mode": blind, "agreement": desk.agreement_stats(), "metrics": desk.desk_metrics(),
        "overdue_unflagged": [o["patient_id"] for o in desk.overdue_unflagged()],
        "last_sweep": rt.last_sweep, "sweep_every_min": SWEEP_MIN,
    }


def _bad(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=code)


def create_app(make_agent=None) -> Starlette:
    """make_agent: async context manager factory returning (agent, tasks_module). Tests inject a stub."""

    async def default_agent():
        from agent import graph
        return graph.TriageAgent(), graph

    @contextlib.asynccontextmanager
    async def lifespan(app):
        agent, tasks = await (make_agent or default_agent)()
        async with agent:
            rt = DeskRuntime(agent, tasks)
            rt.worker = asyncio.create_task(rt.work())
            if SWEEP_MIN > 0:
                rt.sweeper = asyncio.create_task(rt.sweep_loop())
            app.state.rt = rt
            yield
            rt.worker.cancel()
            if rt.sweeper:
                rt.sweeper.cancel()

    async def index(request: Request):
        return FileResponse(STATIC / "desk.html")

    async def state(request: Request):
        after = int(request.query_params.get("after", "0"))
        return JSONResponse(state_payload(request.app.state.rt, after))

    async def triage(request: Request):
        rt: DeskRuntime = request.app.state.rt
        body = await request.json()
        ids = body.get("patient_ids") or []
        if body.get("all"):
            ids = [c["patient_id"] for c in _cards() if not store.get_patient(c["patient_id"])]
        busy = {j["patient_id"] for j in rt.waiting} | ({rt.current["patient_id"]} if rt.current else set())
        added = []
        for pid in ids:
            f = INTAKE_DIR / f"{pid}.json"
            if not f.exists() or store.get_patient(pid) or pid in busy:
                continue
            rt.submit({"patient_id": pid, "label": f"Triage {pid}", "task": rt.t.intake_task(f),
                       "card_path": str(f)})
            added.append(pid)
        return JSONResponse({"queued": added})

    async def recheck(request: Request):
        rt: DeskRuntime = request.app.state.rt
        body = await request.json()
        pid, reason = body.get("patient_id"), (body.get("reason") or "").strip()
        if not store.get_patient(pid or ""):
            return _bad("Unknown patient. Triage the card first.")
        if len(reason) < 5:
            return _bad("Say what changed, in a few words.")
        vitals = {}
        for k, cast in (("rr", float), ("hr", float), ("sbp", float), ("temp", float)):
            v = body.get("vitals", {}).get(k)
            if v not in (None, ""):
                try:
                    vitals[k] = cast(v)
                except ValueError:
                    return _bad(f"{k} must be a number")
        for k, allowed in (("avpu", sats.AVPU_VALUES), ("mobility", sats.MOBILITY_VALUES)):
            v = body.get("vitals", {}).get(k)
            if v:
                if v not in allowed:
                    return _bad(f"{k} must be one of {sorted(allowed)}")
                vitals[k] = v
        task = rt.t.recheck_task(pid, reason, vitals or None)
        rt.submit({"patient_id": pid, "label": f"Re-check {pid}", "task": task})
        return JSONResponse({"queued": [pid]})

    async def decide(request: Request):
        rt: DeskRuntime = request.app.state.rt
        b = await request.json()
        clin = (b.get("clinician") or "").strip()
        action = b.get("action")
        if len(clin) < 2:
            return _bad("Enter the name of the nurse on the desk first.")
        if action == "override" and (b.get("override_colour") not in sats.RANK or len((b.get("note") or "").strip()) < 3):
            return _bad("An override needs a colour and a short note.")
        if action == "reject" and len((b.get("note") or "").strip()) < 3:
            return _bad("Say why you are rejecting it.")
        if (b.get("proposal_id") is not None and action != "reject" and desk.blind_mode()
                and not store.blind_call(int(b["proposal_id"]))):
            return _bad("Blind mode is on. Choose your own priority first.")
        decision = {"clinician": clin, "action": action, "override_colour": b.get("override_colour"),
                    "note": (b.get("note") or "").strip()}
        if b.get("proposal_id") is not None:
            key = ("p", int(b["proposal_id"]))
        elif b.get("flag_id") is not None:
            key = ("f", int(b["flag_id"]))
        else:
            return _bad("Nothing to decide on.")
        run = rt.paused.pop(key, None)
        if run is not None:  # resume the paused agent: its gate node records the decision
            await rt.agent.resume(run, decision)
            rt.agent.save_run(run)
            result = run["state"].get("decision") or {}
        else:  # proposal from an earlier session: record directly, same audit trail
            try:
                if key[0] == "p":
                    result = desk.record_clinician_decision(key[1], clin, action, decision["override_colour"],
                                                            decision["note"])
                    tool = "record_clinician_decision"
                else:
                    result = desk.close_flag(key[1], clin, decision["note"] or "handled at desk")
                    tool = "close_flag"
                store.log(f"clinician:{clin}", tool, decision, result, True)
            except desk.DeskError as e:
                result = {"error": str(e)}
        if "error" in result:
            return _bad(result["error"])
        rt.agent.emit({"kind": "decision", "patient": result.get("patient_id"),
                       "text": f"{clin}: {action}" + (f" to {decision['override_colour']}" if action == "override" else "")})
        return JSONResponse(result)

    async def audit(request: Request):
        pid = request.path_params["pid"]
        if desk.blind_mode() and any(p["patient_id"] == pid and not store.blind_call(p["id"])
                                     for p in store.pending_all()):
            return _bad("Make your blind call first; the audit trail would show the agent's answer.")
        return JSONResponse(desk.audit_timeline(pid))

    async def audit_text(request: Request):
        pid = request.path_params["pid"]
        if desk.blind_mode() and any(p["patient_id"] == pid and not store.blind_call(p["id"])
                                     for p in store.pending_all()):
            return PlainTextResponse("Make your blind call first.", status_code=400)
        lines = [f"Spark Triage audit trail for {pid}", f"Printed {store.now()}", ""]
        for r in desk.audit_timeline(pid):
            lines.append(f"{r['ts']}  {'    ' if r['ok'] else 'FAIL'}  {r['summary']}")
        return PlainTextResponse("\n".join(lines) + "\n")

    async def blind_call(request: Request):
        b = await request.json()
        clin = (b.get("clinician") or "").strip()
        try:
            res = desk.record_blind_call(int(b.get("proposal_id")), clin, b.get("colour"))
        except (desk.DeskError, TypeError, ValueError) as e:
            return _bad(str(e))
        store.log(f"clinician:{clin}", "record_blind_call", {"patient_id": res["patient_id"], **b}, res, True)
        request.app.state.rt.agent.emit({"kind": "decision", "patient": res["patient_id"],
                                         "text": f"{clin}: blind call {res['nurse_colour']}, agent {res['agent_colour']}"})
        return JSONResponse(res)

    async def set_priority(request: Request):
        b = await request.json()
        clin = (b.get("clinician") or "").strip()
        try:
            res = desk.nurse_set_priority(b.get("patient_id", ""), clin, b.get("colour"), (b.get("note") or "").strip())
        except desk.DeskError as e:
            return _bad(str(e)[0].upper() + str(e)[1:] + ".")
        store.log(f"clinician:{clin}", "nurse_set_priority", {"patient_id": res["patient_id"], **b}, res, True)
        request.app.state.rt.agent.emit({"kind": "decision", "patient": res["patient_id"],
                                         "text": f"{clin}: set priority {res['final_colour']}"})
        return JSONResponse(res)

    async def seen(request: Request):
        b = await request.json()
        clin = (b.get("clinician") or "").strip()
        try:
            res = desk.mark_seen(b.get("patient_id", ""), clin)
        except desk.DeskError as e:
            return _bad(str(e)[0].upper() + str(e)[1:] + ".")
        store.log(f"clinician:{clin}", "mark_seen", {"patient_id": res["patient_id"]}, res, True)
        request.app.state.rt.agent.emit({"kind": "decision", "patient": res["patient_id"],
                                         "text": f"{clin}: sent to a bay after {res['waited_min']} min"})
        return JSONResponse(res)

    async def settings(request: Request):
        b = await request.json()
        try:
            res = desk.set_blind_mode(bool(b.get("blind_mode")), (b.get("clinician") or "").strip())
        except desk.DeskError as e:
            return _bad(str(e))
        store.log(f"clinician:{res['set_by']}", "set_blind_mode", b, res, True)
        return JSONResponse(res)

    async def sweep(request: Request):
        rt: DeskRuntime = request.app.state.rt
        ids = rt.submit_sweep()
        return JSONResponse({"queued": ids, "message": "Sweep queued." if ids else
                             "Nobody is waiting past target without a flag."})

    async def reset(request: Request):
        rt: DeskRuntime = request.app.state.rt
        if rt.current or rt.waiting:
            return _bad("Wait for the agent to finish before resetting.")
        import sys
        sys.path.insert(0, str(ROOT / "data"))
        import make_synthetic
        make_synthetic.main()
        with store.connect() as c:
            for t in ("patients", "vitals", "proposals", "flags", "blind_calls"):
                c.execute(f"DELETE FROM {t}")
        store.log("desk", "reset_demo", {}, {"ok": True}, True)
        rt.paused.clear()
        return JSONResponse({"ok": True})

    return Starlette(lifespan=lifespan, routes=[
        Route("/", index),
        Route("/api/state", state),
        Route("/api/triage", triage, methods=["POST"]),
        Route("/api/recheck", recheck, methods=["POST"]),
        Route("/api/decide", decide, methods=["POST"]),
        Route("/api/audit/{pid}", audit),
        Route("/audit/{pid}.txt", audit_text),
        Route("/api/blind", blind_call, methods=["POST"]),
        Route("/api/settings", settings, methods=["POST"]),
        Route("/api/set_priority", set_priority, methods=["POST"]),
        Route("/api/seen", seen, methods=["POST"]),
        Route("/api/sweep", sweep, methods=["POST"]),
        Route("/api/reset", reset, methods=["POST"]),
    ])


def serve(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> None:
    import threading
    import webbrowser

    import uvicorn

    if open_browser:
        threading.Timer(2.5, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    print(f"Spark Triage desk on http://{host}:{port}  (Ctrl+C to stop)")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
