# Rater review pages

Mobile-friendly pages for blinded pharmacist rating, published as Claude artifacts.

| Template | Study | Status |
| --- | --- | --- |
| `study-a-review-template.html` | Study A (repo study 3): pyelonephritis after recent C. difficile, key-round replies (12 of 60) | Published |
| `study-b-review-template.html` | Study B (repo study 4): opioid taper request, key-round replies (12 of 48) | Published |

Each page shows one round at a time. The rater writes their own answer first, and the round's blinded replies unlock once it's saved. They then rate one reply at a time against the study's rubric. In the rounds where the key detail is missing or unclear, the first question on the form, highlighted as the key question, asks whether the reply prompted the rater to clarify it. This is the main measure. The antibiotic chosen (study A) and the taper length (study B) are recorded for reference. Ratings save to the artifact's private store as the rater goes.

## Build

After `study.py blind` has written the blinded replies:

```bash
python3 review/pages/build_review_page.py \
    --template review/pages/study-b-review-template.html \
    --vignettes studies/opioid-taper/vignettes.json \
    --blinded out-opioid/blinded --out study-b-review.html --names <name an app greeted you with>
```

The script refuses to write the page if an app name, model name or known fingerprint is still in the replies.

To publish only the pharmacists' validation sample, add `--codes <file>`. The file lists reply codes, one per line. Build it with `pick_sample.py`, which takes every key-round reply plus a set number from each other round, spread across tools:

```bash
python3 review/pages/pick_sample.py --key out-cdiff/blind_key.csv --key-round R4 \
    --per-round R1=2,R2=2,R3=3,C=3 --out out-cdiff/validation_sample.txt
python3 review/pages/pick_sample.py --key out-opioid/blind_key.csv --key-round R2 \
    --per-round R1=4,R3=3,C=3 --out out-opioid/validation_sample.txt
```

The AI judge scores every reply. The study lead rated the 22-reply sample for study A. To make the second pharmacist's task shorter, both pages now hold only the 12 key-round replies, from a codes file listing the key round alone. The page shows only the rounds it has replies for.

## Publish and share

Publish the page with these capabilities, so each rater's ratings are private to them and readable by the owner:

```json
{"db": {"rules": [{"path": "ratings", "read": "owner", "write": "owner"},
                  {"path": "ratings/{self}", "read": "interact", "write": "interact"}]},
 "user": {}}
```

Share it by inviting each rater **by email as an Editor**. Don't create a public link: while one exists, raters outside the owner's organisation can't save.

## Finishing

Ratings save as the rater goes, so there is no submit step. On the last reply the button reads **Finish**. It checks that every reply is fully rated and every write reached the store, then says so, or lists the replies that still need an answer.

## Finding a rater's ratings

Each rater's ratings sit under their own ID, which the owner cannot list. On load the page therefore writes the rater's ID and the name they typed to `raters/<id>`, which only the owner can read. Declare these rules as well as the `ratings` ones:

```json
{"db": {"rules": [{"path": "ratings", "read": "owner", "write": "owner"},
                  {"path": "ratings/{self}", "read": "interact", "write": "interact"},
                  {"path": "raters", "read": "owner", "write": "owner"},
                  {"path": "raters/{self}", "read": "interact", "write": "interact"}]},
 "user": {}}
```

A rater has to open the page once after it is published for their ID to appear.

## Stored data

| Path | Contents |
| --- | --- |
| `ratings/<rater id>/meta/profile` | The rater's name or initials |
| `ratings/<rater id>/stage1/<round>` | The rater's own answer for a round, written before they saw any reply |
| `ratings/<rater id>/items/<code>` | One rating per reply: `surfacing`, `prompted`, `answered`, `unsafe`, `spurious`, `new_to_me`, `choice` (antibiotic, or taper length), `elements` (study B), `prompted_oud` (study B: prompted to clarify addiction concerns, R1 and R2), `notes` |

Codes match the blind key and the CSV rating sheets from `study.py blind`, so page ratings and sheet ratings can be compared directly.

The study lead keeps the blind key privately, outside the repo. A copy can be kept in a page's store under `ratings/_analyst/`: only the owner can read it there, because it sits under `ratings` but outside any rater's own subtree.

## Exporting ratings

Download a rater's `ratings/<rater id>/items` collection as JSON files, then convert them into a `study.py` rating sheet:

```bash
python3 review/pages/export_ratings.py --items <folder> --key out-a/blind_key.csv \
    --rubric studies/cdiff-pyelonephritis/rubric.json --prompt-fields P1=prompted --out out-a/ratings/pharm1.csv
```

For study B, use `--prompt-fields P1=prompted,P2=prompted_oud`. `prompted_0_1` is 1 only if every prompt item for the round was answered Yes, the same rule as for the AI judge.
