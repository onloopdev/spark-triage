# SATS reference used by Spark Triage

Every `rule_id` returned by the triage-desk server points to a line in this file.

**Source.** "Adult SATS chart" (c) 2012 South African Triage Group under the auspices of the Emergency Medicine Society of South Africa (EMSSA), licensed [CC BY-NC-SA 3.0](http://creativecommons.org/licenses/by-nc-sa/3.0/). Official materials: https://emssa.org.za/sats/ . This file is a hand transcription for software testing, distributed under the same licence. It has **not** been checked line by line against the official chart by a clinician yet; see EVALS.md.

Scope: patients aged 12 and over (or taller than 150 cm). Children need the paediatric chart, which is not implemented.

## Triage Early Warning Score (TEWS)

| rule_id | Parameter | Range | Points |
|---|---|---|---|
| `SATS-TEWS-MOB-WALKING` | Mobility | walking | 0 |
| `SATS-TEWS-MOB-WITH-HELP` | Mobility | walks with help | 1 |
| `SATS-TEWS-MOB-STRETCHER` | Mobility | stretcher / immobile | 2 |
| `SATS-TEWS-RR-LT9` | Respiratory rate | below 9 /min | 2 |
| `SATS-TEWS-RR-9-14` | Respiratory rate | 9-14 | 0 |
| `SATS-TEWS-RR-15-20` | Respiratory rate | 15-20 | 1 |
| `SATS-TEWS-RR-21-29` | Respiratory rate | 21-29 | 2 |
| `SATS-TEWS-RR-GT29` | Respiratory rate | above 29 | 3 |
| `SATS-TEWS-HR-LT41` | Heart rate | below 41 /min | 2 |
| `SATS-TEWS-HR-41-50` | Heart rate | 41-50 | 1 |
| `SATS-TEWS-HR-51-100` | Heart rate | 51-100 | 0 |
| `SATS-TEWS-HR-101-110` | Heart rate | 101-110 | 1 |
| `SATS-TEWS-HR-111-129` | Heart rate | 111-129 | 2 |
| `SATS-TEWS-HR-GT129` | Heart rate | above 129 | 3 |
| `SATS-TEWS-SBP-LT71` | Systolic BP | below 71 mmHg | 3 |
| `SATS-TEWS-SBP-71-80` | Systolic BP | 71-80 | 2 |
| `SATS-TEWS-SBP-81-100` | Systolic BP | 81-100 | 1 |
| `SATS-TEWS-SBP-101-199` | Systolic BP | 101-199 | 0 |
| `SATS-TEWS-SBP-GT199` | Systolic BP | above 199 | 2 |
| `SATS-TEWS-TEMP-LT35` | Temperature | below 35 C (cold) | 2 |
| `SATS-TEWS-TEMP-35-38.4` | Temperature | 35-38.4 C | 0 |
| `SATS-TEWS-TEMP-GT38.4` | Temperature | above 38.4 C (hot) | 2 |
| `SATS-TEWS-AVPU-ALERT` | AVPU | alert | 0 |
| `SATS-TEWS-AVPU-VOICE` | AVPU | reacts to voice | 1 |
| `SATS-TEWS-AVPU-CONFUSED` | AVPU | confused | 1 |
| `SATS-TEWS-AVPU-PAIN` | AVPU | reacts to pain | 2 |
| `SATS-TEWS-AVPU-UNRESPONSIVE` | AVPU | unresponsive | 3 |
| `SATS-TEWS-TRAUMA-NO` | Trauma | no | 0 |
| `SATS-TEWS-TRAUMA-YES` | Trauma | yes | 1 |

## Colour bands and target times

| rule_id | TEWS | Colour | Seen within |
|---|---|---|---|
| `SATS-TEWS-BANDS` | 7 or more | red | immediately |
| `SATS-TEWS-BANDS` | 5-6 | orange | 10 min |
| `SATS-TEWS-BANDS` | 3-4 | yellow | 60 min |
| `SATS-TEWS-BANDS` | 0-2 | green | 240 min |

Final priority is the most urgent of the TEWS colour and any discriminator colour. A senior clinician may override (recorded with a note).

| rule_id | Meaning |
|---|---|
| `SATS-NURSE-DISCRETION` | Priority set or changed by the triage nurse's own clinical judgement, with her name and reason |

## Clinical discriminators

`symptom` discriminators need an exact quote from the intake card. `measured` ones are computed by the server from recorded values.

| rule_id | Discriminator | Colour | Kind |
|---|---|---|---|
| `SATS-DISC-AIRWAY_COMPROMISED` | Airway compromised or obstructed | red | symptom |
| `SATS-DISC-BREATHING_NOT_EFFECTIVE` | Not breathing effectively | red | symptom |
| `SATS-DISC-CARDIAC_ARREST` | Cardiac arrest / no pulse | red | symptom |
| `SATS-DISC-SEIZURE_CURRENT` | Seizure, current | red | symptom |
| `SATS-DISC-BURN_FACIAL_INHALATION` | Burn, facial or inhalation | red | symptom |
| `SATS-DISC-HYPOGLYCAEMIA` | Hypoglycaemia, glucose below 3 mmol/L | red | measured |
| `SATS-DISC-SOB_ACUTE` | Shortness of breath, acute | orange | symptom |
| `SATS-DISC-COUGHING_BLOOD` | Coughing blood | orange | symptom |
| `SATS-DISC-CHEST_PAIN` | Chest pain | orange | symptom |
| `SATS-DISC-HAEMORRHAGE_UNCONTROLLED` | Haemorrhage, uncontrolled | orange | symptom |
| `SATS-DISC-SEIZURE_POST_ICTAL` | Seizure, post-ictal | orange | symptom |
| `SATS-DISC-FOCAL_NEUROLOGY_ACUTE` | Focal neurology, acute | orange | symptom |
| `SATS-DISC-LOC_REDUCED` | Level of consciousness reduced | orange | symptom |
| `SATS-DISC-PSYCHOSIS_AGGRESSION` | Psychosis or aggression | orange | symptom |
| `SATS-DISC-THREATENED_LIMB` | Threatened limb | orange | symptom |
| `SATS-DISC-DISLOCATION_LARGER_JOINT` | Dislocation of larger joint (not finger or toe) | orange | symptom |
| `SATS-DISC-FRACTURE_COMPOUND` | Fracture, compound (open) | orange | symptom |
| `SATS-DISC-BURN_OVER_20PCT` | Burn over 20% of body surface | orange | symptom |
| `SATS-DISC-BURN_ELECTRICAL` | Burn, electrical | orange | symptom |
| `SATS-DISC-BURN_CIRCUMFERENTIAL` | Burn, circumferential | orange | symptom |
| `SATS-DISC-BURN_CHEMICAL` | Burn, chemical | orange | symptom |
| `SATS-DISC-POISONING_OVERDOSE` | Poisoning or overdose | orange | symptom |
| `SATS-DISC-DIABETIC_GLUCOSE_GT11_KETONES` | Diabetic, glucose above 11 mmol/L with ketonuria | orange | measured |
| `SATS-DISC-VOMITING_FRESH_BLOOD` | Vomiting fresh blood | orange | symptom |
| `SATS-DISC-PREGNANCY_ABDO_TRAUMA_OR_PAIN` | Pregnancy with abdominal trauma or abdominal pain | orange | symptom |
| `SATS-DISC-PAIN_VERY_SEVERE` | Very severe pain | orange | symptom |
| `SATS-DISC-HAEMORRHAGE_CONTROLLED` | Haemorrhage, controlled | yellow | symptom |
| `SATS-DISC-FRACTURE_CLOSED` | Fracture, closed | yellow | symptom |
| `SATS-DISC-BURN_OTHER` | Burn, other | yellow | symptom |
| `SATS-DISC-ABDOMINAL_PAIN` | Abdominal pain | yellow | symptom |
| `SATS-DISC-DIABETIC_GLUCOSE_GT17_NO_KETONES` | Diabetic, glucose above 17 mmol/L, no ketonuria | yellow | measured |
| `SATS-DISC-VOMITING_PERSISTENT` | Vomiting, persistent | yellow | symptom |
| `SATS-DISC-PREGNANCY_AND_TRAUMA` | Pregnancy and trauma | yellow | symptom |
| `SATS-DISC-PAIN_MODERATE` | Moderate pain | yellow | symptom |
