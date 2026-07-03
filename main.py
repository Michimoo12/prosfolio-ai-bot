"""
main.py
The bot. Run this file to start ProsFolio AI.

From the project folder:   python main.py
"""

import os
import re
import sys
import html
import time
import logging
import functools

# Let this file import its sibling modules (config, sheets, ...).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import telebot
from telebot import types

import config
import sheets
import parser
import logic
import rates

# ---- startup checks & logging ----
config.check()

os.makedirs("logs", exist_ok=True)

# IMPORTANT: log to BOTH a local file and stdout. Railway's "Logs" tab only
# captures stdout/stderr — if you only log to a file, everything after your
# first print() (including pyTelegramBotAPI's own connection/retry messages
# and any exception you catch) disappears from Railway's view even though the
# bot is still doing something. This was the reason logs seemed to stop dead
# after "ProsFolio AI is running."
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log"),
        logging.StreamHandler(sys.stdout),
    ],
)

# Make sure pyTelegramBotAPI's own internal logger (connection errors, 409
# conflicts, retries) also flows through to the handlers above instead of
# being silently dropped.
telebot.logger.setLevel(logging.INFO)

bot = telebot.TeleBot(config.TELEGRAM_BOT_TOKEN, parse_mode="HTML")
ALLOWED_ID = int(config.ALLOWED_TELEGRAM_USER_ID)

# Remembers the transaction(s) waiting for your Confirm tap. Keyed by user id.
pending = {}
# User ids whose Confirm is currently being saved (guards double-taps).
saving = set()
# Waiting /delete confirmations: uid -> Transaction ID.
pending_delete = {}
# The Transaction IDs shown by the user's last /history, in display order,
# so "/delete 2" can resolve the number to a real transaction.
hist_map = {}
# Per-user memory of the last message that parsed into actions but was
# missing something (we asked e.g. "Which account?"). A short reply like
# "unionbank" used to be parsed from scratch, lose all context, and get
# answered with a balance lookup. Now the reply is combined with the stored
# message and re-parsed, so answering the question completes the original
# transaction. uid -> (text, timestamp)
followup = {}
FOLLOWUP_TTL = 600  # seconds


def account_mentioned(text):
    """
    If EXACTLY ONE known account name literally appears in the text, return
    it. Deterministic safety net for when the AI misses an explicit
    "... using unionbank" — Python, not the model, gets the final say.
    Word-bounded so "using gcash" can never also count as "Cash".
    """
    found = []
    low = str(text).lower()
    for a in sheets.get_accounts():
        pat = r"\b" + re.escape(a["name"].lower()).replace(r"\ ", r"\s+") + r"\b"
        if re.search(pat, low):
            found.append(a["name"])
    return found[0] if len(found) == 1 else None

# Escape user-typed names/notes before they go inside HTML replies. Without
# this, an account called "R&B" or a note containing "<3" makes Telegram
# reject the whole message (parse error) and the reply never arrives.
esc = html.escape


def authorized(message):
    return message.from_user.id == ALLOWED_ID


def typing(chat_id):
    """
    Show "typing…" in Telegram while we work. The AI parse plus sheet reads
    take a couple of seconds; without this the bot just looks dead until the
    reply lands. Never allowed to break the actual handling.
    """
    try:
        bot.send_chat_action(chat_id, "typing")
    except Exception:
        pass


def guarded(handler):
    """
    Wrap a command handler so a failure actually replies to you instead of
    vanishing into total silence.

    Before this, every /command handler had NO error handling at all (only
    the free-text handler did). If sheets.py, Google Sheets, or anything else
    threw an exception inside a command — e.g. a permissions error on a
    write — pyTelegramBotAPI would swallow it internally and you'd just never
    get a reply, with nothing to go on. This makes every command behave like
    handle_text already did: log the real exception (now visible in Railway
    thanks to the stdout logging fix) AND tell you on Telegram that it
    failed, instead of leaving you guessing whether the bot even saw your
    message.
    """
    @functools.wraps(handler)
    def wrapped(m, *args, **kwargs):
        try:
            return handler(m, *args, **kwargs)
        except Exception:
            logging.exception(f"{handler.__name__} failed")
            try:
                bot.reply_to(
                    m,
                    "⚠️ That command failed. Most likely cause: the Google Sheet "
                    "isn't shared as Editor with your service account, or a tab/column "
                    "name doesn't match. Check Railway's deploy logs for the exact error "
                    "(search for \"Exception\" right after this message)."
                )
            except Exception:
                pass
    return wrapped


