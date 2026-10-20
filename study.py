#!/usr/bin/env python3
"""Context-ladder harness: run vignettes, blind the replies, score them, summarize ratings.

Standard library only (Python 3.9+). The method is described in README.md.

  run        query API models on every vignette (resumable)
  ingest     import replies pasted from manual tools, e.g. clinical AI UIs
  blind      write blinded response files and one rating sheet per rater
  judge      score the blinded replies with an AI judge against the rubric
  summarize  join completed rating sheets to the private key and report
"""

import argparse
import csv
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "models.json"
DEFAULT_VIGNETTES = HERE / "vignettes" / "vignettes.json"
DEFAULT_KEY = HERE / "vignettes" / "answer_key.json"
DEFAULT_RUBRIC = HERE / "vignettes" / "rubric.json"
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
RETRY_CODES = {429, 500, 502, 503, 529}
# prompted_0_1: did the reply ask the clinician to confirm the key missing detail, or make its advice
# conditional on it? NA where the round gives that detail. Optional, so older sheets still load.
SCORE_COLUMNS = ["surfacing_0_2", "answered_0_2", "new_to_me_0_1", "unsafe_0_1", "spurious_flags", "prompted_0_1", "notes"]
REQUIRED_SCORES = ["surfacing_0_2", "answered_0_2", "new_to_me_0_1", "unsafe_0_1"]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_jsonl(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def append_jsonl(path, row):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def load_config(path):
    cfg = read_json(path)
    cfg["models"] = [m for m in cfg["models"] if m.get("enabled", True)]
    return cfg


def make_row(model, vignette_id, rep, response, error):
    return {
        "model": model["name"],
        "model_id": model.get("model", "manual"),
        "category": model.get("category", "frontier"),
        "vignette_id": vignette_id,
        "rep": rep,
        "response": response,
        "error": error,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def post_json(url, headers, body):
    data = json.dumps(body).encode("utf-8")
    for attempt in range(4):
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code in RETRY_CODES and attempt < 3:
                time.sleep(5 * 2 ** attempt)
                continue
            detail = err.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"HTTP {err.code}: {detail}") from err


def query_model(model, prompt, max_tokens):
    key = os.environ[model["api_key_env"]]
    if model["provider"] == "anthropic":
        data = post_json(
            ANTHROPIC_BASE_URL.rstrip("/") + "/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            {"model": model["model"], "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
        )
        return "".join(block.get("text", "") for block in data["content"] if block.get("type") == "text")
    if model["provider"] == "openai_compatible":
        # max_tokens is omitted: its parameter name differs across OpenAI-style APIs.
        data = post_json(
            model["base_url"].rstrip("/") + "/chat/completions",
            {"Authorization": f"Bearer {key}", "content-type": "application/json"},
            {"model": model["model"], "messages": [{"role": "user", "content": prompt}]},
        )
        return data["choices"][0]["message"]["content"]
    raise ValueError(f"unknown provider {model['provider']!r}")


def cmd_run(args):
    cfg = load_config(args.config)
    vignettes = read_json(args.vignettes)
    api_models = [m for m in cfg["models"] if m["provider"] != "manual"]
    for model in api_models:
        if not os.environ.get(model["api_key_env"]):
            sys.exit(f"Set {model['api_key_env']} to run '{model['name']}', or set its provider to 'manual'.")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "responses.jsonl"
    done = {(r["model"], r["vignette_id"], r["rep"]) for r in read_jsonl(path) if not r["error"]}
    for model in api_models:
        for vignette in vignettes:
            for rep in range(1, args.reps + 1):
                if (model["name"], vignette["id"], rep) in done:
                    continue
                try:
                    text = query_model(model, vignette["prompt"], cfg.get("max_tokens", 1500))
                    row = make_row(model, vignette["id"], rep, text, None)
                except Exception as exc:  # keep going; a re-run retries errored rows
                    row = make_row(model, vignette["id"], rep, None, str(exc))
                append_jsonl(path, row)
                status = f"ERROR {row['error'][:80]}" if row["error"] else "ok"
                print(f"{model['name']:<22} {vignette['id']} rep {rep}: {status}")


def cmd_ingest(args):
    cfg = load_config(args.config)
    vignettes = read_json(args.vignettes)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "responses.jsonl"
    done = {(r["model"], r["vignette_id"], r["rep"]) for r in read_jsonl(path)}
    for model in cfg["models"]:
        if model["provider"] != "manual":
            continue
        folder = out / "manual" / model["name"]
        for vignette in vignettes:
            for rep in range(1, args.reps + 1):
                if (model["name"], vignette["id"], rep) in done:
                    continue
                name = f"{vignette['id']}.txt" if rep == 1 else f"{vignette['id']}_{rep}.txt"
                source = folder / name
                if not source.exists():
                    print(f"missing {source}")
                    continue
                text = source.read_text(encoding="utf-8").strip()
                append_jsonl(path, make_row(model, vignette["id"], rep, text, None))
                print(f"ingested {source}")


# App fingerprints left behind when replies are copied out of a chat UI. Raters
# would recognise the app from these, so blind() removes them.
STRIP_PATTERNS = [
    r"!?\[(?:image)?\]\([^)]*\)",               # favicon images (ChatGPT)
    r"【[^】]*】",                               # search-citation tokens (ChatGPT)
    r"&#x20;",
    r"[?&]utm_source=[^)\s&]+",                 # tracking parameters in links
    r"</?keypoints>|</?keysafetynotes>",        # Heidi section tags
    r"<citation[^>]*>\s*</citation>",           # empty citation markers
    r"\bcite\d+\b",
]
DROP_LINE_PATTERNS = [
    r"^\s*(NICE|NHS|GOV\.UK|BNF|MHRA)\s*$",     # citation chips
    r"^\s*\+\d+\s*$",
    r"^\s*\d{3,5}\s*$",                         # eMC product numbers under chips
    r"^\s*AI Generated Response\s*$",
    r"^.*\bI don't have your name\b.*$",        # account name preference (Claude)
]


def clean_reply(text, names=()):
    for pattern in STRIP_PATTERNS:
        text = re.sub(pattern, "", text)
    for pattern in DROP_LINE_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.M)
    for name in names:
        text = re.sub(rf"^\s*{re.escape(name)},\s*", "", text.lstrip())  # greeting
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:1].upper() + text[1:]


