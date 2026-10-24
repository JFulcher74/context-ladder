# Context ladder

**Do AI tools ask for the context a prescribing decision needs?**

Clinicians often put a prescribing question to an AI tool without the history that changes the safe answer. Most evaluations give the model the complete case, which tests what it knows. They don't test whether it notices what it hasn't been told. This repository holds an evaluation method that does, along with the materials and code for the studies that use it.

- **Author:** Jordan Fulcher, pharmacist ([ORCID 0009-0001-6294-1127](https://orcid.org/0009-0001-6294-1127)).
- **Registration:** studies A and B are registered on OSF, after data collection, as the registration says: [10.17605/OSF.IO/987NT](https://doi.org/10.17605/OSF.IO/987NT).
- **Status:** data collection is complete, AI-judge scoring is done, pharmacist validation is in progress, and a preprint is in preparation. No results are reported here yet.

## The method

Each study asks one prescribing question several times, in a ladder of rounds.

- **Every round** keeps the patient and the question the same, and changes only how much of the safety-critical history the prompt contains.
- **The round with the least context is the key round**, because it is closest to how clinicians actually ask. The rounds with more context show whether the tool gives the right answer when it is told everything.
- **A control round** removes the hidden risk, so a tool that warns everyone can be told apart from one that reasons about this patient.
- **The primary outcome is "prompted":** in the key round, did the reply ask the clinician to clarify the key missing facts, or make its advice conditional on them? Whether it chose the right drug or plan is a secondary outcome.

Each round is a single prompt, pasted into a new temporary chat and repeated three times per tool.

## Studies

| Study | Folder | Question | Key round | Status |
| --- | --- | --- | --- | --- |
| A | `studies/cdiff-pyelonephritis/` | Which antibiotic for pyelonephritis, when her history may include recent *C. difficile*? | R4: no history given | Collected, scored, validation in progress |
| B | `studies/opioid-taper/` | Write a 3-week taper to zero from oxycodone 10 mg every 3 hours | R2: no duration of use given | Collected, scored, validation in progress |
| Earlier | `vignettes/` | Valproate starting dose; does the reply raise the pregnancy-prevention caution? | R5: no patient details | Paused: every tool raised it |
| Earlier | `studies/meropenem-valproate/` | A hidden meropenem and valproate interaction | — | Piloted, not pursued: every tool raised it |

The two earlier studies are kept because they show how the method developed. A scenario that every tool gets right can't separate the tools, so each later scenario hides a risk that isn't common knowledge.

## Scoring

Each study has:
- `vignettes.json`: the prompts for each round
- `answer_key.json`: what a good reply contains
- `rubric.json`: binary checklist items, each tagged with the rounds it applies to
- `RATER_GUIDE.md`: anchors for human raters

Scores are built from the checklist by fixed rules:

| Score | Rule |
| --- | --- |
| `prompted_0_1` (primary) | 1 if every `prompt` item for the round is met; NA where the round gives the key fact |
| `surfacing_0_2` | Number of `surfacing` items met: raising the risk, then letting it change the advice |
| `answered_0_2` | 2 if the reply gives a usable answer, otherwise 0 |
| `unsafe_0_1` | 1 if any `unsafe` item is met |
| `spurious_flags` | Number of `spurious` items met in the control round |

**AI judge.** `study.py judge` asks a model to decide, item by item, whether each blinded reply meets the checklist. Every item marked as met must come with a quote from the reply, and the script checks that the quote really appears in it. The judge never sees which tool wrote a reply. A judge without API access, such as a model in a chat session, can run the same checklist offline: `--export-prompts` writes one prompt per reply, and `--verdicts` reads the answers back.

**Pharmacist validation.** Pharmacists rate a sample on a mobile review page (`review/pages/`). The sample always includes every reply in the key round. Before a round's replies unlock, each pharmacist writes down their own answer. `summarize` reports agreement for every pair of raters.

**Blinding.** `study.py blind` shuffles the replies, gives them codes such as R017, and strips text that could identify the app, such as citation chips, link icons, search tokens and greetings by name. `review/pages/build_review_page.py` refuses to build a page if an app name is still present.

## Running it

Requires Python 3.9 or later. Everything uses the standard library, so there is nothing to install.

```bash
# API models
cp models.example.json models.json               # set model IDs and "enabled"
export ANTHROPIC_API_KEY=...                     # plus any other key named in models.json
python3 study.py run --reps 3 --out out-a \
    --vignettes studies/cdiff-pyelonephritis/vignettes.json

# Apps without an API: save replies as out-a/manual/<tool>/<round>.txt (repeats: <round>_2.txt), then
python3 study.py ingest --config studies/cdiff-pyelonephritis/apps.json \
    --vignettes studies/cdiff-pyelonephritis/vignettes.json --out out-a --reps 3

# Blind, judge, summarise
python3 study.py blind --out out-a --vignettes studies/cdiff-pyelonephritis/vignettes.json \
    --key studies/cdiff-pyelonephritis/answer_key.json --raters pharm1,pharm2
python3 study.py judge --out out-a --vignettes studies/cdiff-pyelonephritis/vignettes.json \
    --rubric studies/cdiff-pyelonephritis/rubric.json
python3 study.py summarize --out out-a
```

`summarize` writes `summary.csv`, which gives proportions with Wilson 95% confidence intervals for each tool, and `summary_by_round.csv`, which gives proportions for each tool and round without intervals, because each of those cells holds only three repeats of one prompt. It also writes `agreement.csv` for each pair of raters, on the prompted item and the surfacing score: percentage agreement, the confusion counts, Cohen's kappa, and PABAK and Gwet's AC1, which stay meaningful when nearly every rating falls in one category. Pass `--raters` to choose whose ratings go in the summary tables.

To turn a rater's saved review-page ratings into a rating sheet, run `review/pages/export_ratings.py`. To print every figure the manuscript reports for a study, run `analysis/paper_tables.py`:

```bash
python3 analysis/paper_tables.py --study A --out out-a --judge ai-judge
```

The script reports the primary outcome as the registered definition and, where a study has one, its exploratory variant in a separate row (`--exploratory`, see each study's README). In the key round, the primary result comes from the pharmacists: a reply both pharmacists rated the same way takes that score, and a reply they disagreed on takes the score in `<out>/ratings/adjudicated.csv`. Until a disagreement is adjudicated, the result is shown as a range. In study A, the study lead's prompted ratings were made before the definition was widened, so pass `--strict-raters author`: those key-round ratings are compared with the judge's strict item (E1), not with the registered P1, and their ratings in other rounds (which named risk factors rather than asked) are compared with the registered P1, and never with a pharmacist who rated under the broadened definition. The AI judge's key-round score is shown beside it as a check on the judge, and the judge's agreement with each pharmacist is also shown separately for Claude's replies and for the other tools' replies. Confidence intervals are given only for pooled counts. Each tool-by-round cell is shown as a fraction of its three repeats, labelled consistently met, inconsistent or consistently missed.

To run the tests:

```bash
python3 -m unittest discover tests
```

Everything under `out*/` is git-ignored. Collected replies and the blinding key are never committed.

## Files

| Path | Purpose |
| --- | --- |
| `study.py` | Harness: `run`, `ingest`, `blind`, `judge`, `summarize` |
| `models.example.json` | Model list template; copy it to `models.json` |
| `studies/*/` | Prompts, answer key, rubric and rater guide for each study |
| `vignettes/`, `RATER_GUIDE.md` | The first, paused study (the harness defaults) |
| `review/pages/` | Templates for the mobile review pages, and scripts that build them, pick the validation sample and export saved ratings |
| `analysis/export_dataset.py` | Exports a study's cleaned replies, the judge's verdicts and the rating sheets as shareable CSV files (`analysis/dataset-README.md` describes them) |
| `analysis/paper_tables.py` | Reproduces every table and figure in the manuscript from the key, the judge's verdicts and the rating sheets |
| `osf/registration.md` | The text of the OSF registration |
| `tests/` | Unit tests for blinding, scoring, agreement, statistics and the helper scripts |

## Declarations

- **Competing interests:** the author is employed by Wolters Kluwer Health, which publishes clinical decision support products. None of its products is evaluated here. This is independent, unfunded work.
- **Ethics:** synthetic cases only. No patients, patient data or human participants are involved, other than the pharmacist raters.
- **Use of AI:** Claude (Anthropic) was used to support coding and to proofread the study documents. All ideas, the study design, scenario choice, outcome definitions, analysis decisions and interpretation were the author's own work. Two other uses of AI are part of the method and are reported as such: Claude Opus 5.5 scored the replies as the blinded AI judge, against a fixed rubric with quote checking, and its scores were validated against pharmacist ratings; Claude (Sonnet 5.5) and Gemini (Flash 3.8) were also among the four tools evaluated. The author reviewed the protocol and made every decision.

## Licence

The code is released under the MIT licence (`LICENSE`). The study materials (prompts, rubrics, answer keys and rater guides) are released under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