def context_for_ai():
    expense, income, sources = sheets.get_categories()
    return {
        "accounts": sheets.get_accounts(),
        "expense_categories": expense,
        "income_categories": income,
        "income_sources": sources,
        "clients": sheets.get_clients(),
    }


# ---------------- commands ----------------

@bot.message_handler(commands=["start"])
@guarded
def cmd_start(m):
    if not authorized(m):
        return bot.reply_to(m, "This bot is private.")
    bot.reply_to(
        m,
        "<b>ProsFolio AI</b> — your personal AI income and expense tracker.\n\n"
        "Just tell me what happened in plain words and I'll log it and update your balances.\n"
        "e.g. <i>“Arcadia paid me $450 for TMGM”</i> or <i>“spent ₱1,000 on food using GCash”</i>\n\n"
        "Type /help to see all commands.",
    )


@bot.message_handler(commands=["help"])
@guarded
def cmd_help(m):
    if not authorized(m):
        return
    bot.reply_to(
        m,
        "<b>How to use ProsFolio AI</b>\n\n"
        "• Log anything by typing it normally:\n"
        "  “paid ₱1,625 for internet”\n"
        "  “received $300 from freelance”\n"
        "  “transfer ₱20,000 from UnionBank to Emergency Savings”\n\n"
        "• Just telling me a real balance also works:\n"
        "  “I have ₱2,032 in GCash”\n"
        "  “GCash is now down to 400”\n\n"
        "• I'll show a summary and you tap <b>Confirm</b> before it's saved.\n\n"
        "<b>Commands</b>\n"
        "/balance – total + per-account balances\n"
        "/accounts – list your accounts\n"
        "/history – last 10 transactions (numbered)\n"
        "/report – this month's income, expenses, savings\n"
        "/undo – reverse the newest transaction\n"
        "/delete 2 – permanently remove item #2 from /history\n"
        "/setbalance GCash 2032 – correct an account balance directly\n"
        "/setrate 58.9 – set the USD→PHP rate manually\n"
        "/rate – show current USD→PHP rate\n\n"
        "<b>➕ Add new items</b>\n"
        "/addclient Name – add a client\n"
        "/addsource Name – add an income source\n"
        "/addcategory Name – add an expense category\n"
        "/addaccount Name PHP 5000 – add an account\n\n"
        "<b>➖ Remove items</b>\n"
        "/removeclient Name\n"
        "/removecategory Name\n"
        "/removeaccount Name\n\n"
        "<b>📊 View lists</b>\n"
        "/clients  /categories  /accounts",
    )


@bot.message_handler(commands=["balance", "accounts"])
@guarded
def cmd_balance(m):
    if not authorized(m):
        return
    rate = rates.get_rate(sheets)
    accounts = sheets.get_accounts()
    total_php = 0
    lines = ["<b>Balances</b>"]
    for a in accounts:
        lines.append(f"{esc(a['name'])}: {logic.money(a['balance'], a['currency'])}")
        total_php += a["balance"] * rate if a["currency"] == "USD" else a["balance"]
    lines.append(f"\n<b>Net worth:</b> {logic.peso(total_php)}  (at ₱{rate}/USD)")
    bot.reply_to(m, "\n".join(lines))


