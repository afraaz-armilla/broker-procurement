# Runbook

## Scheduled workflows

| Workflow | Cron (UTC) | Command |
|---|---|---|
| `sanctions-refresh.yml` | daily 03:00 | `bpb refresh-sanctions` |
| `discover.yml` | Mon 06:00 | `bpb discover --path a` |
| `sweep.yml` | Mon 07:00 | `bpb discover --path b` |
| `assemble.yml` | Mon 09:00 | `bpb assemble` |
| `approvals.yml` | every 30 min, weekdays 13:00-23:00 | `bpb poll-approvals` |
| `weekly-report.yml` | Fri 16:00 | `bpb report --weekly --no-dry-run` |

All six share one `concurrency: {group: bpb-global, cancel-in-progress: false}`
— no two of them ever run at once, which is the primary defence against
concurrent writes to the Sheets store (see "Concurrency" below). All support
`workflow_dispatch` for a manual run.

## The pipeline, stage by stage

1. **`discover --path a`** — RSS (default) / Brave / Anthropic-hosted search →
   fetch/extract → LLM extraction → firm resolution → `Signal` + `Prospect`
   rows. Costs zero lookup credits.
2. **`discover --path b`** — Apollo targeting (free) → role-priority banding →
   2-3-per-firm shortlist selection, for target firms Path A hasn't already
   hit. Skips entirely if the credit ledger reports Path B as degraded.
3. **`assemble`** — promotes reserves, then runs every `selected` prospect
   through the gate cascade (suppression → sanctions → email
   resolution/verification → drafting) and posts the resulting queue to Slack.
4. **`poll-approvals`** — decides pending items from reactions, hands off
   approvals (finalizes the Slack message, writes the suppression, logs to
   HubSpot), confirms sends (📤), and releases approved-but-never-confirmed
   items past the TTL.
5. **`report --weekly`** — funnel + credit-spend summary.

## Stage boundaries and flushing

`Repo.load()` once per run, mutate in memory, `Repo.flush()` at each stage
boundary and in a `finally` (see `runctx.py`). The credit ledger writes
write-ahead — before the paid call, not after — so a crash over-counts spend
rather than under-counting it.

## Concurrency

A global advisory lock (`Locks` tab, `runctx.py`) covers the case GitHub
Actions' own concurrency group can't: a human running the CLI locally while a
scheduled job fires. `Signals`, `Verifications`, `Screenings`, `CreditLedger`,
`OutreachLog`, `Runs`, `SanctionsSnapshots`, `Emails` are append-only tabs —
concurrent appends can't clobber each other. `Firms`, `Prospects`,
`QueueItems`, `Suppressions` use optimistic-concurrency versioning as a
conflict *detector*.

## Degradation

If Apollo/Hunter credit spend crosses `degrade_at_pct` (default 85%,
`config/budget.yaml`), Path B is skipped entirely for the rest of that cycle —
Path A keeps running (it costs nothing). Crossing `hard_stop_at_pct` (95%)
stops all paid calls; queue assembly continues using whatever's already
resolved. `bpb credits` prints current spend per bucket.

## Troubleshooting

- **`bpb screen`/`refresh-sanctions` fails with "No sanctions index"** — run
  `bpb refresh-sanctions` first; the local index is a cache file
  (`.bpb_cache/sanctions_index.pkl`), not stored in Sheets.
- **Schema drift error on any Sheets write** — someone edited a header row.
  `store/sheets_schema.py` is the canonical definition; restore the header or
  update it deliberately (this is a data-safety guard, not a bug).
- **A queue item never gets approved/rejected** — check
  `TEMPLATE_policy.yaml`'s `approver_slack_user_ids`; only those Slack user
  IDs' reactions count.
