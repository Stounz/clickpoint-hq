# ClickPoint Marketing — Project Notes for Claude

## ⚠️ CRITICAL: Railway deploys from `main` only

**Railway watches the `main` branch.** All work happens on `claude/*` worktree branches.
After every session that changes `server.py` or `workspace.html`, the working branch
MUST be merged into `main` and pushed — otherwise the live site at
`platform.clickpointconsulting.com.au` never gets the changes.

**Do this at the end of every coding session:**
```bash
git checkout main
git merge --no-ff origin/claude/<worktree-branch-name>
git push origin main
```

This has bitten us twice. Don't wait for the user to notice the live site is stale.

---

## Architecture

- **Live site:** `https://platform.clickpointconsulting.com.au`
- **Hosted by:** Railway (auto-deploys on push to `main`)
- **Domain registrar:** CrazyDomains — DNS only, no files hosted there
- **Worktree location:** `/Users/admin/Desktop/ClickPoint Marketing/.claude/worktrees/flamboyant-aryabhata-b5218f/`
- **Key files:** `server.py` (Python HTTP server + all API routes), `workspace.html` (full SPA frontend)

## Railway Environment Variables (set in Railway dashboard)

- `ANTHROPIC_API_KEY` — Claude API
- `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` — database
- `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` — OAuth
- `GOOGLE_ADS_DEVELOPER_TOKEN` — Google Ads REST API
- `GOOGLE_ADS_LOGIN_CUSTOMER_ID` — MCC account ID (659-774-3478, no dashes)
- `INTEGRATION_ENCRYPTION_KEY` — token encryption
- `HQ_ADMIN_EMAIL` + `HQ_ADMIN_PASS` — admin login

## Google Ads Connection Flow

Two things must be saved for campaigns to publish:
1. `platform='google_oauth'` row in `client_integrations` — the OAuth token (from popup flow)
2. `platform='google_ads'` row in `client_integrations` — the Customer ID (e.g. 659-774-3478)

The "Connect Google" button on the Google Ads row now prompts for the Customer ID first,
then runs OAuth, then saves both in one go.

## Supabase Schema Notes

- `client_integrations` uses `client` column (NOT `workspace_id`) for the workspace identifier
- Campaign data lives in the `brief` JSON blob column of the `campaigns` table
- `google_oauth` tokens stored separately from `google_ads` account ID rows

## Agents / Specialists

- **Sarah Lin** — CMO, reviews all campaign briefs, assigns specialists
- **Derek Wu** — Paid Search (Google Ads), publishes campaigns live via API
- **Cleo** — Organic Social / Paid Social
- **Emma** — Email marketing
- **Jess** — SEO / Content
- **Zara** — Design / Display / YouTube
- **Raj** — Analytics