@bot.message_handler(commands=["history"])
@guarded
def cmd_history(m):
    if not authorized(m):
        return
    rows = sheets.get_recent_transactions(10)
    if not rows:
        return bot.reply_to(m, "No transactions yet.")
    lines = ["<b>Recent</b>"]
    signs = {"income": "+", "expense": "−", "adjustment": "±"}
    ids = []
    for i, r in enumerate(rows, start=1):
        sign = signs.get(str(r.get("Type", "")).lower(), "↔")
        # _to_float instead of float(): the sheet can hand back "1,234.56" as
        # a formatted string, which float() refuses.
        amt = logic.money(sheets._to_float(r.get("Amount", 0)), r.get("Currency", "PHP"))
        tag = "  (reversed)" if str(r.get("Status", "")).lower() == "reversed" else ""
        lines.append(f"{i}. {sign} {amt} · {esc(str(r.get('Account') or ''))} · {r.get('Transaction Date')}{tag}")
        ids.append(str(r.get("Transaction ID", "")))
    hist_map[m.from_user.id] = ids
    lines.append("\n/undo reverses the newest · /delete 2 removes #2 permanently")
    bot.reply_to(m, "\n".join(lines))


@bot.message_handler(commands=["report"])
@guarded
def cmd_report(m):
    if not authorized(m):
        return
    # config.now() is Manila time — datetime.date.today() on Railway is UTC,
    # which put the first 8 hours of every month into the previous month.
    ym = config.now().strftime("%Y-%m")
    rows = sheets.get_month_transactions(ym)
    income = sum(sheets._to_float(r.get("PHP Equivalent", 0)) for r in rows if r.get("Type") == "income")
    expense = sum(sheets._to_float(r.get("PHP Equivalent", 0)) for r in rows if r.get("Type") == "expense")
    net = income - expense
    rate_txt = f"{(net/income*100):.0f}%" if income > 0 else "—"
    cats = {}
    for r in rows:
        if r.get("Type") == "expense":
            cats[r.get("Category")] = cats.get(r.get("Category"), 0) + sheets._to_float(r.get("PHP Equivalent", 0))
    top = sorted(cats.items(), key=lambda x: x[1], reverse=True)[:3]
    lines = [
        f"<b>This month ({ym})</b>",
        f"Income: {logic.peso(income)}",
        f"Expenses: {logic.peso(expense)}",
        f"Net / savings: {logic.peso(net)}  (savings rate {rate_txt})",
    ]
    if top:
        lines.append("\nTop spending:")
        for c, v in top:
            lines.append(f"  {esc(str(c))}: {logic.peso(v)}")
    bot.reply_to(m, "\n".join(lines))


@bot.message_handler(commands=["undo"])
@guarded
def cmd_undo(m):
    if not authorized(m):
        return
    bot.reply_to(m, logic.undo_last(m.from_user.id))


@bot.message_handler(commands=["delete"])
@guarded
def cmd_delete(m):
    if not authorized(m):
        return
    # Usage: /delete 2 (number from your last /history) or /delete TX-AB12CD34
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        return bot.reply_to(m, "Usage: /delete 2  (the number shown by /history)\n"
                               "or /delete TX-AB12CD34\n\n"
                               "Run /history first to see the numbers.")
    ref = parts[1].strip()
    if ref.isdigit():
        ids = hist_map.get(m.from_user.id) or []
        idx = int(ref) - 1
        if idx < 0 or idx >= len(ids) or not ids[idx]:
            return bot.reply_to(m, "That number isn't on your last /history list — "
                                   "run /history again and use a number it shows.")
        tx_id = ids[idx]
    else:
        tx_id = ref
    r = sheets.find_transaction(tx_id)
    if not r:
        return bot.reply_to(m, "Couldn't find that transaction. Run /history and use the number shown.")
    pending_delete[m.from_user.id] = str(r.get("Transaction ID"))
    amt = logic.money(sheets._to_float(r.get("Amount", 0)), r.get("Currency", "PHP") or "PHP")
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🗑 Delete", callback_data="delconfirm"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
    )
    bot.reply_to(
        m,
        "<b>Delete this transaction permanently?</b>\n"
        f"{esc(str(r.get('Type') or ''))} · {amt} · {esc(str(r.get('Account') or ''))} · "
        f"{esc(str(r.get('Transaction Date') or ''))}\n"
        f"{esc(str(r.get('Description') or ''))}\n\n"
        "The row is removed from your sheet and its balance effect is reversed.",
        reply_markup=kb,
    )


