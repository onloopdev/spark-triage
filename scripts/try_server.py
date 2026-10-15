"""Use the triage-desk MCP server with no agent and no model.

Proves the server stands on its own: any MCP client can connect, list the
tools and call them. Uses a throwaway database, so it never touches the desk's.

  python scripts/try_server.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
CARD = ROOT / "data" / "intake" / "P012.json"


def text_of(result) -> str:
    return "\n".join(getattr(c, "text", str(c)) for c in result.content)


def failed(result) -> bool:  # isError in MCP SDK v1, is_error in v2
    return bool(getattr(result, "is_error", getattr(result, "isError", False)))


async def main() -> None:
    if not CARD.exists():
        sys.exit("No intake cards yet. Run: python data/make_synthetic.py")
    db = os.path.join(tempfile.mkdtemp(), "try_server.db")
    params = StdioServerParameters(command=sys.executable, args=["-m", "triage_mcp.server"], cwd=str(ROOT),
                                   env={**os.environ, "TRIAGE_DB": db, "TRIAGE_ACTOR": "try_server.py"})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            print(f"1. Connected. The server offers {len(tools)} tools:")
            for t in tools:
                print(f"   - {t.name}: {(t.description or '').strip().splitlines()[0]}")

            card = CARD.read_text()
            r = await s.call_tool("register_patient", {"card_json": card})
            print(f"\n2. register_patient(P012) -> next step: {json.loads(text_of(r))['next_step']}")

            r = await s.call_tool("compute_tews", {"patient_id": "P012"})
            t = json.loads(text_of(r))
            print(f"3. compute_tews -> score {t['tews']} ({t['colour']}), from {t['vitals_entry']['source']}")

            bad = {"patient_id": "P012", "rationale": "demo",
                   "discriminators": [{"code": "FOCAL_NEUROLOGY_ACUTE", "evidence_quote": "stroke symptoms"}]}
            r = await s.call_tool("propose_priority", bad)
            print(f"4. propose_priority with a quote NOT on the card -> refused: {failed(r)}")
            print(f"   server says: {text_of(r)[:150]}")

            good = {**bad, "discriminators": [{"code": "FOCAL_NEUROLOGY_ACUTE",
                                               "evidence_quote": "Weakness of left arm"}]}
            r = await s.call_tool("propose_priority", good)
            p = json.loads(text_of(r))
            print(f"5. propose_priority with the card's own words -> {p['proposed_colour']}, see within "
                  f"{p['target_minutes']} min, {len(p['sources'])} sources")
            print(f"   status: {p['status']}")

            r = await s.call_tool("get_queue", {})
            q = json.loads(text_of(r))
            print(f"6. get_queue -> confirmed in queue: {len(q['queue'])}, awaiting the nurse: "
                  f"{[a['patient_id'] for a in q['awaiting_nurse']]}")
            names = {t.name for t in tools}
            print(f"\n7. Can this client approve its own proposal? "
                  f"{'YES (problem!)' if 'record_clinician_decision' in names else 'No. There is no approval tool on the server.'}")

    sys.path.insert(0, str(ROOT))
    os.environ["TRIAGE_DB"] = db
    from triage_mcp import store
    print(f"\n8. Audit log written by the server ({db}):")
    with store.connect() as c:
        for row in c.execute("SELECT ts, actor, tool, ok FROM audit ORDER BY id"):
            print(f"   {row['ts']}  {row['actor']:<14} {row['tool']:<20} {'ok' if row['ok'] else 'REFUSED'}")


if __name__ == "__main__":
    asyncio.run(main())
