#!/usr/bin/env python3
"""Export one study's replies, judge verdicts and rating sheets as a shareable dataset.

The replies are the cleaned text raters and the judge saw (read from the blinded
files), so app fingerprints and any greeting by name are already removed. The
script refuses to write the dataset if one is still present.

  python3 analysis/export_dataset.py --study A --out out-cdiff \
      --rubric studies/cdiff-pyelonephritis/rubric.json \
      --raters ai-judge,pharm1-page --rater-labels pharm1-page=author \
      --dest out-cdiff/dataset

Files written under <dest>/study-<a|b>/:
  replies.csv   one row per reply: code, tool, model version, round, repeat, reply text
  verdicts.csv  the judge's item-by-item answers, with the quote that supports each "met"
  scores.csv    each rater's scores per reply (judge and pharmacists), under neutral labels
  items.csv     the rubric items: id, role, rounds it applies to, wording
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from study import SCORE_COLUMNS, read_csv, read_jsonl, write_csv  # noqa: E402

TOOL_NAMES = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini", "copilot": "Copilot"}
# Text that must not appear in a published reply.
FORBIDDEN = [r"favicons", r"utm_source", r"【", r"uptodate", r"wolters", r"don['’]t have your name"]


def reply_text(blinded_file):
    return blinded_file.read_text(encoding="utf-8").split("## Response\n\n", 1)[1].strip()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--study", required=True, choices=["A", "B"])
    p.add_argument("--out", required=True, help="the study's output folder (blind key, blinded replies, ratings)")
    p.add_argument("--rubric", required=True)
    p.add_argument("--raters", required=True, help="comma-separated rating sheets to include, e.g. ai-judge,pharm1-page")
    p.add_argument("--rater-labels", default="", help="neutral label per sheet, e.g. pharm1-page=author (default: the sheet name)")
    p.add_argument("--forbid", default="", help="extra comma-separated words that must not appear in any reply, e.g. a first name")
    p.add_argument("--judge-label", default="", help="name to publish for the judge model (default: as recorded)")
    p.add_argument("--dest", required=True)
    args = p.parse_args()

    out = Path(args.out)
    key = {k["code"]: k for k in read_csv(out / "blind_key.csv") if k["model"] in TOOL_NAMES}
    labels = dict(part.split("=") for part in args.rater_labels.split(",") if part)
    forbidden = FORBIDDEN + [re.escape(w.strip()) for w in args.forbid.split(",") if w.strip()]

    replies = []
    for code, k in sorted(key.items()):
        text = reply_text(out / "blinded" / f"{code}.md")
        for pattern in forbidden:
            if re.search(pattern, text, re.I):
                sys.exit(f"{code}: reply still contains text matching {pattern!r}. Clean it before exporting.")
        replies.append({
            "code": code, "study": args.study, "tool": TOOL_NAMES[k["model"]], "model_version": k["model_id"],
            "round": k["vignette_id"], "is_control": k["is_control"], "repeat": k["rep"], "reply": text,
        })

    verdicts = []
    for d in read_jsonl(out / "judge_details.jsonl"):
        if d["items"] and d["code"] in key:
            for item_id, v in d["items"].items():
                verdicts.append({"code": d["code"], "item": item_id, "met": "yes" if v["met"] else "no",
                                 "quote": v["quote"] if v["met"] else "", "judge": args.judge_label or d["judge_model"]})

    scores = []
    for sheet in (s.strip() for s in args.raters.split(",")):
        for row in read_csv(out / "ratings" / f"{sheet}.csv"):
            if row["code"] in key and row["answered_0_2"].strip():
                scores.append({"code": row["code"], "rater": labels.get(sheet, sheet),
                               **{c: row[c] for c in SCORE_COLUMNS if c != "notes"}})

    items = [{"item": i["id"], "role": i["role"], "rounds": " ".join(i["applies_to"]), "wording": i["text"]}
             for i in json.loads(Path(args.rubric).read_text(encoding="utf-8"))["items"]]

    dest = Path(args.dest) / f"study-{args.study.lower()}"
    dest.mkdir(parents=True, exist_ok=True)
    write_csv(dest / "replies.csv", replies, list(replies[0]))
    write_csv(dest / "verdicts.csv", verdicts, ["code", "item", "met", "quote", "judge"])
    write_csv(dest / "scores.csv", scores, ["code", "rater", *[c for c in SCORE_COLUMNS if c != "notes"]])
    write_csv(dest / "items.csv", items, ["item", "role", "rounds", "wording"])
    print(f"study {args.study}: {len(replies)} replies, {len(verdicts)} judge verdicts, {len(scores)} score rows -> {dest}")


if __name__ == "__main__":
    csv.field_size_limit(10_000_000)
    main()