@bot.message_handler(commands=["rate"])
@guarded
def cmd_rate(m):
    if not authorized(m):
        return
    bot.reply_to(m, f"Current USD→PHP rate: ₱{rates.get_rate(sheets)}")


@bot.message_handler(commands=["setrate"])
@guarded
def cmd_setrate(m):
    if not authorized(m):
        return
    parts = m.text.split()
    if len(parts) < 2:
        return bot.reply_to(m, "Usage: /setrate 58.90")
    try:
        rate = float(parts[1])
    except ValueError:
        return bot.reply_to(m, "That doesn't look like a number. Try: /setrate 58.90")
    rates.set_manual_rate(sheets, rate)
    bot.reply_to(m, f"Done. USD→PHP rate set to ₱{rate} (manual for today).")


@bot.message_handler(commands=["setbalance"])
@guarded
def cmd_setbalance(m):
    if not authorized(m):
        return
    # Usage: /setbalance AccountName Amount
    # A guaranteed, no-AI way to correct an account's balance — use this if
    # you ever say "I have X in Y" in plain chat and it doesn't stick.
    parts = m.text.split(maxsplit=2)
    if len(parts) < 3:
        return bot.reply_to(m, "Usage: /setbalance AccountName Amount\nExample: /setbalance GCash 2032")
    name = parts[1].strip()
    acct = sheets.get_account(name)
    if not acct:
        names = ", ".join(a["name"] for a in sheets.get_accounts())
        return bot.reply_to(m, f"Account not found. Your accounts: {esc(names)}")
    try:
        new_balance = float(parts[2].replace(",", "").replace("₱", "").replace("$", "").strip())
    except ValueError:
        return bot.reply_to(m, "That doesn't look like a number.")
    rate = rates.get_rate(sheets)
    tx, question = logic.validate(
        {"type": "adjust_balance", "amount": new_balance, "currency": acct["currency"], "account": acct["name"]},
        rate,
    )
    if question:
        return bot.reply_to(m, question)
    pending[m.from_user.id] = [tx]
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ Confirm", callback_data="confirm"),
        types.InlineKeyboardButton("✏️ Edit", callback_data="edit"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
    )
    bot.reply_to(m, logic.confirm_text(tx), reply_markup=kb)


# ---------------- list views ----------------

@bot.message_handler(commands=["clients"])
@guarded
def cmd_clients(m):
    if not authorized(m):
        return
    items = sheets.get_clients()
    if not items:
        return bot.reply_to(m, "No clients yet. Use /addclient to add one.")
    lines = ["<b>Clients</b>"] + [f"• {c}" for c in sorted(items)]
    lines.append("\n/addclient YourClientName")
    bot.reply_to(m, "\n".join(lines))


@bot.message_handler(commands=["categories"])
@guarded
def cmd_categories(m):
    if not authorized(m):
        return
    expense, income, sources = sheets.get_categories()
    lines = ["<b>Expense categories</b>"] + [f"• {c}" for c in expense]
    lines += ["\n<b>Income sources</b>"] + [f"• {s}" for s in sources]
    lines.append("\nAdd new: /addcategory FoodName  or  /addsource SourceName")
    bot.reply_to(m, "\n".join(lines))


# ---------------- add new items ----------------

@bot.message_handler(commands=["addclient"])
@guarded
def cmd_addclient(m):
    if not authorized(m):
        return
    # Usage: /addclient ClientName [USD|PHP]
    parts = m.text.split(maxsplit=2)
    if len(parts) < 2:
        return bot.reply_to(
            m,
            "Usage: /addclient ClientName [USD or PHP]\n"
            "Examples:\n"
            "  /addclient Roobet\n"
            "  /addclient LocalClient PHP"
        )
    name = parts[1].strip()
    currency = parts[2].strip().upper() if len(parts) > 2 else "USD"
    if currency not in ("PHP", "USD"):
        currency = "USD"
    added = sheets.add_client(name, currency)
    if added:
        bot.reply_to(m, f"✅ Client <b>{esc(name)}</b> ({currency}) added.\n"
                        f"You can now say \"<i>{esc(name)} paid me $300</i>\" and I'll recognize it.")
    else:
        bot.reply_to(m, f"<b>{esc(name)}</b> already exists in your clients list.")


