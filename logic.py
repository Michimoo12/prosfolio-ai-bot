"""
logic.py
The trustworthy part. Python (not the AI) validates each action, does all the
math, updates balances, and writes to the sheet.
"""

import html
import uuid
import logging
import threading

import config
import sheets

# Free-text fields (account/category/client names, etc.) get interpolated
# into HTML-mode Telegram messages — escape them or names containing
# & / < / > make Telegram reject the whole reply.
_esc = html.escape


def peso(n):
    return "₱" + f"{round(n):,}"


def money(amount, currency):
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if currency == "USD":
        return f"{sign}$" + f"{amount:,.2f}"
    return f"{sign}₱" + f"{round(amount):,}"


def _convert(amount, from_ccy, to_ccy, rate):
    """Convert between USD and PHP using the given USD->PHP rate."""
    if from_ccy == to_ccy:
        return amount
    if from_ccy == "USD" and to_ccy == "PHP":
        return amount * rate
    if from_ccy == "PHP" and to_ccy == "USD":
        return amount / rate
    return amount


def validate(action, rate):
    """
    Check one parsed action. Returns (transaction_dict, None) if good,
    or (None, "question to ask the user") if something required is missing.
    """
    typ = (action.get("type") or "").lower()
    amount = action.get("amount")
    if typ not in ("income", "expense", "transfer", "adjust_balance"):
        return None, "Is that income, an expense, a transfer, or just telling me your current balance?"
    if amount is None or float(amount) < 0:
        return None, "How much was it?"
    amount = float(amount)
    if amount == 0 and typ != "adjust_balance":
        return None, "How much was it?"

    currency = (action.get("currency") or "").upper()
    if currency not in ("PHP", "USD"):
        currency = "PHP" if typ == "expense" else ""

    if typ == "adjust_balance":
        return _build_adjustment(amount, currency, action, rate)

    if typ == "transfer":
        src = sheets.get_account(action.get("account") or "")
        dst = sheets.get_account(action.get("destination_account") or "")
        if not src:
            return None, "Which account is the money coming FROM?"
        if not dst:
            return None, "Which account should it go TO?"
        if src["name"] == dst["name"]:
            return None, "The two accounts are the same — pick a different destination."
        if not currency:
            currency = src["currency"]
        return _build_transfer(amount, currency, src, dst, rate, action), None

    # income or expense
    acct = sheets.get_account(action.get("account") or "")
    if not acct:
        names = ", ".join(a["name"] for a in sheets.get_accounts())
        return None, f"Which account? ({_esc(names)})"
    if not currency:
        if typ == "income":
            return None, "Was that in pesos (₱) or dollars ($)?"
        currency = "PHP"

    php_equiv = _convert(amount, currency, "PHP", rate)

    # Always store the real USD<->PHP rate used at the time of this
    # transaction (not just "1 when currency is PHP"). Undo/reporting later
    # needs the real rate to convert correctly in either direction — storing
    # 1 whenever the tx happened to be in PHP silently threw that information
    # away, which mattered as soon as an account's currency didn't match the
    # transaction's currency (e.g. a USD expense logged against a PHP wallet).
    used_rate = rate

    # The amount actually applied to the account's own balance. A transaction
    # can be logged in a different currency than the account holding it (the
    # AI is allowed to pick e.g. "GCash" for a message that said "$20" if
    # that's the closest account match) — so this always converts into the
    # account's own currency before any balance math happens, exactly like
    # transfers already do below via _out_amount/_in_amount.
    acct_delta = _convert(amount, currency, acct["currency"], rate)

    tx = {
        "Transaction ID": _new_id(),
        "Created Timestamp": _now(),
        "Transaction Date": action.get("date") or config.today_iso(),
        "Type": typ,
        "Amount": round(amount, 2),
        "Currency": currency,
        "Exchange Rate": used_rate,
        "PHP Equivalent": round(php_equiv, 2),
        "Category": action.get("category") or ("Other" if typ == "expense" else "Other Income"),
        "Client": action.get("client") or "",
        "Income Source": action.get("income_source") or "",
        "Account": acct["name"],
        "Destination Account": "",
        "Description": action.get("description") or "",
        "Telegram User ID": "",
        "Status": "Active",
        "_account_currency": acct["currency"],   # internal, not written
        "_acct_delta": round(acct_delta, 2),      # internal, not written
    }
    return tx, None


def _build_transfer(amount, currency, src, dst, rate, action):
    # Deduct from source in its own currency; add to dest in its own currency.
    out_amount = _convert(amount, currency, src["currency"], rate)
    in_amount = _convert(amount, currency, dst["currency"], rate)
    php_equiv = _convert(amount, currency, "PHP", rate)
    used_rate = rate if (src["currency"] == "USD" or dst["currency"] == "USD") else 1
    return {
        "Transaction ID": _new_id(),
        "Created Timestamp": _now(),
        "Transaction Date": action.get("date") or config.today_iso(),
        "Type": "transfer",
        "Amount": round(amount, 2),
        "Currency": currency,
        "Exchange Rate": used_rate,
        "PHP Equivalent": round(php_equiv, 2),
        "Category": "",
        "Client": "",
        "Income Source": "",
        "Account": src["name"],
        "Destination Account": dst["name"],
        "Description": action.get("description") or "",
        "Telegram User ID": "",
        "Status": "Active",
        "_out_amount": round(out_amount, 2),
        "_in_amount": round(in_amount, 2),
        "_src_currency": src["currency"],
        "_dst_currency": dst["currency"],
    }


