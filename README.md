# ProsFolio AI — setup guide

*Your personal AI income and expense tracker, as a Telegram bot.*

This one document has **everything** you need, in order. Do it in phases — one
sitting per phase is totally fine. You've already created the bot, so you're
further along than it feels.

> **Quick safety note:** the bot token you screenshotted earlier is like a
> password. Before you go live, open **@BotFather → `/revoke` → pick your bot**
> to get a fresh token. The old one instantly stops working. You never paste the
> token into a chat with anyone — only into your private `.env` file (Phase D).

---

## What's done vs. what's left

- ✅ You created the bot with BotFather (you have a token).
- ⬜ Phase A — get two more free keys (your user ID + Anthropic key). *~10 min*
- ⬜ Phase B — set up Google access and build your Sheet. *~20 min, the biggest part*
- ⬜ Phase C — put these files on your computer and install Python bits. *~15 min*
- ⬜ Phase D — fill in your `.env`. *~5 min*
- ⬜ Phase E — run it and text your bot. *~2 min, the fun part*

Take breaks. Nothing here expires.

---

## Phase A — two quick keys

**A1. Your Telegram user ID** (locks the bot to only you)
In Telegram, search **@userinfobot**, tap Start. It replies with your numeric
**Id** (e.g. `5582931746`). Save that number.

**A2. Your Anthropic API key** (the AI that reads your messages)
1. Go to **console.anthropic.com** and sign in.
2. Click **Settings → API keys → Create key**, name it "prosfolio", copy it
   (starts with `sk-ant-`). Save it.
3. Click **Billing** and add a small amount of credit (even $5 lasts a very long
   time — each message costs a fraction of a cent).

---

## Phase B — Google Sheets (your database)

### B1. Create the project and turn on the APIs
1. Go to **console.cloud.google.com**. Up top, create a new project called
   `prosfolio-ai`.
2. Left menu → **APIs & Services → Library**. Search **Google Sheets API** →
   open it → **Enable**. Then search **Google Drive API** → **Enable** too.

### B2. Create a service account (a robot Google account for your bot)
1. **APIs & Services → Credentials → Create credentials → Service account.**
2. Give it a name like `prosfolio-bot` → **Create and continue** → **Done**
   (you can skip the optional roles).
3. Click the service account you just made → **Keys** tab → **Add key → Create
   new key → JSON**. A file downloads.
4. Rename that file to **`google-service-account.json`** and move it into this
   project's **`credentials/`** folder.
5. Open the JSON in a text editor, find the line `"client_email"`, and copy the
   address (it looks like `prosfolio-bot@prosfolio-ai.iam.gserviceaccount.com`).
   You'll need it in B4.

### B3. Build the Sheet
Create a new Google Sheet named **ProsFolio AI DB**. Make **7 tabs** (rename the
default tab, then add the rest with the **+** at the bottom-left). Put the header
text in **row 1** of each tab, exactly as written.

**Transactions**
```
Transaction ID | Created Timestamp | Transaction Date | Type | Amount | Currency | Exchange Rate | PHP Equivalent | Category | Client | Income Source | Account | Destination Account | Description | Telegram User ID | Status
```

**Accounts** — headers, then these rows. Put your **real current balances** in the
*Starting Balance* and *Current Balance* columns (use the same number in both).
```
Account Name | Account Type | Currency | Starting Balance | Current Balance | Last Updated
GCash | e-wallet | PHP | 0 | 0 |
Maya | e-wallet | PHP | 0 | 0 |
UnionBank | bank | PHP | 0 | 0 |
RCBC PHP | bank | PHP | 0 | 0 |
RCBC USD | bank | USD | 0 | 0 |
Cash | cash | PHP | 0 | 0 |
Emergency Savings | savings | PHP | 0 | 0 |
Road to Millionaire | savings | PHP | 0 | 0 |
USD Wallet | wallet | USD | 0 | 0 |
```

**Clients**
```
Client Name | Default Currency | Notes
TMGM | USD |
Aptos | USD |
Pi Network | USD |
Wire Network | USD |
Roobet | USD |
MultiBank | USD |
Diamante | USD |
Bitpanda | USD |
```

**Categories** — one table for three kinds of labels (the `Type` column tells
them apart, which keeps you at exactly 7 tabs):
```
Name | Type | Notes
Food | Expense Category |
Transportation | Expense Category |
Rent | Expense Category |
Electricity | Expense Category |
Water | Expense Category |
Internet | Expense Category |
Groceries | Expense Category |
School | Expense Category |
Health | Expense Category |
Skincare | Expense Category |
Entertainment | Expense Category |
Shopping | Expense Category |
Subscriptions | Expense Category |
Gifts | Expense Category |
Travel | Expense Category |
Business Expense | Expense Category |
Other | Expense Category |
Client Payment | Income Category |
Other Income | Income Category |
Arcadia | Income Source |
Degen Token Base | Income Source |
Non-EC Client | Income Source |
Freelance | Income Source |
Other Income | Income Source |
```