@bot.message_handler(commands=["addsource"])
@guarded
def cmd_addsource(m):
    if not authorized(m):
        return
    # Usage: /addsource SourceName
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        return bot.reply_to(
            m,
            "Usage: /addsource SourceName\n"
            "Example: /addsource Fiverr\n\n"
            "Income sources are WHO PAYS you (e.g. Arcadia, Fiverr, Direct Client)."
        )
    name = parts[1].strip()
    added = sheets.add_income_source(name)
    if added:
        bot.reply_to(m, f"✅ Income source <b>{esc(name)}</b> added.\n"
                        f"You can now say \"<i>received $200 from {esc(name)}</i>\" and I'll recognize it.")
    else:
        bot.reply_to(m, f"<b>{esc(name)}</b> already exists as an income source.")


@bot.message_handler(commands=["addcategory"])
@guarded
def cmd_addcategory(m):
    if not authorized(m):
        return
    # Usage: /addcategory CategoryName
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        return bot.reply_to(
            m,
            "Usage: /addcategory CategoryName\n"
            "Example: /addcategory Laundry\n\n"
            "Categories are for EXPENSES (e.g. Food, Transport, Laundry).\n"
            "For income sources, use /addsource instead."
        )
    name = parts[1].strip()
    added = sheets.add_expense_category(name)
    if added:
        bot.reply_to(m, f"✅ Expense category <b>{esc(name)}</b> added.\n"
                        f"You can now say \"<i>spent ₱500 on {esc(name)}</i>\" and I'll categorize it correctly.")
    else:
        bot.reply_to(m, f"<b>{esc(name)}</b> already exists as an expense category.")


@bot.message_handler(commands=["addaccount"])
@guarded
def cmd_addaccount(m):
    if not authorized(m):
        return
    # Usage: /addaccount AccountName [PHP|USD] [balance]
    # Example: /addaccount SeaBank PHP 5000
    parts = m.text.split(maxsplit=3)
    if len(parts) < 2:
        return bot.reply_to(
            m,
            "Usage: /addaccount AccountName [PHP or USD] [starting balance]\n"
            "Examples:\n"
            "  /addaccount SeaBank PHP 5000\n"
            "  /addaccount Kraken USD 0\n\n"
            "Currency defaults to PHP, starting balance defaults to 0."
        )
    name = parts[1].strip()
    currency = parts[2].strip().upper() if len(parts) > 2 else "PHP"
    if currency not in ("PHP", "USD"):
        currency = "PHP"
    try:
        balance = float(parts[3].replace(",", "")) if len(parts) > 3 else 0.0
    except ValueError:
        balance = 0.0
    added = sheets.add_account(name, "bank", currency, balance)
    if added:
        bal_str = logic.money(balance, currency)
        bot.reply_to(m, f"✅ Account <b>{esc(name)}</b> ({currency}, {bal_str}) added.\n"
                        f"You can now say \"<i>spent ₱200 using {esc(name)}</i>\" and I'll update it.")
    else:
        bot.reply_to(m, f"<b>{esc(name)}</b> already exists in your accounts.")


@bot.message_handler(commands=["removeclient"])
@guarded
def cmd_removeclient(m):
    if not authorized(m):
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        return bot.reply_to(m, "Usage: /removeclient ClientName\nExample: /removeclient TMGM")
    name = parts[1].strip()
    removed = sheets.remove_client(name)
    if removed:
        bot.reply_to(m, f"✅ Client <b>{esc(name)}</b> removed.")
    else:
        bot.reply_to(m, f"❌ Client <b>{esc(name)}</b> not found. Check /clients for the exact name.")