def _build_adjustment(amount, currency, action, rate):
    """
    Handle "I have ₱2032 in GCash" / "my Maya balance is 500" style statements.
    Unlike income/expense/transfer, `amount` here is the ACTUAL current
    balance the user just told us — not a delta to apply. We diff it against
    whatever the sheet currently has and log the difference as an adjustment
    so there's still an audit trail, but the account ends up exactly at the
    number the user stated.
    """
    acct = sheets.get_account(action.get("account") or "")
    if not acct:
        names = ", ".join(a["name"] for a in sheets.get_accounts())
        return None, f"Which account is that balance for? ({_esc(names)})"
    if not currency:
        currency = acct["currency"]

    stated_balance = _convert(amount, currency, acct["currency"], rate)
    delta = round(stated_balance - acct["balance"], 2)

    tx = {
        "Transaction ID": _new_id(),
        "Created Timestamp": _now(),
        "Transaction Date": action.get("date") or config.today_iso(),
        "Type": "adjustment",
        "Amount": delta,  # signed: how much this correction changes the balance by
        "Currency": acct["currency"],
        "Exchange Rate": rate,
        "PHP Equivalent": round(_convert(delta, acct["currency"], "PHP", rate), 2),
        "Category": "Balance Correction",
        "Client": "",
        "Income Source": "",
        "Account": acct["name"],
        "Destination Account": "",
        "Description": action.get("description") or "Balance correction from chat",
        "Telegram User ID": "",
        "Status": "Active",
        "_new_balance": round(stated_balance, 2),
        "_old_balance": round(acct["balance"], 2),
    }
    return tx, None


def confirm_text(tx):
    """The 'please confirm' summary shown before saving."""
    if tx["Type"] == "adjustment":
        old = money(tx["_old_balance"], tx["Currency"])
        new = money(tx["_new_balance"], tx["Currency"])
        return (
            "<b>Please confirm this balance correction</b>\n"
            f"Account: {_esc(tx['Account'])}\n"
            f"Current on record: {old}\n"
            f"You said: {new}\n"
            f"Date: {tx['Transaction Date']}"
        )
    if tx["Type"] == "transfer":
        return (
            "<b>Please confirm this transfer</b>\n"
            f"Amount: {money(tx['Amount'], tx['Currency'])}\n"
            f"From: {_esc(tx['Account'])}\n"
            f"To: {_esc(tx['Destination Account'])}\n"
            f"Date: {tx['Transaction Date']}"
        )
    label = "income" if tx["Type"] == "income" else "expense"
    lines = [f"<b>Please confirm this {label}</b>"]
    if tx["Currency"] == "USD":
        lines.append(f"Amount: {money(tx['Amount'],'USD')} → {peso(tx['PHP Equivalent'])} (₱{tx['Exchange Rate']}/USD)")
    else:
        lines.append(f"Amount: {peso(tx['Amount'])}")
    lines.append(f"Category: {_esc(tx['Category'])}")
    if tx["Income Source"]:
        lines.append(f"Source: {_esc(tx['Income Source'])}")
    if tx["Client"]:
        lines.append(f"Client: {_esc(tx['Client'])}")
    lines.append(f"Account: {_esc(tx['Account'])}")
    lines.append(f"Date: {tx['Transaction Date']}")
    return "\n".join(lines)