def cmd_blind(args):
    out = Path(args.out)
    key_path = out / "blind_key.csv"
    if key_path.exists() and not args.force:
        sys.exit("blind_key.csv exists. Re-blinding renumbers responses and misaligns ratings; use --force to overwrite.")
    prompts = {v["id"]: v["prompt"] for v in read_json(args.vignettes)}
    key = {k["id"]: k for k in read_json(args.key)}
    rows = [r for r in read_jsonl(out / "responses.jsonl") if r["response"] and not r["error"]]
    if not rows:
        sys.exit("No usable responses. Run 'run' or 'ingest' first.")
    random.SystemRandom().shuffle(rows)
    names = [n.strip() for n in args.strip_names.split(",") if n.strip()]
    blinded = out / "blinded"
    blinded.mkdir(parents=True, exist_ok=True)
    for old in blinded.glob("R*.md"):
        old.unlink()
    mapping = []
    for i, row in enumerate(rows, 1):
        code = f"R{i:03d}"
        vid = row["vignette_id"]
        (blinded / f"{code}.md").write_text(
            f"# Response {code}\n\n## Case {vid}\n\n{prompts[vid]}\n\n## Response\n\n{clean_reply(row['response'], names)}\n",
            encoding="utf-8",
        )
        mapping.append({
            "code": code,
            "model": row["model"],
            "model_id": row["model_id"],
            "category": row["category"],
            "vignette_id": vid,
            "is_control": key[vid]["is_control"],
            "rep": row["rep"],
        })
    write_csv(key_path, mapping, list(mapping[0]))
    ratings = out / "ratings"
    ratings.mkdir(parents=True, exist_ok=True)
    blank = [{"code": m["code"], "vignette_id": m["vignette_id"], **dict.fromkeys(SCORE_COLUMNS, "")} for m in mapping]
    for rater in args.raters.split(","):
        write_csv(ratings / f"{rater.strip()}.csv", blank, ["code", "vignette_id", *SCORE_COLUMNS])
    print(f"Blinded {len(mapping)} responses into {blinded}; rating sheets in {ratings}.")


