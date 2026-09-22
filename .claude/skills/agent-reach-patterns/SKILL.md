---
name: agent-reach-patterns
description: "Use when working in Agent Reach, especially before editing a channel, touching credential/URL/subprocess handling, updating the bundled agent SKILL.md or READMEs, placing tests, or writing commits — conventions measured from git history"
metadata:
  version: "1.0.0"
  source: local-git-analysis
  analyzed_commits: "126"
---

# Agent Reach Patterns

Agent Reach is an installer + doctor + config tool for 15 internet channels.
After install, agents call upstream tools (OpenCLI, yt-dlp, gh, mcporter,
public APIs) directly. Agent Reach only routes and probes — it does not wrap.

## Commit Conventions

- 105 of 106 non-merge commits use `type(scope): message` (the other is a
  `[codex] ...` one-off). Types by frequency: `fix` 50, `docs` 24, `test` 14,
  `feat` 12, `chore` 3, `security` 1, `refactor` 1.
- Scope is the area touched: a channel name (`youtube`, `v2ex`, `xueqiu`,
  `xhs`), or `security`, `readme`, `skill`, `docs`, `cli`, `doctor`, `ci`,
  `transcribe`, `examples`.
- Security fixes go under `fix(security): ...` (7 commits), e.g.
  "enforce least-privilege credential boundaries", "honor HOME for credential
  paths". Messages are imperative and lowercase.
- Test-only coverage PRs use `test(<channel>): ...` from `test/<channel>-...`
  branches. Other work comes from `codex/...` or `claude/...` branches, merged
  to `main` by PR.
- Version bumps are `chore: bump version to X.Y.Z` and must touch
  `pyproject.toml`, `agent_reach/__init__.py` and the version expectations in
  `tests/test_cli.py` together.

## Code Architecture

- `agent_reach/channels/<platform>.py`: one file per platform. Each is a
  subclass of `Channel` (`channels/base.py`). It sets `name`, `description`,
  `backends` (ordered; `backends[0]` is preferred) and `tier` (0 = zero config,
  1 = free key, 2 = setup), and implements `can_handle(url)` and
  `check(config) -> (status, message)`. The status is one of
  `ok`/`warn`/`off`/`error`.
  - Note: the root `CLAUDE.md` mentions `BaseChannel` with `read`/`search`.
    The code has neither. The real contract is `Channel` with
    `can_handle` + `check` + `active_backend`.
- `check()` must set `self.active_backend` to the backend that is actually
  serving, or `None`. `shutil.which()` alone is not proof of health: probe with
  `agent_reach.probe.probe_command(cmd, args, package=...)`, which runs a
  side-effect-free version/status command. Honor user overrides through
  `self.ordered_backends(config)` (`<channel>_backend` config key or
  `<CHANNEL>_BACKEND` env var).
- OpenCLI-backed sites (facebook, instagram, ...) are about 10-line subclasses
  of `OpenCLISiteChannel` in `channels/_opencli_site.py`. They declare `site`,
  `domains`, `usage` and `login_hint`.
- Registry: add each channel's import and instance to `ALL_CHANNELS` in
  `channels/__init__.py`. The doctor (`doctor.py`) groups channels by `tier`.
- Shared helpers live in `agent_reach/utils/`. Use them; don't re-implement:
  - `url.normalize_public_http_url`, `url.host_matches`, `url.domain_matches`:
    SSRF-safe URL handling that blocks localhost, `.internal`, metadata hosts
    and private IPs.
  - `text.scrub_url_credentials`: strip credentials from anything that is
    echoed or logged.
  - `paths.atomic_write_private_text`, `paths.make_private_dir`,
    `paths.ensure_no_symlink_path`, `paths.home_dir`: credential files are
    written atomically with mode 0o600, symlinks are refused, and `HOME` is
    honored.
  - `process.utf8_subprocess_env`: pass as `env=` to every subprocess (for
    Windows encoding).
- Network code bounds everything: explicit timeouts, capped response size
  (`v2ex.py` caps at 1 MiB), allowlisted hosts/paths validated before the
  request, and HTTPS only.
- Doctor and `check()` must stay read-only: no installs, writes or config
  mutation during diagnostics ("make diagnostics provably read-only").
- User-facing CLI/doctor strings are mostly Chinese. Match the surrounding
  language.
- Ruff: `select = ["E", "F", "I"]`, line length 100, target py310. Mypy runs
  on the package (tests excluded).

## Workflows

- **Channel fix** (most common): `agent_reach/channels/<x>.py` +
  `tests/test_<x>_channel.py` change together, often with nothing else.
- **New channel**: `channels/<x>.py` + `channels/__init__.py` +
  `tests/test_channels.py` + `tests/test_channel_contracts.py` +
  `tests/test_cli.py` + `cli.py` + bundled skill (`skill/SKILL.md`,
  `skill/SKILL_en.md`, `skill/references/<category>.md`) + `README.md`,
  `docs/README_en.md`, `docs/install.md`, `llms.txt`. See the `add-channel`
  skill.
- **Docs pairs**: `README.md` and `docs/README_en.md` change together in 26 of
  38 commits touching either (`README_ja.md`/`README_ko.md` follow for
  user-facing features). `skill/SKILL.md` and `skill/SKILL_en.md` change
  together in 10 of 12. Update both languages in the same commit.
- **Security hardening**: a sweep across many `channels/*.py` plus
  `utils/{url,text,paths}.py`, each with a dedicated test
  (`test_url_security.py`, `test_scrub_credentials.py`,
  `test_cookie_security.py`, `test_private_file_writes.py`,
  `test_doctor_credential_boundaries.py`).
- Cookie auth (Twitter, XHS): Cookie-Editor export only. Never QR login.

## Testing Patterns

- `pytest` with flat files in `tests/`: `test_<channel>_channel.py` for
  per-channel behavior, `test_channel_contracts.py` for registry-wide
  invariants (unique names, valid tier, `check()` status set, `active_backend`
  type).
- Tests are hermetic: `monkeypatch` `shutil.which`, `subprocess.run` and
  `urllib.request.urlopen` (raise `URLError("offline")`), and point
  `Config(config_path=tmp_path / "config.yaml")` at `tmp_path`.
  `tests/conftest.py` isolates `HOME` for installer tests.
- Parametrize hostile inputs (non-API URLs, credentials in URLs, private IPs)
  and assert that they are rejected before any network call.
- Run `pytest tests/ -q` before committing. CI runs Python 3.10–3.13 on Ubuntu,
  3.12 on Windows, and a wheel-build gate (`python -m build`, checking for
  duplicate entries and data files). New package data must ship in the wheel.
- Known local gotcha: an ambient `GITHUB_TOKEN`/`GH_TOKEN` makes
  `tests/test_config.py::TestConfig::test_get_configured_features` fail. Run
  with `env -u GH_TOKEN -u GITHUB_TOKEN pytest` when those are set.