def apply(tx, user_id):
    """Write the transaction and update balances. Returns the 'recorded' message."""
    tx["Telegram User ID"] = str(user_id)

    if tx["Type"] == "adjustment":
        acct = sheets.get_account(tx["Account"])
        new_bal = tx["_new_balance"]
        sheets.update_account_balance(acct["name"], new_bal)
        clean = {k: v for k, v in tx.items() if not k.startswith("_")}
        sheets.append_transaction(clean)
        _audit_async("adjustment", tx["Transaction ID"], tx["Account"], user_id)
        return (
            "✅ <b>Balance updated</b>\n"
            f"{_esc(acct['name'])} is now {money(new_bal, acct['currency'])}"
        )

    if tx["Type"] == "transfer":
        src = sheets.get_account(tx["Account"])
        dst = sheets.get_account(tx["Destination Account"])
        new_src = src["balance"] - tx["_out_amount"]
        new_dst = dst["balance"] + tx["_in_amount"]
        sheets.update_account_balance(src["name"], new_src)
        sheets.update_account_balance(dst["name"], new_dst)
        clean = {k: v for k, v in tx.items() if not k.startswith("_")}
        sheets.append_transaction(clean)
        _audit_async("transfer", tx["Transaction ID"],
                     f"{tx['Account']} -> {tx['Destination Account']}", user_id)
        return (
            "✅ <b>Transfer recorded</b>\n"
            f"{money(tx['Amount'], tx['Currency'])}  {_esc(tx['Account'])} → {_esc(tx['Destination Account'])}\n"
            f"New {_esc(src['name'])} balance: {money(new_src, src['currency'])}\n"
            f"New {_esc(dst['name'])} balance: {money(new_dst, dst['currency'])}\n"
            "<i>Net worth unchanged</i>"
        )

    acct = sheets.get_account(tx["Account"])
    # Use the pre-converted, account-currency delta computed in validate()
    # rather than tx["Amount"] directly — tx["Amount"] is in the
    # transaction's own currency, which is not guaranteed to match the
    # account's currency.
    acct_delta = tx.get("_acct_delta", tx["Amount"])
    delta = acct_delta if tx["Type"] == "income" else -acct_delta
    new_bal = acct["balance"] + delta
    sheets.update_account_balance(acct["name"], new_bal)
    clean = {k: v for k, v in tx.items() if not k.startswith("_")}
    sheets.append_transaction(clean)
    _audit_async(tx["Type"], tx["Transaction ID"], tx["Account"], user_id)

    head = "✅ <b>Income recorded</b>" if tx["Type"] == "income" else "✅ <b>Expense recorded</b>"
    out = [head]
    if tx["Currency"] == "USD":
        out.append(f"Amount: {money(tx['Amount'],'USD')} → {peso(tx['PHP Equivalent'])}")
    else:
        out.append(f"Amount: {peso(tx['Amount'])}")
    out.append(f"Category: {_esc(tx['Category'])}  ·  Account: {_esc(acct['name'])}")
    out.append(f"New {_esc(acct['name'])} balance: {money(new_bal, acct['currency'])}")
    return "\n".join(out)


def undo_last(user_id):
    """Reverse the most recent active transaction."""
    rows = sheets.get_recent_transactions(30)
    for r in rows:
        if str(r.get("Status", "")).lower() == "active":
            return _reverse(r, user_id)
    return "Nothing to undo."


def _reverse(r, user_id):
    typ = str(r.get("Type", "")).lower()
    # _to_float, not float(): rows read back from the sheet can carry
    # formatted strings like "1,234.56" or "₱-4,000".
    amount = sheets._to_float(r.get("Amount", 0))
    currency = r.get("Currency", "PHP") or "PHP"
    stored_rate = sheets._to_float(r.get("Exchange Rate", 1)) or 1
    if typ == "adjustment":
        acct = sheets.get_account(r.get("Account"))
        # Amount is the signed delta the correction applied — undo by
        # subtracting that same delta back out.
        sheets.update_account_balance(acct["name"], acct["balance"] - amount)
        sheets.mark_reversed(r.get("Transaction ID"))
        _audit_async("undo", r.get("Transaction ID"), "reversed by user", user_id)
        return f"↩️ Undone: balance correction on {_esc(str(r.get('Account')))}."
    if typ == "transfer":
        src = sheets.get_account(r.get("Account"))
        dst = sheets.get_account(r.get("Destination Account"))
        out_amount = _convert(amount, currency, src["currency"], stored_rate)
        in_amount = _convert(amount, currency, dst["currency"], stored_rate)
        sheets.update_account_balance(src["name"], src["balance"] + out_amount)
        sheets.update_account_balance(dst["name"], dst["balance"] - in_amount)
    else:
        acct = sheets.get_account(r.get("Account"))
        # Same fix as apply(): convert into the account's own currency before
        # touching its balance, using the rate that was in effect at the time.
        acct_amount = _convert(amount, currency, acct["currency"], stored_rate)
        delta = -acct_amount if typ == "income" else acct_amount  # opposite of original
        sheets.update_account_balance(acct["name"], acct["balance"] + delta)
    sheets.mark_reversed(r.get("Transaction ID"))
    _audit_async("undo", r.get("Transaction ID"), "reversed by user", user_id)
    return f"↩️ Undone: {typ} {money(amount, currency)} on {_esc(str(r.get('Account')))}."


def _audit_async(action, transaction_id, detail, user_id):
    """
    Write the audit row off the critical path. It's a convenience log — the
    Transactions row (written synchronously above) is the source of truth —
    so the user shouldn't wait an extra round trip to Google for it.
    """
    def work():
        try:
            sheets.append_audit(action, transaction_id, detail, user_id)
        except Exception:
            logging.exception("audit write failed (non-critical)")
    threading.Thread(target=work, daemon=True).start()


def _new_id():
    return "TX-" + uuid.uuid4().hex[:8].upper()


def _now():
    # Manila wall-clock time, not the server's UTC (see config.now()).
    return config.now().strftime("%Y-%m-%d %H:%M:%S")