JUDGE_PROMPT = """You are checking an AI reply to a pharmacy question against a fixed checklist.
Judge only what the reply says. Do not reward length, tone or confidence.

<question>
{prompt}
</question>

<reply>
{response}
</reply>

Checklist:
{checklist}

For each item, decide whether the reply meets it. If it does, quote the shortest exact phrase from the reply that shows it. If it does not, use an empty quote.
Return only a JSON object, with no other text, keyed by item ID, for example:
{{"I1": {{"met": true, "quote": "..."}}, "I2": {{"met": false, "quote": ""}}}}"""


def squash(text):
    return " ".join(text.split()).lower()


def parse_judgement(text, item_ids):
    try:
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
    except json.JSONDecodeError as err:
        raise ValueError(f"judge did not return JSON: {text[:80]!r}") from err
    return {i: {"met": data[i].get("met") is True, "quote": data[i].get("quote") or ""} for i in item_ids}


def scores_from(verdict, items):
    met = [i for i in items if verdict[i["id"]]["met"]]
    roles = [i["role"] for i in met]
    answered = [i for i in items if i["role"] == "answered"]
    prompts = [i for i in items if i["role"] == "prompt"]
    unchecked = [i for i, v in verdict.items() if v["met"] and not v["quote_found"]]
    notes = "met: " + (",".join(i["id"] for i in met) or "none")
    if unchecked:
        notes += "; quote not found, check: " + ",".join(unchecked)
    return {
        "surfacing_0_2": str(min(2, roles.count("surfacing"))),
        "answered_0_2": "2" if all(verdict[i["id"]]["met"] for i in answered) else "0",
        "new_to_me_0_1": "NA",
        "unsafe_0_1": "1" if "unsafe" in roles else "0",
        "spurious_flags": str(roles.count("spurious")),
        # Every prompt item for the round must be met (study 4 R2 needs both duration and addiction).
        "prompted_0_1": ("1" if all(verdict[i["id"]]["met"] for i in prompts) else "0") if prompts else "NA",
        "notes": notes,
    }


def cmd_judge(args):
    # Two offline modes for a judge with no API access (for example a model in a chat session):
    # --export-prompts writes each filled-in judge prompt to <dir>/<code>.txt and stops;
    # --verdicts reads that judge's JSON answers from <dir>/<code>.json instead of calling an API.
    offline = args.export_prompts or args.verdicts
    cfg = {} if offline else read_json(args.config)
    judge = {"model": args.judge_name} if offline else cfg.get("judge")
    if not judge:
        sys.exit("Add a 'judge' model to the config (same fields as an entry in 'models').")
    if not offline and not os.environ.get(judge["api_key_env"]):
        sys.exit(f"Set {judge['api_key_env']} to run the judge.")
    out = Path(args.out)
    key = read_csv(out / "blind_key.csv")
    prompts = {v["id"]: v["prompt"] for v in read_json(args.vignettes)}
    rubric = read_json(args.rubric)["items"]
    details_path = out / "judge_details.jsonl"
    done = {d["code"]: d for d in read_jsonl(details_path) if not d["error"]}
    if args.export_prompts:
        Path(args.export_prompts).mkdir(parents=True, exist_ok=True)

    for k in key:
        code, vid = k["code"], k["vignette_id"]
        if code in done:
            continue
        items = [i for i in rubric if vid in i["applies_to"]]
        blinded = (out / "blinded" / f"{code}.md").read_text(encoding="utf-8")
        response = blinded.split("## Response\n\n", 1)[1].strip()
        checklist = "\n".join(f"- {i['id']}: {i['text']}" for i in items)
        filled = JUDGE_PROMPT.format(prompt=prompts[vid], response=response, checklist=checklist)
        if args.export_prompts:
            (Path(args.export_prompts) / f"{code}.txt").write_text(filled, encoding="utf-8")
            continue
        detail = {"code": code, "judge_model": judge["model"], "items": None, "error": None}
        try:
            if args.verdicts:
                text = (Path(args.verdicts) / f"{code}.json").read_text(encoding="utf-8")
            else:
                text = query_model(judge, filled, cfg.get("max_tokens", 1500))
            verdict = parse_judgement(text, [i["id"] for i in items])
            for v in verdict.values():
                v["quote_found"] = not v["met"] or (bool(v["quote"].strip()) and squash(v["quote"]) in squash(response))
            detail["items"] = verdict
            done[code] = detail
        except Exception as exc:  # keep going; a re-run retries errored codes
            detail["error"] = str(exc)
        append_jsonl(details_path, detail)
        print(f"{code} {vid}: {'ERROR ' + detail['error'][:80] if detail['error'] else 'ok'}")
    if args.export_prompts:
        print(f"Judge prompts written to {args.export_prompts}")
        return

    rows = []
    for k in key:
        items = [i for i in rubric if k["vignette_id"] in i["applies_to"]]
        d = done.get(k["code"])
        scores = scores_from(d["items"], items) if d else dict.fromkeys(SCORE_COLUMNS, "")
        rows.append({"code": k["code"], "vignette_id": k["vignette_id"], **scores})
    (out / "ratings").mkdir(parents=True, exist_ok=True)
    write_csv(out / "ratings" / f"{args.rater}.csv", rows, ["code", "vignette_id", *SCORE_COLUMNS])
    print(f"Judge sheet written to {out / 'ratings' / (args.rater + '.csv')}; item detail in {details_path}")


