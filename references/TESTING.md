# Testing guide

## Local tests

Create an environment and run:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

The unit tests use synthetic data and do not require network access. They cover increasing, flat/noisy, and spike-driven trends, as well as report generation.

## Live Wikimedia smoke test

Set a descriptive Wikimedia user agent and run a short analysis:

```bash
export WIKIMEDIA_USER_AGENT='WikipediaInterestSkill/1.0 (https://example.com/project)'
python scripts/wiki_interest.py resolve --topic astronomy --languages uk,pl --search-language en
python scripts/wiki_interest.py analyze --qid Q333 --languages uk,pl --start 2024-01-01 --end 2025-12-31 --out outputs/smoke
```

Inspect `outputs/smoke/analysis.json`, `chart.png`, and `report.pdf`.

## Agent test on a cheap/free model

OpenRouter provides an OpenAI-compatible API and a free-model router that can select tool-capable free models. After setting an API key:

```bash
export OPENROUTER_API_KEY='...'
python scripts/openrouter_agent_test.py \
  --model openrouter/free \
  --prompt 'Compare interest in astronomy in Ukrainian and Polish Wikipedia over the last two years. Produce a short evidence-based conclusion.'
```

The harness loads `SKILL.md`, exposes a narrow structured tool, records tool calls, and fails if the agent never performs an analysis. For reproducibility, prefer an explicit tool-capable model slug once chosen instead of relying only on a router.

## Agent Skills validation

The reference Agent Skills project provides `skills-ref`. After installing it, validate the repository root:

```bash
skills-ref validate .
```
