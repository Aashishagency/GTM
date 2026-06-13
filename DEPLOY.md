# Deploying the GTM Workflow app to a public URL (Render)

This puts the app on a stable, always-on HTTPS URL so that:
- **Apollo phone numbers** are delivered to the app (Apollo POSTs to `/apollo/phone-webhook`).
- **Email open / click tracking** works (the tracking pixel/link must be publicly reachable).
- The app is protected by a **login**.

> Vercel is **not** used: it's serverless (ephemeral disk, no background threads), which
> would lose your database and stop the scheduler/campaign sender. Render is a proper
> always-on web service with a database, which this app needs.

---

## 0. One-time: put the code on GitHub
From `C:\Users\User\Desktop\claude`:
```
git init
git add .
git commit -m "GTM workflow app"
```
Create an empty repo on github.com, then:
```
git remote add origin https://github.com/<you>/<repo>.git
git branch -M main
git push -u origin main
```
`.gitignore` already keeps `.env` and the local `*.db` out of git — your secrets are **not** pushed.

## 1. Create the service on Render
1. Go to https://render.com → sign up (free) → **New ➜ Blueprint**.
2. Connect the GitHub repo. Render reads `render.yaml` and proposes a **web service** + a
   **free Postgres database**. Click **Apply**.
3. It builds and deploys. `DATABASE_URL` is wired to Postgres automatically.

## 2. Fill in the secrets (Render ➜ your service ➜ Environment)
Set these (the blueprint marks them "sync:false" so they're blank until you add them):

| Key | Value |
|-----|-------|
| `HUNTER_API_KEY` | your Hunter key |
| `APOLLO_API_KEY` | your Apollo key |
| `SMTP_PASS` | Google **App Password** for info@aashishagency.com |
| `APP_USERNAME` | a username you choose (the login) |
| `APP_PASSWORD` | a strong password you choose (the login) |
| `APP_BASE_URL` | leave blank for now — set in step 4 |

(`SMTP_USER`, `FROM_NAME`, `DATA_PROVIDER`, etc. are already set by the blueprint.)

## 3. First deploy finishes → note your URL
It'll look like `https://gtm-workflow-xxxx.onrender.com`. Open it — your browser will
ask for the **username/password** you set. ✅ Login works.

## 4. Point tracking + webhooks at the public URL
- Set `APP_BASE_URL` = your `https://...onrender.com` URL (Environment tab) → **Save**
  (the service restarts). Now open-tracking pixels and the Apollo webhook use the public URL.

## 5. Bring your existing 111 leads across (optional but recommended)
The cloud DB starts empty. To copy your local data into it, run **locally** once:
```
# Render ➜ Postgres ➜ "External Database URL"  (copy it)
$env:TARGET_DATABASE_URL = "postgresql://...the external url..."
C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe gtm_workflow\migrate_to_postgres.py
```
It copies leads, campaigns, contacts, and settings into Postgres.

## 6. Done — what now works
- **Search Company (Apollo)** → filters to contacts with a mobile on file, reveals emails,
  and Apollo posts the **actual mobile numbers** to `/apollo/phone-webhook`, which writes
  them onto the leads automatically.
- **Campaigns** send from info@aashishagency.com; **opens/clicks** register from real
  inboxes; **replies/bounces** are auto-detected via IMAP.

---

### Notes
- **Free tier sleeps** after ~15 min idle and wakes on the next request (first hit is slow).
  For a webhook/tracking tool that should always be reachable, consider Render's cheapest
  paid instance to keep it awake.
- Run with **one** web worker (the start command already does `--workers 1`) so the
  background scheduler doesn't run twice.
- To make Apollo the default provider, set `DATA_PROVIDER=apollo` in the environment.
