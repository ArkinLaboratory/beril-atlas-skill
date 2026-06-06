# /beril-atlas-configure — slash command + CLI spec

**Date:** 2026-04-24
**Status:** DRAFT — Task #4 deliverable. Awaits Adam review.

## Design principle: single source of truth

BERIL already has `.env` at its home directory (Adam confirmed 2026-04-24).
`engine/llm_config.py` (current `atlas_lib/llm_config.py`) already reads all
atlas-relevant config from process env, loaded from `BERIL_ROOT/.env`.

**Decision for v0.1: do not introduce a separate config file.** BERIL's `.env`
is the sole atlas configuration source. The configure command:

1. Tells the user exactly which `.env` lines are needed for their provider.
2. Verifies the resulting state with a smoke test.
3. Writes a single `BERIL_ATLAS_CONFIGURED_AT=<timestamp>` marker line on
   success so other commands can detect "configuration complete."

No `~/.beril-atlas/config.yaml`, no YAML schema to maintain, no risk of
divergence between the two files. Revisit if users request cross-install config
sharing — at that point an optional `~/.beril-atlas/defaults.yaml` can be added
as an overlay below `.env`.

**Implication for pyproject.toml**: drop the `[project.scripts]` reference to
`~/.beril-atlas/config.yaml` from LAYOUT.md's path discovery section. The only
user-level dir we still need is `~/.beril-atlas/runs/` for scan outputs.

## CRAFT-CONTRACT §3.4 alignment (Round 2c)

**Status (updated 2026-06-06):** atlas's config is now aligned to the
**CRAFT runtime-config contract v2** (`craft-platform/CRAFT-CONTRACT.md
§3.4`) so it coexists with the three CRAFT skills (beril-adversarial,
beril-paper-writer, beril-presentation-maker) on a shared BERIL `.env`
without shadowing keys.

Atlas is NOT a CRAFT submodule (stays at ArkinLaboratory; releases on
its own). The alignment is **coexistence + consistency**, not a CRAFT
release item.

Key shape changes versus the v0.3.14 template:

  - **Additive-only `.env`.** Atlas's `template-env` block is now a
    sentinel-delimited shared CRAFT block (provider + 3 model tiers)
    plus atlas's per-skill marker. The compose step is existence-aware
    and NEVER re-declares a key the user's `.env` already has — see
    `commands/_env_compose.py::compose_env_append`.
  - **Provider set narrowed to `cborg` + `anthropic`.** The `google`
    stub (PROVIDER_GOOGLE / GoogleClient) was retired. Atlas users
    wanting Gemini reach it through the `cborg` provider by pinning a
    CBORG-served Gemini model id to a tier (e.g.
    `MODEL_FAST=gemini-flash`). A direct Google AI Studio backend is a
    future own-client extension, not v1.
  - **3-tier model resolution.** `MODEL_REASONING` / `MODEL_STANDARD`
    / `MODEL_FAST` replace the dormant `ANNOTATION_MODEL` /
    `TOURNAMENT_MODEL` env vars. Atlas's tier mapping (intended;
    consumers wire as those stages land):
      - default extraction → standard tier
      - annotation stage (future) → fast tier
      - tournament / Elo (future) → reasoning tier
  - **`CBORG_BASE_URL` keeps `/v1`.** Atlas's OpenAI-style client reads
    it directly. Atlas does NOT use `claude -p`, so the contract's
    `bare_host` helper (which strips `/v1`) is not invoked, and atlas
    writes NO `<BERIL_ROOT>/.claude/settings.json`.
  - **Inline-comment stripping.** `_env_compose.parse_env_text` is
    byte-identical to the canary's helper (whitespace-preceded `#`
    opens a trailing comment).

## Atlas environment variables (canonical list)

Authoritative source: `src/beril_atlas/llm_config.py` (canonical CRAFT
resolver, copied verbatim from beril-paper-writer main) +
`src/beril_atlas/engine/llm_config.py` (atlas client config delegating
to the canonical resolver).

