# Methodology and limitations

## What is measured

For each requested Wikipedia language edition, the skill fetches two aligned time series from Wikimedia Analytics:

1. page views for the resolved article;
2. total page views for the whole Wikipedia edition.

Both use the same access mode and agent filter. The default is `all-access` and `user`, which focuses on human-reader traffic rather than spiders or bots.

The primary metric is:

`share_per_million = article_views / project_views * 1,000,000`

This is interpreted as topic salience within that Wikipedia edition. Absolute article views are retained as a separate scale indicator.

## Concept resolution

Natural-language topics are resolved through Wikidata. The workflow searches Wikidata, inspects candidate label/description, and then uses sitelinks from the selected QID to obtain corresponding article titles in each language edition. This is safer than translating article titles independently because it preserves concept identity.

A missing sitelink is treated as missing evidence, not automatically replaced with a loosely related page.

## Trend estimation

The trend is fitted to `log1p(share_per_million)` against time. Log scaling reduces the influence of multiplicative changes and makes the slope interpretable as approximate proportional growth.

The script uses ordinary least squares with Newey-West/HAC standard errors to reduce overconfidence when residuals are autocorrelated or heteroskedastic. The slope is annualized and converted back to a percentage. A configurable confidence interval (95% by default) is reported.

Direction:
- `increasing` when the whole interval is above zero;
- `decreasing` when the whole interval is below zero;
- `unclear` otherwise.

## Stability checks

The report also includes:
- coverage of expected periods;
- recent-window vs previous-window change;
- the largest period's share of all article views;
- `spike_robust`, which checks whether the trend direction remains the same after removing the single largest normalized-interest observation.

`evidence_strength` is deliberately simple and inspectable. It is a heuristic summary, not a calibrated statistical probability.

## Important limitations

- Wikipedia readership is not the same as commercial demand or willingness to pay.
- A single article may poorly represent a broad topic.
- Language-edition traffic is not identical to a country's population or market.
- News cycles and external events can cause short-lived spikes.
- Current-title page-view histories can be affected by page moves, redirects, merges, or splits.
- Normalization by total Wikipedia traffic improves comparability but does not correct demographic differences between language editions.
- Correlation and trend do not establish causality.

## Iterative roadmap

After the basic workflow is reliable, extend it in this order:

1. **Topic baskets:** resolve related pages and aggregate a concept-level basket instead of one article.
2. **Redirect/move history:** discover historical titles and merge page-view histories.
3. **Seasonality:** use multi-year seasonal decomposition and year-over-year matching.
4. **Event robustness:** automatically flag structural breaks and transient news spikes.
5. **Market normalization:** optionally combine Wikipedia signals with population, internet penetration, search demand, app-store data, or product telemetry.
6. **Batch research:** accept many topics × languages, cache requests, and rank candidates with user-defined criteria.
7. **Backtesting:** test whether historical Wikipedia signals predict later product-relevant metrics on known launches.
8. **Large-scale data:** switch from request-by-request APIs to Wikimedia dumps/analytics datasets when the query volume becomes large.
