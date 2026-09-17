# -*- coding: utf-8 -*-
"""
Feed Agent Reach research into OpenMontage (https://github.com/calesthio/OpenMontage),
an open-source agentic video production system (AGPLv3).

These are two separate, independently-licensed tools — this script does not
copy or vendor any OpenMontage code. It only gathers research with Agent
Reach's documented CLI commands (see agent_reach/skill/SKILL.md) and writes
it out shaped like OpenMontage's `research_brief` artifact (schema:
openmontage/schemas/artifacts/research_brief.schema.json in that repo), so
a documentary-montage or similar OpenMontage pipeline can pick it up as a
research seed instead of starting from nothing.

Install:
    pip install subagents-pydantic-ai pydantic-ai   # optional, for --synthesize
    agent-reach install --env=auto                   # configures mcporter + Exa

Usage:
    python examples/openmontage_research_brief.py "topic" -o research_brief.json
    python examples/openmontage_research_brief.py "topic" -o research_brief.json --synthesize

Without --synthesize this only collects raw material (search hits, forum
threads) into the brief's `sources`/`landscape.existing_content` fields —
still useful as a head start, but you'll want to fill in `data_points`,
`audience_insights`, and `angles_discovered` by hand or with an LLM.
--synthesize uses one Claude call (needs ANTHROPIC_API_KEY) to do that
synthesis for you, into a schema-shaped JSON object.
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import date


def _run(cmd: list[str], timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
        if result.returncode != 0:
            return f"[{cmd[0]} failed] {result.stderr.strip()[:500]}"
        return result.stdout.strip()
    except FileNotFoundError:
        return f"[{cmd[0]} not installed — run `agent-reach install`]"
    except subprocess.TimeoutExpired:
        return f"[{cmd[0]} timed out after {timeout}s]"


def web_search(query: str, num_results: int = 8) -> str:
    """Search the web via Exa (through mcporter). Zero-config once
    `agent-reach install` has run."""
    if not shutil.which("mcporter"):
        return "[mcporter not installed — run `agent-reach install`]"
    return _run([
        "mcporter", "call", "exa.web_search_exa",
        f"query={query}", f"numResults={num_results}",
    ])


def gather_raw_research(topic: str) -> dict:
    """Collect raw material for an OpenMontage research_brief via Agent
    Reach's zero-config search — no LLM, no API key required."""
    return {
        "topic": topic,
        "existing_content_search": web_search(f"{topic} video explainer analysis"),
        "discussion_search": web_search(f"{topic} discussion opinions reddit forum"),
        "data_search": web_search(f"{topic} statistics facts study data"),
    }


def build_raw_brief(topic: str, raw: dict) -> dict:
    """Shape raw search output into the research_brief.schema.json layout
    (see OpenMontage's schemas/artifacts/research_brief.schema.json) without
    filling in fields that genuinely need synthesis/judgment."""
    return {
        "version": "1.0",
        "topic": topic,
        "research_date": date.today().isoformat(),
        "landscape": {
            "existing_content": [],
            "saturated_angles": [],
            "underserved_gaps": [],
        },
        "data_points": [],
        "audience_insights": {
            "common_questions": [],
            "misconceptions": [],
            "knowledge_level": "",
        },
        "angles_discovered": [],
        "sources": [],
        "research_summary": "",
        "metadata": {
            "raw_agent_reach_research": raw,
            "note": (
                "Raw Agent Reach search output is in metadata.raw_agent_reach_research. "
                "landscape/data_points/audience_insights/angles_discovered/sources "
                "need to be filled in (by hand, or re-run with --synthesize) before "
                "this satisfies research_brief.schema.json's required fields."
            ),
        },
    }


async def synthesize_brief(topic: str, raw: dict) -> dict:
    """Use one Claude call to shape raw research into a schema-conformant
    research_brief. Needs ANTHROPIC_API_KEY and `pip install pydantic-ai`."""
    from pydantic_ai import Agent

    agent = Agent(
        "anthropic:claude-sonnet-5",
        instructions=(
            "You turn raw web research into a JSON object matching this shape "
            "exactly (OpenMontage's research_brief.schema.json):\n"
            "{version: '1.0', topic, research_date (YYYY-MM-DD), "
            "landscape: {existing_content: [{title, url, source, angle, "
            "what_it_covers, what_it_misses, engagement_signal}] (>=3), "
            "saturated_angles: [str], underserved_gaps: [str] (>=1)}, "
            "data_points: [{claim, source_url, source_name, credibility: "
            "primary_source|secondary_source|anecdotal, surprise_factor: "
            "expected|notable|surprising|counterintuitive, usable_as}] (>=3), "
            "audience_insights: {common_questions: [str] (>=3), "
            "misconceptions: [{myth, reality, source}], knowledge_level, "
            "pain_points: [str]}, "
            "angles_discovered: [{name, hook, type: trending|evergreen|"
            "contrarian|narrative|data_driven, why_now, grounded_in: [str]}] "
            "(>=3), sources: [{url, title, used_for, reliability: primary|"
            "secondary|anecdotal}] (>=5), research_summary}. "
            "Output ONLY the JSON object, no prose, no markdown fences. "
            "Ground every field in the raw research given — do not invent "
            "URLs or quotes that aren't in it."
        ),
    )
    result = await agent.run(
        f"Topic: {topic}\n\nRaw research:\n{json.dumps(raw, indent=2)}"
    )
    text = result.output.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic", help="Video topic to research")
    parser.add_argument("-o", "--output", default="research_brief.json")
    parser.add_argument(
        "--synthesize", action="store_true",
        help="Use Claude to shape the research into a full schema-conformant brief",
    )
    args = parser.parse_args()

    raw = gather_raw_research(args.topic)

    if args.synthesize:
        import asyncio
        brief = asyncio.run(synthesize_brief(args.topic, raw))
    else:
        brief = build_raw_brief(args.topic, raw)

    with open(args.output, "w") as f:
        json.dump(brief, f, indent=2)

    print(f"Wrote {args.output}", file=sys.stderr)
    if not args.synthesize:
        print(
            "This is a raw draft — re-run with --synthesize (needs "
            "ANTHROPIC_API_KEY) for a schema-conformant research_brief.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