| Variable | Required when | Default if unset | Notes |
| --- | --- | --- | --- |
| `ACTIVE_PROVIDER` | always | `cborg` | one of `cborg`, `anthropic` (`google` dropped Round 2c; `subscription` rejected by atlas — no `claude -p`) |
| `CBORG_API_KEY` | provider=cborg | — | required; READ from existing `.env`, never re-declared |
| `CBORG_BASE_URL` | — | `https://api.cborg.lbl.gov/v1` | atlas keeps `/v1` (app-internal OpenAI-style); override for dev/proxy |
| `ANTHROPIC_API_KEY` | provider=anthropic | — | required; READ from existing `.env` |
| `ANTHROPIC_BASE_URL` | — | Anthropic SDK default | rarely overridden |
| `MODEL_REASONING` | — | — | reasoning-tier pin (future: tournament / Elo) |
| `MODEL_STANDARD` | — | — | standard-tier pin (atlas's `default_model` falls back here if set, else provider literal) |
| `MODEL_FAST` | — | — | fast-tier pin (future: annotation stage) |
| `DEFAULT_MODEL` | — | `MODEL_STANDARD` else provider literal | atlas's primary model knob today |
| `DAILY_BUDGET_USD` | — | none (no cap) | optional float; atlas-specific |
| `BERIL_ATLAS_CONFIGURED_AT` | — | — | written by configure on success |
| `BERIL_ATLAS_CONFIGURED_VERSION` | — | — | written by configure on success |

### Per-provider default model fallbacks

| Provider | Default model if DEFAULT_MODEL and MODEL_STANDARD both unset |
| --- | --- |
| `cborg` | `anthropic/claude-sonnet` |
| `anthropic` | `claude-sonnet-4-5` (bump as new models ship; config schema tracks model id in .env) |

(The `google` row was removed in Round 2c; Gemini users pin a
CBORG-served Gemini model id via `cborg`.)

## .env template appended on first configure

`/beril-atlas-configure` (and `beril-atlas configure`) **idempotently**
appends the CRAFT shared block + atlas's per-skill marker to
`BERIL_ROOT/.env`. The compose step:

  - Detects the `# >>> CRAFT shared config` sentinel and SKIPS the
    shared block if another CRAFT skill already wrote it.
  - Detects atlas's per-skill marker and SKIPS the whole append if
    both blocks are already present (idempotent no-op).
  - Drops any `KEY=...` line from the appended block whose `KEY` is
    already present in the user's `.env` (the additive-only contract
    — NEVER re-declares a credential or tier pin the user already set).

Block produced by `beril-atlas template-env`:

```ini
# >>> CRAFT shared config (written once; shared by all CRAFT skills) >>>
# Edit values here, then re-run any skill's `configure` to regenerate
# <BERIL_ROOT>/.claude/settings.json. See CRAFT-CONTRACT.md §3.4.

# Reasoning provider — routes BOTH `claude -p` and app-internal calls.
# One of:
#   anthropic     your own Anthropic Platform key (works anywhere, off-network)
#   cborg         LBL CBORG gateway (needs LBL network/VPN locally; free on the Hub)
#   subscription  ambient Claude Code login (capped by the monthly Agent SDK credit)
ACTIVE_PROVIDER=cborg

# CRAFT READS the provider credentials already present in this .env — it does
# NOT re-declare them. cborg uses CBORG_API_KEY (+ CBORG_BASE_URL); anthropic
# uses ANTHROPIC_API_KEY. If a needed key is missing, `configure` fails loud
# and names which one to add. `claude -p` uses the BARE host (configure strips
# /v1).

# Model tiers. Leave BLANK → `configure` discovers the newest model available
# on your provider per tier and pins it here. Set a value to pin your own
# choice. reasoning = hard/unrecoverable work; fast = mechanical.
MODEL_REASONING=
MODEL_STANDARD=
MODEL_FAST=
# <<< CRAFT shared config <<<

# --- beril-atlas-skill (per-skill) ---
# Atlas tier mapping (Round 2c):
#   default extraction → standard tier (MODEL_STANDARD)
#   annotation stage (future) → fast tier (MODEL_FAST)
#   tournament / Elo (future) → reasoning tier (MODEL_REASONING)
#
# Optional: cap atlas's total LLM spend per day. Unset = no cap.
# DAILY_BUDGET_USD=10.00
#
# Optional: pin a specific default model id. Useful for reproducibility.
# DEFAULT_MODEL=

BERIL_ATLAS_CONFIGURED_AT=
BERIL_ATLAS_CONFIGURED_VERSION=
```

Design notes:
- Credentials are READ from the existing `.env`, NEVER re-declared.
  Re-declaring shadows the BERIL/CRAFT keys via last-write-wins.
- `BERIL_ATLAS_CONFIGURED_AT` is the completion marker; empty means
  "never configured or needs re-verification."

## configure flow

### State model

Atlas configuration is in one of four states:

