# Cost model

The source doc's plan assumed near-zero incremental cost. That holds for every
lookup/verify/CRM/store/scheduling service; it does **not** hold for search and
LLM inference, which the doc assumed would be free manual search. Here's the
honest number.

| Item | Monthly estimate |
|---|---|
| Path A discovery — RSS feeds (default) | $0 (no key, no quota) |
| Path A discovery — Brave, if enabled | ~$0 (small volume fits Brave's $5/mo free credit) |
| Article fetch + extraction (`trafilatura`) | $0 (own compute) |
| Anthropic tokens — Path A extraction (Sonnet, ~40 articles/wk) | ~$2 |
| Anthropic tokens — drafting (Opus, ~80 drafts/mo) | ~$3-8 |
| Apollo, Hunter, ZeroBounce, Abstract | $0 (free tiers, budgeted — see `config/budget.yaml`) |
| Google Sheets + Drive | $0 (existing Workspace) |
| GitHub Actions | $0 (~300 of 2,000 free private-repo minutes/month) |
| HubSpot | existing subscription |
| **Total incremental** | **~$5-8/month** |

Removed from the original plan's cost surface entirely: a transactional email
provider, a sending subdomain (DNS/DKIM/warm-up), and a separate database
vendor — none of these exist in this build. No automated sending means no
sending-domain warm-up lead time either.

## If Phil wants more coverage later

Enable `brave` and/or `anthropic_hosted` in `config/settings.yaml`'s
`path_a.search_providers` list (RSS stays the default; these are additive).
`anthropic_hosted` is the most capable (searches, reads, and extracts in one
call) at ~$10/1,000 searches — verify its exact tool-type string against the
current Anthropic SDK before flipping it on; see the caveat in
`discovery/anthropic_hosted.py`.

## Credit budgets

`config/budget.yaml` defines the monthly cap and warn/degrade/hard-stop
thresholds per provider bucket. `bpb credits` shows current spend. The
degradation ladder (§11 of the original build plan) falls back to Path A only
— never fully stops discovery — because Path A costs no lookup credits at all.
