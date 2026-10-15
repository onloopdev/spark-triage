"""Deterministic tests: scoring rules, sourcing checks, the clinician gate, the
audit log and the MCP server over a real stdio connection. No model needed.

Run:  python -m pytest -q      (or: python -m unittest discover -s tests)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))

import make_synthetic  # noqa: E402
from triage_mcp import desk, sats, store  # noqa: E402


def is_error(result) -> bool:
    """isError in MCP SDK v1, is_error in v2."""
    return bool(getattr(result, "is_error", getattr(result, "isError", False)))


def fresh_db() -> str:
    path = tempfile.mktemp(suffix=".db")
    os.environ["TRIAGE_DB"] = path
    return path


def card(pid: str) -> dict:
    for c, *_ in make_synthetic.CASES:
        if c["patient_id"] == pid:
            c = dict(c)
            c.pop("mins_ago", None)
            c["arrived_at"] = store.now()
            return c
    raise KeyError(pid)


class TewsRules(unittest.TestCase):
    def test_boundaries(self):
        base = dict(rr=12, hr=70, sbp=120, temp=37, avpu="A", mobility="walking", trauma=False)
        self.assertEqual(sats.tews(base)["tews"], 0)
        cases = [("rr", 8, 2), ("rr", 9, 0), ("rr", 14, 0), ("rr", 15, 1), ("rr", 20, 1),
                 ("rr", 21, 2), ("rr", 29, 2), ("rr", 30, 3),
                 ("hr", 40, 2), ("hr", 41, 1), ("hr", 50, 1), ("hr", 51, 0), ("hr", 100, 0),
                 ("hr", 101, 1), ("hr", 110, 1), ("hr", 111, 2), ("hr", 129, 2), ("hr", 130, 3),
                 ("sbp", 70, 3), ("sbp", 71, 2), ("sbp", 80, 2), ("sbp", 81, 1), ("sbp", 100, 1),
                 ("sbp", 101, 0), ("sbp", 199, 0), ("sbp", 200, 2),
                 ("temp", 34.9, 2), ("temp", 35, 0), ("temp", 38.4, 0), ("temp", 38.5, 2),
                 ("avpu", "V", 1), ("avpu", "C", 1), ("avpu", "P", 2), ("avpu", "U", 3),
                 ("mobility", "with_help", 1), ("mobility", "immobile", 2), ("trauma", True, 1)]
        for param, value, pts in cases:
            with self.subTest(param=param, value=value):
                self.assertEqual(sats.tews({**base, param: value})["tews"], pts)

    def test_colour_bands(self):
        for score, colour in [(0, "green"), (2, "green"), (3, "yellow"), (4, "yellow"),
                              (5, "orange"), (6, "orange"), (7, "red"), (12, "red")]:
            self.assertEqual(sats.tews_colour(score), colour)

    def test_synthetic_cards_match_answer_key(self):
        for c, expected, tews, _ in make_synthetic.CASES:
            if tews is None:
                continue
            with self.subTest(pid=c["patient_id"]):
                self.assertEqual(sats.tews(c["vitals"])["tews"], tews)

    def test_every_rule_id_is_documented(self):
        ref = (ROOT / "data" / "sats_reference.md").read_text()
        base = dict(rr=12, hr=70, sbp=120, temp=37, avpu="A", mobility="walking", trauma=False)
        ids = {d["rule_id"] for d in sats.DISCRIMINATORS.values()} | {"SATS-TEWS-BANDS"}
        for vit in [base, dict(base, rr=5, hr=30, sbp=60, temp=34, avpu="U", mobility="immobile", trauma=True),
                    dict(base, rr=17, hr=45, sbp=75, temp=39, avpu="P", mobility="with_help"),
                    dict(base, rr=25, hr=105, sbp=90, avpu="V"), dict(base, rr=35, hr=120, sbp=250, avpu="C"),
                    dict(base, hr=140)]:
            ids |= {c["rule_id"] for c in sats.tews(vit)["components"]}
        for rid in ids:
            self.assertIn(f"`{rid}`", ref, rid)

    def test_missing_vital_refused(self):
        with self.assertRaises(ValueError):
            sats.tews(dict(rr=12, hr=70, sbp=None, temp=37, avpu="A", mobility="walking", trauma=False))


class DeskBehaviour(unittest.TestCase):
    def setUp(self):
        fresh_db()

    def test_discriminator_needs_exact_quote(self):
        desk.register_patient(card("P002"))
        with self.assertRaises(desk.DeskError):
            desk.propose_priority("P002", [{"code": "CHEST_PAIN", "evidence_quote": "crushing chest pain"}], "x")
        self.assertEqual(desk.get_queue()["awaiting_nurse"], [], "rejected proposal must store nothing")
        out = desk.propose_priority("P002", [{"code": "CHEST_PAIN", "evidence_quote": "Central chest pain"}], "x")
        self.assertEqual(out["proposed_colour"], "orange")
        self.assertTrue(any(s.get("rule_id") == "SATS-DISC-CHEST_PAIN" for s in out["sources"]))

    def test_model_cannot_choose_measured_or_unknown_codes(self):
        desk.register_patient(card("P013"))
        for bad in [{"code": "HYPOGLYCAEMIA", "evidence_quote": "knee"}, {"code": "RED", "evidence_quote": "RED"}]:
            with self.assertRaises(desk.DeskError):
                desk.propose_priority("P013", [bad], "x")

    def test_injection_card_stays_green_without_discriminators(self):
        desk.register_patient(card("P013"))
        out = desk.propose_priority("P013", [], "no discriminators")
        self.assertEqual(out["proposed_colour"], "green")

    def test_measured_hypoglycaemia_is_red(self):
        desk.register_patient(card("P007"))
        out = desk.propose_priority("P007", [], "glucose")
        self.assertEqual(out["proposed_colour"], "red")
        self.assertTrue(any(s.get("code") == "HYPOGLYCAEMIA" for s in out["sources"]))

    def test_missing_vitals_and_child_are_not_scored(self):
        r = desk.register_patient(card("P010"))
        self.assertIn("sbp", r["missing_vitals"])
        with self.assertRaises(desk.DeskError):
            desk.propose_priority("P010", [], "x")
        r = desk.register_patient(card("P011"))
        self.assertEqual(r["scale"], "none")
        with self.assertRaises(desk.DeskError):
            desk.compute_tews("P011")

    def test_every_source_points_at_a_rule_and_an_entry(self):
        desk.register_patient(card("P004"))
        out = desk.propose_priority("P004", [{"code": "FRACTURE_COMPOUND", "evidence_quote": "bone showing"}], "x")
        for s in out["sources"]:
            self.assertTrue(s["rule_id"].startswith("SATS-"))
            self.assertTrue(s.get("entry") or s.get("evidence_source"))

    def test_gate_requires_named_clinician_and_controls_queue(self):
        desk.register_patient(card("P001"))
        desk.register_patient(card("P005"))
        a = desk.propose_priority("P001", [], "x")
        b = desk.propose_priority("P005", [], "x")
        q = desk.get_queue()
        self.assertEqual(q["queue"], [], "nothing enters the queue before a nurse confirms")
        self.assertEqual(len(q["awaiting_nurse"]), 2)
        with self.assertRaises(desk.DeskError):
            desk.record_clinician_decision(a["proposal_id"], "", "approve")
        desk.record_clinician_decision(a["proposal_id"], "Nurse Test", "approve")
        desk.record_clinician_decision(b["proposal_id"], "Nurse Test", "approve")
        q = desk.get_queue()["queue"]
        self.assertEqual([r["patient_id"] for r in q], ["P005", "P001"])
        self.assertEqual(q[0]["confirmed_by"], "Nurse Test")
        with self.assertRaises(desk.DeskError):
            desk.record_clinician_decision(a["proposal_id"], "Nurse Test", "approve")

    def test_override_needs_note(self):
        desk.register_patient(card("P009"))
        p = desk.propose_priority("P009", [], "x")
        with self.assertRaises(desk.DeskError):
            desk.record_clinician_decision(p["proposal_id"], "Nurse Test", "override", "yellow")
        d = desk.record_clinician_decision(p["proposal_id"], "Nurse Test", "override", "yellow", "looks unwell")
        self.assertEqual(d["final_colour"], "yellow")

    def test_deterioration_creates_pending_retriage_only(self):
        desk.register_patient(card("P002"))
        p = desk.propose_priority("P002", [{"code": "CHEST_PAIN", "evidence_quote": "chest pain"}], "x")
        desk.record_clinician_decision(p["proposal_id"], "Nurse Test", "approve")
        rc = make_synthetic.RECHECKS["P002"]
        out = desk.raise_recheck_flag("P002", "deterioration", rc["reason"], rc["new_vitals"])
        self.assertEqual(out["new_tews"], rc["expected_tews"])
        self.assertEqual(out["retriage_proposal"]["proposed_colour"], "red")
        q = desk.get_queue()
        self.assertEqual(q["queue"][0]["colour"], "orange", "queue unchanged until nurse confirms")
        self.assertEqual(q["awaiting_nurse"][0]["proposed_colour"], "red")


class McpServer(unittest.TestCase):
    """Talks to the real server over stdio, the way any MCP client would."""

    def test_tools_and_audit_log(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        db = fresh_db()

        async def run():
            params = StdioServerParameters(command=sys.executable, args=["-m", "triage_mcp.server"],
                                           cwd=str(ROOT), env={**os.environ, "TRIAGE_DB": db})
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    names = {t.name for t in (await s.list_tools()).tools}
                    self.assertEqual(names, {"register_patient", "compute_tews", "list_discriminators",
                                             "propose_priority", "raise_recheck_flag", "get_queue"})
                    self.assertNotIn("record_clinician_decision", names)
                    await s.call_tool("register_patient", {"card_json": json.dumps(card("P012"))})
                    bad = await s.call_tool("propose_priority", {
                        "patient_id": "P012", "rationale": "x",
                        "discriminators": [{"code": "FOCAL_NEUROLOGY_ACUTE", "evidence_quote": "stroke"}]})
                    self.assertTrue(is_error(bad))
                    ok = await s.call_tool("propose_priority", {
                        "patient_id": "P012", "rationale": "x",
                        "discriminators": [{"code": "FOCAL_NEUROLOGY_ACUTE",
                                            "evidence_quote": "Weakness of left arm"}]})
                    self.assertFalse(is_error(ok))
                    self.assertEqual(json.loads(ok.content[0].text)["proposed_colour"], "orange")

        asyncio.run(run())
        with store.connect() as c:
            rows = c.execute("SELECT tool, ok FROM audit ORDER BY id").fetchall()
        self.assertEqual([(r["tool"], r["ok"]) for r in rows],
                         [("register_patient", 1), ("propose_priority", 0), ("propose_priority", 1)])


if __name__ == "__main__":
    unittest.main()


class BlindModeSweepAudit(unittest.TestCase):
    def setUp(self):
        fresh_db()

    def test_blind_mode_requires_independent_call_and_counts_agreement(self):
        desk.set_blind_mode(True, "Nurse Test")
        for pid in ("P002", "P014", "P001"):
            desk.register_patient(card(pid))
        p2 = desk.propose_priority("P002", [{"code": "CHEST_PAIN", "evidence_quote": "chest pain"}], "x")
        p14 = desk.propose_priority("P014", [], "x")
        p1 = desk.propose_priority("P001", [], "x")
        with self.assertRaises(desk.DeskError):  # cannot approve before the blind call
            desk.record_clinician_decision(p2["proposal_id"], "Nurse Test", "approve")
        r = desk.record_blind_call(p2["proposal_id"], "Nurse Test", "orange")
        self.assertTrue(r["agree"])
        with self.assertRaises(desk.DeskError):  # the call cannot be changed afterwards
            desk.record_blind_call(p2["proposal_id"], "Nurse Test", "red")
        r = desk.record_blind_call(p14["proposal_id"], "Nurse Test", "orange")  # agent yellow
        self.assertEqual(r["agent_was"], "less urgent")
        r = desk.record_blind_call(p1["proposal_id"], "Nurse Test", "green")
        desk.record_clinician_decision(p2["proposal_id"], "Nurse Test", "approve")
        desk.record_clinician_decision(p14["proposal_id"], "Nurse Test", "override", "orange", "hip pain severe")
        st = desk.agreement_stats()
        self.assertEqual((st["blind_calls"], st["agree"], st["agent_less_urgent"], st["agent_more_urgent"]), (3, 2, 1, 0))
        self.assertEqual(st["disagreements"][0]["patient_id"], "P014")
        self.assertEqual(st["disagreements"][0]["final"], "orange")

    def test_reject_allowed_without_blind_call(self):
        desk.set_blind_mode(True, "Nurse Test")
        desk.register_patient(card("P009"))
        p = desk.propose_priority("P009", [], "x")
        desk.record_clinician_decision(p["proposal_id"], "Nurse Test", "reject", note="wrong card")

    def test_overdue_sweep_check_dedupe_and_cooldown(self):
        desk.register_patient(card("P005"))  # red: target is immediate, so overdue once waiting
        p = desk.propose_priority("P005", [], "x")
        desk.record_clinician_decision(p["proposal_id"], "Nurse Test", "approve")
        with store.connect() as c:  # pretend she arrived 30 minutes ago
            c.execute("UPDATE patients SET arrived_at=? WHERE id='P005'", ("2020-01-01T00:00:00+00:00",))
        self.assertEqual([o["patient_id"] for o in desk.overdue_unflagged()], ["P005"])
        f1 = desk.raise_recheck_flag("P005", "nurse_review", "Waited past target, please re-check")
        f2 = desk.raise_recheck_flag("P005", "nurse_review", "Waited past target, please re-check")
        self.assertTrue(f2["duplicate"])
        self.assertEqual(f1["flag_id"], f2["flag_id"])
        self.assertEqual(desk.overdue_unflagged(), [])
        desk.close_flag(f1["flag_id"], "Nurse Test", "seen, bay 2 next")
        self.assertEqual(desk.overdue_unflagged(), [], "a nurse just looked: cooldown applies")
        self.assertEqual(len(desk.overdue_unflagged(cooldown_min=0)), 1)
        with self.assertRaises(desk.DeskError):
            desk.close_flag(f1["flag_id"], "Nurse Test", "again")

    def test_audit_timeline_reads_like_a_record(self):
        from triage_mcp import server  # noqa: F401  (audited tools write the audit rows)
        server.register_patient(card_json=json.dumps(card("P012")))
        server.propose_priority(patient_id="P012", rationale="x",
                                discriminators=[{"code": "FOCAL_NEUROLOGY_ACUTE", "evidence_quote": "Weakness of left arm"}])
        prop = store.pending_all()[0]
        res = desk.record_clinician_decision(prop["id"], "Nurse Test", "approve")
        store.log("clinician:Nurse Test", "record_clinician_decision", {"patient_id": "P012"}, res, True)
        lines = [r["summary"] for r in desk.audit_timeline("P012")]
        self.assertIn("Agent proposed orange", lines[1])
        self.assertIn('"Weakness of left arm"', lines[1])
        self.assertEqual(lines[-1], "Nurse Test approved orange")


class NurseDiscretion(unittest.TestCase):
    def setUp(self):
        fresh_db()

    def test_nurse_can_set_and_change_priority_with_a_reason(self):
        desk.register_patient(card("P011"))  # child: not scorable on the adult scale
        f = desk.raise_recheck_flag("P011", "nurse_review", "child, adult scale does not apply")
        with self.assertRaises(desk.DeskError):
            desk.nurse_set_priority("P011", "Nurse Test", "yellow", "")
        r = desk.nurse_set_priority("P011", "Nurse Test", "yellow", "paediatric chart by hand")
        self.assertEqual(r["final_colour"], "yellow")
        self.assertEqual(desk.get_queue()["queue"][0]["patient_id"], "P011")
        self.assertEqual(store.open_flags("P011"), [], "her decision resolves the flag")
        r = desk.nurse_set_priority("P011", "Nurse Test", "orange", "now drowsy")
        self.assertEqual((r["previous_colour"], r["final_colour"]), ("yellow", "orange"))
        self.assertEqual(desk.get_queue()["queue"][0]["colour"], "orange")

    def test_cannot_bypass_a_pending_proposal(self):
        desk.register_patient(card("P001"))
        desk.propose_priority("P001", [], "x")
        with self.assertRaises(desk.DeskError):
            desk.nurse_set_priority("P001", "Nurse Test", "red", "looks unwell")

    def test_entering_missing_vitals_leads_to_a_proposal_and_clears_the_flag(self):
        desk.register_patient(card("P010"))
        desk.raise_recheck_flag("P010", "missing_vitals", "no BP machine at the desk")
        out = desk.raise_recheck_flag("P010", "deterioration", "Vitals measured at desk", {"sbp": 96})
        prop = out["retriage_proposal"]
        self.assertEqual(prop["tews"], 2)  # rr 18 (+1) and sbp 96 (+1); everything else 0
        desk.record_clinician_decision(prop["proposal_id"], "Nurse Test", "approve")
        self.assertEqual(store.open_flags("P010"), [])


class SendToBayAndMetrics(unittest.TestCase):
    def setUp(self):
        fresh_db()

    def test_send_to_bay_leaves_queue_and_counts_against_target(self):
        desk.register_patient(card("P001"))
        desk.register_patient(card("P005"))
        with self.assertRaises(desk.DeskError):  # no confirmed priority yet
            desk.mark_seen("P001", "Nurse Test")
        for pid in ("P001", "P005"):
            p = desk.propose_priority(pid, [], "x")
            desk.record_clinician_decision(p["proposal_id"], "Nurse Test", "approve")
        with store.connect() as c:  # P005 (red, target immediate) arrived an hour ago
            c.execute("UPDATE patients SET arrived_at=? WHERE id='P005'", ("2020-01-01T00:00:00+00:00",))
        r = desk.mark_seen("P001", "Nurse Test")
        self.assertTrue(r["within_target"])
        r = desk.mark_seen("P005", "Nurse Test")
        self.assertFalse(r["within_target"])
        with self.assertRaises(desk.DeskError):
            desk.mark_seen("P005", "Nurse Test")
        self.assertEqual(desk.get_queue()["queue"], [])
        self.assertEqual(desk.overdue_unflagged(), [], "a patient in a bay is never flagged as overdue")
        m = desk.desk_metrics()
        self.assertEqual((m["registered"], m["with_priority"], m["seen"], m["seen_within_target"]), (2, 2, 2, 1))

    def test_metrics_count_when_a_nurse_lowers_the_agent(self):
        desk.register_patient(card("P002"))
        p = desk.propose_priority("P002", [{"code": "CHEST_PAIN", "evidence_quote": "chest pain"}], "x")
        desk.record_clinician_decision(p["proposal_id"], "Nurse Test", "override", "yellow", "pain is old, stable")
        m = desk.desk_metrics()
        self.assertEqual((m["nurse_lowered"], m["nurse_raised"]), (1, 0))
        self.assertIsNotNone(m["median_min_to_priority"])