| State | Detection |
| --- | --- |
| `unconfigured` | no `BERIL_ATLAS_*` keys in `.env` at all |
| `template-present` | template appended but required key for ACTIVE_PROVIDER is empty |
| `keys-present-unverified` | required key set but `BERIL_ATLAS_CONFIGURED_AT` empty, OR marker older than current atlas package version |
| `configured` | required key set AND `BERIL_ATLAS_CONFIGURED_AT` set AND marker matches current package version |

### Flow by entry state

#### `unconfigured`

1. Announce: "Atlas is not yet configured for this BERIL install."
2. `AskUserQuestion`: which provider? (CBORG / Anthropic)
3. `AskUserQuestion`: confirm appending template block to `BERIL_ROOT/.env`.
4. Append template (verbatim above, with `ACTIVE_PROVIDER` set to user's choice
   and the corresponding key-line uncommented).
5. Print instructions for the user to paste their API key:
   - Exact file path to edit (`BERIL_ROOT/.env`).
   - Which line to edit (`CBORG_API_KEY=` on line N).
   - Safety reminder: "Do not commit `.env`. BERIL's `.gitignore` already
     excludes it — verify with `git check-ignore .env`."
6. Transition to `template-present`, prompt user to re-run
   `/beril-atlas-configure` after pasting their key.

#### `template-present`

1. Detect which provider is active and which key slot is empty.
2. If the same state we just left: prompt user to actually paste the key
   (avoid infinite loop by limiting to one re-prompt then exit cleanly).
3. If user has pasted key: transition to `keys-present-unverified`.

#### `keys-present-unverified`

1. Run smoke test (see below).
2. On success: update both marker lines in `.env`:
   ```
   BERIL_ATLAS_CONFIGURED_AT=2026-04-24T14:30:00Z
   BERIL_ATLAS_CONFIGURED_VERSION=0.1.0
   ```
   Transition to `configured`. Print success summary.
3. On failure: print diagnostic + remediation hint. Do NOT update markers.
   Stay in `keys-present-unverified`.

#### `configured`

1. Compare `BERIL_ATLAS_CONFIGURED_VERSION` to current package version.
   - If equal: announce "Atlas configured (provider=X, model=Y, last
     verified TIMESTAMP, version Z). Skipping smoke test." Ask
     AskUserQuestion: "Re-verify anyway?" On yes, run smoke test.
   - If package is newer: announce "Configuration was verified against
     vX, current package is vY. Re-verifying." Run smoke test
     automatically.
2. On smoke test success: refresh both marker lines.
3. On failure: print diagnostic. Transition state back to
   `keys-present-unverified`.

### Re-configuration / provider switch

User edits `.env` manually (changes `ACTIVE_PROVIDER`, pastes new key,
whatever). Re-runs `/beril-atlas-configure`. State is re-detected; smoke
test re-runs; marker refreshed or error raised.

No "reset" subcommand needed for v0.1 — editing `.env` is the reset path.

## Smoke test

Purpose: verify credentials actually work AND that `chat_json()` parses
structured output end-to-end, before declaring configuration complete.

### Shape

- Load `LLMConfig` via `engine.llm_config.load_atlas_config()`.
- Instantiate the correct provider client (v0.1: CBORG only).
- Call `chat_json()` with a deterministic structured-response prompt:
  ```
  Respond with JSON in exactly this shape:
  {"capital_of_france": "<string>", "year": <integer>}
  ```
  `max_tokens=40`, `temperature=0`.
- **Success criteria** (all must hold):
  - HTTP 200 from provider
  - Response body parses as JSON after `chat_json()` extraction
  - Parsed JSON has both `capital_of_france` (non-empty string) and
    `year` (integer) keys
  - `capital_of_france` = `"Paris"` is NOT checked (don't assert model
    correctness; we only verify the pipeline); but the presence + type
    check is strict
- Why structured factual response over `{"ok": true}`: the bare response
  doesn't exercise `chat_json()`'s "extract JSON from fenced-or-raw text"
  path. Real models sometimes wrap JSON in prose or markdown fences; we
  want the smoke test to catch that variation since it's the same code
  path extractors hit during a real scan.
- Failure: any exception from the client layer OR shape-check failure.
  Classified:

