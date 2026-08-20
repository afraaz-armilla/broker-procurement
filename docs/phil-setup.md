# Setup checklist

Everything below is one-time setup before the first live (non-`--dry-run`) run.

## 1. Fill in the business config

Four files in `config/` are marked `TEMPLATE_*` and ship with placeholders —
the pipeline runs without them (as configurable defaults), but nothing
meaningful gets drafted until they're filled in:

- **`TEMPLATE_target_firms.yaml`** — the brokerages to target (name + domain).
  Get the domain right — it must be the firm's actual email-sending domain,
  used for pattern matching and Apollo/Hunter lookups.
- **`TEMPLATE_cities.yaml`** — SF/LA/NY/Chicago plus the two more from the
  source doc.
- **`TEMPLATE_message_sequence.yaml`** — your outreach voice/wording. Every
  approved item is handed to *you* to send — nothing sends automatically — so
  write it as text you're comfortable pasting into your own mail client or
  LinkedIn, not an automated-campaign template.
- **`TEMPLATE_policy.yaml`** — `b_only_policy` (soft vs. hold for Path-B-only
  brokers), `approver_slack_user_ids`, `slack_channel_id`, and the
  send-confirmation/TTL knobs.

## 2. Google Sheets + Drive (the operational store)

1. Create a Google Sheet — its ID (from the URL) is `GOOGLE_SHEETS_SPREADSHEET_ID`.
2. Create a Drive folder for the sanctions-list raw archive — its ID is
   `GOOGLE_DRIVE_FOLDER_ID`.
3. Create a GCP service account with the Sheets and Drive APIs enabled; share
   both the Sheet and the Drive folder with its email as Editor.
4. Prefer **Workload Identity Federation** (no long-lived key) — set
   `GCP_WORKLOAD_IDENTITY_PROVIDER` + `GCP_SERVICE_ACCOUNT_EMAIL` as GitHub
   Actions secrets. If your GCP org can't support WIF, fall back to a
   base64-encoded service-account key in `GCP_SERVICE_ACCOUNT_KEY_B64`.
5. Run `bpb init-store` — creates every tab with its header row and a README
   tab inside the workbook itself explaining which columns are bot-owned.

## 3. Slack

1. Create a Slack app with bot scopes: `chat:write`, `reactions:read`,
   `channels:history` (or `groups:history`/`im:history` per channel type),
   `users:read`.
2. Install it to the workspace, invite the bot to the approval channel.
3. Set `SLACK_BOT_TOKEN`. Put the channel's ID (not its name) in
   `TEMPLATE_policy.yaml`'s `slack_channel_id`.
4. Get each approver's Slack member ID ("Copy member ID" on their profile)
   into `approver_slack_user_ids` — only these IDs' reactions count.

## 4. The other API keys

Apollo, Hunter, ZeroBounce, Abstract, HubSpot (private app token), Anthropic —
see `.env.example` for the full list. All are free-tier except Anthropic
(small usage-based cost, see `docs/cost-model.md`).

## 5. First live run — the recommended sequence

1. `bpb refresh-sanctions --dry-run` then for real: `bpb refresh-sanctions`
2. `bpb validate-batch --city "<one city>" --limit 20` — the source doc's
   recommended step 0. This is the one step that spends real lookup/verify
   credits; review the reachable-rate funnel it prints before doing anything
   else.
3. `bpb discover` (both paths) → `bpb assemble` → check Slack → react to
   approve/reject → `bpb poll-approvals`.
4. Wire up the GitHub Actions schedules in `.github/workflows/` (they're
   already written; they just need the secrets above set on the repo/environment).
