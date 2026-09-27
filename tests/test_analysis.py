from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "wiki_interest.py"
spec = importlib.util.spec_from_file_location("wiki_interest", MODULE_PATH)
wiki = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = wiki
spec.loader.exec_module(wiki)


def frame_from_share(values, lang="xx"):
    dates = pd.date_range("2024-01-01", periods=len(values), freq="MS", tz="UTC")
    article = np.asarray(values) * 1000
    return pd.DataFrame(
        {
            "date": dates,
            "language": lang,
            "title": "Example",
            "article_views": article,
            "project_views": np.full(len(values), 1_000_000.0),
            "share_per_million": np.asarray(values, dtype=float),
            "coverage_ratio": np.ones(len(values)),
        }
    )


def test_increasing_series_detected():
    values = np.exp(np.linspace(0, 1.0, 24))
    m = wiki.analyze_series(frame_from_share(values), "monthly", 0.95)
    assert m["trend_direction"] == "increasing"
    assert m["normalized_trend_annualized_pct"] > 0
    assert m["evidence_strength"] in {"moderate", "high"}


def test_flat_series_not_overstated():
    values = np.ones(24) * 4.0
    m = wiki.analyze_series(frame_from_share(values), "monthly", 0.95)
    assert m["trend_direction"] == "unclear"
    assert abs(m["normalized_trend_annualized_pct"]) < 0.01


def test_spike_robustness_flags_single_large_spike():
    values = np.ones(24) * 2.0
    values[-1] = 60.0
    m = wiki.analyze_series(frame_from_share(values), "monthly", 0.95)
    assert m["largest_period_share_pct"] > 50
    assert m["spike_robust"] is False


def test_reanalysis_generates_artifacts(tmp_path):
    df = frame_from_share(np.linspace(2, 5, 24), lang="uk")
    csv_path = tmp_path / "input.csv"
    x = df.copy()
    x["date"] = x["date"].dt.strftime("%Y-%m-%d")
    x.to_csv(csv_path, index=False)
    out = tmp_path / "out"
    analysis = wiki.reanalyze_file(csv_path, out, 0.95)
    assert (out / "analysis.json").exists()
    assert (out / "chart.png").exists()
    assert (out / "report.pdf").exists()
    assert analysis["metrics"]["uk"]["trend_direction"] == "increasing"


def test_skill_frontmatter_name_matches_directory():
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: wikipedia-interest-analysis" in text
    assert ROOT.name == "wikipedia-interest-analysis"


def test_comparison_exposes_ci_and_evidence_reasons():
    values = np.exp(np.linspace(0, 0.5, 24))
    m = wiki.analyze_series(frame_from_share(values), "monthly", 0.95)
    row = wiki._comparison_table({"xx": m})[0]
    assert "trend_ci_low_pct" in row
    assert "trend_ci_high_pct" in row
    assert "evidence_reasons" in row
    assert isinstance(row["evidence_reasons"], list)


def test_agent_choices_use_wikimedia_automated_value():
    parser = wiki.build_parser()
    args = parser.parse_args([
        "analyze", "--qid", "Q1", "--languages", "en", "--agent", "automated", "--out", "tmp"
    ])
    assert args.agent == "automated"

class _FakeClient:
    def get_json(self, url, params):
        action = params.get("action")
        if action == "wbsearchentities":
            return {"search": [{"id": "QX", "label": "topic", "description": "example"}]}
        if action == "wbgetentities":
            return {
                "entities": {
                    "QX": {
                        "labels": {"en": {"value": "topic"}},
                        "descriptions": {"en": {"value": "example"}},
                        "aliases": {},
                        "sitelinks": {"cswiki": {"title": "Tema"}},
                    }
                }
            }
        raise AssertionError(params)


def test_resolver_marks_missing_language_as_not_ready():
    result = wiki.resolve_topic("topic", ["pl", "cs"], "en", _FakeClient())
    assert result["recommended_qid"] == "QX"
    assert result["ambiguous"] is False
    assert result["ready_for_analysis"] is False
    assert result["missing_languages"] == ["pl"]
    assert "Do not run a direct cross-language comparison" in result["warning"]
