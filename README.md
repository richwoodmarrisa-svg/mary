# Telegram Dedup Bot

Transfers messages between Telegram chats/topics while automatically skipping duplicate files.

## Features

- ✅ Login with your Telegram account (session saved — login once)
- ✅ Browse all your chats: groups, channels, topics, private chats
- ✅ Select source and destination (with topics support)
- ✅ Set message range by pasting message links
- ✅ Duplicate detection via `file_unique_id` + file size
- ✅ Scan scope: destination topic only / entire group / disabled
- ✅ File type filters: photos, videos, docs, audio, voice, stickers, text
- ✅ Dry run mode (preview without forwarding)
- ✅ Forwards keep original sender header ("Forwarded from X")
- ✅ Progress tracking with live updates
- ✅ Final report + downloadable list of duplicate links

---

## What You Need

### Already have:
- ✅ Bot Token (from @BotFather)
- ✅ API ID + API Hash (from my.telegram.org)

### Still need:
1. **Railway account** → https://railway.app (free tier works)
2. **GitHub account** → https://github.com (to push the code)
3. Your **Telegram user ID** → message @userinfobot on Telegram

---

## Setup Guide

### Step 1 — Get your Telegram User ID

1. Open Telegram and message **@userinfobot**
2. It will reply with your user ID (a number like `123456789`)
3. Save this — you'll need it for `ADMIN_ID`

### Step 2 — Prepare the code

```bash
git init
git add .
git commit -m "Initial commit"
```

Create a GitHub repo and push:
```bash
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

### Step 3 — Set up Railway

1. Go to https://railway.app and sign up (free)
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your repo
4. Railway will detect the Python app automatically

### Step 4 — Add PostgreSQL

1. In your Railway project, click **+ New**
2. Select **Database** → **PostgreSQL**
3. Railway creates the DB and auto-sets `DATABASE_URL` as an environment variable

### Step 5 — Set environment variables

In Railway → your service → **Variables**, add:

| Variable | Value |
|----------|-------|
| `BOT_TOKEN` | Your bot token from @BotFather |
| `API_ID` | From my.telegram.org |
| `API_HASH` | From my.telegram.org |
| `ADMIN_ID` | Your Telegram user ID |
| `SESSION_KEY` | Run `python -c "import secrets; print(secrets.token_hex(32))"` locally |

`DATABASE_URL` is set automatically by Railway when you add PostgreSQL.

### Step 6 — Deploy

Railway will auto-deploy after you push to GitHub.

To check logs: Railway dashboard → your service → **Logs**

---

## Usage

1. Open your bot on Telegram and send `/start`
2. Tap **🔑 Login Account**
3. Enter your phone number (international format, e.g. `+33612345678`)
4. Enter the verification code Telegram sends you
5. If you have 2FA, enter your password (message is auto-deleted)
6. You're logged in! Session is saved permanently.

### Making a Transfer

1. Tap **➕ New Transfer**
2. Set **Source** chat and topic
3. Set **Destination** chat and topic
4. Tap **📌 Set Message Range** → paste first and last message links
5. Choose **Scan Scope** (topic/group/disabled)
6. Choose **File Types**
7. Tap **✅ Review & Confirm**
8. Optionally enable **Dry Run** to preview
9. Tap **🚀 Start Transfer**

The bot will show live progress and send a report when done, including a `.txt` file listing all duplicate links that were skipped.

---

## Architecture

```
main.py                 ← Entry point, starts aiogram bot
├── bot/
│   ├── handlers/
│   │   ├── login.py    ← /start, login flow, logout
│   │   ├── selection.py← Chat/topic/range/type selection wizard
│   │   └── jobs.py     ← Start, monitor, stop transfers
│   ├── keyboards.py    ← All inline keyboard builders
│   └── states.py       ← Per-user wizard state (in-memory)
├── userbot/
│   ├── engine.py       ← Telethon client pool, dialogs, topics, forwarding
│   └── worker.py       ← Core transfer loop with duplicate detection
└── db/
    ├── models.py        ← SQLAlchemy models + engine setup
    └── queries.py       ← Async query helpers
```

### Two-component design:
- **Control Bot** (aiogram + Bot Token) — menus, buttons, progress messages
- **User Engine** (Telethon + API ID/Hash) — reads user's chats, forwards messages as the user

---

## Duplicate Detection

Priority order:
1. **`file_unique_id`** — Telegram's own ID, fastest and most reliable
2. **File size** — fallback when unique_id isn't available

The bot pre-scans the destination and builds an in-memory fingerprint set before starting. Results are also cached in the database (`file_index` table) for future runs.

---

## Free Tier Notes

Railway free tier gives you $5/month of credit. A bot that runs only during transfers uses very little — you should stay comfortably within the free tier for personal use.

PostgreSQL on Railway free tier: up to 1GB storage.

---

## Troubleshooting

**Bot doesn't respond to /start**
- Check Railway logs for errors
- Verify `BOT_TOKEN` is correct

**Login fails**
- Double-check `API_ID` and `API_HASH` from my.telegram.org
- Make sure you're using the same phone number

**"Chat forwards restricted"**
- The source chat has forwarding disabled
- Those messages will be skipped automatically

**Database connection error**
- Make sure PostgreSQL is added to the Railway project
- `DATABASE_URL` must be set (Railway does this automatically)
