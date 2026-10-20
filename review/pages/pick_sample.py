#!/usr/bin/env python3
"""Pick the replies pharmacists rate to check the AI judge.

Every reply in the key round, plus a fixed number from each other round, spread
across tools where possible. Uses the blind key, so run it as the analyst; the
output lists codes only.

  python3 review/pages/pick_sample.py --key out-cdiff/blind_key.csv --key-round R4 \
      --per-round R1=2,R2=2,R3=3,C=3 --out out-cdiff/validation_sample.txt
"""

import argparse
import csv
import random
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--key", required=True, help="blind_key.csv from study.py blind")
    p.add_argument("--key-round", required=True, help="round whose replies are all included")
    p.add_argument("--per-round", required=True, help="other rounds and how many to take from each, e.g. R1=2,C=3")
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    with open(args.key, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rng = random.Random(args.seed)
    picked = [r["code"] for r in rows if r["vignette_id"] == args.key_round]
    counts = {}
    for part in args.per_round.split(","):
        rnd, n = part.split("=")
        pool = [r for r in rows if r["vignette_id"] == rnd]
        rng.shuffle(pool)
        # Take one per tool in turn, so the sample isn't dominated by one tool.
        by_tool = {}
        for r in pool:
            by_tool.setdefault(r["model"], []).append(r)
        take = []
        while len(take) < int(n) and any(by_tool.values()):
            # Prefer the tools picked least so far across all rounds, ties broken at random.
            order = sorted((t for t in by_tool if by_tool[t]), key=lambda t: (counts.get(t, 0), rng.random()))
            r = by_tool[order[0]].pop()
            counts[r["model"]] = counts.get(r["model"], 0) + 1
            take.append(r)
        picked += [r["code"] for r in take]
    Path(args.out).write_text("\n".join(sorted(picked)) + "\n", encoding="utf-8")
    print(f"{len(picked)} codes written to {args.out}")


if __name__ == "__main__":
    main()