| Failure | Diagnostic |
| --- | --- |
| `LLMRateLimitError` (429) | "Rate-limited. Key is valid but quota is hit. Wait and retry." |
| HTTP 401/403 | "Key rejected. Verify you pasted the correct key, and that it has permissions for the base URL." |
| HTTP 404 | "Endpoint not found. Check CBORG_BASE_URL / ANTHROPIC_BASE_URL." |
| Network timeout / connection refused | "Cannot reach base URL. Check network, VPN, base URL setting." |
| `LLMValidationError` (non-JSON response) | "Provider returned non-JSON. Likely a provider-side outage; retry later. If persistent, file an issue." |
| `ValueError` from load_atlas_config | "Configuration incomplete — see prior output for the specific missing field." |

### Cost + traceability

- One call, ~20 output tokens, negligible cost. Don't apply
  `DAILY_BUDGET_USD` to this call (it's infrastructure, not a scan).
- Smoke test is NOT cached; re-running configure always makes a fresh call.
- Result is logged to `stderr` but never written to any file other than
  the `BERIL_ATLAS_CONFIGURED_AT` timestamp.

## Cross-platform instructions

The configure flow shows the user **exactly** what command to run on their
platform. Platform detection via `platform.system()` inside the CLI:

### macOS / Linux (bash, zsh)

```bash
# Edit the file directly
nano $BERIL_ROOT/.env
# or
vim $BERIL_ROOT/.env

# After editing, re-run the slash command in Claude Code:
#   /beril-atlas-configure
```

### Windows (PowerShell)

```powershell
notepad $Env:BERIL_ROOT\.env
# or the configure command points at the file with `code`, `notepad++`, etc.
```

### Windows (cmd)

```cmd
notepad %BERIL_ROOT%\.env
```

Not shown: per-shell `export` commands for setting env vars interactively.
We don't use those because BERIL's `.env` is the persistence layer; in-session
env vars would be lost on next `claude` launch anyway.

## The slash command file

Lives at `src/beril_atlas/skill/commands/beril-atlas-configure.md` in the
package; installed at `<BERIL_ROOT>/.claude/skills/beril-atlas/commands/beril-atlas-configure.md`
by `beril-atlas install-skill`.

### Contents (draft — slash-command driven)

```markdown
---
description: Configure the BERIL Atlas skill — pick an LLM provider, set env vars, run a smoke test.
argument-hint: (no arguments)
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, AskUserQuestion
---

# /beril-atlas-configure

Configure the BERIL Atlas skill for this BERIL install. Run this once after
`beril-atlas install-skill .`, or any time credentials change.

## Step 1 — Verify the package is installed

Run in a Bash block:

    beril-atlas --version

If the command is not found, tell the user:

> The `beril-atlas` package isn't on your PATH. Install it with:
>
>     pipx install git+https://github.com/ArkinLaboratory/beril-atlas-skill.git
>
> (Windows: `python -m pip install --user pipx; python -m pipx ensurepath` first.)
> Then re-run `/beril-atlas-configure`.

Stop here if the command is missing. Don't try any fallback install.

## Step 2 — Detect current state

Run:

    beril-atlas config-status --json

Parse the JSON. Output shape:

```json
{
  "beril_root": "/path/to/beril",
  "env_path": "/path/to/beril/.env",
  "state": "unconfigured | template-present | keys-present-unverified | configured",
  "active_provider": "cborg | anthropic | null",
  "missing_keys": ["CBORG_API_KEY"],
  "marker_timestamp": "2026-04-24T14:30:00Z | null",
  "marker_version": "0.1.0 | null",
  "package_version": "0.1.0"
}
```

Branch by `state`:

### state = configured

If `marker_version == package_version`:
- Tell the user: "Atlas is configured (provider=X, last verified TIMESTAMP, version X). Nothing to do."
- Ask via AskUserQuestion: "Re-verify anyway?"
- On yes → go to Step 4 (smoke test).
- On no → done.

If `marker_version < package_version`:
- Tell the user: "Configuration was last verified against version X; current version is Y. Re-verifying."
- Go to Step 4 (smoke test).

### state = keys-present-unverified

Go directly to Step 4 (smoke test).

### state = template-present

The .env has the template block but the required key slot is empty.
- Tell the user exactly which line is empty (use `missing_keys` from state).
  Example: "Your .env has the Atlas template but `CBORG_API_KEY=` is empty
  on line N. Paste your CBORG key there, save the file, then re-run this
  command."
- Do NOT edit .env for them in this state — they must paste the secret.
- Stop.

### state = unconfigured

Go to Step 3.

## Step 3 — Append template for fresh install

AskUserQuestion — "Atlas is not yet configured. Add the Atlas configuration
template to `BERIL_ROOT/.env` now? Only CBORG is supported in v0.1; Anthropic
and Google are reserved for v0.2."

Choices: `Yes, append template` / `No, I'll edit .env myself`.

If yes:

1. Run `beril-atlas template-env` via Bash to get the template text.
2. Read the current `.env` via Read tool.
3. Use Edit tool to append the template to `.env`. Never replace existing
   content.
4. Tell the user: "Template appended at line N of `.env`. Open the file and
   paste your CBORG key on the `CBORG_API_KEY=` line. Then re-run this
   command to verify."
5. Stop. Do NOT proceed to smoke test — they haven't pasted the key yet.

If no:
- Run `beril-atlas template-env` and show the block inline.
- Tell the user to add it to .env manually.
- Stop.

## Step 4 — Smoke test

Run:

    beril-atlas smoke-test --json

Output shape:

```json
{
  "success": true | false,
  "provider": "cborg",
  "model": "anthropic/claude-sonnet",
  "latency_ms": 1234,
  "error_class": null | "auth | not_found | rate_limit | network | malformed_json | config",
  "error_message": null | "..."
}
```

On success:
- Run `beril-atlas mark-configured` which updates
  `BERIL_ATLAS_CONFIGURED_AT=<ISO timestamp>` and
  `BERIL_ATLAS_CONFIGURED_VERSION=<current package version>` in .env via
  Edit.
- Tell the user: "Atlas configured successfully. Provider=X, model=Y, latency=Zms.
  You can now run `/beril-atlas` (in Claude Code) or `beril-atlas scan` (CLI)
  from this BERIL directory."

On failure, branch by `error_class`:

| error_class | User message |
| --- | --- |
| `auth` | "CBORG rejected the key (401). Double-check you pasted the correct key into `CBORG_API_KEY` in .env, and that it has access to the base URL (default https://api.cborg.lbl.gov/v1)." |
| `not_found` | "CBORG endpoint not found (404). Check `CBORG_BASE_URL` — default is https://api.cborg.lbl.gov/v1. Override only if you know you need to." |
| `rate_limit` | "CBORG rate-limited the smoke call (429). Your key is valid but quota is hit. Wait and retry." |
| `network` | "Can't reach CBORG (network/timeout). Check VPN, DNS, and `CBORG_BASE_URL`." |
| `malformed_json` | "CBORG returned non-JSON when asked for structured output. Likely a provider-side issue. Try again; if persistent, file an issue." |
| `config` | "Configuration incomplete — specifically: {error_message}. Fix in .env and re-run." |

Do NOT update the marker on failure.

## What this does NOT do

- Does not store API keys in any file other than `BERIL_ROOT/.env` (which
  BERIL's `.gitignore` already excludes).
- Does not read, log, or echo the key itself. Smoke test output contains
  provider, model, latency, error class — never the key.
- Does not configure other BERIL skills. Only atlas-specific entries.
- Does not support `anthropic` or `google` providers in v0.1. Those
  commented-out entries in the template are reserved for v0.2.
```

## CLI leaf utilities (called by the slash command)

The slash command orchestrates the flow; these are the small, single-purpose
CLI utilities it invokes via Bash. Each prints machine-readable output (JSON
where applicable) so the agent can parse reliably. Each also works
standalone for scripted use.

### `beril-atlas config-status --json`

```json
{
  "beril_root": "/path/to/beril",
  "env_path": "/path/to/beril/.env",
  "state": "unconfigured | template-present | keys-present-unverified | configured",
  "active_provider": "cborg | anthropic | null",
  "missing_keys": ["CBORG_API_KEY"],
  "marker_timestamp": "2026-04-24T14:30:00Z | null",
  "marker_version": "0.1.0 | null",
  "package_version": "0.1.0"
}
```

Exit code: 0 always (even for `unconfigured`). Failure to locate BERIL_ROOT
exits 1 with a plain-text error to stderr.

### `beril-atlas smoke-test --json`

Runs the structured factual-response probe (decision 3).

```json
{
  "success": true,
  "provider": "cborg",
  "model": "anthropic/claude-sonnet",
  "latency_ms": 1234,
  "error_class": null,
  "error_message": null
}
```

On failure, `success=false`, `error_class` one of
`auth | not_found | rate_limit | network | malformed_json | config`,
`error_message` a plain-text diagnostic suitable for the user.

Exit code: 0 on success, 2 on failure. Marker is NOT updated by this
command — that's `mark-configured`.

### `beril-atlas template-env`

Prints the atlas `.env` template block to stdout. No arguments.
Slash command reads the output and uses Edit to append to `BERIL_ROOT/.env`.

### `beril-atlas mark-configured`

After a successful smoke test, updates `BERIL_ATLAS_CONFIGURED_AT` and
`BERIL_ATLAS_CONFIGURED_VERSION` in `BERIL_ROOT/.env`. Exits non-zero if
the marker lines don't exist in .env (caller bug; means template wasn't
appended).

### `beril-atlas configure` (scriptable alternative)

For users who prefer CLI over the slash command, or for CI/automation:

```
beril-atlas configure                            # interactive — runs the same state machine as the slash command, using terminal prompts
beril-atlas configure --provider cborg --yes     # non-interactive — appends template if needed, prompts only for the key
beril-atlas configure --smoke-test-only          # skip all editing, just verify and refresh marker
beril-atlas configure <path>                     # explicit BERIL_ROOT (positional)
```

This is a convenience wrapper around the leaf utilities. Kept because:
(a) some users will want to run from a terminal; (b) scripted install flows
need a non-interactive path; (c) testing the state machine in Python is
easier than driving the slash command end-to-end.

Slash command is the primary UX; CLI `configure` is the fallback.

## Decisions (resolved 2026-04-24)

1. **Slash-command driven, not CLI-driven.** Claude Code orchestrates the
   flow (AskUserQuestion for provider choice, Edit tool for `.env` modification,
   Bash tool for CLI helpers). The CLI (`beril-atlas configure`) exists as a
   scriptable alternative but is not the primary path. Rationale: smoothest UX
   for users watching the agent work. The agent explains each step; the user
   never sees a raw CLI prompt.

   Implication: the slash command body (below) is the authoritative logic,
   invoking Python helpers via Bash:
   - `beril-atlas config-status` — returns machine-readable state JSON
     (state, active_provider, missing_keys, marker_timestamp, marker_version).
   - `beril-atlas smoke-test` — runs the smoke call, returns structured result.
   - `beril-atlas template-env` — prints the `.env` template block to stdout
     for the agent to paste via Edit.
   These are small leaf utilities, each doing one thing, callable from both
   the slash command and from scripts.

2. **CBORG-only in v0.1 provider menu.** `anthropic` and `google` providers
   are hidden from the interactive menu because `AnthropicClient` is a stub
   and there's no Google client code. Template .env keeps those entries as
   commented-out future options so users see the full schema. Tracked as
   Task #11 (v0.2 follow-up).

3. **Smoke test uses structured factual response.** Not `{"ok": true}`.
   Actual prompt:
   ```
   Respond with JSON: {"capital_of_france": "<string>", "year": <integer>}
   ```
   Verification: response is valid JSON with both fields, `capital_of_france`
   string non-empty, `year` integer. Exercises `chat_json()` parse path and
   catches "provider returns text around JSON" failures the bare `{"ok": true}`
   test would miss.

4. **Marker format includes package version.** Written as two lines:
   ```
   BERIL_ATLAS_CONFIGURED_AT=2026-04-24T14:30:00Z
   BERIL_ATLAS_CONFIGURED_VERSION=0.1.0
   ```
   `configure` detects "marker version < current package version" and prompts
   for re-verification. Lets v0.2+ introduce new required keys without silently
   breaking older installs.

5. **`install-skill` auto-invokes `configure --smoke-test-only` after copy.**
   If smoke test fails (or .env is unconfigured post-upgrade), install-skill
   exits with a non-zero warning code but does NOT roll back the file copy.
   User sees: "Skill files installed. Configuration verification failed: <reason>.
   Run /beril-atlas-configure to fix." Copy succeeds, verification is
   advisory-but-loud.

## Cross-command interactions

Deltas to update in LAYOUT.md during Task #6:

- Remove references to `~/.beril-atlas/config.yaml` — this file no longer
  exists. Only `~/.beril-atlas/runs/` remains as a user-level dir (for scan
  outputs).
- `install-skill` post-copy auto-invokes `beril-atlas configure
  --smoke-test-only`. Advisory only — non-zero exit does not roll back.
- New leaf CLI utilities (`config-status`, `smoke-test`, `template-env`,
  `mark-configured`) live under `commands/` in the package layout.
- Slash command at `src/beril_atlas/skill/commands/beril-atlas-configure.md`
  is the primary UX; its content is the markdown block above.
