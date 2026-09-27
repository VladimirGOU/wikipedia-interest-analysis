#!/usr/bin/env python3
"""Minimal OpenRouter tool-use harness for testing the skill on a cheap model."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "wiki_interest.py"
spec = importlib.util.spec_from_file_location("wiki_interest", MODULE_PATH)
wiki = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = wiki
spec.loader.exec_module(wiki)


def build_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "wikipedia_interest_skill",
            "description": "Resolve a Wikipedia topic through Wikidata or analyze a resolved QID and produce data/chart/PDF artifacts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["resolve", "analyze"]},
                    "topic": {"type": "string"},
                    "qid": {"type": "string"},
                    "languages": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "search_language": {"type": "string"},
                    "start": {"type": "string", "description": "YYYY-MM-DD"},
                    "end": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["action", "languages"],
                "additionalProperties": False,
            },
        },
    }


def execute_tool(args: dict[str, Any], out_root: Path) -> dict[str, Any]:
    user_agent = os.environ.get("WIKIMEDIA_USER_AGENT", wiki.DEFAULT_USER_AGENT)
    client = wiki.CachedSession(user_agent, ROOT / ".cache" / "agent-test")
    action = args["action"]
    languages = args["languages"]
    if action == "resolve":
        if not args.get("topic"):
            return {"error": "topic is required for resolve"}
        return wiki.resolve_topic(args["topic"], languages, args.get("search_language"), client)
    if action == "analyze":
        if not args.get("qid"):
            return {"error": "qid is required for analyze; resolve first"}
        out_dir = out_root / args["qid"].lower()
        analysis = wiki.run_analysis(
            qid=args["qid"],
            languages=languages,
            start=args.get("start"),
            end=args.get("end"),
            out_dir=out_dir,
            client=client,
        )
        return {
            "concept": analysis["concept"],
            "request": analysis["request"],
            "comparison": analysis["comparison"],
            "warnings": analysis["warnings"],
            "limitations": analysis["limitations"],
            "artifacts": analysis["artifacts"],
        }
    return {"error": f"unsupported action: {action}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="openrouter/free")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--out", default="outputs/openrouter-agent-test")
    args = parser.parse_args()

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY is required", file=sys.stderr)
        return 2

    skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": "You are a low-cost tool-using agent. Follow the loaded Agent Skill exactly. Do not calculate Wikipedia analytics yourself.\n\n" + skill_text,
        },
        {"role": "user", "content": args.prompt},
    ]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    tool = build_tool()
    tool_trace: list[dict[str, Any]] = []
    analyzed = False

    for _ in range(args.max_turns):
        payload = {"model": args.model, "messages": messages, "tools": [tool], "tool_choice": "auto"}
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=120)
        if not r.ok:
            print(f"OpenRouter error {r.status_code}: {r.text[:1000]}", file=sys.stderr)
            return 3
        data = r.json()
        message = data["choices"][0]["message"]
        messages.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            print(message.get("content", ""))
            print("\nTOOL_TRACE=" + json.dumps(tool_trace, ensure_ascii=False))
            if not analyzed:
                print("Agent test failed: model never called analyze.", file=sys.stderr)
                return 4
            return 0
        for call in calls:
            fn_args = json.loads(call["function"]["arguments"])
            result = execute_tool(fn_args, Path(args.out))
            tool_trace.append({"args": fn_args, "result_summary": result.get("comparison") or result.get("recommended_qid") or result.get("error")})
            if fn_args.get("action") == "analyze" and "error" not in result:
                analyzed = True
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": call["function"]["name"],
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    print("Agent test failed: max turns exhausted.", file=sys.stderr)
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
