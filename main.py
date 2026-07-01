"""
main.py
The bot. Run this file to start ProsFolio AI.

From the project folder:   python src/main.py
"""

import os
import sys
import logging

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
logging.basicConfig(
    filename="logs/bot.log",
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)

bot = telebot.TeleBot(config.TELEGRAM_BOT_TOKEN, parse_mode="HTML")
ALLOWED_ID = int(config.ALLOWED_TELEGRAM_USER_ID)

# Remembers the transaction(s) waiting for your Confirm tap. Keyed by user id.
pending = {}


def authorized(message):
    return message.from_user.id == ALLOWED_ID


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
        "• I'll show a summary and you tap <b>Confirm</b> before it's saved.\n\n"
        "<b>Commands</b>\n"
        "/balance – total + per-account balances\n"
        "/accounts – list your accounts\n"
        "/history – last 10 transactions\n"
        "/report – this month's income, expenses, savings\n"
        "/undo – reverse the last transaction\n"
        "/setrate 58.9 – set the USD→PHP rate manually\n"
        "/undo – reverse the last transaction\n\n"
        "<b>➕ Add new items</b>\n"
        "/addclient Name – add a client\n"
        "/addsource Name – add an income source\n"
        "/addcategory Name – add an expense category\n"
        "/addaccount Name PHP 5000 – add an account\n\n"
        "<b>📊 View lists</b>\n"
        "/clients  /categories  /accounts\n"
        "/rate – show current USD→PHP rate",
    )


@bot.message_handler(commands=["balance", "accounts"])
def cmd_balance(m):
    if not authorized(m):
        return
    rate = rates.get_rate(sheets)
    accounts = sheets.get_accounts()
    total_php = 0
    lines = ["<b>Balances</b>"]
    for a in accounts:
        lines.append(f"{a['name']}: {logic.money(a['balance'], a['currency'])}")
        total_php += a["balance"] * rate if a["currency"] == "USD" else a["balance"]
    lines.append(f"\n<b>Net worth:</b> {logic.peso(total_php)}  (at ₱{rate}/USD)")
    bot.reply_to(m, "\n".join(lines))


@bot.message_handler(commands=["history"])
def cmd_history(m):
    if not authorized(m):
        return
    rows = sheets.get_recent_transactions(10)
    if not rows:
        return bot.reply_to(m, "No transactions yet.")
    lines = ["<b>Recent</b>"]
    for r in rows:
        sign = "+" if r.get("Type") == "income" else ("−" if r.get("Type") == "expense" else "↔")
        amt = logic.money(float(r.get("Amount", 0) or 0), r.get("Currency", "PHP"))
        tag = "  (reversed)" if str(r.get("Status", "")).lower() == "reversed" else ""
        lines.append(f"{sign} {amt} · {r.get('Account')} · {r.get('Transaction Date')}{tag}")
    bot.reply_to(m, "\n".join(lines))


@bot.message_handler(commands=["report"])
def cmd_report(m):
    if not authorized(m):
        return
    import datetime
    ym = datetime.date.today().strftime("%Y-%m")
    rows = sheets.get_month_transactions(ym)
    income = sum(float(r.get("PHP Equivalent", 0) or 0) for r in rows if r.get("Type") == "income")
    expense = sum(float(r.get("PHP Equivalent", 0) or 0) for r in rows if r.get("Type") == "expense")
    net = income - expense
    rate_txt = f"{(net/income*100):.0f}%" if income > 0 else "—"
    cats = {}
    for r in rows:
        if r.get("Type") == "expense":
            cats[r.get("Category")] = cats.get(r.get("Category"), 0) + float(r.get("PHP Equivalent", 0) or 0)
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
            lines.append(f"  {c}: {logic.peso(v)}")
    bot.reply_to(m, "\n".join(lines))


@bot.message_handler(commands=["undo"])
def cmd_undo(m):
    if not authorized(m):
        return
    bot.reply_to(m, logic.undo_last(m.from_user.id))


