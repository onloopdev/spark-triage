# Submission text (Healthcare: The Triage Desk)

**Project:** Spark Triage
**Sub-theme:** Symptom-based priority scoring. The re-check and the overdue sweep exist only to keep that priority correct while the patient waits; we are not entering the other themes.
**Team:** Cyprian Obiakor, solo, SparkBlood Nigeria (Awka, Anambra State)
**Repo:** <add GitHub URL>  **Demo video:** <add YouTube/Vimeo URL>

## Description (about 300 words)

Spark Triage is an agentic assistant for the triage desk of a busy public outpatient department, built for [FACILITY NAME, Anambra State]. In one Nigerian general hospital study, outpatients waited 104 minutes just to have their vitals taken, so an urgent patient can sit unnoticed in a routine queue. Spark Triage turns each intake card into a South African Triage Scale priority and hands it to the nurse as a pending proposal with every source shown. It never diagnoses, never prescribes and never decides who is seen.

The agent runs offline on an ordinary laptop with an open-weights model through Ollama, orchestrated with LangGraph, so patient data never leaves the facility. It plans, reads the card through the official MCP filesystem server, then works through our own triage-desk MCP server: register the card, compute the Triage Early Warning Score from the stored vitals, cite SATS discriminators and propose a priority. The model cannot set a colour. It must quote the card's exact words, the server rejects anything else, and code computes the colour, so the agent can never score a patient below what the vitals say.

Code, not the model, then verifies the proposal and checks the vitals were not altered. LangGraph pauses for a named nurse, who approves, changes or rejects; only then does the patient get a queue position. Missing vitals and children go back to her as flags. Worse re-check vitals produce a pending re-triage, and a sweep flags anyone waiting past their SATS target.

Blind mode guards against automation bias: the nurse makes her own call before the proposal is revealed, and the desk counts agreement and every case where the agent was less urgent than her. The desk also reports time to a confirmed priority and how many patients were sent to a bay within their SATS target, and every step is in a plain-language audit trail.

## Workflow served

Walk-in adult patients at the OPD triage desk. The nurse takes vitals and writes the complaint on the card; Spark Triage proposes the priority while the next patient sits down; the nurse confirms it on the desk screen, where the queue shows who is next, how long each patient has waited against the SATS target, and who is overdue.

## Before submitting, fill in

* [FACILITY NAME] and the nurse you spoke to (judges score "Did you talk to one?").
* Repo and video links.
