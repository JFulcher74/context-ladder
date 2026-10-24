"""Unit tests for the harness: blinding, judge parsing, scoring and statistics."""

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import study  # noqa: E402


class CleanReplyTest(unittest.TestCase):
    def test_strips_app_fingerprints(self):
        text = "Use cefalexin.\n\n[image](https://www.google.com/s2/favicons?domain=nice.org.uk)\n\nNICE\n\n+1\n\nReview at 48 hours."
        cleaned = study.clean_reply(text)
        self.assertNotIn("favicons", cleaned)
        self.assertNotIn("NICE\n", cleaned)
        self.assertNotIn("+1", cleaned)
        self.assertIn("Review at 48 hours.", cleaned)

    def test_strips_search_tokens_and_tracking(self):
        cleaned = study.clean_reply("See guidance【4†source】 [link](https://example.org/a?utm_source=chat).")
        self.assertNotIn("【", cleaned)
        self.assertNotIn("utm_source", cleaned)

    def test_strips_greeting_name_and_recapitalises(self):
        self.assertEqual(study.clean_reply("Sam, this taper is too fast.", names=["Sam"]), "This taper is too fast.")


class JudgeTest(unittest.TestCase):
    def test_parse_judgement_tolerates_surrounding_text(self):
        verdict = study.parse_judgement('Here: {"I1": {"met": true, "quote": "x"}, "I2": {"met": false}}', ["I1", "I2"])
        self.assertEqual(verdict, {"I1": {"met": True, "quote": "x"}, "I2": {"met": False, "quote": ""}})

    def test_parse_judgement_rejects_non_json(self):
        with self.assertRaises(ValueError):
            study.parse_judgement("no json here", ["I1"])


def verdict(**met):
    return {k: {"met": v, "quote": "q" if v else "", "quote_found": True} for k, v in met.items()}


class ScoresTest(unittest.TestCase):
    items = [
        {"id": "I1", "role": "surfacing"}, {"id": "I2", "role": "surfacing"}, {"id": "I4", "role": "answered"},
        {"id": "N1", "role": "unsafe"}, {"id": "P1", "role": "prompt"}, {"id": "P2", "role": "prompt"},
    ]

    def test_prompted_needs_every_prompt_item(self):
        both = study.scores_from(verdict(I1=1, I2=0, I4=1, N1=0, P1=1, P2=1), self.items)
        one = study.scores_from(verdict(I1=1, I2=0, I4=1, N1=0, P1=1, P2=0), self.items)
        self.assertEqual(both["prompted_0_1"], "1")
        self.assertEqual(one["prompted_0_1"], "0")

    def test_prompted_is_na_without_prompt_items(self):
        items = [i for i in self.items if i["role"] != "prompt"]
        self.assertEqual(study.scores_from(verdict(I1=0, I2=0, I4=1, N1=0), items)["prompted_0_1"], "NA")

    def test_surfacing_answered_unsafe(self):
        scores = study.scores_from(verdict(I1=1, I2=1, I4=0, N1=1, P1=0, P2=0), self.items)
        self.assertEqual((scores["surfacing_0_2"], scores["answered_0_2"], scores["unsafe_0_1"]), ("2", "0", "1"))

    def test_unverified_quote_is_flagged(self):
        v = verdict(I1=1, I2=0, I4=1, N1=0, P1=0, P2=0)
        v["I1"]["quote_found"] = False
        self.assertIn("quote not found, check: I1", study.scores_from(v, self.items)["notes"])


class WilsonTest(unittest.TestCase):
    def test_known_intervals(self):
        low, high = study.wilson(0, 12)
        self.assertAlmostEqual(low, 0.0, places=6)
        self.assertAlmostEqual(high, 0.2425, places=3)
        low, high = study.wilson(9, 12)
        self.assertAlmostEqual(low, 0.4677, places=3)
        self.assertAlmostEqual(high, 0.9111, places=3)