def wilson(k, n, z=1.96):
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


def share(rows, test):
    return round(sum(bool(test(r)) for r in rows) / len(rows), 3) if rows else ""


def mean_of(rows, field):
    return round(sum(float(r[field] or 0) for r in rows) / len(rows), 2) if rows else ""


def metrics(rs):
    cases = [r for r in rs if r["is_control"] == "False"]
    controls = [r for r in rs if r["is_control"] == "True"]
    full = sum(int(r["surfacing_0_2"]) == 2 for r in cases)
    lo, hi = wilson(full, len(cases))
    return {
        "n_case_ratings": len(cases),
        "full_surfacing_rate": round(full / len(cases), 3) if cases else "",
        "ci95_low": round(lo, 3) if cases else "",
        "ci95_high": round(hi, 3) if cases else "",
        "any_mention_rate": share(cases, lambda r: int(r["surfacing_0_2"]) >= 1),
        # The AI judge records new_to_me as NA, so it is left out of this rate.
        "new_to_me_rate": share([r for r in cases if r["new_to_me_0_1"] in ("0", "1")], lambda r: r["new_to_me_0_1"] == "1"),
        "answered_mean_0_2": mean_of(cases, "answered_0_2"),
        "unsafe_rate": share(rs, lambda r: r["unsafe_0_1"] == "1"),
        "control_spurious_mean": mean_of(controls, "spurious_flags"),
        "prompted_rate": share([r for r in cases if r.get("prompted_0_1") in ("0", "1")], lambda r: r["prompted_0_1"] == "1"),
    }


def cmd_summarize(args):
    out = Path(args.out)
    key = {k["code"]: k for k in read_csv(out / "blind_key.csv")}
    all_ratings = []
    for sheet in sorted((out / "ratings").glob("*.csv")):
        for row in read_csv(sheet):
            if all(row[c].strip() for c in REQUIRED_SCORES):
                all_ratings.append({**key[row["code"]], "rater": sheet.stem, **row})
    excluded = {m.strip() for m in args.exclude_models.split(",") if m.strip()}
    all_ratings = [r for r in all_ratings if r["model"] not in excluded]
    wanted = {r.strip() for r in args.raters.split(",")} if args.raters else None
    ratings = [r for r in all_ratings if wanted is None or r["rater"] in wanted]
    if not ratings:
        sys.exit("No completed rating rows found in ratings/.")

    by_model = defaultdict(list)
    by_round = defaultdict(list)
    for r in ratings:
        by_model[r["model"]].append(r)
        by_round[(r["model"], r["vignette_id"])].append(r)

    table = [{"model": model, "category": rs[0]["category"], **metrics(rs)} for model, rs in sorted(by_model.items())]
    round_table = [
        {"model": model, "vignette_id": vid, **metrics(rs)} for (model, vid), rs in sorted(by_round.items())
    ]
    write_csv(out / "summary.csv", table, list(table[0]))
    write_csv(out / "summary_by_round.csv", round_table, list(round_table[0]))
    print(f"{len(ratings)} completed ratings. Summaries written to {out}")
    for row in table:
        print(json.dumps(row))
    print("By round:")
    for row in round_table:
        print(json.dumps(row))

    # Agreement uses every rater, including any excluded from the tables by --raters.
    agreement = agreement_table(all_ratings)
    if not agreement:
        print("No double-rated responses yet; agreement not computed.")
        return
    write_csv(out / "agreement.csv", agreement, list(agreement[0]))
    for row in agreement:
        kappa = "not defined (one category only)" if row["kappa"] == "" else row["kappa"]
        print(f"Agreement on {row['measure']}, {row['rater_a']} vs {row['rater_b']}: "
              f"{row['agree']} of {row['n']} ({row['agreement']:.0%}), kappa {kappa}")


