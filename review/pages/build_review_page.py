#!/usr/bin/env python3
"""Fill a rater review page with one study's blinded replies.

Reads the blinded R###.md files written by `study.py blind`, checks that no app
name or fingerprint survived the cleaning, and writes a single HTML page ready
to publish as an artifact.

  python3 review/pages/build_review_page.py \
      --template review/pages/study-b-review-template.html \
      --vignettes studies/opioid-taper/vignettes.json \
      --blinded out-opioid/blinded --out study-b-review.html
"""

import argparse
import json
import re
from pathlib import Path

LEAK_WORDS = ["chatgpt", "openai", "claude", "anthropic", "gemini", "google", "copilot", "microsoft",
              "favicons", "utm_source"]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--template", required=True)
    p.add_argument("--vignettes", required=True)
    p.add_argument("--blinded", required=True, help="folder of blinded R###.md files")
    p.add_argument("--out", required=True)
    p.add_argument("--names", default="", help="comma-separated names that must not appear (e.g. a greeting)")
    p.add_argument("--codes", default="", help="file listing the reply codes to include, one per line (default: all)")
    args = p.parse_args()

    prompts = {v["id"]: v["prompt"] for v in json.loads(Path(args.vignettes).read_text(encoding="utf-8"))}
    replies = []
    for f in sorted(Path(args.blinded).glob("R*.md")):
        m = re.match(r"# Response (R\d{3})\n\n## Case (\w+)\n\n.*?\n\n## Response\n\n(.*)\Z", f.read_text(encoding="utf-8"), re.S)
        if not m:
            raise SystemExit(f"{f} is not a blinded reply file")
        replies.append({"code": m.group(1), "round": m.group(2), "text": m.group(3).strip()})
    if args.codes:
        keep = set(Path(args.codes).read_text(encoding="utf-8").split())
        missing = keep - {r["code"] for r in replies}
        if missing:
            raise SystemExit(f"codes not in the blinded folder: {sorted(missing)}")
        replies = [r for r in replies if r["code"] in keep]
    if not replies:
        raise SystemExit("no blinded replies found")
    unknown = {r["round"] for r in replies} - set(prompts)
    if unknown:
        raise SystemExit(f"replies for rounds not in the vignettes: {sorted(unknown)}")

    blob = json.dumps({"prompts": prompts, "replies": replies}, ensure_ascii=False)
    lowered = blob.lower()
    words = LEAK_WORDS + [n.strip().lower() for n in args.names.split(",") if n.strip()]
    leaks = [w for w in words if w in lowered]
    if leaks:
        raise SystemExit(f"identifying words still in the replies: {leaks}. Clean them before publishing.")

    template = Path(args.template).read_text(encoding="utf-8")
    if template.count("/*DATA*/") != 1:
        raise SystemExit("template must contain the /*DATA*/ placeholder exactly once")
    Path(args.out).write_text(template.replace("/*DATA*/", blob.replace("</", "<\\/")), encoding="utf-8")
    counts = {}
    for r in replies:
        counts[r["round"]] = counts.get(r["round"], 0) + 1
    print(f"wrote {args.out}: {len(replies)} replies {counts}")


if __name__ == "__main__":
    main()