class KappaTest(unittest.TestCase):
    def test_perfect_and_chance_agreement(self):
        self.assertEqual(study.cohen_kappa([("1", "1"), ("0", "0"), ("1", "1")]), (1.0, 1.0))
        observed, kappa = study.cohen_kappa([("1", "1"), ("1", "0"), ("0", "1"), ("0", "0")])
        self.assertEqual(observed, 0.5)
        self.assertAlmostEqual(kappa, 0.0)

    def test_kappa_undefined_for_one_shared_category(self):
        self.assertEqual(study.cohen_kappa([("0", "0"), ("0", "0")]), (1.0, None))

    def test_skewed_ratings_collapse_kappa_but_not_pabak_or_ac1(self):
        pairs = [("0", "0")] * 9 + [("1", "0")]
        stats = study.agreement_stats(pairs, ["0", "1"])
        self.assertAlmostEqual(stats["kappa"], 0.0)
        self.assertAlmostEqual(stats["pabak"], 0.8)
        self.assertAlmostEqual(stats["ac1"], 0.8895, places=4)
        self.assertEqual(stats["confusion"], "0/0: 9; 1/0: 1")

    def test_pabak_and_ac1_use_every_possible_category(self):
        stats = study.agreement_stats([("2", "2"), ("1", "1"), ("0", "1")], ["0", "1", "2"])
        self.assertAlmostEqual(stats["pabak"], (3 * 2 / 3 - 1) / 2)
        self.assertEqual(study.agreement_stats([("0", "0")], ["0", "1"])["ac1"], 1.0)

    def test_agreement_table_by_group(self):
        rows = []
        for rater, values in [("judge", ["1", "0", "1"]), ("pharm", ["1", "1", "1"])]:
            for code, value, model in zip(["R001", "R002", "R003"], values, ["claude", "gemini", "gemini"]):
                rows.append({"code": code, "rater": rater, "is_control": "False", "model": model,
                             "surfacing_0_2": "1", "prompted_0_1": value})
        table = study.agreement_table(rows, group=lambda r: "Claude" if r["model"] == "claude" else "Other tools")
        prompted = {r["group"]: r for r in table if r["measure"] == "prompted"}
        self.assertEqual((prompted["Claude"]["n"], prompted["Claude"]["agree"]), (1, 1))
        self.assertEqual((prompted["Other tools"]["n"], prompted["Other tools"]["agree"]), (2, 1))

    def test_agreement_table_skips_controls_and_na(self):
        rows = []
        for rater, values in [("judge", ["1", "0", "NA"]), ("pharm", ["1", "1", "NA"])]:
            for code, value, control in zip(["R001", "R002", "R003"], values, ["False", "False", "True"]):
                rows.append({"code": code, "rater": rater, "is_control": control,
                             "surfacing_0_2": "2", "prompted_0_1": value})
        table = {r["measure"]: r for r in study.agreement_table(rows)}
        self.assertEqual(table["surfacing"]["n"], 2)
        self.assertEqual((table["prompted"]["n"], table["prompted"]["agree"]), (2, 1))


