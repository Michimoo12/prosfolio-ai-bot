# Running ProsFolio AI 24/7 (Phase 2)

*Only do this once your bot already works on your computer (Phase E in the
README). This is the step that keeps it alive when your laptop is off.*

Take your time — this is genuinely optional. Your bot is fully usable running on
your own computer whenever it's on. This guide is for when you want it hands-off.

---

## First, the honest landscape

Your bot "listens" by constantly asking Telegram *"any new messages?"* (this is
called **polling**). That means it needs a machine that stays **awake and
running** all the time. Because of that:

- **Don't** use Render's free tier, Vercel, or AWS Lambda — they either sleep
  after a few minutes or can't run a always-on process at all. Your bot would go
  silent.
- The two options that actually work well are below. Pick one.

---

## Option 1 — A device at home (free, most private) 🏠

Best if you have an old laptop, a spare phone, or a Raspberry Pi you can leave
plugged in. Your tokens and Google key never leave your own hardware — nice for a
money bot.

1. Copy the `prosfolio-ai-bot` folder onto that device (with its `.env` and the
   `credentials/` JSON, exactly as on your main computer).
2. Install Python, set up the venv, and `pip install -r requirements.txt` — same
   as the README Phase C.
3. Run `python src/main.py`.
4. Stop the computer from sleeping so it keeps running:
   - Windows: Settings → Power → Screen and sleep → set "sleep" to **Never** while
     plugged in.
   - Mac: System Settings → Battery/Lock Screen → prevent sleep when plugged in
     (or run `caffeinate -s python src/main.py`).
   - Raspberry Pi: it never sleeps by default. ✔

That's it — as long as that device is on and online, your bot answers. If it
restarts, just run the command again (or ask me later how to auto-start it).

**Trade-off:** the device has to stay on. If that's annoying, use Option 2.

---

## Option 2 — Railway (easiest cloud, a few dollars a month) ☁️

No server to manage: you connect your code, paste your secrets into a dashboard,
and it runs. Realistically it costs about **$5/month** after a small free trial
credit — the truly-free hosts can't keep a polling bot awake, so this is the
honest "cheap and reliable" choice.

The project is already set up so you can host it **without uploading any secrets**.

### Step 1 — Put your code on GitHub (secrets stay out)
1. Make a free account at **github.com**.
2. Create a **new repository** (name it `prosfolio-ai-bot`), set it to
   **Private**.
3. Upload the project files. The included `.gitignore` already blocks `.env`,
   the `credentials/` folder, and `logs/`, so your secrets won't be uploaded —
   **double-check that `.env` and your JSON key are NOT in the uploaded files.**
   (Easiest no-command way: install **GitHub Desktop**, drag the folder in, and
   it respects `.gitignore` automatically.)

### Step 2 — Get your Google key ready as text
Open `credentials/google-service-account.json` in a text editor and copy the
**entire contents** (it's one big block starting with `{` and ending with `}`).
You'll paste this into Railway in Step 4 — this is how the bot gets its Google
key without the file being in your repo.

### Step 3 — Create the Railway project
1. Go to **railway.com**, sign in **with GitHub** (this verifies your account).
2. **New Project → Deploy from GitHub repo →** pick `prosfolio-ai-bot`.
3. Railway auto-detects Python and installs from `requirements.txt`. No Dockerfile
   needed.

### Step 4 — Add your secrets as Variables
In your service → **Variables**, add these (same values as your local `.env`):

```
TELEGRAM_BOT_TOKEN         = your fresh bot token
ALLOWED_TELEGRAM_USER_ID   = your numeric id
ANTHROPIC_API_KEY          = your sk-ant-... key
GOOGLE_SHEET_ID            = your sheet id
GOOGLE_CREDENTIALS_JSON    = (paste the ENTIRE JSON from Step 2 here)
DEFAULT_USD_TO_PHP_RATE    = 58.50
TIMEZONE                   = Asia/Manila
```

### Step 5 — Tell it how to start
In **Settings → Deploy → Start Command**, set:
```
python src/main.py
```

### Step 6 — Deploy and check
Open the **Logs** tab. When you see **"ProsFolio AI is running."**, message your
bot on Telegram — it should reply.

### Step 7 — Important: only one copy at a time ⚠️
Telegram allows only **one** running bot per token. Once Railway is running your
bot, **stop the copy on your computer** (press Ctrl+C in that terminal).
Otherwise you'll see a "conflict" error and replies get flaky.

---

## Backups (you asked for this)

Good news — Google Sheets backs itself up automatically:

- **Automatic:** every edit is saved in **File → Version history → See version
  history**. You can roll back to any earlier point for free.
- **Manual habit:** once a month, **File → Make a copy** and name it like
  `ProsFolio AI DB — backup 2026-07`. Thirty seconds, total peace of mind.
- Later, if you want it fully automatic, we can add a tiny Google Apps Script
  that copies the sheet on a schedule.

---

## Which should you pick?

- Want it **free** and don't mind a device staying on → **Option 1 (home)**.
- Want it **hands-off** and are okay with ~$5/month → **Option 2 (Railway)**.

Either way, start only after your local bot works. And whenever you get stuck on
a step, that's normal — save your progress and pick it up later. No rush. 💛
