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