@bot.message_handler(commands=["removecategory"])
@guarded
def cmd_removecategory(m):
    if not authorized(m):
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        return bot.reply_to(m, "Usage: /removecategory CategoryName\nExample: /removecategory Laundry")
    name = parts[1].strip()
    removed = sheets.remove_category(name)
    if removed:
        bot.reply_to(m, f"✅ Category <b>{esc(name)}</b> removed.")
    else:
        bot.reply_to(m, f"❌ <b>{esc(name)}</b> not found. Check /categories for the exact name.")


@bot.message_handler(commands=["removeaccount"])
@guarded
def cmd_removeaccount(m):
    if not authorized(m):
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        return bot.reply_to(m, "Usage: /removeaccount AccountName\nExample: /removeaccount SeaBank")
    name = parts[1].strip()
    removed = sheets.remove_account(name)
    if removed:
        bot.reply_to(m, f"✅ Account <b>{esc(name)}</b> removed.")
    else:
        bot.reply_to(m, f"❌ Account <b>{esc(name)}</b> not found. Check /accounts for the exact name.")


# ---------------- natural language ----------------
#
# ⚠️ REGISTRATION ORDER IS LOAD-BEARING. pyTelegramBotAPI dispatches each
# message to the FIRST registered handler whose filters match, and
# handle_text's filter (func=lambda m: True) matches EVERYTHING — including
# /commands. It must therefore be registered AFTER every command handler.
# When it sat in the middle of this file, every command defined below it
# (/addaccount, /addclient, /clients, /remove..., etc.) was unreachable:
# handle_text swallowed them and returned silently, which looked exactly like
# "the bot is randomly not responding." If you add a new /command, add it
# ABOVE this section.

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(m):
    if not authorized(m):
        return
    if m.text.startswith("/"):
        # This handler is registered last, so a "/" message reaching it means
        # no real command matched. Say so instead of staying silent.
        return bot.reply_to(m, "Unknown command. Type /help to see everything I understand.")
    typing(m.chat.id)
    try:
        uid = m.from_user.id
        rate = rates.get_rate(sheets)
        ctx = context_for_ai()

        # Let the NLU decide whether this is something to LOG. Keyword lists
        # are too blunt for routing: the old CHAT_TRIGGERS list contained
        # "spent", so "spent 700 on skincare" was answered as a question and
        # never logged. Now: parse first; if there's nothing to log, answer
        # it as a question with real data.
        effective = m.text
        parsed = parser.parse(effective, ctx, rate)
        actions = parsed.get("actions") or []

        if not actions:
            # Nothing to log on its own. If we just asked this user a
            # question ("Which account?"), treat this message as the ANSWER:
            # re-parse it together with the stored original message.
            prev = followup.get(uid)
            if prev and time.time() - prev[1] < FOLLOWUP_TTL:
                typing(m.chat.id)
                effective = prev[0] + "\n(Follow-up answer from the user: " + m.text + ")"
                parsed = parser.parse(effective, ctx, rate)
                actions = parsed.get("actions") or []
            if not actions:
                typing(m.chat.id)  # second AI call ahead — keep the indicator alive
                recent = sheets.get_recent_transactions(30)
                return bot.reply_to(m, parser.chat(m.text, ctx, rate, recent))

        followup.pop(uid, None)

        # Deterministic account fallback: if the user literally named exactly
        # one account in the message but the AI left `account` empty or
        # unmatchable, trust the message text.
        mention = account_mentioned(effective)
        if mention:
            for a in actions:
                if a.get("type") != "transfer" and not sheets.get_account(a.get("account") or ""):
                    a["account"] = mention

        built = []
        for a in actions:
            tx, question = logic.validate(a, rate)
            if question:
                # Remember what we were working on so the user's next short
                # reply ("unionbank", "970", "$") can complete it.
                followup[uid] = (effective, time.time())
                return bot.reply_to(m, question)
            built.append(tx)
        pending[uid] = built
        summary = "\n\n".join(logic.confirm_text(tx) for tx in built)
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("✅ Confirm", callback_data="confirm"),
            types.InlineKeyboardButton("✏️ Edit", callback_data="edit"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
        )
        bot.reply_to(m, summary, reply_markup=kb)
    except Exception:
        logging.exception("handle_text failed")
        bot.reply_to(m, "Something went wrong. Try again!")


