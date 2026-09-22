---
name: add-channel
description: "Use when adding a new platform channel to Agent Reach or switching or adding a backend for an existing one: which files change together, the Channel contract, and the tests CI expects"
metadata:
  version: "1.0.0"
  source: local-git-analysis
  reference_commit: "b060a48 feat(channels): add facebook and instagram opencli support"
---

# Add a Channel

## When to Use

- You're adding support for a new platform (a new file under `agent_reach/channels/`).
- You're adding or reordering backends for an existing channel.

## How It Works

Agent Reach doesn't fetch content itself. A channel only (1) recognizes URLs and
(2) reports whether an upstream tool is installed and healthy. Agents then call
that upstream tool directly, using the commands documented in the bundled skill.

### 1. Channel file: `agent_reach/channels/<name>.py`

- For an OpenCLI-backed site, subclass `OpenCLISiteChannel` and declare
  `name`, `description`, `site`, `domains`, `usage` and `login_hint`. See
  `facebook.py`.
- Otherwise subclass `Channel`. Set `name`, `description`, `backends` (ordered,
  preferred first) and `tier` (0/1/2), then implement:
  - `can_handle(url)`: use `agent_reach.utils.url.host_matches(url, "example.com")`,
    not substring checks.
  - `check(config=None) -> (status, message)`: status is one of
    `ok`/`warn`/`off`/`error`. Iterate `self.ordered_backends(config)`, probe each
    with `agent_reach.probe.probe_command(...)`, and set `self.active_backend`
    to the working one or `None`. Keep it read-only and bounded: timeouts, no
    installs, no writes.
- For HTTP APIs: validate the target host/scheme/path before the request, set
  a timeout, cap the response size, and pass any echoed URLs through
  `scrub_url_credentials`. Give subprocesses `env=utf8_subprocess_env()`.

### 2. Register it

- Import the channel and add an instance to `ALL_CHANNELS` in
  `agent_reach/channels/__init__.py`. Names must be unique.
- If it needs install or config steps, wire them in `agent_reach/cli.py`, and
  add a `agent_reach/guides/setup-<name>.md` if setup is non-trivial.

### 3. Tests

- `tests/test_<name>_channel.py`: `can_handle` matches and rejects;
  `check()` in the ok, warn and missing cases, with `active_backend` asserted
  for each; and hostile URLs rejected before the network is touched.
- Add the channel to the expectations in `tests/test_channel_contracts.py` and
  `tests/test_channels.py`. Add CLI coverage in `tests/test_cli.py` if
  `cli.py` changed.
- Mock `shutil.which`, `subprocess.run` and `urllib.request.urlopen`. Tests
  must never hit the network.

### 4. Docs and bundled skill (same commit, both languages)

- `agent_reach/skill/SKILL.md` and `agent_reach/skill/SKILL_en.md`: routing
  table row and platform trigger words.
- `agent_reach/skill/references/<category>.md`: the upstream commands agents
  should run (social, video, dev, career, finance, search or web).
- `README.md` and `docs/README_en.md` (plus `README_ja.md`/`README_ko.md`),
  `docs/install.md`, `llms.txt`. Update the platform count if it changes.

### 5. Verify

```bash
pytest tests/ -q                     # all green (unset GH_TOKEN/GITHUB_TOKEN locally)
python -m agent_reach.cli doctor     # new channel shows under the right tier
```

## Examples

- Minimal OpenCLI channel: `agent_reach/channels/facebook.py` (13 lines).
- Public API channel with strict host validation and TLS retry:
  `agent_reach/channels/v2ex.py` + `tests/test_v2ex_channel.py`.
- Commit message: `feat(channels): add <name> <backend> support`.
