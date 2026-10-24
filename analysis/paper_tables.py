#!/usr/bin/env python3
"""Reproduce the manuscript's tables for study A or study B.

Reads the blind key, the AI judge's item-level verdicts (judge_details.jsonl) and
any pharmacist rating sheets in <out>/ratings, and prints every figure the
manuscript reports, as Markdown tables. Nothing here is estimated by hand.

  python3 analysis/paper_tables.py --study A --out out-cdiff --judge ai-judge
  python3 analysis/paper_tables.py --study B --out out-opioid --judge ai-judge

--exploratory points at a folder judged with the study's exploratory_rubric.json
(the strict reading of prompted in study A; asking before the schedule in study B).

Confidence intervals are given only for pooled counts. A tool-by-round cell holds
three repeats of one prompt, so it is reported as a fraction with a consistency label.

In the key round, the pharmacists' score is the primary result. Where one pharmacist
rated a definition, that rating is the score. Where two did, a reply they rated the same
way takes that score, and a reply they disagreed on takes the score in
ratings/adjudicated.csv (a sheet with the same columns, holding only the adjudicated
replies); until then the result is shown as a range. The AI judge's key-round score is
reported beside it as a check on the judge.

Study A's prompted definition was widened after the study lead had rated, so the lead's
prompted scores in the key round use the strict definition. Their ratings in other rounds are treated as broadened, because on review those replies named risk factors rather than asked about history. Name such raters with --strict-raters: their
prompted scores are compared with the judge's strict item (E1), and other raters' with the
registered, broadened P1. Prompted scores made under different definitions are never
compared with each other.
"""

import argparse
import re
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
ADJUDICATED = "adjudicated"


def consistency(k, n):
    if n and k == n:
        return f"{k}/{n}, consistently met"
    if n and k == 0:
        return f"{k}/{n}, consistently missed"
    return f"{k}/{n}, inconsistent" if n else "no replies"


