# Wikipedia Interest Analysis — Agent Skill

An Agent Skills-compatible skill for B2C product discovery using Wikimedia page-view data. It resolves a concept across Wikipedia languages with Wikidata, fetches article and project traffic, normalizes topic interest, estimates trend uncertainty, generates a chart, and produces a one-page PDF report.

## Why this design

Raw page views are not directly comparable across language editions because the editions have different audience sizes and traffic trends. The skill therefore reports both:

- **absolute article views** — an observed scale proxy;
- **normalized topic share** — article views per one million total views in that Wikipedia edition, used as the primary cross-language interest signal.

The analytical work is deterministic Python code rather than model arithmetic. This makes the skill usable by fast inexpensive agents: the model resolves intent, chooses the Wikidata concept, invokes the code, and explains the returned JSON.

## Repository structure

```text
.
├── SKILL.md
├── README.md
├── requirements.txt
├── requirements-dev.txt
├── scripts/
│   ├── wiki_interest.py
│   └── openrouter_agent_test.py
├── references/
│   ├── METHODOLOGY.md
│   └── TESTING.md
├── examples/
│   └── title-map.example.json
└── tests/
    └── test_analysis.py
```

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export WIKIMEDIA_USER_AGENT='WikipediaInterestSkill/1.0 (https://example.com/project)'
```

Resolve a topic:

```bash
python scripts/wiki_interest.py resolve \
  --topic "intermittent fasting" \
  --languages pl,cs \
  --search-language en
```

Only continue when the resolver reports `ready_for_analysis: true`. If a requested language is missing, treat that as a data limitation rather than silently substituting another article. Then analyze the selected QID:

```bash
QID="Q..."
python scripts/wiki_interest.py analyze \
  --qid "$QID" \
  --languages pl,cs \
  --start 2024-01-01 \
  --end 2025-12-31 \
  --out outputs/fasting-pl-cs
```

For a follow-up that changes confidence assumptions but reuses the same data:

```bash
python scripts/wiki_interest.py reanalyze \
  --input outputs/fasting-pl-cs/data.csv \
  --confidence 0.90 \
  --out outputs/fasting-pl-cs-90
```

## Output

`analysis.json` contains compact metrics designed for an agent to summarize. `data.csv` preserves the reusable series. `chart.png` visualizes normalized interest. `report.pdf` is a single-page report suitable for sharing.

## Verification of AI-assisted development

AI tools were used during development to brainstorm the architecture, identify edge cases, and review implementation choices. Their output was not treated as ground truth. Verification is separated into deterministic checks:

- unit tests on synthetic increasing, flat/noisy, and spike-driven data;
- report-generation smoke test;
- explicit concept resolution through Wikidata rather than model-generated title translation;
- inspectable formulas and assumptions in `references/METHODOLOGY.md`;
- a live Wikimedia smoke-test procedure;
- an OpenRouter agent harness to verify that a low-cost tool-capable model can follow the skill instead of doing the analysis itself;
- Agent Skills schema validation with `skills-ref` before submission.

## Data-source caveat

Wikipedia interest is a proxy for attention. It is not evidence of willingness to pay, product-market fit, or causal demand. The generated PDF states this explicitly.
