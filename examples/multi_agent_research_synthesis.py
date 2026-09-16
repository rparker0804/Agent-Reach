# -*- coding: utf-8 -*-
"""
Planner -> Research (3 subagents) -> Synthesis pipeline, built on Agent Reach.

Agent Reach is a glue layer, not a Python SDK: it installs and configures
upstream CLIs (mcporter/Exa, gh, twitter-cli, ...) and agents call those CLIs
directly (see SKILL.md / `agent-reach doctor --json`). The tool functions
below follow that contract — they shell out to the same commands documented
in agent_reach/skill/SKILL.md instead of reimplementing search/scraping.

Install:
    pip install subagents-pydantic-ai pydantic-ai
    agent-reach install --env=auto   # configures mcporter + Exa for web_search()

This is a starting point, not a finished product: swap MODEL for whatever
provider/model you use, add more Agent Reach-backed tools (twitter/reddit/
xiaohongshu need login cookies — see `agent-reach doctor --json` before
wiring them in), and adjust the prompts for your use case.
"""

import asyncio
import json
import shutil
import subprocess

from pydantic_ai import Agent
from pydantic_ai.toolsets import FunctionToolset
from subagents_pydantic_ai import SubAgentCapability, SubAgentConfig

MODEL = "anthropic:claude-sonnet-5"  # swap for a cheaper model on subagents if needed

# --- Agent Reach-backed tools -------------------------------------------
#
# These call the zero-config commands from SKILL.md's routing table via
# subprocess, exactly as an agent would from a shell — never through a
# private Agent Reach Python API. Each degrades to an error string instead
# of raising, so one missing upstream tool doesn't crash the whole pipeline.


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


def web_search(query: str, num_results: int = 5) -> str:
    """Search the web via Exa (through mcporter). Zero-config once
    `agent-reach install` has run."""
    if not shutil.which("mcporter"):
        return "[mcporter not installed — run `agent-reach install`]"
    return _run([
        "mcporter", "call", "exa.web_search_exa",
        f"query={query}", f"numResults={num_results}",
    ])


def read_url(url: str) -> str:
    """Read a web page's main content via the Jina Reader proxy."""
    return _run(["curl", "-s", f"https://r.jina.ai/{url}"])


def github_search_repos(query: str, limit: int = 10) -> str:
    """Search public GitHub repos via the `gh` CLI (no auth needed)."""
    return _run([
        "gh", "search", "repos", query, "--sort", "stars", "--limit", str(limit),
    ])


research_toolset = FunctionToolset(tools=[web_search, read_url, github_search_repos])

# --- Specialist research subagents -----------------------------------

competitor_agent = Agent(
    MODEL,
    instructions=(
        "You research competitors for a given product idea using web_search "
        "and github_search_repos. Return: top 3-5 competitors, their pricing, "
        "and positioning. Be concise, no fluff."
    ),
    toolsets=[research_toolset],
)

audience_agent = Agent(
    MODEL,
    instructions=(
        "You research target audience for a given product idea using "
        "web_search and read_url. Return: primary buyer persona, pain "
        "points, where they hang out online."
    ),
    toolsets=[research_toolset],
)

trends_agent = Agent(
    MODEL,
    instructions=(
        "You research market/category trends relevant to a given product "
        "idea using web_search. Return: 3 relevant trends with one-line "
        "evidence for each."
    ),
    toolsets=[research_toolset],
)

# --- Research agent: delegates to the 3 specialists above -------------

research_agent = Agent(
    MODEL,
    instructions=(
        "You coordinate market research. Delegate to your subagents, "
        "then merge their findings into one structured summary."
    ),
    capabilities=[
        SubAgentCapability(
            default_model=MODEL,
            subagents=[
                SubAgentConfig(
                    name="competitors", description="Researches competitors",
                    instructions="", agent=competitor_agent,
                ),
                SubAgentConfig(
                    name="audience", description="Researches target audience",
                    instructions="", agent=audience_agent,
                ),
                SubAgentConfig(
                    name="trends", description="Researches market trends",
                    instructions="", agent=trends_agent,
                ),
            ],
        )
    ],
)

# --- Synthesis agent: turns research into a usable deliverable --------

synthesis_agent = Agent(
    MODEL,
    instructions=(
        "You turn raw market research into a short, actionable brief: "
        "positioning statement, target audience, and 3 marketing angles. "
        "No filler, no restating the research verbatim."
    ),
)

# --- Planner: top-level orchestrator -----------------------------------

planner_agent = Agent(
    MODEL,
    instructions=(
        "You are a planning agent for product marketing tasks. "
        "Given a product idea, decide what research is needed, "
        "delegate it, then pass results to synthesis. "
        "Track and report token/cost usage from each subagent call."
    ),
    capabilities=[
        SubAgentCapability(
            default_model=MODEL,
            subagents=[
                SubAgentConfig(
                    name="research", description="Runs competitor/audience/trends research",
                    instructions="", agent=research_agent,
                ),
                SubAgentConfig(
                    name="synthesis", description="Turns research into a marketing brief",
                    instructions="", agent=synthesis_agent,
                ),
            ],
        )
    ],
)


async def run_pipeline(product_idea: str) -> dict:
    """
    Entry point. Returns the final brief plus a token-usage breakdown
    per agent so you can see where your budget is going.
    """
    result = await planner_agent.run(
        f"Product idea: {product_idea}\n\n"
        "1. Delegate market research (competitors, audience, trends).\n"
        "2. Pass findings to the synthesis agent for a final brief.\n"
        "3. Return the brief plus a per-agent token usage summary."
    )

    return {
        "brief": result.output,
        "usage": result.usage(),  # per-call token tracking from the library
    }


if __name__ == "__main__":
    idea = "AI-powered meal planning app for people with food allergies"
    output = asyncio.run(run_pipeline(idea))
    print(output["brief"])
    print("\n--- token usage ---")
    print(json.dumps(output["usage"].__dict__, default=str, indent=2))
