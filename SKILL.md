---
name: wikipedia-interest-analysis
description: Analyze Wikipedia page-view interest for a topic across one or more language editions, compare growth and normalized topic salience, assess trend stability, generate charts, and create a one-page PDF report. Use for B2C product discovery, topic demand proxies, localization research, audience prioritization, or follow-up questions that change date ranges or growth assumptions.
license: MIT
compatibility: Requires Python 3.9+, internet access to Wikimedia APIs, and packages from requirements.txt. Optional OpenRouter key for low-cost agent smoke testing.
metadata:
  version: "1.0.1"
---

# Wikipedia Interest Analysis

Use this skill when a user wants to estimate how interest in a topic changes over time in one or more Wikipedia language editions. Treat Wikipedia page views as a **research signal**, not as proof of demand, willingness to pay, or market size.

## Core workflow

1. **Clarify only what is necessary.** Extract:
   - topic/concept;
   - target Wikipedia language codes, such as `uk`, `pl`, `cs`, `es`;
   - time window; default to the last 24 complete months when the user does not specify one;
   - any explicit decision rule the user gave (growth, scale, stability, etc.).
2. **Resolve the concept before measuring it.** Run:

   ```bash
   python scripts/wiki_interest.py resolve --topic "<topic>" --languages <codes> --search-language <request-language>
   ```

   Prefer a Wikidata QID with sitelinks in the requested editions. Inspect the candidate label and description. If multiple candidates are genuinely plausible, ask the user one short disambiguation question instead of guessing.

   Before analysis, require `ready_for_analysis: true`. If it is `false`, inspect `missing_languages` and the warning. Explain that the exact concept has no article in those editions; do not silently substitute another page. Continue only if the user explicitly confirms a proxy/title mapping or changes the language set.
3. **Analyze the resolved concept.** Run:

   ```bash
   python scripts/wiki_interest.py analyze --qid <QID> --languages <codes> --start YYYY-MM-DD --end YYYY-MM-DD --out outputs/<short-name>
   ```

   Omit `--start` and `--end` for the default 24-complete-month window. The command creates:
   - `analysis.json` — compact machine-readable results;
   - `data.csv` — reusable time series;
   - `chart.png` — normalized interest trend;
   - `report.pdf` — one-page shareable report.
4. **Answer from `analysis.json`, not from visual inspection alone.** Report:
   - direction of the normalized trend (`increasing`, `decreasing`, `unclear`);
   - annualized normalized-trend estimate and its 95% interval;
   - recent-vs-previous-window change;
   - absolute page-view scale;
   - evidence-strength label and reasons;
   - major limitations.
5. **For cross-language comparisons, do not compare raw page views as if language editions had equal audience size.** Use normalized topic share (`article views / total project views × 1,000,000`) for topic salience and raw views only as a secondary scale signal.
6. **For follow-up requests, reuse existing data when possible.** If the user changes a threshold or interpretation but not the topic, languages, or dates, read the existing `analysis.json`/`data.csv`; do not fetch again. To recompute the same saved data with a different confidence level, run:

   ```bash
   python scripts/wiki_interest.py reanalyze --input outputs/<short-name>/data.csv --out outputs/<new-name> --confidence 0.90
   ```

## Interpreting the result

The script's primary series is `share_per_million`: page views of the selected article per one million total user page views in the same Wikipedia edition and period. This adjusts for changes in overall Wikipedia traffic and makes language editions more comparable.

`trend_direction` and its confidence interval are based on a log-linear trend with heteroskedasticity/autocorrelation-consistent (Newey-West) standard errors. It is **not** a probability that a business idea will succeed.

`evidence_strength` is a transparent heuristic based on four checks: enough observations, high data coverage, a confidence interval that excludes zero, and trend direction that survives removal of the largest spike. Read `evidence_reasons` before using the label.

When the user asks “which audience should we investigate next?”, frame the result as a research priority, not a market verdict. A useful pattern is: high/credible normalized growth + non-trivial raw scale + stable signal → worth further validation. Mention that conversion, competition, acquisition cost, willingness to pay, and demographics are outside this dataset.

## Edge cases

- **Ambiguous topic:** resolve with Wikidata and ask the user if two candidates are plausible.
- **No sitelink for a requested language:** report that the exact cross-language concept is unavailable; do not silently substitute a different article. An explicit title can be supplied with `--title-map` if the user confirms it.
- **Recent incomplete periods:** monthly analysis automatically excludes the current incomplete month; daily analysis excludes the current day.
- **Short windows:** fewer observations reduce evidence strength. Do not overstate the trend.
- **Spikes/news events:** check `largest_period_share` and `spike_robust`. A one-off event can create an apparent trend.
- **Page moves/redirects:** the basic version uses the current canonical title. Historical moves or split/merged articles can distort long histories; flag this for important decisions.
- **Broad topics:** one Wikipedia article is only a proxy. For strategic work, expand to a basket of related articles in a later iteration.

## Efficient use on small/fast models

Keep reasoning outside the model when code can do it. The model should mainly: resolve intent, choose a QID, run one analysis command, and summarize the compact JSON. Do not ask the model to calculate trends, normalize traffic, or manually create charts.

For a low-cost agent smoke test with OpenRouter, see `scripts/openrouter_agent_test.py` and [references/TESTING.md](references/TESTING.md).

For methodology, assumptions, and planned extensions, see [references/METHODOLOGY.md](references/METHODOLOGY.md).
