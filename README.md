# Broker Prospecting Bot

Sources and reaches high-fit insurance brokers, with a human (Phil) approving
every send. Two discovery paths converge into one gated pipeline:

- **Path A — Signal**: watches trade press/blogs/newsletters (RSS by default,
  free) for brokers already writing about AI insurance. Costs zero lookup
  credits.
- **Path B — Coverage**: systematic title × city × firm sweep (Apollo/Hunter)
  for target firms with no public signal. Spends scarce paid credits, so it's
  tightly budgeted.

Every prospect passes through: suppression check → sanctions screen → email
resolution → email verification → drafting → **Slack approval** → hand-off.
**Nothing sends automatically** — every approved item (email or LinkedIn) is
handed to Phil as copy-ready text for him to send himself.

See [docs/phil-setup.md](docs/phil-setup.md) to configure this for first use,
[docs/runbook.md](docs/runbook.md) for how it runs day to day,
[docs/compliance.md](docs/compliance.md) for the LinkedIn/sanctions boundaries,
and [docs/cost-model.md](docs/cost-model.md) for what this actually costs.

## Quick start

```bash
pip install -e ".[dev]"
cp .env.example .env   # fill in API keys — see docs/phil-setup.md

bpb init-store                                  # create the Sheets workbook tabs
bpb refresh-sanctions                           # populate the sanctions index
bpb validate-batch --city "New York" --limit 20 # the recommended first step
```

Every command supports `--dry-run` (or `--dry-run`/`--no-dry-run` for
`report`), which runs the full pipeline logic against an in-memory store and
fixture API responses — zero external calls, zero credits spent. Run the test
suite with `pytest tests/unit`.