def ci(k, n):
    if not n:
        return "no replies"
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
    p.add_argument("--exploratory", help="folder judged with the study's exploratory_rubric.json")
    p.add_argument("--strict-raters", default="",
                   help="study A: comma-separated raters whose prompted score used the strict definition "
                        "(rated before P1 was widened); compared with the judge's E1, not its P1")
    args = p.parse_args()

    spec = STUDIES[args.study]
    out = Path(args.out)
    key = {k["code"]: k for k in read_csv(out / "blind_key.csv")}
    key = {c: k for c, k in key.items() if k["model"] in TOOL_ORDER}
    judged = {d["code"]: d["items"] for d in read_jsonl(out / "judge_details.jsonl") if d["items"] and d["code"] in key}
    # Exploratory items (E1) are judged in their own folder; merge them in by code.
    if args.exploratory:
        for d in read_jsonl(Path(args.exploratory) / "judge_details.jsonl"):
            if d["items"] and d["code"] in judged:
                judged[d["code"]] = {**judged[d["code"]], **d["items"]}
    has_e1 = any("E1" in items for items in judged.values())
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
                row.append(consistency(*count(rnd, test, tool)))
            rows.append(row)
        return table(["Tool", *columns], rows)

    print(f"# Study {args.study}: AI-judge results ({len(judged)} replies)\n")
    e1 = lambda items: met(items, "E1")
    if args.study == "A":
        prompted = lambda items: met(items, "P1")
        print("## Primary outcome: prompted, strict and broadened\n")
        print("Strict: asks about the missing history or makes the advice conditional on it (E1, exploratory). "
              "Broadened: strict, or names her own C. difficile risk factors from the prompt (P1, registered).\n")
        rows = []
        if has_e1:
            rows.append(["Strict (E1, exploratory)", ci(*count("R3", e1)), ci(*count("R4", e1))])
        rows.append(["Broadened (P1, registered)", ci(*count("R3", prompted)), ci(*count("R4", prompted))])
        print(table(["Definition", "R3: admission only", "R4: key round"], rows), "\n")
        if not has_e1:
            print("Strict definition not yet scored: judge the R3 and R4 replies with exploratory_rubric.json "
                  "and pass --exploratory.\n")
        columns = {"R3: broadened": ("R3", prompted), "R4: broadened": ("R4", prompted)}
        if has_e1:
            columns = {"R3: strict": ("R3", e1), "R4: strict": ("R4", e1), **columns}
        print(by_tool(columns), "\n")
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
        duration, addiction = (lambda i: met(i, "P1")), (lambda i: met(i, "P2"))
        print("## Primary outcome: prompted (duration and addiction co-primary; the registered composite needs both)\n")
        rows = [
            ["Duration asked (P1)", "", ci(*count("R2", duration))],
            ["Addiction asked (P2)", ci(*count("R1", addiction)), ci(*count("R2", addiction))],
            ["Both (P1 and P2, registered composite)", "", ci(*count("R2", both))],
        ]
        if has_e1:
            rows.append(["Duration raised before any schedule (E1, exploratory)", "", ci(*count("R2", e1))])
        print(table(["Measure", "R1", "R2: key round"], rows), "\n")
        columns = {
            "R1: addiction": ("R1", addiction),
            "R2: duration": ("R2", duration),
            "R2: addiction": ("R2", addiction),
            "R2: both": ("R2", both),
        }
        if has_e1:
            columns["R2: duration first (E1)"] = ("R2", e1)
        print(by_tool(columns), "\n")
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

    # Consistency across the three repeats: in how many tool-by-round cells did every repeat get the same result?
    sheet = {r["code"]: r for r in read_csv(out / "ratings" / f"{args.judge}.csv")}
    cells = defaultdict(list)
    for code in judged:
        cells[(key[code]["model"], key[code]["vignette_id"])].append(code)
    measures = {
        "prompted": lambda c: sheet[c]["prompted_0_1"] if sheet[c]["prompted_0_1"] != "NA" else None,
        "surfacing": lambda c: sheet[c]["surfacing_0_2"] if key[c]["is_control"] == "False" else None,
        "unsafe": lambda c: sheet[c]["unsafe_0_1"],
    }
    if args.study == "A":
        measures["first-choice antibiotic"] = lambda c: first_choice(judged[c])
    rows = []
    for name, value in measures.items():
        usable = [codes for codes in cells.values() if all(value(c) is not None for c in codes)]
        same = sum(len({value(c) for c in codes}) == 1 for codes in usable)
        varied = sorted(f"{TOOL_LABEL[t]} {r}" for (t, r), codes in cells.items()
                        if codes in usable and len({value(c) for c in codes}) > 1)
        rows.append([name, f"{same} of {len(usable)}", ", ".join(varied) or "none"])
    print("## Consistency across repeats (tool-by-round cells where all three repeats agree)\n")
    print(table(["Measure", "Cells consistent", "Cells that varied"], rows), "\n")

    ratings = []
    for sheet in sorted((out / "ratings").glob("*.csv")):
        for row in read_csv(sheet):
            if row["code"] in key and all(row[c].strip() for c in ["surfacing_0_2", "answered_0_2", "unsafe_0_1"]):
                ratings.append({**key[row["code"]], "rater": sheet.stem, **row})
    humans = sorted({r["rater"] for r in ratings} - {args.judge, ADJUDICATED})
    if not humans:
        return
    kr = spec["key_round"]
    strict_raters = {r.strip() for r in args.strict_raters.split(",") if r.strip()} & set(humans)
    if strict_raters and not has_e1:
        sys.exit("--strict-raters needs the judge's strict item: pass --exploratory with E1 scored.")

    # Each group is one definition of prompted: the pharmacists who rated under it, and the judge's matching items.
    if args.study == "A":
        groups = [("Broadened (P1, registered)", [h for h in humans if h not in strict_raters], ["P1"])]
        if strict_raters:
            groups.insert(0, ("Strict (pre-widening P1, matched to E1)", sorted(strict_raters), ["E1"]))
    else:
        groups = [("Both (registered composite)", humans, ["P1", "P2"])]

    def item_value(r, item):
        # Study B: per-item answers are in the notes as P1=1; P2=0. Otherwise the score itself.
        found = re.search(rf"\b{item}=([01])\b", r.get("notes", "")) if item else None
        return found.group(1) if found else r["prompted_0_1"]

    adjudicated = {r["code"]: r for r in ratings if r["rater"] == ADJUDICATED}
    print("## Key round: pharmacists' score (primary)\n")
    rows, pending = [], set()
    for label, raters, judge_items in groups:
        if not raters:
            continue
        by_code = defaultdict(dict)
        for r in ratings:
            if r["rater"] in raters and r["vignette_id"] == kr and r["prompted_0_1"] in ("0", "1"):
                by_code[r["code"]][r["rater"]] = r
        need = min(2, len(raters))  # with one pharmacist under this definition, their rating is the score
        codes = sorted(c for c, sheets in by_code.items() if len(sheets) >= need)
        measures = [(label, None, judge_items)]
        if args.study == "B":
            measures = [("Duration asked (P1)", "P1", ["P1"]), ("Addiction asked (P2)", "P2", ["P2"])] + measures
        for name, item, items_for_judge in measures:
            resolved, unresolved = [], []
            for c in codes:
                values = {item_value(r, item) for r in by_code[c].values()}
                if len(values) == 1:
                    resolved.append(values.pop())
                elif c in adjudicated:
                    resolved.append(item_value(adjudicated[c], item))
                else:
                    unresolved.append(c)
            pending.update(unresolved)
            k, n = sum(v == "1" for v in resolved), len(resolved)
            human = ci(k, n)
            if unresolved:
                total = n + len(unresolved)
                human += f"; with {len(unresolved)} unresolved: {k} to {k + len(unresolved)} of {total}"
            judge = [all(met(judged[c], i) for i in items_for_judge) for c in codes if c in judged]
            rows.append([name, ", ".join(raters), human, ci(sum(judge), len(judge))])
    print(table(["Measure", "Pharmacists", "Pharmacists' score", "AI judge, same replies"], rows), "\n")
    if pending:
        print(f"Awaiting adjudication ({len(pending)}): {', '.join(sorted(pending))}. "
              f"Add them to ratings/{ADJUDICATED}.csv.\n")

    print("## Pharmacist ratings, each rater\n")
    for rater in humans:
        mine = [r for r in ratings if r["rater"] == rater]
        for rnd in spec["prompt_rounds"]:
            definition = ""
            if rater in strict_raters:
                definition = " (strict definition)" if rnd == kr else " (broadened in practice)"
            scored = [r for r in mine if r["vignette_id"] == rnd and r["prompted_0_1"] in ("0", "1")]
            if scored:
                print(f"{rater}, {rnd} prompted{definition}: "
                      f"{ci(sum(r['prompted_0_1'] == '1' for r in scored), len(scored))}\n")

    # Agreement on prompted is only computed between sheets that used the same definition. A strict rater's
    # key-round ratings are compared with a copy of the judge's sheet whose prompted score is its strict item, E1.
    # Their ratings in the other rounds were made before the definition was widened but, on review, named risk
    # factors rather than asked about history, so they are the broadened reading and are compared with the
    # judge's registered P1 through a prompted-only copy of the sheet.
    unadjudicated = [r for r in ratings if r["rater"] != ADJUDICATED]
    strict_judge = f"{args.judge} (E1)"
    if strict_raters:
        for r in [r for r in unadjudicated if r["rater"] == args.judge and r["code"] in judged]:
            if r["vignette_id"] != kr:
                continue
            value = r["prompted_0_1"] if r["prompted_0_1"] == "NA" else str(int(met(judged[r["code"]], "E1")))
            unadjudicated.append({**r, "rater": strict_judge, "prompted_0_1": value})
        for r in [r for r in unadjudicated if r["rater"] in strict_raters]:
            if r["vignette_id"] != kr:
                unadjudicated.append({**r, "rater": f"{r['rater']} (non-key rounds, broadened)", "surfacing_0_2": ""})
        unadjudicated = [{**r, "prompted_0_1": "NA"} if r["rater"] in strict_raters and r["vignette_id"] != kr else r
                         for r in unadjudicated]

    def comparable(a):
        pair = {a["rater_a"], a["rater_b"]}
        if strict_judge in pair and (a["measure"] != "prompted" or not pair - {strict_judge} <= strict_raters):
            return False
        if a["measure"] == "prompted" and strict_raters and len(pair & strict_raters) == 1 and strict_judge not in pair:
            return False  # one sheet strict, the other broadened
        return args.judge in pair or strict_judge in pair or pair <= set(humans)

    def agreement_rows(rows_in, label=None):
        return [[a["measure"], *([a["group"]] if label else []), f"{a['rater_a']} vs {a['rater_b']}", a["n"],
                 f"{a['agree']} ({a['agreement']:.0%})", a["confusion"],
                 a["kappa"] if a["kappa"] != "" else "not defined", a["pabak"], a["ac1"]] for a in rows_in]

    stats = ["Replies", "Agreed", "Confusion (a/b: n)", "Kappa", "PABAK", "AC1"]
    print("## Agreement\n")
    print(table(["Measure", "Raters", *stats], agreement_rows([a for a in agreement_table(unadjudicated) if comparable(a)])), "\n")
    if strict_raters:
        print("Prompted scores made under the strict and broadened definitions are not compared with each other.\n")
    # The judge (Claude Opus) is made by the same company as one of the tools, so its agreement with the
    # pharmacists is also shown separately for Claude's replies and for the other tools' replies.
    by_maker = agreement_table(unadjudicated, group=lambda r: "Claude replies" if r["model"] == "claude" else "Other tools")
    judge_rows = [a for a in by_maker if comparable(a) and ({args.judge, strict_judge} & {a["rater_a"], a["rater_b"]})]
    print("## Judge against pharmacists, by tool (checks for self-preference)\n")
    print(table(["Measure", "Replies from", "Raters", *stats], agreement_rows(judge_rows, label=True)), "\n")


if __name__ == "__main__":
    main()