@bot.message_handler(commands=["rate"])
def cmd_rate(m):
    if not authorized(m):
        return
    bot.reply_to(m, f"Current USD→PHP rate: ₱{rates.get_rate(sheets)}")


@bot.message_handler(commands=["setrate"])
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


# ---------------- natural language ----------------

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(m):
    if not authorized(m):
        return
    try:
        rate = rates.get_rate(sheets)
        parsed = parser.parse(m.text, context_for_ai(), rate)
        actions = parsed.get("actions", [])
        if not actions:
            return bot.reply_to(m, parsed.get("reply") or "I couldn't read that — try rephrasing.")

        built = []
        for a in actions:
            tx, question = logic.validate(a, rate)
            if question:
                return bot.reply_to(m, question)  # ask, don't save anything yet
            built.append(tx)

        pending[m.from_user.id] = built
        summary = "\n\n".join(logic.confirm_text(tx) for tx in built)

        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("✅ Confirm", callback_data="confirm"),
            types.InlineKeyboardButton("✏️ Edit", callback_data="edit"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
        )
        bot.reply_to(m, summary, reply_markup=kb)
    except Exception as e:
        logging.exception("handle_text failed")
        bot.reply_to(m, "Something went wrong on my side. Try again, or check logs/bot.log.")


@bot.callback_query_handler(func=lambda c: True)
def handle_button(c):
    if c.from_user.id != ALLOWED_ID:
        return
    uid = c.from_user.id
    items = pending.get(uid)
    bot.answer_callback_query(c.id)

    if c.data == "cancel":
        pending.pop(uid, None)
        return bot.edit_message_text("❌ Cancelled. Nothing was saved.",
                                     c.message.chat.id, c.message.message_id)
    if c.data == "edit":
        pending.pop(uid, None)
        return bot.edit_message_text("No problem — just send it again with the correction.",
                                     c.message.chat.id, c.message.message_id)
    if c.data == "confirm":
        if not items:
            return bot.edit_message_text("That request expired. Please send it again.",
                                         c.message.chat.id, c.message.message_id)
        try:
            results = [logic.apply(tx, uid) for tx in items]
            pending.pop(uid, None)
            bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=None)
            bot.send_message(c.message.chat.id, "\n\n".join(results))
        except Exception:
            logging.exception("confirm failed")
            bot.send_message(c.message.chat.id, "Couldn't save that — check logs/bot.log.")



# ---------------- list views ----------------

@bot.message_handler(commands=["clients"])
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
        bot.reply_to(m, f"✅ Client <b>{name}</b> ({currency}) added.\n"
                        f"You can now say \"<i>{name} paid me $300</i>\" and I'll recognize it.")
    else:
        bot.reply_to(m, f"<b>{name}</b> already exists in your clients list.")


@bot.message_handler(commands=["addsource"])
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
        bot.reply_to(m, f"✅ Income source <b>{name}</b> added.\n"
                        f"You can now say \"<i>received $200 from {name}</i>\" and I'll recognize it.")
    else:
        bot.reply_to(m, f"<b>{name}</b> already exists as an income source.")


@bot.message_handler(commands=["addcategory"])
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
        bot.reply_to(m, f"✅ Expense category <b>{name}</b> added.\n"
                        f"You can now say \"<i>spent ₱500 on {name}</i>\" and I'll categorize it correctly.")
    else:
        bot.reply_to(m, f"<b>{name}</b> already exists as an expense category.")


@bot.message_handler(commands=["addaccount"])
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
        bot.reply_to(m, f"✅ Account <b>{name}</b> ({currency}, {bal_str}) added.\n"
                        f"You can now say \"<i>spent ₱200 using {name}</i>\" and I'll update it.")
    else:
        bot.reply_to(m, f"<b>{name}</b> already exists in your accounts.")


if __name__ == "__main__":
    print("ProsFolio AI is running. Press Ctrl+C to stop.")
    logging.info("Bot started.")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)
