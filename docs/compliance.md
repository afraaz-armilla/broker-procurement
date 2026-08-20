# Compliance boundaries

## LinkedIn — what this bot does and does not do

- It **reads public LinkedIn URLs surfaced by normal web search** (RSS/Brave/
  Anthropic-hosted results can include `linkedin.com/pulse` or
  `linkedin.com/posts` links) and stores the profile URL for Phil to open by
  hand.
- It **never fetches linkedin.com** — no login, no scraping, no automation, no
  fake accounts. This is enforced in code, not just policy: see
  `discovery/article_fetcher.py:is_linkedin_url` and the check at the top of
  `fetch_article()`, which returns `None` before any network call for a
  LinkedIn URL, live or dry-run.
- It **never sends anything on LinkedIn**. The `linkedin` channel produces
  copy-ready text plus the profile URL; Phil sends it himself from his own
  account. There is no LinkedIn API integration anywhere in this codebase.

## Sanctions screening — name-only, not KYC-grade

`gates/sanctions/matcher.py` screens a name against four government watchlists
(OFAC SDN + Consolidated, UK Sanctions List, Canada's Consolidated Autonomous
Sanctions List, UN Security Council Consolidated List) using fuzzy name
matching only — no date of birth, jurisdiction, ownership structure, or
adverse-media check. This is **the floor of compliance appropriate for a
business-development prospecting gate**, not a KYC or sanctions-compliance
bind decision. If Phil ever needs KYC-grade screening for an actual deal, that
should go through Armilla's real compliance process, not this bot.

A `match` verdict permanently suppresses the prospect. A `potential_match`
holds for human review and is never auto-advanced — it does not get drafted,
queued, or sent under any circumstance.

Every screening records which exact list snapshot (by SHA-256, archived in
Google Drive) it ran against, so a past screening is always reproducible —
government lists aren't retrievable retroactively otherwise.

## No automated sending

Nothing in this codebase sends an email or a LinkedIn message. `outreach/
handoff.py` finalizes an approved item as copy-ready text in Slack; Phil sends
it himself. This was a deliberate build decision (not a limitation) — see
`README.md` and the plan this was built from.

## Alias lists

OFAC's alias files (`ALT.CSV` / `*_ADVANCED.XML`) and the other lists' alias
fields are not fetched — screening runs against primary names only. This is a
known, documented gap, not an oversight; extending `gates/sanctions/parsers.py`
and `config/sanctions.yaml` to include alias sources is a natural follow-up if
Phil wants broader coverage.
