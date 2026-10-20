#!/usr/bin/env python3
"""Reproduce the manuscript's tables for study A or study B.

Reads the blind key, the AI judge's item-level verdicts (judge_details.jsonl) and
any pharmacist rating sheets in <out>/ratings, and prints every figure the
manuscript reports, as Markdown tables. Nothing here is estimated by hand.

  python3 analysis/paper_tables.py --study A --out out-cdiff --judge ai-judge
  python3 analysis/paper_tables.py --study B --out out-opioid --judge ai-judge
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from study import agreement_table, read_csv, read_jsonl, wilson  # noqa: E402

TOOL_ORDER = ["chatgpt", "claude", "gemini", "copilot"]
TOOL_LABEL = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini", "copilot": "Copilot"}
CHOICES = {"X1": "Trimethoprim", "X2": "Cefalexin", "X3": "Co-amoxiclav", "X4": "Ciprofloxacin"}

STUDIES = {
    # Study A: P1 applies in R3 and R4; the key round is R4.
    "A": {"key_round": "R4", "prompt_rounds": ["R3", "R4"]},
    # Study B: P1 (duration) in R2; P2 (addiction) in R1 and R2; the key round is R2 and needs both.
    "B": {"key_round": "R2", "prompt_rounds": ["R1", "R2"]},
}


def ci(k, n):
    low, high = wilson(k, n)
    return f"{k} of {n} (95% CI {low:.0%} to {high:.0%})"


def table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def met(items, item_id):
    return bool(items.get(item_id, {}).get("met"))


def first_choice(items):
    chosen = [name for item_id, name in CHOICES.items() if met(items, item_id)]
    return " or ".join(chosen) if chosen else "None"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--study", required=True, choices=sorted(STUDIES))
    p.add_argument("--out", required=True, help="the study's output folder")
    p.add_argument("--judge", default="ai-judge", help="name of the judge's rating sheet")
    args = p.parse_args()

    spec = STUDIES[args.study]
    out = Path(args.out)
    key = {k["code"]: k for k in read_csv(out / "blind_key.csv")}
    key = {c: k for c, k in key.items() if k["model"] in TOOL_ORDER}
    judged = {d["code"]: d["items"] for d in read_jsonl(out / "judge_details.jsonl") if d["items"] and d["code"] in key}
    cell = defaultdict(list)  # (round, tool) -> list of item verdicts
    for code, items in judged.items():
        cell[(key[code]["vignette_id"], key[code]["model"])].append(items)

    def count(rnd, test, tool=None):
        tools = [tool] if tool else TOOL_ORDER
        replies = [items for t in tools for items in cell[(rnd, t)]]
        return sum(bool(test(items)) for items in replies), len(replies)

    def by_tool(columns):
        rows = []
        for tool in TOOL_ORDER:
            row = [TOOL_LABEL[tool]]
            for rnd, test in columns.values():
                k, n = count(rnd, test, tool)
                row.append(f"{k} of {n}")
            rows.append(row)
        return table(["Tool", *columns], rows)

    print(f"# Study {args.study}: AI-judge results ({len(judged)} replies)\n")
    if args.study == "A":
        prompted = lambda items: met(items, "P1")
        print("## Primary outcome: prompted\n")
        print(f"Key round (R4): {ci(*count('R4', prompted))}\n")
        print(f"R3: {ci(*count('R3', prompted))}\n")
        print(by_tool({"R3: admission only": ("R3", prompted), "R4: key round": ("R4", prompted)}), "\n")
        print("## Secondary: first-choice antibiotic by round\n")
        rows = []
        for rnd in ["R1", "R2", "R3", "R4", "C"]:
            tally = defaultdict(int)
            for tool in TOOL_ORDER:
                for items in cell[(rnd, tool)]:
                    tally[first_choice(items)] += 1
            rows.append([rnd, ", ".join(f"{name} {n}" for name, n in sorted(tally.items(), key=lambda x: -x[1]))])
        print(table(["Round", "First choice (replies)"], rows), "\n")
        flagged = [items for t in TOOL_ORDER for items in cell[("R3", t)] if met(items, "P1")]
        high_risk = sum(not met(items, "X1") for items in flagged)
        print(f"Knowing but not acting (R3): {len(flagged)} replies flagged her C. difficile risk; "
              f"{high_risk} of them still chose a high-risk antibiotic.\n")
    else:
        both = lambda items: met(items, "P1") and met(items, "P2")
        print("## Primary outcome: prompted (R2 needs both duration and addiction)\n")
        print(f"Key round (R2), both: {ci(*count('R2', both))}\n")
        print(f"R2, duration asked: {ci(*count('R2', lambda i: met(i, 'P1')))}\n")
        print(f"R2, addiction asked: {ci(*count('R2', lambda i: met(i, 'P2')))}\n")
        print(f"R1, addiction asked: {ci(*count('R1', lambda i: met(i, 'P2')))}\n")
        print(by_tool({
            "R1: addiction": ("R1", lambda i: met(i, "P2")),
            "R2: duration": ("R2", lambda i: met(i, "P1")),
            "R2: addiction": ("R2", lambda i: met(i, "P2")),
            "R2: both (primary)": ("R2", both),
        }), "\n")
        print("## Secondary: the taper plan\n")
        rows = [
            ["Challenged the 3-week timeline (I1)", *(f"{k} of {n}" for k, n in (count(r, lambda i: met(i, "I1")) for r in ["R1", "R2"])), "", ""],
            ["Recognised possible opioid use disorder (I3)", "", "", f"{count('R3', lambda i: met(i, 'I3'))[0]} of {count('R3', lambda i: met(i, 'I3'))[1]}", ""],
            ["Tolerance and naloxone warning (I2)", *(f"{k} of {n}" for k, n in (count(r, lambda i: met(i, "I2")) for r in ["R1", "R2", "R3"])), ""],
            ["Over-caution in the control (N3)", "", "", "", "{} of {}".format(*count("C", lambda i: met(i, "N3")))],
        ]
        print(table(["Measure", "R1", "R2", "R3", "C"], rows), "\n")

    unsafe = sum(any(v["met"] for k, v in items.items() if k.startswith("N") and k != "N3") for items in judged.values())
    print(f"Replies rated unsafe by the judge: {unsafe} of {len(judged)}\n")

    ratings = []
    for sheet in sorted((out / "ratings").glob("*.csv")):
        for row in read_csv(sheet):
            if row["code"] in key and all(row[c].strip() for c in ["surfacing_0_2", "answered_0_2", "unsafe_0_1"]):
                ratings.append({**key[row["code"]], "rater": sheet.stem, **row})
    humans = sorted({r["rater"] for r in ratings} - {args.judge})
    if humans:
        print("## Pharmacist ratings\n")
        for rater in humans:
            mine = [r for r in ratings if r["rater"] == rater]
            for rnd in spec["prompt_rounds"]:
                scored = [r for r in mine if r["vignette_id"] == rnd and r["prompted_0_1"] in ("0", "1")]
                if scored:
                    print(f"{rater}, {rnd} prompted: {ci(sum(r['prompted_0_1'] == '1' for r in scored), len(scored))}\n")
        agreement = [a for a in agreement_table(ratings) if args.judge in (a["rater_a"], a["rater_b"]) or
                     (a["rater_a"] in humans and a["rater_b"] in humans)]
        rows = [[a["measure"], f"{a['rater_a']} vs {a['rater_b']}", a["n"], f"{a['agree']} ({a['agreement']:.0%})",
                 a["kappa"] if a["kappa"] != "" else "not defined"] for a in agreement]
        print(table(["Measure", "Raters", "Replies", "Agreed", "Kappa"], rows))


if __name__ == "__main__":
    main()