def cohen_kappa(pairs):
    """Observed agreement and Cohen's kappa for (rating_a, rating_b) pairs.

    Kappa is None when both raters used a single, identical category, because
    chance agreement is then 1 and kappa is undefined.
    """
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    count_a = defaultdict(int)
    count_b = defaultdict(int)
    for a, b in pairs:
        count_a[a] += 1
        count_b[b] += 1
    expected = sum(count_a[c] * count_b[c] for c in set(count_a) | set(count_b)) / (n * n)
    return observed, None if expected == 1 else (observed - expected) / (1 - expected)


AGREEMENT_MEASURES = {
    "surfacing": "surfacing_0_2",  # control rounds excluded: surfacing is entered as 0 there by rule
    "prompted": "prompted_0_1",    # only replies where both raters gave 0 or 1
}


def agreement_table(ratings):
    by_code = defaultdict(dict)
    for r in ratings:
        by_code[r["code"]][r["rater"]] = r
    raters = sorted({r["rater"] for r in ratings})
    rows = []
    for measure, column in AGREEMENT_MEASURES.items():
        for i, a in enumerate(raters):
            for b in raters[i + 1:]:
                pairs = []
                for sheets in by_code.values():
                    if a not in sheets or b not in sheets:
                        continue
                    if measure == "surfacing" and sheets[a]["is_control"] == "True":
                        continue
                    x, y = sheets[a].get(column, "").strip(), sheets[b].get(column, "").strip()
                    if measure == "prompted" and not ({x, y} <= {"0", "1"}):
                        continue
                    if x and y:
                        pairs.append((x, y))
                if pairs:
                    observed, kappa = cohen_kappa(pairs)
                    rows.append({"measure": measure, "rater_a": a, "rater_b": b, "n": len(pairs),
                                 "agree": sum(x == y for x, y in pairs), "agreement": round(observed, 3),
                                 "kappa": "" if kappa is None else round(kappa, 2)})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, func, help_text):
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.set_defaults(func=func)
        p.add_argument("--out", default=str(HERE / "out"), help="output directory (default: ./out)")
        return p

    p = add("run", cmd_run, "query API models on every vignette")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--vignettes", default=str(DEFAULT_VIGNETTES))
    p.add_argument("--reps", type=int, default=3, help="repeats per model per vignette")

    p = add("ingest", cmd_ingest, "import replies pasted from manual tools")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--vignettes", default=str(DEFAULT_VIGNETTES))
    p.add_argument("--reps", type=int, default=1, help="repeats saved per vignette (files <id>.txt, <id>_2.txt, ...)")

    p = add("blind", cmd_blind, "write blinded responses and rating sheets")
    p.add_argument("--vignettes", default=str(DEFAULT_VIGNETTES))
    p.add_argument("--key", default=str(DEFAULT_KEY))
    p.add_argument("--raters", default="rater1", help="comma-separated rater IDs")
    p.add_argument("--force", action="store_true", help="overwrite an existing blind key")
    p.add_argument("--strip-names", default="", help="comma-separated names an app greeted the user with, removed from replies")

    p = add("judge", cmd_judge, "score blinded replies with an AI judge against the rubric")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--vignettes", default=str(DEFAULT_VIGNETTES))
    p.add_argument("--rubric", default=str(DEFAULT_RUBRIC))
    p.add_argument("--rater", default="ai-judge", help="name of the judge's rating sheet")
    p.add_argument("--export-prompts", default="", help="write each judge prompt to this folder and stop")
    p.add_argument("--verdicts", default="", help="read judge answers from <folder>/<code>.json instead of an API")
    p.add_argument("--judge-name", default="offline-judge", help="judge model name recorded with --verdicts")

    p = add("summarize", cmd_summarize, "combine completed ratings with the key and report")
    p.add_argument("--raters", default="", help="comma-separated raters to include in the tables (default: all)")
    p.add_argument("--exclude-models", default="", help="comma-separated model names to leave out of all output")

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
