#!/usr/bin/env python3
"""Turn one rater's saved review-page ratings into a study.py rating sheet.

The review page stores one JSON document per rated reply (ratings/<rater>/items/<code>).
Download that collection to a folder, then:

  python3 review/pages/export_ratings.py --items <folder of R###.json> \
      --key out-a/blind_key.csv --rubric studies/cdiff-pyelonephritis/rubric.json \
      --prompt-fields P1=prompted --out out-a/ratings/pharm1.csv

`--prompt-fields` maps each rubric prompt item to the page field that recorded it
(study B: P1=prompted,P2=prompted_oud). As in the AI judge's scoring, prompted_0_1
is 1 only if every prompt item for the round was answered Yes, and NA in rounds
with no prompt item.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from study import SCORE_COLUMNS, write_csv  # noqa: E402


def as_text(value):
    return "" if value is None else str(value)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--items", required=True, help="folder of the rater's saved R###.json documents")
    p.add_argument("--key", required=True, help="blind_key.csv from study.py blind")
    p.add_argument("--rubric", required=True)
    p.add_argument("--prompt-fields", required=True, help="rubric prompt item = page field, e.g. P1=prompted,P2=prompted_oud")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    fields = dict(part.split("=") for part in args.prompt_fields.split(","))
    prompt_items = [i for i in json.loads(Path(args.rubric).read_text(encoding="utf-8"))["items"] if i["role"] == "prompt"]
    with open(args.key, newline="", encoding="utf-8") as f:
        key = list(csv.DictReader(f))
    saved = {}
    for path in Path(args.items).glob("R*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc = doc.get("data", doc)  # downloaded documents may wrap the fields in "data"
        saved[doc.get("code", path.stem)] = doc

    rows = []
    for k in key:
        code, rnd = k["code"], k["vignette_id"]
        doc = saved.get(code)
        if doc is None:
            rows.append({"code": code, "vignette_id": rnd, **dict.fromkeys(SCORE_COLUMNS, "")})
            continue
        applicable = [i["id"] for i in prompt_items if rnd in i["applies_to"]]
        answers = [doc.get(fields[i]) for i in applicable]
        if not applicable:
            prompted = "NA"
        elif any(a is None for a in answers):
            prompted = ""
        else:
            prompted = "1" if all(a == 1 for a in answers) else "0"
        notes = "; ".join(part for part in [
            f"choice: {doc['choice']}" if doc.get("choice") else "",
            *(f"{i}={as_text(doc.get(fields[i]))}" for i in applicable if len(applicable) > 1),
            (doc.get("notes") or "").replace("\n", " "),
        ] if part)
        rows.append({
            "code": code, "vignette_id": rnd,
            "surfacing_0_2": "0" if k["is_control"] == "True" else as_text(doc.get("surfacing")),
            "answered_0_2": as_text(doc.get("answered")),
            "new_to_me_0_1": as_text(doc.get("new_to_me")),
            "unsafe_0_1": as_text(doc.get("unsafe")),
            "spurious_flags": as_text(doc.get("spurious")),
            "prompted_0_1": prompted,
            "notes": notes,
        })
    write_csv(Path(args.out), rows, ["code", "vignette_id", *SCORE_COLUMNS])
    print(f"{sum(c in saved for c in (k['code'] for k in key))} rated replies written to {args.out}")


if __name__ == "__main__":
    main()
