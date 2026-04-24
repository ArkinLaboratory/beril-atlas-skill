# Sync Protocol (placeholder)

The original sync-protocol doc shipped with the spike was specific to the
maintainer's 3-hop fork sync (upstream → personal fork → spike working tree).
That's not directly applicable to operators of other BERIL deployments.

**For a deployment operator**, the relevant sync story is simpler:
- Pull updates from upstream `kbaseincubator/BERIL-research-observatory` when
  you want new skill versions or seed content.
- Run `beril-atlas install-skill .` after pulls to refresh the shipped skill
  files in your checkout.
- Your `vocab-local/`, `state/`, and `contrib/` directories are preserved.

Atlas-specific sync is handled by the package release flow in the
`ArkinLaboratory/beril-atlas-skill` repo — `pipx upgrade beril-atlas-skill` picks
up new vocab + scoring + methodology when releases ship.