**Monthly Summary** (headers only)
```
Month | Total Income (PHP) | Total Expenses (PHP) | Net Cash Flow (PHP) | Savings (PHP) | Savings Rate | Notes
```

**Settings**
```
Setting | Value
USD_TO_PHP_RATE | 58.50
DEFAULT_CURRENCY | PHP
TIMEZONE | Asia/Manila
```

**Audit Log**
```
Timestamp | Action | Transaction ID | Detail | Telegram User ID
```

### B4. Share the Sheet with the robot account
Click **Share** (top-right of the Sheet) → paste the `client_email` from B2.5 →
set it to **Editor** → send. *(This is the step people forget — without it the
bot can't open the Sheet.)*

### B5. Copy the Sheet ID
Look at the Sheet's URL:
`https://docs.google.com/spreadsheets/d/`**`THIS_LONG_PART`**`/edit`
Copy the bold part — that's your **Sheet ID**.

---

## Phase C — put it on your computer

1. **Install Python 3.11+** from python.org. On Windows, tick *"Add Python to
   PATH"* during install. (Install VS Code too if you'd like a nice editor.)
2. Put this whole `prosfolio-ai-bot` folder somewhere easy, like your Desktop.
3. Open a terminal **inside the folder**:
   - Windows: open the folder, type `cmd` in the address bar, press Enter.
   - Mac: right-click the folder → *New Terminal at Folder*.
4. Create and turn on a virtual environment (a clean sandbox for the packages):
   ```
   python -m venv venv
   ```
   - Windows: `venv\Scripts\activate`
   - Mac/Linux: `source venv/bin/activate`
   You'll see `(venv)` appear at the start of the line.
5. Install the packages:
   ```
   pip install -r requirements.txt
   ```

---

## Phase D — fill in your `.env`

1. In the folder, copy `.env.example` to a new file named exactly **`.env`**.
2. Open `.env` and replace each `PASTE_...` with your real values:
   - `TELEGRAM_BOT_TOKEN` — your **fresh** token from the revoke step.
   - `ALLOWED_TELEGRAM_USER_ID` — your number from A1.
   - `ANTHROPIC_API_KEY` — your key from A2.
   - `GOOGLE_SHEET_ID` — from B5.
   - Leave `GOOGLE_SERVICE_ACCOUNT_FILE` as is (the JSON is already in
     `credentials/`).
3. Save. **Never share this file.**

---

## Phase E — run it 🎉

With `(venv)` still showing, run:
```
python src/main.py
```
You should see **"ProsFolio AI is running."** Leave that terminal open.

Now open Telegram, go to your bot, and try these one at a time:

- `/start`
- `spent ₱150 on food using GCash` → tap **Confirm** → it shows your new GCash balance
- `Arcadia paid me $450 for TMGM` → see the USD→PHP conversion
- `transfer ₱1000 from UnionBank to Emergency Savings`
- `/balance`
- `/report`
- `/undo`

Check your Google Sheet — the rows are there. That's a real, working bot. 🥳

To stop it, press **Ctrl+C** in the terminal. To start it again later, reopen the
terminal in the folder, run the activate command from C4, then `python src/main.py`.

---

## If something breaks

- **"Setup not finished… missing"** — a value in `.env` is blank or still says
  `PASTE`, or the JSON isn't in `credentials/`. Fix and rerun.
- **`SpreadsheetNotFound` / `PermissionError`** — you didn't share the Sheet with
  the `client_email` (Phase B4), or the Sheet ID is wrong (B5).
- **`WorksheetNotFound`** — a tab name doesn't match exactly (check spelling and
  capitalization against Phase B3).
- **Bot doesn't reply at all** — wrong `TELEGRAM_BOT_TOKEN`, or
  `ALLOWED_TELEGRAM_USER_ID` isn't your number. Also make sure the terminal still
  shows it's running.
- **AI error** — check the Anthropic key and that you added billing credit.
- Details of any error are saved in `logs/bot.log`.

---

## Later (version 2 — only when you're ready)

These are upgrades, not requirements:

- **Run it 24/7** without your laptop on. Easiest free-ish hosts for a small
  Python bot: **Railway**, **Render** (background worker), or a **Raspberry Pi**
  at home. We can do this together step by step when you want.
- **Receipt photos** → send a photo, it reads and logs it.
- **Voice notes** → speak it instead of typing.
- **Smart memory** → it learns that "Grab" = Transport, "Netflix" = Subscriptions.
- **Auto monthly summary** messaged to you on the 1st.
- **Budgets & gentle alerts** ("80% of your food budget").
- **Looker Studio dashboard** connected to the same Sheet for charts.

---

## What each file does (for the curious)

- `src/main.py` — the bot: commands and the confirm-before-saving flow.
- `src/parser.py` — sends your message to Claude, gets back structured data.
- `src/logic.py` — validates it, does all the money math, updates balances.
- `src/sheets.py` — reads and writes your Google Sheet.
- `src/rates.py` — the daily live USD→PHP rate (and your manual override).
- `src/config.py` — loads your `.env` and checks nothing's missing.

The golden rule baked into the code: **the AI only decides what you meant; Python
does every calculation.** Your balances can't drift.
