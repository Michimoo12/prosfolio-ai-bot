"""
logic.py
The trustworthy part. Python (not the AI) validates each action, does all the
math, updates balances, and writes to the sheet.
"""

import uuid
import datetime

import sheets


def peso(n):
    return "₱" + f"{round(n):,}"


def money(amount, currency):
    if currency == "USD":
        return "$" + f"{amount:,.2f}"
    return "₱" + f"{round(amount):,}"


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
    if typ not in ("income", "expense", "transfer"):
        return None, "Is that income, an expense, or a transfer?"
    if amount is None or float(amount) <= 0:
        return None, "How much was it?"
    amount = float(amount)

    currency = (action.get("currency") or "").upper()
    if currency not in ("PHP", "USD"):
        currency = "PHP" if typ == "expense" else ""

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
        return None, f"Which account? ({names})"
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
        "Transaction Date": action.get("date") or datetime.date.today().isoformat(),
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
        "Transaction Date": action.get("date") or datetime.date.today().isoformat(),
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


def confirm_text(tx):
    """The 'please confirm' summary shown before saving."""
    if tx["Type"] == "transfer":
        return (
            "<b>Please confirm this transfer</b>\n"
            f"Amount: {money(tx['Amount'], tx['Currency'])}\n"
            f"From: {tx['Account']}\n"
            f"To: {tx['Destination Account']}\n"
            f"Date: {tx['Transaction Date']}"
        )
    label = "income" if tx["Type"] == "income" else "expense"
    lines = [f"<b>Please confirm this {label}</b>"]
    if tx["Currency"] == "USD":
        lines.append(f"Amount: {money(tx['Amount'],'USD')} → {peso(tx['PHP Equivalent'])} (₱{tx['Exchange Rate']}/USD)")
    else:
        lines.append(f"Amount: {peso(tx['Amount'])}")
    lines.append(f"Category: {tx['Category']}")
    if tx["Income Source"]:
        lines.append(f"Source: {tx['Income Source']}")
    if tx["Client"]:
        lines.append(f"Client: {tx['Client']}")
    lines.append(f"Account: {tx['Account']}")
    lines.append(f"Date: {tx['Transaction Date']}")
    return "\n".join(lines)


def apply(tx, user_id):
    """Write the transaction and update balances. Returns the 'recorded' message."""
    tx["Telegram User ID"] = str(user_id)

    if tx["Type"] == "transfer":
        src = sheets.get_account(tx["Account"])
        dst = sheets.get_account(tx["Destination Account"])
        new_src = src["balance"] - tx["_out_amount"]
        new_dst = dst["balance"] + tx["_in_amount"]
        sheets.update_account_balance(src["name"], new_src)
        sheets.update_account_balance(dst["name"], new_dst)
        clean = {k: v for k, v in tx.items() if not k.startswith("_")}
        sheets.append_transaction(clean)
        sheets.append_audit("transfer", tx["Transaction ID"],
                            f"{tx['Account']} -> {tx['Destination Account']}", user_id)
        return (
            "✅ <b>Transfer recorded</b>\n"
            f"{money(tx['Amount'], tx['Currency'])}  {tx['Account']} → {tx['Destination Account']}\n"
            f"New {src['name']} balance: {money(new_src, src['currency'])}\n"
            f"New {dst['name']} balance: {money(new_dst, dst['currency'])}\n"
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
    sheets.append_audit(tx["Type"], tx["Transaction ID"], tx["Account"], user_id)

    head = "✅ <b>Income recorded</b>" if tx["Type"] == "income" else "✅ <b>Expense recorded</b>"
    out = [head]
    if tx["Currency"] == "USD":
        out.append(f"Amount: {money(tx['Amount'],'USD')} → {peso(tx['PHP Equivalent'])}")
    else:
        out.append(f"Amount: {peso(tx['Amount'])}")
    out.append(f"Category: {tx['Category']}  ·  Account: {acct['name']}")
    out.append(f"New {acct['name']} balance: {money(new_bal, acct['currency'])}")
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
    amount = float(r.get("Amount", 0) or 0)
    currency = r.get("Currency", "PHP") or "PHP"
    stored_rate = float(r.get("Exchange Rate", 1) or 1)
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
    sheets.append_audit("undo", r.get("Transaction ID"), "reversed by user", user_id)
    return f"↩️ Undone: {typ} {money(amount, currency)} on {r.get('Account')}."


def _new_id():
    return "TX-" + uuid.uuid4().hex[:8].upper()


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
