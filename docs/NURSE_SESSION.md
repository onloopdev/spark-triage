# Nurse test session

A 25-minute session with a practising triage or OPD nurse. Only synthetic patients are used, so no patient data, ethics approval or facility permission is involved. One nurse is enough to answer the judges' question "did you talk to one?"; three or more give you agreement numbers worth reporting.

## Invitation (WhatsApp or in person)

> Good day [Name]. I'm Cyprian, a computer science student in Awka. I've built a tool that helps the triage nurse at an OPD desk sort walk-in patients by urgency using the South African Triage Scale. It never diagnoses, and the nurse confirms every decision. Could I have 25 minutes of your time this week to try it on 14 made-up patient cards and tell me where it's wrong? It can be in person or on a video call. Thank you.

## Before you start

* Desk running (`./run.sh`), blind mode **on**, demo reset so all 14 cards are new.
* Ask permission, out loud, and note the answer:
  1. "All patients are invented. Is it OK if I note your triage calls and comments?"
  2. "May I name you and your workplace in my submission, or should I keep you anonymous?"
* Type her name in the top bar (or "Nurse A" if she prefers to stay anonymous).

## The session

1. **Two minutes of context.** "The agent scores each card. In blind mode you make your own call first, then it shows you its answer and its reasons. You decide."
2. **Triage all 14 cards** (about 15 minutes). Click "Triage all" and let her work through the cards as they arrive. Ask her to think aloud. Don't help or explain unless she is stuck.
3. **Try one re-check.** After P002 is confirmed, have her enter worse vitals: breathing 31, pulse 132, BP 86, confused.
4. **Five questions** (about 5 minutes):
   1. Which proposal did you disagree with most, and why?
   2. Did the highlighted quote and the reasons help you trust or reject the proposal?
   3. Would this save time at your desk on a busy morning? Where would it slow you down?
   4. What would stop your unit from using this?
   5. Is anything on the screen confusing or unnecessary?

## What to record

* The agreement panel under the queue (screenshot it).
* Every disagreement: patient, her colour, the agent's colour, her reason.
* Two or three direct quotes from her answers.
* Anything that broke or confused her.

Paste the numbers into EVALS.md under "Nurse session", and one sentence about the session into SUBMISSION.md. Report exactly what happened, including the cards the agent got wrong.
