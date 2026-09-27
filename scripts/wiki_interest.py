#!/usr/bin/env python3
"""Wikipedia interest analysis for Agent Skills.

The module resolves topics through Wikidata, fetches Wikimedia Analytics
page-view series, normalizes article traffic by total project traffic,
estimates trend uncertainty, and produces machine-readable and shareable
artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import textwrap
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests


AQS_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
DEFAULT_USER_AGENT = "WikipediaInterestSkill/1.0 (educational case study)"
MIN_AQS_DATE = date(2015, 7, 1)


class SkillError(RuntimeError):
    pass


@dataclass
class DateWindow:
    start: date
    end: date
    warnings: list[str]


class CachedSession:
    def __init__(self, user_agent: str, cache_dir: Path, ttl_seconds: int = 24 * 3600):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        key_payload = json.dumps([url, params or {}], sort_keys=True, ensure_ascii=False)
        key = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) <= self.ttl_seconds:
            return json.loads(cache_file.read_text(encoding="utf-8"))

        try:
            response = self.session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            raise SkillError(f"Network request failed for {url}: {exc}") from exc

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "unknown")
            raise SkillError(f"Wikimedia rate limit reached. Retry-After={retry_after}")
        if not response.ok:
            detail = response.text[:500]
            raise SkillError(f"HTTP {response.status_code} for {response.url}: {detail}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SkillError(f"Non-JSON response from {response.url}") from exc
        cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload


def _split_languages(raw: str | Iterable[str]) -> list[str]:
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in raw.split(",")]
    else:
        parts = [str(p).strip().lower() for p in raw]
    langs = [p for p in parts if p]
    if not langs:
        raise SkillError("At least one language code is required.")
    if len(langs) > 12:
        raise SkillError("Limit the basic workflow to 12 language editions per run.")
    return list(dict.fromkeys(langs))


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().replace("_", " ").split())


def _default_window(granularity: str) -> DateWindow:
    today = datetime.now(timezone.utc).date()
    if granularity == "monthly":
        this_month = today.replace(day=1)
        end = this_month - timedelta(days=1)
        start_month = (pd.Timestamp(this_month) - pd.DateOffset(months=24)).date()
        return DateWindow(start_month, end, [])
    end = today - timedelta(days=1)
    start = end - timedelta(days=729)
    return DateWindow(start, end, [])


def _parse_window(start_raw: str | None, end_raw: str | None, granularity: str) -> DateWindow:
    if not start_raw and not end_raw:
        return _default_window(granularity)
    defaults = _default_window(granularity)
    try:
        start = date.fromisoformat(start_raw) if start_raw else defaults.start
        end = date.fromisoformat(end_raw) if end_raw else defaults.end
    except ValueError as exc:
        raise SkillError("Dates must use YYYY-MM-DD format.") from exc

    warnings: list[str] = []
    today = datetime.now(timezone.utc).date()
    if granularity == "monthly":
        last_complete = today.replace(day=1) - timedelta(days=1)
    else:
        last_complete = today - timedelta(days=1)
    if end > last_complete:
        warnings.append(f"End date was clamped to last complete {granularity} period: {last_complete}.")
        end = last_complete
    if start < MIN_AQS_DATE:
        warnings.append(f"Start date was clamped to Wikimedia page-view availability: {MIN_AQS_DATE}.")
        start = MIN_AQS_DATE
    if start > end:
        raise SkillError("Start date must be on or before end date after completeness adjustments.")
    return DateWindow(start, end, warnings)


def _aqs_stamp(d: date) -> str:
    return d.strftime("%Y%m%d") + "00"


def _expected_index(window: DateWindow, granularity: str) -> pd.DatetimeIndex:
    if granularity == "monthly":
        return pd.date_range(pd.Timestamp(window.start).replace(day=1), pd.Timestamp(window.end).replace(day=1), freq="MS", tz="UTC")
    return pd.date_range(window.start, window.end, freq="D", tz="UTC")


def resolve_topic(
    topic: str,
    languages: list[str],
    search_language: str | None,
    client: CachedSession,
    limit: int = 6,
) -> dict[str, Any]:
    languages = _split_languages(languages)
    query_languages = []
    if search_language:
        query_languages.append(search_language.lower())
    query_languages.extend(languages)
    query_languages.append("en")
    query_languages = list(dict.fromkeys(query_languages))

    found: list[dict[str, Any]] = []
    used_search_language = None
    for lang in query_languages:
        payload = client.get_json(
            WIKIDATA_API,
            {
                "action": "wbsearchentities",
                "format": "json",
                "search": topic,
                "language": lang,
                "uselang": lang,
                "type": "item",
                "limit": limit,
                "origin": "*",
            },
        )
        if payload.get("search"):
            found = payload["search"]
            used_search_language = lang
            break

    if not found:
        return {
            "topic": topic,
            "languages": languages,
            "search_language": search_language,
            "recommended_qid": None,
            "ambiguous": True,
            "candidates": [],
            "warning": "No Wikidata candidates found. Try a synonym or explicit QID/title mapping.",
        }

    ids = [item["id"] for item in found]
    sites = [f"{lang}wiki" for lang in languages]
    details = client.get_json(
        WIKIDATA_API,
        {
            "action": "wbgetentities",
            "format": "json",
            "ids": "|".join(ids),
            "props": "labels|descriptions|aliases|sitelinks",
            "languages": "|".join(list(dict.fromkeys([used_search_language or "en", "en"] + languages))),
            "sitefilter": "|".join(sites),
            "origin": "*",
        },
    ).get("entities", {})

    normalized_topic = _normalize_text(topic)
    candidates: list[dict[str, Any]] = []
    for rank, item in enumerate(found):
        qid = item["id"]
        entity = details.get(qid, {})
        labels = entity.get("labels", {})
        descriptions = entity.get("descriptions", {})
        aliases = entity.get("aliases", {})
        sitelinks = entity.get("sitelinks", {})
        label = item.get("label") or labels.get(used_search_language or "en", {}).get("value") or qid
        desc = item.get("description") or descriptions.get(used_search_language or "en", {}).get("value", "")
        strings = [label]
        for entries in aliases.values():
            strings.extend(a.get("value", "") for a in entries)
        exact = any(_normalize_text(s) == normalized_topic for s in strings if s)
        titles = {lang: sitelinks.get(f"{lang}wiki", {}).get("title") for lang in languages}
        coverage_count = sum(1 for v in titles.values() if v)
        coverage = coverage_count / len(languages)
        score = (4.0 if exact else 0.0) + 2.0 * coverage + 1.0 / (rank + 1)
        candidates.append(
            {
                "qid": qid,
                "label": label,
                "description": desc,
                "exact_label_or_alias_match": exact,
                "sitelink_coverage": round(coverage, 3),
                "has_full_language_coverage": coverage_count == len(languages),
                "titles": titles,
                "score": round(score, 3),
            }
        )
    candidates.sort(key=lambda x: x["score"], reverse=True)

    recommended = None
    ambiguous = True
    if candidates:
        top = candidates[0]
        second_score = candidates[1]["score"] if len(candidates) > 1 else -999
        if top["sitelink_coverage"] > 0 and (
            top["exact_label_or_alias_match"] and not (len(candidates) > 1 and candidates[1]["exact_label_or_alias_match"])
            or top["score"] - second_score >= 1.0
            or len(candidates) == 1
        ):
            recommended = top["qid"]
            ambiguous = False

    missing_languages: list[str] = []
    ready_for_analysis = False
    if recommended:
        selected = next((c for c in candidates if c["qid"] == recommended), None)
        if selected:
            missing_languages = [lang for lang, title in selected["titles"].items() if not title]
            ready_for_analysis = len(missing_languages) == 0

    result = {
        "topic": topic,
        "languages": languages,
        "search_language_used": used_search_language,
        "recommended_qid": recommended,
        "ambiguous": ambiguous,
        "ready_for_analysis": ready_for_analysis,
        "missing_languages": missing_languages,
        "candidates": candidates,
    }
    if recommended and not ready_for_analysis:
        result["warning"] = (
            "The concept is resolved, but the selected Wikidata item has no article in: "
            + ", ".join(missing_languages)
            + ". Do not run a direct cross-language comparison unless the user explicitly confirms a proxy/title mapping or changes the language set."
        )
    return result


def get_entity_titles(qid: str, languages: list[str], client: CachedSession) -> dict[str, Any]:
    languages = _split_languages(languages)
    sites = [f"{lang}wiki" for lang in languages]
    payload = client.get_json(
        WIKIDATA_API,
        {
            "action": "wbgetentities",
            "format": "json",
            "ids": qid,
            "props": "labels|descriptions|sitelinks",
            "languages": "|".join(list(dict.fromkeys(["en"] + languages))),
            "sitefilter": "|".join(sites),
            "origin": "*",
        },
    )
    entity = payload.get("entities", {}).get(qid)
    if not entity or "missing" in entity:
        raise SkillError(f"Wikidata entity {qid} was not found.")
    label = entity.get("labels", {}).get("en", {}).get("value")
    if not label:
        for lang in languages:
            if lang in entity.get("labels", {}):
                label = entity["labels"][lang]["value"]
                break
    label = label or qid
    description = entity.get("descriptions", {}).get("en", {}).get("value", "")
    sitelinks = entity.get("sitelinks", {})
    titles = {lang: sitelinks.get(f"{lang}wiki", {}).get("title") for lang in languages}
    return {"qid": qid, "label": label, "description": description, "titles": titles}


def _fetch_aqs_series(
    project: str,
    article: str | None,
    window: DateWindow,
    granularity: str,
    access: str,
    agent: str,
    client: CachedSession,
) -> tuple[pd.Series, int]:
    start = _aqs_stamp(window.start)
    end = _aqs_stamp(window.end)
    if article is None:
        url = f"{AQS_BASE}/aggregate/{project}/{access}/{agent}/{granularity}/{start}/{end}"
    else:
        encoded = quote(article.replace(" ", "_"), safe="")
        url = f"{AQS_BASE}/per-article/{project}/{access}/{agent}/{encoded}/{granularity}/{start}/{end}"
    payload = client.get_json(url)
    items = payload.get("items", [])
    expected = _expected_index(window, granularity)
    values: dict[pd.Timestamp, float] = {}
    for item in items:
        ts = str(item.get("timestamp", ""))
        if len(ts) < 8:
            continue
        if granularity == "monthly":
            dt = pd.Timestamp(datetime.strptime(ts[:6], "%Y%m").replace(tzinfo=timezone.utc))
        else:
            dt = pd.Timestamp(datetime.strptime(ts[:8], "%Y%m%d").replace(tzinfo=timezone.utc))
        values[dt] = float(item.get("views", 0))
    series = pd.Series(values, dtype=float).reindex(expected, fill_value=0.0)
    series.name = "views"
    return series, len(items)


def _hac_trend(values: np.ndarray, periods_per_year: float, confidence: float) -> dict[str, float | None]:
    y_raw = np.asarray(values, dtype=float)
    valid = np.isfinite(y_raw)
    y_raw = y_raw[valid]
    n = len(y_raw)
    if n < 4 or np.allclose(y_raw, y_raw[0]):
        return {
            "annualized_pct": 0.0 if n >= 1 else None,
            "ci_low_pct": 0.0 if n >= 1 else None,
            "ci_high_pct": 0.0 if n >= 1 else None,
            "r2": 0.0 if n >= 1 else None,
            "slope": 0.0 if n >= 1 else None,
        }

    y = np.log1p(np.clip(y_raw, a_min=0, a_max=None))
    t = np.arange(n, dtype=float)
    t = t - t.mean()
    X = np.column_stack([np.ones(n), t])
    xtx_inv = np.linalg.pinv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    residuals = y - X @ beta

    # Newey-West HAC covariance with an automatic, bounded lag.
    lag = max(1, min(6, int(math.floor(4 * (n / 100) ** (2 / 9)))))
    S = np.zeros((2, 2), dtype=float)
    for i in range(n):
        xi = X[i][:, None]
        S += residuals[i] ** 2 * (xi @ xi.T)
    for L in range(1, lag + 1):
        weight = 1.0 - L / (lag + 1)
        Gamma = np.zeros((2, 2), dtype=float)
        for i in range(L, n):
            xi = X[i][:, None]
            xj = X[i - L][:, None]
            Gamma += residuals[i] * residuals[i - L] * (xi @ xj.T)
        S += weight * (Gamma + Gamma.T)
    cov = xtx_inv @ S @ xtx_inv
    se_slope = float(math.sqrt(max(cov[1, 1], 0.0)))

    # Normal critical value is adequate for this lightweight workflow.
    if abs(confidence - 0.90) < 1e-9:
        z = 1.6448536269514722
    elif abs(confidence - 0.99) < 1e-9:
        z = 2.5758293035489004
    else:
        z = 1.959963984540054

    slope = float(beta[1])
    low = slope - z * se_slope
    high = slope + z * se_slope

    def annualize(s: float) -> float:
        # guard against floating overflow on pathological tiny/zero series
        exponent = float(np.clip(s * periods_per_year, -20, 20))
        return (math.exp(exponent) - 1.0) * 100.0

    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 0.0 if ss_tot <= 1e-12 else max(0.0, 1.0 - ss_res / ss_tot)
    return {
        "annualized_pct": annualize(slope),
        "ci_low_pct": annualize(low),
        "ci_high_pct": annualize(high),
        "r2": r2,
        "slope": slope,
    }


def analyze_series(df_lang: pd.DataFrame, granularity: str, confidence: float) -> dict[str, Any]:
    if df_lang.empty:
        raise SkillError("No data rows available for analysis.")
    df = df_lang.sort_values("date").copy()
    n = len(df)
    periods_per_year = 12.0 if granularity == "monthly" else 365.25
    metric = df["share_per_million"].to_numpy(dtype=float)
    article = df["article_views"].to_numpy(dtype=float)
    trend = _hac_trend(metric, periods_per_year, confidence)

    if trend["ci_low_pct"] is None:
        direction = "unclear"
    elif trend["ci_low_pct"] > 0:
        direction = "increasing"
    elif trend["ci_high_pct"] < 0:
        direction = "decreasing"
    else:
        direction = "unclear"

    if granularity == "monthly":
        recent_n = min(6, n // 2)
        enough_n = n >= 18
    else:
        recent_n = min(90, n // 2)
        enough_n = n >= 180

    if recent_n > 0:
        prev = metric[-2 * recent_n : -recent_n]
        recent = metric[-recent_n:]
        prev_mean = float(np.mean(prev)) if len(prev) else math.nan
        recent_mean = float(np.mean(recent)) if len(recent) else math.nan
        if prev_mean > 0:
            recent_change = (recent_mean / prev_mean - 1.0) * 100.0
        elif recent_mean > 0:
            recent_change = None
        else:
            recent_change = 0.0
    else:
        recent_change = None

    total_article = float(np.sum(article))
    largest_period_share = (float(np.max(article)) / total_article * 100.0) if total_article > 0 else 0.0

    spike_robust = None
    if n >= 6 and np.any(np.isfinite(metric)):
        drop_idx = int(np.nanargmax(metric))
        reduced = np.delete(metric, drop_idx)
        reduced_trend = _hac_trend(reduced, periods_per_year, confidence)
        base_slope = trend.get("slope") or 0.0
        reduced_slope = reduced_trend.get("slope") or 0.0
        base_annual = float(trend.get("annualized_pct") or 0.0)
        reduced_annual = float(reduced_trend.get("annualized_pct") or 0.0)
        def meaningful_sign(value: float) -> int:
            if abs(value) < 1.0:
                return 0
            return 1 if value > 0 else -1
        spike_robust = meaningful_sign(base_annual) == meaningful_sign(reduced_annual)

    coverage = float(df["coverage_ratio"].iloc[0]) if "coverage_ratio" in df else 1.0
    ci_excludes_zero = bool((trend["ci_low_pct"] or 0) > 0 or (trend["ci_high_pct"] or 0) < 0)
    checks = {
        "enough_observations": enough_n,
        "coverage_at_least_90pct": coverage >= 0.90,
        "confidence_interval_excludes_zero": ci_excludes_zero,
        "spike_robust": bool(spike_robust),
    }
    passed = sum(checks.values())
    strength = "high" if passed == 4 else "moderate" if passed >= 2 else "low"
    reasons = [f"{name}={str(value).lower()}" for name, value in checks.items()]

    return {
        "observations": n,
        "coverage_ratio": round(coverage, 4),
        "total_article_views": int(round(total_article)),
        "median_period_views": round(float(np.median(article)), 2),
        "median_share_per_million": round(float(np.median(metric)), 6),
        "recent_vs_previous_window_pct": None if recent_change is None else round(float(recent_change), 2),
        "recent_window_periods": recent_n,
        "trend_direction": direction,
        "normalized_trend_annualized_pct": None if trend["annualized_pct"] is None else round(float(trend["annualized_pct"]), 2),
        "trend_ci_low_pct": None if trend["ci_low_pct"] is None else round(float(trend["ci_low_pct"]), 2),
        "trend_ci_high_pct": None if trend["ci_high_pct"] is None else round(float(trend["ci_high_pct"]), 2),
        "trend_r2_log_scale": None if trend["r2"] is None else round(float(trend["r2"]), 4),
        "largest_period_share_pct": round(largest_period_share, 2),
        "spike_robust": spike_robust,
        "evidence_strength": strength,
        "evidence_reasons": reasons,
    }


def _build_dataframe(
    titles: dict[str, str],
    languages: list[str],
    window: DateWindow,
    granularity: str,
    access: str,
    agent: str,
    client: CachedSession,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    rows: list[pd.DataFrame] = []
    fetch_meta: dict[str, dict[str, Any]] = {}
    for lang in languages:
        title = titles.get(lang)
        if not title:
            continue
        project = f"{lang}.wikipedia.org"
        article_series, article_returned = _fetch_aqs_series(project, title, window, granularity, access, agent, client)
        project_series, project_returned = _fetch_aqs_series(project, None, window, granularity, access, agent, client)
        expected = len(article_series)
        # Use project-series coverage as the data-availability check. Article zeros
        # may legitimately be omitted by analytics responses for low-traffic pages.
        coverage_ratio = min(1.0, project_returned / expected) if expected else 0.0
        frame = pd.DataFrame(
            {
                "date": article_series.index,
                "language": lang,
                "title": title,
                "article_views": article_series.values,
                "project_views": project_series.values,
            }
        )
        denom = frame["project_views"].replace(0, np.nan)
        frame["share_per_million"] = (frame["article_views"] / denom * 1_000_000.0).fillna(0.0)
        frame["coverage_ratio"] = coverage_ratio
        rows.append(frame)
        fetch_meta[lang] = {
            "article_items_returned": article_returned,
            "project_items_returned": project_returned,
            "expected_periods": expected,
            "coverage_ratio": round(coverage_ratio, 4),
        }
    if not rows:
        raise SkillError("No requested language had a usable article title.")
    return pd.concat(rows, ignore_index=True), fetch_meta


def _comparison_table(metrics: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for lang, m in metrics.items():
        out.append(
            {
                "language": lang,
                "trend_direction": m["trend_direction"],
                "annualized_normalized_trend_pct": m["normalized_trend_annualized_pct"],
                "trend_ci_low_pct": m["trend_ci_low_pct"],
                "trend_ci_high_pct": m["trend_ci_high_pct"],
                "recent_change_pct": m["recent_vs_previous_window_pct"],
                "median_share_per_million": m["median_share_per_million"],
                "total_article_views": m["total_article_views"],
                "largest_period_share_pct": m["largest_period_share_pct"],
                "spike_robust": m["spike_robust"],
                "evidence_strength": m["evidence_strength"],
                "evidence_reasons": m["evidence_reasons"],
            }
        )
    out.sort(key=lambda x: ((x["annualized_normalized_trend_pct"] is not None), x["annualized_normalized_trend_pct"] or -1e99), reverse=True)
    return out


def _plot_chart(df: pd.DataFrame, concept_label: str, out_file: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 5.2))
    for lang, g in df.groupby("language"):
        ax.plot(g["date"], g["share_per_million"], marker="o", markersize=2.5, linewidth=1.4, label=lang)
    ax.set_title(f"Normalized Wikipedia interest: {concept_label}")
    ax.set_ylabel("Article views per 1M project views")
    ax.set_xlabel("Date")
    ax.grid(True, alpha=0.25)
    ax.legend(title="Wikipedia")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    fig.tight_layout()
    fig.savefig(out_file, dpi=180)
    plt.close(fig)


def _format_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"


def _create_pdf(
    df: pd.DataFrame,
    analysis: dict[str, Any],
    out_file: Path,
) -> None:
    concept = analysis["concept"]["label"]
    metrics = analysis["metrics"]
    fig = plt.figure(figsize=(8.27, 11.69))
    fig.text(0.07, 0.95, "Wikipedia Interest Signal", fontsize=20, weight="bold")
    fig.text(0.07, 0.92, concept, fontsize=14)
    fig.text(
        0.07,
        0.892,
        f"{analysis['request']['start']} to {analysis['request']['end']} · {analysis['request']['granularity']} · normalized by total Wikipedia traffic",
        fontsize=8.5,
    )

    ax = fig.add_axes([0.09, 0.50, 0.84, 0.32])
    for lang, g in df.groupby("language"):
        ax.plot(g["date"], g["share_per_million"], linewidth=1.4, label=lang)
    ax.set_ylabel("Article views / 1M project views", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.grid(True, alpha=0.22)
    ax.legend(fontsize=8, ncol=min(4, max(1, len(metrics))))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=7))
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))

    fig.text(0.07, 0.455, "Evidence summary", fontsize=11, weight="bold")
    y = 0.425
    header = f"{'Wiki':<7} {'Trend':<11} {'Annualized trend (CI)':<31} {'Recent':<11} {'Views':>10} {'Evidence':>10}"
    fig.text(0.07, y, header, family="monospace", fontsize=7.3)
    y -= 0.025
    for lang, m in metrics.items():
        ci = f"{_format_pct(m['normalized_trend_annualized_pct'])} [{_format_pct(m['trend_ci_low_pct'])}, {_format_pct(m['trend_ci_high_pct'])}]"
        line = f"{lang:<7} {m['trend_direction']:<11} {ci:<31} {_format_pct(m['recent_vs_previous_window_pct']):<11} {m['total_article_views']:>10,} {m['evidence_strength']:>10}"
        fig.text(0.07, y, line, family="monospace", fontsize=7.2)
        y -= 0.023

    y -= 0.012
    fig.text(0.07, y, "Interpretation", fontsize=10.5, weight="bold")
    y -= 0.025
    statements = []
    for lang, m in metrics.items():
        statements.append(
            f"{lang}: {m['trend_direction']} normalized interest; annualized estimate {_format_pct(m['normalized_trend_annualized_pct'])}, evidence {m['evidence_strength']}."
        )
    interpretation = " ".join(statements)
    fig.text(0.07, y, textwrap.fill(interpretation, width=105), fontsize=8.5, va="top")

    caveat = (
        "Decision caveat: Wikipedia attention is a proxy, not demand or willingness to pay. Cross-language normalization reduces traffic-size bias but does not correct demographics, competition, conversion, page moves, or one-article topic coverage."
    )
    fig.text(0.07, 0.12, "Limitations", fontsize=10.5, weight="bold")
    fig.text(0.07, 0.095, textwrap.fill(caveat, width=110), fontsize=8.2, va="top")
    fig.text(0.07, 0.035, "Source: Wikimedia Analytics API + Wikidata · Generated by wikipedia-interest-analysis", fontsize=7.5)
    fig.savefig(out_file, format="pdf")
    plt.close(fig)


def run_analysis(
    *,
    qid: str | None,
    languages: list[str],
    start: str | None,
    end: str | None,
    out_dir: Path,
    client: CachedSession,
    granularity: str = "monthly",
    access: str = "all-access",
    agent: str = "user",
    confidence: float = 0.95,
    title_map: dict[str, str] | None = None,
    concept_label: str | None = None,
) -> dict[str, Any]:
    languages = _split_languages(languages)
    if granularity not in {"monthly", "daily"}:
        raise SkillError("Granularity must be monthly or daily.")
    if access not in {"all-access", "desktop", "mobile-app", "mobile-web"}:
        raise SkillError("Unsupported access mode.")
    if agent not in {"user", "all-agents", "spider", "automated"}:
        raise SkillError("Unsupported agent filter.")
    if confidence not in {0.90, 0.95, 0.99}:
        raise SkillError("Confidence must be 0.90, 0.95, or 0.99.")

    window = _parse_window(start, end, granularity)
    title_map = title_map or {}
    if qid:
        entity = get_entity_titles(qid, languages, client)
        titles = {lang: title_map.get(lang) or entity["titles"].get(lang) for lang in languages}
        concept = {"qid": qid, "label": concept_label or entity["label"], "description": entity["description"], "titles": titles}
    else:
        if not title_map:
            raise SkillError("Provide --qid or a --title-map JSON file.")
        titles = {lang: title_map.get(lang) for lang in languages}
        concept = {"qid": None, "label": concept_label or "Custom title mapping", "description": "", "titles": titles}

    missing = [lang for lang, title in titles.items() if not title]
    if missing:
        raise SkillError(
            "Selected concept has no sitelink/title for: " + ", ".join(missing) + ". Confirm an explicit title mapping instead of silently substituting another concept."
        )

    df, fetch_meta = _build_dataframe(titles, languages, window, granularity, access, agent, client)
    metrics = {lang: analyze_series(g, granularity, confidence) for lang, g in df.groupby("language")}
    out_dir.mkdir(parents=True, exist_ok=True)
    data_file = out_dir / "data.csv"
    chart_file = out_dir / "chart.png"
    pdf_file = out_dir / "report.pdf"
    json_file = out_dir / "analysis.json"

    export_df = df.copy()
    export_df["date"] = export_df["date"].dt.strftime("%Y-%m-%d")
    export_df.to_csv(data_file, index=False)
    _plot_chart(df, concept["label"], chart_file)

    analysis: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "request": {
            "languages": languages,
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "granularity": granularity,
            "access": access,
            "agent": agent,
            "confidence": confidence,
        },
        "concept": concept,
        "metric_definition": "share_per_million = article user pageviews / total Wikipedia project user pageviews * 1,000,000",
        "metrics": metrics,
        "comparison": _comparison_table(metrics),
        "fetch_metadata": fetch_meta,
        "warnings": window.warnings,
        "limitations": [
            "Wikipedia attention is not willingness to pay or product-market fit.",
            "One article may not represent a broad topic.",
            "Cross-language normalization does not remove demographic differences.",
            "News spikes, page moves, redirects, merges, or splits can distort trends.",
        ],
        "artifacts": {
            "data_csv": str(data_file),
            "chart_png": str(chart_file),
            "report_pdf": str(pdf_file),
        },
    }
    _create_pdf(df, analysis, pdf_file)
    json_file.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    return analysis


def reanalyze_file(input_csv: Path, out_dir: Path, confidence: float) -> dict[str, Any]:
    if confidence not in {0.90, 0.95, 0.99}:
        raise SkillError("Confidence must be 0.90, 0.95, or 0.99.")
    df = pd.read_csv(input_csv)
    required = {"date", "language", "title", "article_views", "project_views", "share_per_million", "coverage_ratio"}
    missing = required - set(df.columns)
    if missing:
        raise SkillError(f"Input CSV is missing required columns: {sorted(missing)}")
    df["date"] = pd.to_datetime(df["date"], utc=True)
    if df.empty:
        raise SkillError("Input CSV is empty.")
    # infer granularity from median gap
    dates = sorted(df["date"].drop_duplicates())
    median_gap = float(np.median(np.diff(pd.DatetimeIndex(dates).asi8) / 86_400_000_000_000)) if len(dates) > 1 else 30.0
    granularity = "daily" if median_gap <= 2 else "monthly"
    metrics = {lang: analyze_series(g, granularity, confidence) for lang, g in df.groupby("language")}
    titles = {lang: g["title"].iloc[0] for lang, g in df.groupby("language")}
    label = "Saved Wikipedia concept"
    out_dir.mkdir(parents=True, exist_ok=True)
    data_file = out_dir / "data.csv"
    chart_file = out_dir / "chart.png"
    pdf_file = out_dir / "report.pdf"
    json_file = out_dir / "analysis.json"
    export_df = df.copy()
    export_df["date"] = export_df["date"].dt.strftime("%Y-%m-%d")
    export_df.to_csv(data_file, index=False)
    _plot_chart(df, label, chart_file)
    analysis = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "request": {
            "languages": sorted(metrics),
            "start": df["date"].min().date().isoformat(),
            "end": df["date"].max().date().isoformat(),
            "granularity": granularity,
            "access": "reused",
            "agent": "reused",
            "confidence": confidence,
        },
        "concept": {"qid": None, "label": label, "description": "Reanalysis of saved data", "titles": titles},
        "metric_definition": "share_per_million = article user pageviews / total Wikipedia project user pageviews * 1,000,000",
        "metrics": metrics,
        "comparison": _comparison_table(metrics),
        "warnings": ["Reanalysis reused saved data and did not refetch Wikimedia APIs."],
        "limitations": [
            "Wikipedia attention is not willingness to pay or product-market fit.",
            "One article may not represent a broad topic.",
            "Cross-language normalization does not remove demographic differences.",
        ],
        "artifacts": {"data_csv": str(data_file), "chart_png": str(chart_file), "report_pdf": str(pdf_file)},
    }
    _create_pdf(df, analysis, pdf_file)
    json_file.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    return analysis


def _client_from_args(args: argparse.Namespace) -> CachedSession:
    user_agent = getattr(args, "user_agent", None) or os.environ.get("WIKIMEDIA_USER_AGENT", DEFAULT_USER_AGENT)
    cache_dir = Path(getattr(args, "cache_dir", ".cache/wiki-interest"))
    return CachedSession(user_agent, cache_dir)


def _load_title_map(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    p = Path(path)
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillError(f"Could not read title-map JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SkillError("Title-map must be a JSON object such as {\"uk\": \"Астрономія\"}.")
    return {str(k).lower(): str(v) for k, v in payload.items()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze Wikipedia topic interest across language editions.")
    sub = parser.add_subparsers(dest="command", required=True)

    common_network = argparse.ArgumentParser(add_help=False)
    common_network.add_argument("--cache-dir", default=".cache/wiki-interest")
    common_network.add_argument("--user-agent", default=None, help="Override WIKIMEDIA_USER_AGENT.")

    p_resolve = sub.add_parser("resolve", parents=[common_network], help="Resolve a topic to Wikidata candidates and language sitelinks.")
    p_resolve.add_argument("--topic", required=True)
    p_resolve.add_argument("--languages", required=True, help="Comma-separated Wikipedia language codes.")
    p_resolve.add_argument("--search-language", default=None)
    p_resolve.add_argument("--limit", type=int, default=6)

    p_analyze = sub.add_parser("analyze", parents=[common_network], help="Fetch data, analyze trend, and generate artifacts.")
    p_analyze.add_argument("--qid", default=None)
    p_analyze.add_argument("--languages", required=True)
    p_analyze.add_argument("--start", default=None)
    p_analyze.add_argument("--end", default=None)
    p_analyze.add_argument("--granularity", choices=["monthly", "daily"], default="monthly")
    p_analyze.add_argument("--access", choices=["all-access", "desktop", "mobile-app", "mobile-web"], default="all-access")
    p_analyze.add_argument("--agent", choices=["user", "all-agents", "spider", "automated"], default="user")
    p_analyze.add_argument("--confidence", type=float, choices=[0.90, 0.95, 0.99], default=0.95)
    p_analyze.add_argument("--title-map", default=None, help="Optional JSON mapping language codes to exact article titles.")
    p_analyze.add_argument("--concept-label", default=None)
    p_analyze.add_argument("--out", required=True)

    p_re = sub.add_parser("reanalyze", help="Recompute metrics/report from a saved data.csv without network calls.")
    p_re.add_argument("--input", required=True)
    p_re.add_argument("--out", required=True)
    p_re.add_argument("--confidence", type=float, choices=[0.90, 0.95, 0.99], default=0.95)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "resolve":
            client = _client_from_args(args)
            result = resolve_topic(args.topic, _split_languages(args.languages), args.search_language, client, args.limit)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "analyze":
            client = _client_from_args(args)
            analysis = run_analysis(
                qid=args.qid,
                languages=_split_languages(args.languages),
                start=args.start,
                end=args.end,
                out_dir=Path(args.out),
                client=client,
                granularity=args.granularity,
                access=args.access,
                agent=args.agent,
                confidence=args.confidence,
                title_map=_load_title_map(args.title_map),
                concept_label=args.concept_label,
            )
            # Keep stdout compact for small agents.
            compact = {
                "concept": analysis["concept"],
                "request": analysis["request"],
                "comparison": analysis["comparison"],
                "warnings": analysis["warnings"],
                "limitations": analysis["limitations"],
                "artifacts": analysis["artifacts"],
            }
            print(json.dumps(compact, ensure_ascii=False, indent=2))
            return 0
        if args.command == "reanalyze":
            analysis = reanalyze_file(Path(args.input), Path(args.out), args.confidence)
            print(json.dumps({"comparison": analysis["comparison"], "artifacts": analysis["artifacts"]}, ensure_ascii=False, indent=2))
            return 0
    except SkillError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
