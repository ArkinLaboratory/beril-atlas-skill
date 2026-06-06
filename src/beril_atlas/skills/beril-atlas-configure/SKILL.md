---
name: beril-atlas-configure
description: Configure the BERIL Atlas skill for this BERIL install — pick an LLM provider, set env vars in BERIL_ROOT/.env, and run a smoke test. Use once after `beril-atlas install-skill`, or any time credentials change.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, AskUserQuestion
user-invocable: true
---

# beril-atlas-configure

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

**CRAFT-CONTRACT §3.4 / Round 2c:** providers narrowed to `cborg` +
`anthropic`. The `google` stub was retired — users wanting Gemini reach
it through the `cborg` provider by pinning a CBORG-served Gemini model
id to a tier (e.g. `MODEL_FAST=gemini-flash`).

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
  Example: "Your .env has the Atlas template but `CBORG_API_KEY=` is empty.
  Open `{env_path}` and paste your CBORG key on that line. Then re-run this
  command."
- Do NOT edit .env for them in this state — they must paste the secret.
- Stop.

### state = unconfigured

Go to Step 3.

## Step 3 — Append template for fresh install

AskUserQuestion — "Atlas is not yet configured. Add the Atlas (CRAFT-aligned)
configuration template to `BERIL_ROOT/.env` now? CBORG is the supported
v1 provider; Anthropic is a v0.2 hook. For Gemini, use ACTIVE_PROVIDER=cborg
and pin a CBORG-served Gemini model id to a tier."

Choices: `Yes, append template` / `No, I'll edit .env myself`.

If yes:

1. Run `beril-atlas configure --yes` (which uses the additive-only
   `compose_env_append` path: skips the CRAFT shared block if another
   CRAFT skill already wrote it; drops any line that would re-declare
   a key the user's `.env` already has). The CLI handles the write.
2. Alternatively for inspection-then-paste: run `beril-atlas template-env`
   to see the block, then Read the current `.env`, then use Edit to
   append. NEVER overwrite existing keys (`CBORG_API_KEY`, etc.) — the
   compose helper already guards this when going through `configure`.
3. Tell the user: "CRAFT shared block + atlas marker appended to `.env`.
   Credentials (CBORG_API_KEY) are READ from your existing `.env`; if
   they aren't set yet, paste them now. Then re-run this command to
   verify."
4. Stop. Do NOT proceed to smoke test — they may still need to paste
   the credential.

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
  "success": true,
  "provider": "cborg",
  "model": "anthropic/claude-sonnet",
  "latency_ms": 1234,
  "error_class": null,
  "error_message": null
}
```

On success:
- Run `beril-atlas mark-configured` which updates
  `BERIL_ATLAS_CONFIGURED_AT=<ISO timestamp>` and
  `BERIL_ATLAS_CONFIGURED_VERSION=<current package version>` in .env via
  Edit.
- Tell the user: "Atlas configured successfully. Provider=X, model=Y, latency=Zms.
  You can now run `beril-atlas scan` from this BERIL directory."

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
