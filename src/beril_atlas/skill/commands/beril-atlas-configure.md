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
>     pipx install git+ssh://git@github.com/ArkinLaboratory/beril-atlas-skill.git
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
  "active_provider": "cborg | anthropic | google | null",
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
  Example: "Your .env has the Atlas template but `CBORG_API_KEY=` is empty.
  Open `{env_path}` and paste your CBORG key on that line. Then re-run this
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
4. Tell the user: "Template appended to `.env`. Open the file and paste your
   CBORG key on the `CBORG_API_KEY=` line. Then re-run this command to verify."
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