class ExportRatingsTest(unittest.TestCase):
    def test_prompted_needs_every_field(self):
        tmp = Path(tempfile.mkdtemp())
        items = tmp / "items"
        items.mkdir()
        rubric = {"items": [{"id": "P1", "role": "prompt", "applies_to": ["R2"]},
                            {"id": "P2", "role": "prompt", "applies_to": ["R1", "R2"]}]}
        (tmp / "rubric.json").write_text(json.dumps(rubric), encoding="utf-8")
        with (tmp / "key.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["code", "model", "vignette_id", "is_control"])
            w.writerows([["R001", "a", "R2", "False"], ["R002", "a", "R1", "False"], ["R003", "a", "C", "True"]])
        base = {"surfacing": 1, "answered": 2, "new_to_me": 0, "unsafe": 0}
        docs = {"R001": {**base, "prompted": 1, "prompted_oud": 0}, "R002": {**base, "prompted_oud": 1},
                "R003": {**base, "surfacing": 2, "spurious": 0}}
        for code, doc in docs.items():
            (items / f"{code}.json").write_text(json.dumps({"data": doc}), encoding="utf-8")
        subprocess.run(
            [sys.executable, str(ROOT / "review" / "pages" / "export_ratings.py"), "--items", str(items),
             "--key", str(tmp / "key.csv"), "--rubric", str(tmp / "rubric.json"),
             "--prompt-fields", "P1=prompted,P2=prompted_oud", "--out", str(tmp / "sheet.csv")],
            check=True, capture_output=True,
        )
        with (tmp / "sheet.csv").open(newline="") as f:
            sheet = {r["code"]: r for r in csv.DictReader(f)}
        self.assertEqual(sheet["R001"]["prompted_0_1"], "0")
        self.assertEqual(sheet["R002"]["prompted_0_1"], "1")
        self.assertEqual((sheet["R003"]["prompted_0_1"], sheet["R003"]["surfacing_0_2"]), ("NA", "0"))


class ReviewPageTest(unittest.TestCase):
    def build(self, reply_text):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "blinded").mkdir()
        (tmp / "blinded" / "R001.md").write_text(f"# Response R001\n\n## Case R1\n\nQ\n\n## Response\n\n{reply_text}\n", encoding="utf-8")
        (tmp / "v.json").write_text(json.dumps([{"id": "R1", "prompt": "Q"}]), encoding="utf-8")
        (tmp / "t.html").write_text("<script>/*DATA*/</script>", encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(ROOT / "review" / "pages" / "build_review_page.py"), "--template", str(tmp / "t.html"),
             "--vignettes", str(tmp / "v.json"), "--blinded", str(tmp / "blinded"), "--out", str(tmp / "out.html")],
            capture_output=True, text=True,
        )

    def test_builds_clean_page(self):
        self.assertEqual(self.build("Use trimethoprim.").returncode, 0)

    def test_refuses_app_name(self):
        result = self.build("As ChatGPT, I suggest trimethoprim.")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("chatgpt", result.stderr)


class ExportDatasetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "blinded").mkdir()
        (self.tmp / "ratings").mkdir()
        with (self.tmp / "blind_key.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["code", "model", "model_id", "category", "vignette_id", "is_control", "rep"])
            w.writerow(["R001", "claude", "Claude Sonnet 5.5", "frontier", "R1", "False", 1])
        (self.tmp / "judge_details.jsonl").write_text(json.dumps(
            {"code": "R001", "judge_model": "x", "error": None,
             "items": {"I1": {"met": True, "quote": "ask first", "quote_found": True}}}) + "\n", encoding="utf-8")
        with (self.tmp / "ratings" / "ai-judge.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["code", "vignette_id", *study.SCORE_COLUMNS])
            w.writerow(["R001", "R1", 2, 2, "NA", 0, 0, "NA", ""])
        (self.tmp / "rubric.json").write_text(json.dumps(
            {"items": [{"id": "I1", "role": "surfacing", "applies_to": ["R1"], "text": "t"}]}), encoding="utf-8")

    def export(self, reply):
        (self.tmp / "blinded" / "R001.md").write_text(f"# Response R001\n\n## Case R1\n\nQ\n\n## Response\n\n{reply}\n", encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(ROOT / "analysis" / "export_dataset.py"), "--study", "A", "--out", str(self.tmp),
             "--rubric", str(self.tmp / "rubric.json"), "--raters", "ai-judge", "--dest", str(self.tmp / "dataset")],
            capture_output=True, text=True,
        )

    def test_writes_four_files_with_tool_label(self):
        self.assertEqual(self.export("Ask first.").returncode, 0)
        folder = self.tmp / "dataset" / "study-a"
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["items.csv", "replies.csv", "scores.csv", "verdicts.csv"])
        with (folder / "replies.csv").open(newline="") as f:
            row = next(csv.DictReader(f))
        self.assertEqual((row["tool"], row["reply"]), ("Claude", "Ask first."))

    def test_refuses_leftover_fingerprint(self):
        result = self.export("See [x](https://a.org/?utm_source=chat).")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("utm_source", result.stderr)


