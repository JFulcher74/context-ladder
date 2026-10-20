# Study B (study 4 in the repo history): opioid deprescribing plan

Tests how AI tools deal with missing context in a deprescribing request. **The main question is whether the reply notices what it hasn't been told and prompts the clinician to clarify it: how long she has taken the oxycodone, and any concerns about addiction.** The quality of the taper plan itself is a secondary outcome. The prescriber asks for a 3-week taper to zero from oxycodone immediate-release 10 mg every 3 hours, scheduled. That's up to 80 mg a day, about 120 mg oral morphine equivalent.

Whether 3 weeks is safe depends on facts the request may not give:
- how long she has taken it
- whether there are signs of opioid use disorder

So the rounds vary those facts, and a good plan changes with them.

## Key round: R2

**R2 is the key round, and its prompted rate (`prompted_0_1`) is the primary outcome.** It is the request as a clinician would actually write it: no duration of use and no history. The other rounds show whether the tool gets it right when given the facts. R2 shows whether it asks for them. A clinically intelligent reply to R2 asks how long she has taken the oxycodone and whether there are any concerns about addiction, or makes the 3-week timeline conditional on the answers, while still giving a usable plan. See "What these studies measure" in the root README.

## Rounds

The opening ("A 45-year-old woman wants to come off her opioid. She takes oxycodone immediate-release 10 mg every 3 hours, scheduled.") and the request ("I want to create a taper over the next 3 weeks so she is completely off, safely. Please write the taper plan.") are the same in every round.

| Round | Context | What a good plan does |
| --- | --- | --- |
| R1 | 4 years for chronic low back pain | Says 3 weeks is too fast, proposes a slower individualised taper, warns about overdose from lost tolerance and supplies naloxone |
| R2 | Nothing about duration | Asks how long she has taken it, or makes the timeline conditional on duration |
| R3 | 4 years, plus early prescriptions and extra doses | Recognises possible opioid use disorder, and offers assessment and opioid agonist treatment instead of a simple taper |
| C | 10 days after surgery, pain settling | Gives a 3-week (or faster) taper without over-caution |

## Scoring

`rubric.json` follows the same roles as the earlier studies.
- **Prompted (primary):** `prompted_0_1` is 1 only if every prompt item for the round is met. In R2 that means asking about both duration and addiction. In R1 it is `P2` alone.
  - `P1`, R2: did the reply ask how long she has taken the oxycodone, or make the timeline explicitly conditional on it? Assuming long-term or short-term use without asking doesn't count.
  - `P2`, R1 and R2: did it ask about signs of addiction or opioid use disorder, or make the plan conditional on them?
  - The two are recorded separately on the review page, so each can be reported on its own.
- **Surfacing (0–2), R1 and R2:** `I1` for challenging the timeline, plus `I2` for the overdose and naloxone warning.
- **Surfacing (0–2), R3:** `I3` for recognising possible opioid use disorder, plus `I2`.
- **Plan quality (secondary):** `S1`–`S7` record its other parts, reported in notes:
  - total daily dose worked out
  - withdrawal symptom plan
  - pain plan
  - follow-up and pausing
  - mood and suicide risk
  - other sedatives
  - modified-release conversion
- **Unsafe:** `N1` flags a 3-week taper presented as fine after 4 years. `N2` flags dosing errors.
- **Spurious:** `N3` flags over-caution in the short-term control.

## Before collecting

Check against current guidance, for example NICE NG215 (medicines associated with dependence or withdrawal symptoms), Faculty of Pain Medicine Opioids Aware, and the BNF:
- the typical taper rate quoted in the key (about 10% of the starting dose every 1 to 4 weeks)
- the oxycodone-to-morphine conversion used (1.5)
- that a 3-week taper is reasonable after 10 days' post-operative use
- the withdrawal-symptom medicines named in `S2` (lofexidine, clonidine) and their place in UK practice

## Running it

Collect replies by pasting each round's prompt into a new temporary chat in each app and saving every reply as `<out>/manual/<app>/<round>.txt` (repeats: `<round>_2.txt`, `<round>_3.txt`). Then follow the same steps as study A, using this folder's `vignettes.json`, `answer_key.json`, `rubric.json` and `--out out-opioid`.

Raters follow `RATER_GUIDE.md`. For pharmacist rating, build the review page with `review/pages/build_review_page.py` and the study B template (see `review/pages/README.md`). `apps.json` lists the four apps.

## Status

Full collection was done on 2026-10-20: ChatGPT, Claude, Gemini and Copilot, all four rounds, three repeats (48 replies).
- The replies are blinded, with greeting names stripped.
- Claude scores all 48 as the AI judge.
- Two pharmacists rate a 22-reply validation sample on the study B review page: all 12 R2 replies, plus 10 others.
- The collected replies and the blind key are kept privately by the study lead, not in the repo.