@bot.callback_query_handler(func=lambda c: True)
def handle_button(c):
    if c.from_user.id != ALLOWED_ID:
        return
    uid = c.from_user.id
    bot.answer_callback_query(c.id)

    if c.data == "cancel":
        pending.pop(uid, None)
        pending_delete.pop(uid, None)
        return bot.edit_message_text("❌ Cancelled. Nothing was saved.",
                                      c.message.chat.id, c.message.message_id)

    if c.data == "delconfirm":
        if uid in saving:
            return
        tx_id = pending_delete.pop(uid, None)
        if not tx_id:
            try:
                bot.edit_message_text("That request expired. Please send it again.",
                                      c.message.chat.id, c.message.message_id)
            except Exception:
                pass
            return
        saving.add(uid)
        typing(c.message.chat.id)
        try:
            result = logic.delete_transaction(tx_id, uid)
        except Exception:
            logging.exception("delete failed")
            return bot.send_message(c.message.chat.id,
                                    "⚠️ Couldn't delete that. Check /history — it may still be there.")
        finally:
            saving.discard(uid)
        try:
            bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=None)
        except Exception:
            pass
        return bot.send_message(c.message.chat.id, result)
    if c.data == "edit":
        pending.pop(uid, None)
        return bot.edit_message_text("No problem — just send it again with the correction.",
                                      c.message.chat.id, c.message.message_id)
    if c.data == "confirm":
        # Ignore taps that land while a save for this user is already running
        # (a fast double-tap used to edit the card to "expired" mid-save and
        # then trip a Telegram 400 when the buttons were removed twice).
        if uid in saving:
            return
        # Pop BEFORE applying. TeleBot runs handlers on worker threads, so a
        # double-tap on Confirm used to deliver two callbacks that could both
        # see the same pending items and save the transaction twice.
        items = pending.pop(uid, None)
        if not items:
            try:
                bot.edit_message_text("That request expired. Please send it again.",
                                      c.message.chat.id, c.message.message_id)
            except Exception:
                pass  # e.g. re-tapping an already-expired card: text unchanged -> 400
            return
        saving.add(uid)
        typing(c.message.chat.id)
        try:
            results = [logic.apply(tx, uid) for tx in items]
        except Exception:
            logging.exception("confirm failed")
            return bot.send_message(
                c.message.chat.id,
                "⚠️ Couldn't save that. Check /history before retrying — "
                "part of it may have been saved. (Details are in the deploy logs.)")
        finally:
            saving.discard(uid)
        # The save SUCCEEDED past this point. Removing the buttons is purely
        # cosmetic — if it fails (e.g. they're already gone), never let that
        # turn a successful save into a scary ⚠️ message.
        try:
            bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.send_message(c.message.chat.id, "\n\n".join(results))


# ---------------- entry point ----------------
# NOTE: this must be the LAST thing in the file — every handler above needs to
# be registered with @bot before we start polling.

if __name__ == "__main__":
    print("ProsFolio AI is running. Press Ctrl+C to stop.", flush=True)
    logging.info("Bot started.")

    # Clear any stuck webhook AND drop queued updates from a previous/overlapping
    # instance. This is the main defense against the classic Railway redeploy
    # overlap: the old container is still polling for a second or two while the
    # new one boots, which is exactly what produces a 409 Conflict from Telegram.
    bot.delete_webhook(drop_pending_updates=True)
    time.sleep(1)

    # Outer retry loop: if polling ever raises (network blip, 409 conflict that
    # infinity_polling's own retry didn't absorb, etc.), log it somewhere visible
    # and restart instead of letting the process die silently.
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=30)
        except Exception:
            logging.exception("Polling crashed — restarting in 5 seconds.")
            time.sleep(5)