class PaperTablesTest(unittest.TestCase):
    """Study B, four key-round replies: the pharmacists disagree on R004 (P1) until it is adjudicated."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "ratings").mkdir()
        tools = ["chatgpt", "claude", "gemini", "copilot"]
        with (self.tmp / "blind_key.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["code", "model", "vignette_id", "is_control", "rep"])
            w.writerows([[f"R00{i + 1}", tool, "R2", "False", 1] for i, tool in enumerate(tools)])
        judged = {"R001": (1, 1), "R002": (1, 0), "R003": (0, 0), "R004": (1, 1)}
        with (self.tmp / "judge_details.jsonl").open("w", encoding="utf-8") as f:
            for code, (p1, p2) in judged.items():
                items = {"P1": {"met": bool(p1)}, "P2": {"met": bool(p2)}}
                f.write(json.dumps({"code": code, "items": items}) + "\n")
        pharm = {"pharm1": {"R001": (1, 1), "R002": (1, 0), "R003": (0, 0), "R004": (1, 1)},
                 "pharm2": {"R001": (1, 1), "R002": (1, 0), "R003": (0, 0), "R004": (0, 1)}}
        self.sheet("ai-judge", {c: v for c, v in judged.items()})
        for rater, values in pharm.items():
            self.sheet(rater, values)

    def sheet(self, rater, values):
        with (self.tmp / "ratings" / f"{rater}.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["code", "vignette_id", *study.SCORE_COLUMNS])
            for code, (p1, p2) in values.items():
                w.writerow([code, "R2", 1, 2, 0, 0, 0, int(p1 and p2), f"P1={p1}; P2={p2}"])

    def tables(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "analysis" / "paper_tables.py"), "--study", "B", "--out", str(self.tmp)],
            capture_output=True, text=True, check=True,
        )
        return result.stdout

    def test_co_primary_rows_and_consistency_labels(self):
        text = self.tables()
        self.assertIn("| Duration asked (P1) |  | 3 of 4 (95% CI", text)
        self.assertIn("| Claude | no replies | 1/1, consistently met | 0/1, consistently missed | 0/1, consistently missed |", text)
        self.assertIn("| Addiction asked (P2) | no replies |", text)

    def test_key_round_waits_for_adjudication(self):
        text = self.tables()
        self.assertIn("Awaiting adjudication (1): R004", text)
        self.assertIn("| Both (registered composite) | pharm1, pharm2 | 1 of 3 (95% CI 6% to 79%); with 1 unresolved: 1 to 2 of 4 |", text)
        self.sheet("adjudicated", {"R004": (1, 1)})
        text = self.tables()
        self.assertNotIn("Awaiting adjudication", text)
        self.assertIn("| Both (registered composite) | pharm1, pharm2 | 2 of 4 (95% CI", text)
        self.assertIn("| Duration asked (P1) | pharm1, pharm2 | 3 of 4 (95% CI", text)

    def test_judge_agreement_split_by_maker(self):
        text = self.tables()
        self.assertIn("| prompted | Claude replies | ai-judge vs pharm1 | 1 |", text)
        self.assertIn("| prompted | Other tools | ai-judge vs pharm1 | 3 |", text)
        self.assertIn("PABAK", text)


class PaperTablesStrictRaterTest(unittest.TestCase):
    """Study A key round: the lead rated under the strict definition, a second pharmacist under the broadened one."""

    def test_definitions_are_kept_apart(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "ratings").mkdir()
        (tmp / "ex").mkdir()
        # (tool, judge P1, judge E1, author strict, pharm2 broadened)
        replies = {"R001": ("chatgpt", 1, 1, 1, 1), "R002": ("claude", 1, 0, 0, 1),
                   "R003": ("gemini", 0, 0, 0, 0), "R004": ("copilot", 1, 0, 1, 1)}
        with (tmp / "blind_key.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["code", "model", "vignette_id", "is_control", "rep"])
            w.writerows([[c, v[0], "R4", "False", 1] for c, v in replies.items()])
        (tmp / "judge_details.jsonl").write_text("".join(
            json.dumps({"code": c, "items": {"P1": {"met": bool(v[1])}}}) + "\n" for c, v in replies.items()))
        (tmp / "ex" / "judge_details.jsonl").write_text("".join(
            json.dumps({"code": c, "items": {"E1": {"met": bool(v[2])}}}) + "\n" for c, v in replies.items()))
        for rater, col in [("ai-judge", 1), ("author", 3), ("pharm2", 4)]:
            with (tmp / "ratings" / f"{rater}.csv").open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["code", "vignette_id", *study.SCORE_COLUMNS])
                w.writerows([[c, "R4", 1, 2, 0, 0, 0, v[col], ""] for c, v in replies.items()])
        text = subprocess.run(
            [sys.executable, str(ROOT / "analysis" / "paper_tables.py"), "--study", "A", "--out", str(tmp),
             "--exploratory", str(tmp / "ex"), "--strict-raters", "author"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("| Strict (pre-widening P1, matched to E1) | author | 2 of 4 (95% CI", text)
        self.assertIn("| Broadened (P1, registered) | pharm2 | 3 of 4 (95% CI", text)
        self.assertIn("| prompted | ai-judge (E1) vs author | 4 | 3 (75%) |", text)
        self.assertIn("| prompted | ai-judge vs pharm2 | 4 | 4 (100%) |", text)
        self.assertNotIn("| prompted | author vs pharm2 |", text)
        self.assertNotIn("| prompted | ai-judge vs author |", text)
        self.assertIn("| surfacing | author vs pharm2 |", text)

    def test_strict_raters_non_key_rounds_are_compared_with_the_judges_p1(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "ratings").mkdir()
        (tmp / "ex").mkdir()
        # R3 replies: the lead said yes to both (risk-factor naming), the judge's P1 met on both, E1 met on neither.
        rows = [("R001", "R3", 1, 0, 1), ("R002", "R3", 1, 0, 1), ("R003", "R4", 0, 0, 0), ("R004", "R4", 0, 0, 0)]
        with (tmp / "blind_key.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["code", "model", "vignette_id", "is_control", "rep"])
            w.writerows([[c, "claude", r, "False", 1] for c, r, *_ in rows])
        (tmp / "judge_details.jsonl").write_text("".join(
            json.dumps({"code": c, "items": {"P1": {"met": bool(p1)}}}) + "\n" for c, _, p1, _, _ in rows))
        (tmp / "ex" / "judge_details.jsonl").write_text("".join(
            json.dumps({"code": c, "items": {"E1": {"met": bool(e1)}}}) + "\n" for c, _, _, e1, _ in rows))
        for rater, col in [("ai-judge", 2), ("author", 4)]:
            with (tmp / "ratings" / f"{rater}.csv").open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["code", "vignette_id", *study.SCORE_COLUMNS])
                w.writerows([[c, r, 1, 2, 0, 0, 0, row[col], ""] for row in rows for c, r in [row[:2]]])
        text = subprocess.run(
            [sys.executable, str(ROOT / "analysis" / "paper_tables.py"), "--study", "A", "--out", str(tmp),
             "--exploratory", str(tmp / "ex"), "--strict-raters", "author"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("author, R3 prompted (broadened in practice): 2 of 2", text)
        self.assertIn("| prompted | ai-judge vs author (non-key rounds, broadened) | 2 | 2 (100%) |", text)
        self.assertIn("| prompted | ai-judge (E1) vs author | 2 | 2 (100%) |", text)
        self.assertNotIn("| surfacing | ai-judge vs author (non-key", text)


class PickSampleTest(unittest.TestCase):
    def test_includes_key_round_and_is_repeatable(self):
        tmp = Path(tempfile.mkdtemp())
        key = tmp / "key.csv"
        with key.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["code", "model", "vignette_id"])
            n = 0
            for rnd in ["R1", "R2", "C"]:
                for model in ["a", "b", "c", "d"]:
                    for _ in range(3):
                        n += 1
                        w.writerow([f"R{n:03d}", model, rnd])
        outs = []
        for name in ["one.txt", "two.txt"]:
            subprocess.run(
                [sys.executable, str(ROOT / "review" / "pages" / "pick_sample.py"), "--key", str(key), "--key-round", "R2",
                 "--per-round", "R1=4,C=2", "--out", str(tmp / name)],
                check=True, capture_output=True,
            )
            outs.append((tmp / name).read_text().split())
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(len(outs[0]), 12 + 4 + 2)
        with key.open(newline="") as f:
            rows = {r["code"]: r for r in csv.DictReader(f)}
        self.assertEqual(sum(rows[c]["vignette_id"] == "R2" for c in outs[0]), 12)
        r1_models = {rows[c]["model"] for c in outs[0] if rows[c]["vignette_id"] == "R1"}
        self.assertEqual(r1_models, {"a", "b", "c", "d"})


if __name__ == "__main__":
    unittest.main()
