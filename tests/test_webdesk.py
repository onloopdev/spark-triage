"""Web desk API tests with a stand-in agent.

The stand-in calls the same desk functions the real agent reaches through MCP,
with no model. It checks the desk's wiring (job queue, nurse gate, overrides,
re-checks, audit), not the model's judgement; that is what evals/ measures.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))

try:
    from starlette.testclient import TestClient
except Exception:  # httpx missing
    TestClient = None

import make_synthetic  # noqa: E402
from triage_mcp import desk, store  # noqa: E402


class StandInTasks:
    @staticmethod
    def intake_task(path):
        return {"intake": str(path)}

    @staticmethod
    def recheck_task(pid, reason, vitals):
        return {"recheck": pid, "reason": reason, "vitals": vitals}

    @staticmethod
    def sweep_task(overdue):
        return {"sweep": [o["patient_id"] for o in overdue]}


class StandInAgent:
    """Deterministic substitute for agent.graph.TriageAgent. Test use only."""

    def __init__(self):
        self.events = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    def emit(self, e):
        self.events.append({"n": len(self.events) + 1, "ts": store.now(), **e})

    async def start(self, task, card_path=None, patient_id=None, seed=None, mode="intake"):
        await asyncio.sleep(0)
        if "sweep" in task:
            flagged = []
            for r in desk.get_queue()["queue"]:
                if r["overdue"]:
                    out = desk.raise_recheck_flag(r["patient_id"], "nurse_review",
                                                  f"Waited {r['waited_min']} min, target {r['target_min']} min")
                    if not out.get("duplicate"):
                        flagged.append(r["patient_id"])
            return {"thread": "x", "seconds": 0.0, "pause": None,
                    "state": {"outcome": {"type": "sweep", "flagged": flagged, "missed": []}}}
        if "intake" in task:
            card = json.loads(Path(task["intake"]).read_text())
            r = desk.register_patient(card)
            pid = r["patient_id"]
            if r["scale"] == "none":
                out = {"type": "flag", **desk.raise_recheck_flag(pid, "nurse_review", "child, adult scale does not apply")}
            elif r["vital_problems"]:
                out = {"type": "flag", **desk.raise_recheck_flag(pid, "missing_vitals", "measure missing vitals")}
            else:
                out = {"type": "proposal", **desk.propose_priority(pid, [], "stand-in")}
        else:
            pid = task["recheck"]
            out = {"type": "flag", **desk.raise_recheck_flag(pid, "deterioration" if task["vitals"] else "nurse_review",
                                                               task["reason"], task["vitals"])}
            if out.get("retriage_proposal"):
                out["proposal_id"] = out["retriage_proposal"]["proposal_id"]
        return {"thread": "x", "seconds": 0.0, "pause": {"outcome": out}, "state": {"patient_id": pid}}

    async def resume(self, run, decision):
        o = run["pause"]["outcome"]
        if o.get("proposal_id"):
            res = desk.record_clinician_decision(o["proposal_id"], decision["clinician"], decision["action"],
                                                 decision.get("override_colour"), decision.get("note", ""))
        else:
            res = desk.close_flag(o["flag_id"], decision["clinician"], decision.get("note", ""))
        store.log(f"clinician:{decision['clinician']}", "record_clinician_decision",  # as agent.graph's gate does
                  {"patient_id": o.get("patient_id"), **decision}, res, True)
        run["state"]["decision"] = res
        return run

    def save_run(self, run):
        return None


@unittest.skipIf(TestClient is None, "httpx not installed")
class WebDesk(unittest.TestCase):
    def setUp(self):
        os.environ["TRIAGE_DB"] = tempfile.mktemp(suffix=".db")
        make_synthetic.main()
        from agent import webdesk

        async def factory():
            return StandInAgent(), StandInTasks

        self.client = TestClient(webdesk.create_app(factory))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def wait_idle(self):
        for _ in range(100):
            s = self.client.get("/api/state").json()
            if not s["running"] and not s["waiting"]:
                return s
            time.sleep(0.02)
        self.fail("agent queue did not drain")

    def test_page_and_full_desk_flow(self):
        self.assertIn("Spark Triage", self.client.get("/").text)
        s = self.client.get("/api/state").json()
        self.assertEqual(len(s["arrivals"]), len(make_synthetic.CASES))

        self.client.post("/api/triage", json={"all": True})
        s = self.wait_idle()
        self.assertEqual(s["arrivals"], [])
        self.assertEqual(s["queue"], [], "nobody is queued before the nurse decides")
        props = [d for d in s["decisions"] if d["type"] == "proposal"]
        flags = [d for d in s["decisions"] if d["type"] == "flag"]
        self.assertEqual({f["patient_id"] for f in flags}, {"P010", "P011"})

        r = self.client.post("/api/decide", json={"proposal_id": props[0]["proposal_id"], "action": "approve"})
        self.assertEqual(r.status_code, 400, "a nurse name is required")
        r = self.client.post("/api/decide", json={"proposal_id": props[0]["proposal_id"], "action": "override",
                                                  "override_colour": "red", "clinician": "Nurse Test"})
        self.assertEqual(r.status_code, 400, "an override needs a note")

        for p in props:
            r = self.client.post("/api/decide", json={"proposal_id": p["proposal_id"], "action": "approve",
                                                      "clinician": "Nurse Test"})
            self.assertEqual(r.status_code, 200, r.text)
        for f in flags:
            r = self.client.post("/api/decide", json={"flag_id": f["flag_id"], "action": "acknowledge",
                                                      "clinician": "Nurse Test", "note": "triaged by hand"})
            self.assertEqual(r.status_code, 200, r.text)
        s = self.client.get("/api/state").json()
        colours = [q["colour"] for q in s["queue"]]
        self.assertEqual(colours, sorted(colours, key=lambda c: -["green", "yellow", "orange", "red"].index(c)))
        self.assertEqual(s["decisions"], [])

        rc = make_synthetic.RECHECKS["P002"]
        self.client.post("/api/recheck", json={"patient_id": "P002", "reason": rc["reason"], "vitals": rc["new_vitals"]})
        s = self.wait_idle()
        retri = [d for d in s["decisions"] if d["type"] == "proposal" and d["kind"] == "retriage"]
        self.assertEqual(retri[0]["colour"], "red")
        p2 = next(q for q in s["queue"] if q["patient_id"] == "P002")
        self.assertNotEqual(p2["colour"], "red", "queue keeps the confirmed colour until the nurse decides")

        audit = self.client.get("/api/audit/P002").json()
        self.assertTrue(any(a["actor"] == "clinician:Nurse Test" for a in audit))

    def test_blind_mode_hides_the_answer_until_the_nurse_calls(self):
        self.assertEqual(self.client.post("/api/settings", json={"blind_mode": True}).status_code, 400)
        self.client.post("/api/settings", json={"blind_mode": True, "clinician": "Nurse Test"})
        self.client.post("/api/triage", json={"patient_ids": ["P005", "P001"]})
        s = self.wait_idle()
        props = [d for d in s["decisions"] if d["type"] == "proposal"]
        self.assertTrue(all(d["hidden"] for d in props))
        for d in props:  # nothing that reveals the agent's colour
            self.assertFalse({"colour", "sources", "tews", "target_min"} & set(d), d.keys())
        self.assertEqual([d["patient_id"] for d in props], ["P005", "P001"], "arrival order, not urgency")
        p5 = next(d for d in props if d["patient_id"] == "P005")
        self.assertEqual(self.client.get("/api/audit/P005").status_code, 400)
        r = self.client.post("/api/decide", json={"proposal_id": p5["proposal_id"], "action": "approve",
                                                  "clinician": "Nurse Test"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/blind", json={"proposal_id": p5["proposal_id"], "colour": "orange",
                                                 "clinician": "Nurse Test"}).json()
        self.assertEqual((r["agent_colour"], r["agent_was"]), ("red", "more urgent"))
        s = self.client.get("/api/state").json()
        d5 = next(d for d in s["decisions"] if d.get("patient_id") == "P005")
        self.assertFalse(d5["hidden"])
        self.assertEqual(d5["blind_call"]["colour"], "orange")
        self.assertEqual(s["agreement"]["blind_calls"], 1)
        r = self.client.post("/api/decide", json={"proposal_id": p5["proposal_id"], "action": "approve",
                                                  "clinician": "Nurse Test"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("audit trail for P005", self.client.get("/audit/P005.txt").text)

    def test_sweep_endpoint_flags_overdue_once(self):
        self.client.post("/api/triage", json={"patient_ids": ["P005"]})
        s = self.wait_idle()
        pid = s["decisions"][0]["proposal_id"]
        self.client.post("/api/decide", json={"proposal_id": pid, "action": "approve", "clinician": "Nurse Test"})
        with store.connect() as c:
            c.execute("UPDATE patients SET arrived_at=? WHERE id='P005'", ("2020-01-01T00:00:00+00:00",))
        self.assertEqual(self.client.post("/api/sweep", json={}).json()["queued"], ["P005"])
        s = self.wait_idle()
        self.assertEqual(s["overdue_unflagged"], [])
        self.assertEqual([d["flag_kind"] for d in s["decisions"]], ["nurse_review"])
        self.assertEqual(self.client.post("/api/sweep", json={}).json()["queued"], [])

    def test_send_to_bay_from_the_desk(self):
        self.client.post("/api/triage", json={"patient_ids": ["P001"]})
        s = self.wait_idle()
        self.client.post("/api/decide", json={"proposal_id": s["decisions"][0]["proposal_id"], "action": "approve",
                                              "clinician": "Nurse Test"})
        self.assertEqual(self.client.post("/api/seen", json={"patient_id": "P001"}).status_code, 400)
        r = self.client.post("/api/seen", json={"patient_id": "P001", "clinician": "Nurse Test"})
        self.assertEqual(r.status_code, 200, r.text)
        s = self.client.get("/api/state").json()
        self.assertEqual(s["queue"], [])
        self.assertEqual((s["metrics"]["seen"], s["metrics"]["seen_within_target"]), (1, 1))
        self.assertIn("sent the patient to a bay", self.client.get("/audit/P001.txt").text)

    def test_recheck_validation(self):
        self.assertEqual(self.client.post("/api/recheck", json={"patient_id": "P001", "reason": "worse"}).status_code, 400)
        self.client.post("/api/triage", json={"patient_ids": ["P001"]})
        self.wait_idle()
        r = self.client.post("/api/recheck", json={"patient_id": "P001", "reason": "feels worse", "vitals": {"hr": "fast"}})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
