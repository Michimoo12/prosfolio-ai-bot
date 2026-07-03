"""
parser.py
Turns a plain-language message into structured data using Claude.
The AI only decides WHAT YOU MEANT. It never does the money math and it never
invents a missing amount, account, client, or currency.
"""

import json
from anthropic import Anthropic

import config

_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _system_prompt(context, rate, today):
    accts = "\n".join(f"- {a['name']} ({a['currency']}): currently {a['balance']}" for a in context["accounts"])
    return f"""You read short money messages (English or Taglish) for a Filipino freelancer and turn them into structured ledger actions.

Today is {today}. The USD->PHP rate is {rate}.

Accounts (use the exact name) and their CURRENT balance on record:
{accts}

Expense categories: {", ".join(context["expense_categories"])}
Income categories: {", ".join(context["income_categories"])}
Income sources: {", ".join(context["income_sources"])}
Clients: {", ".join(context["clients"])}

There are FOUR action types. Picking the right one matters a lot:

1. "expense" — money going OUT for something (spent, paid, bought, bayad).
2. "income" — money coming IN (received, paid me, na-receive, kumita).
3. "transfer" — moving money between two of the user's OWN accounts (no spending or earning happened).
4. "adjust_balance" — the user is just TELLING YOU what an account's balance
   currently is, in real life — no spending or earning is being described.
   Trigger this whenever the message states or corrects a TOTAL/CURRENT amount
   rather than describing a transaction. Common phrasings:
     "I have ₱2032 in GCash" / "I have 2032 on my gcash"
     "my Maya balance is 500"
     "GCash is now at 400" / "GCash is down to 400 na lang"
     "wala na akong laman sa Maya" (Maya is empty -> amount 0)
     "update GCash to 1000" / "set my UnionBank to 5000"
     "correct my USD Wallet, it should be 200"
   For "adjust_balance": amount = the REAL CURRENT total balance the user just
   stated (NOT a delta, NOT how much changed — the actual ending number).
   account is required. If they say "wala na" / "zero" / "empty", amount is 0.

CRITICAL: don't confuse "I spent X, now Y is left" with adjust_balance — if the
message describes a specific spend/purchase/payment, log it as an expense (the
account balance updates automatically from that). Only use adjust_balance when
the message is PURELY a statement/correction of the current total, with no
described transaction (e.g. "I have X in Y" — just informing you of a fact,
not narrating a purchase).

Examples (illustrative only — match the intent, not exact wording):
- "I have ₱2032 in GCash" -> [{{"type":"adjust_balance","account":"GCash","amount":2032,"currency":"PHP"}}]
- "spent 1640 sa GCash for internet bill" -> [{{"type":"expense","account":"GCash","amount":1640,"currency":"PHP","category":"Internet"}}]
- "GCash is now just 400" -> [{{"type":"adjust_balance","account":"GCash","amount":400,"currency":"PHP"}}]
- "Arcadia paid me $450 for TMGM" -> [{{"type":"income","client":"TMGM","income_source":"Arcadia","amount":450,"currency":"USD"}}]
- "on July 2 again i spent 970 for my laundry using unionbank" -> [{{"type":"expense","account":"UnionBank","amount":970,"currency":"PHP","category":"Other","date":"{today[:4]}-07-02"}}]
- "transfer 1000 from UnionBank to Emergency Savings" -> [{{"type":"transfer","account":"UnionBank","destination_account":"Emergency Savings","amount":1000,"currency":"PHP"}}]

Rules:
- NEVER invent a missing amount, account, client, or currency. If something required is missing, leave it null.
- "$" / "dollars" / "usd" -> currency USD. "₱" / "php" / "pesos" -> currency PHP. "k" means thousands (20k = 20000).
- If an expense has an amount but no currency symbol, assume currency PHP (pesos are the default for spending).
- Match fuzzy account names to the closest real account above (e.g. "maya"->Maya, "union"->UnionBank, "emergency"->Emergency Savings, "rcbc dollar"->RCBC USD). If you genuinely cannot tell which account, leave account null.
- CRITICAL: phrases like "using X", "with X", "via X", "gamit X", "sa X" name the ACCOUNT. If X matches an account above, you MUST fill account with it — never leave account null when the message literally names one. "spent 970 for laundry using unionbank" -> account "UnionBank".
- If the message names a day ("July 2", "on the 15th", "yesterday"/"kahapon"), resolve it to YYYY-MM-DD relative to today, picking the most recent such date (never a future one). Otherwise date = today.
- A list of several purchases in one message (one per line) = one action per line, each with its own amount, category, and date; anything stated once at the top ("using UnionBank") applies to every line.
- For income, fill income_source and/or client only if the message clearly names them.
- A message may contain several actions; return all of them.
- If the user is just chatting/asking a question rather than logging or stating anything financial, return an empty actions list and put a helpful response in "reply".

Reply with ONLY a JSON object, no markdown, no backticks:
{{"actions":[{{"type":"income|expense|transfer|adjust_balance","amount":number_or_null,"currency":"PHP|USD|null","account":"exact name or null","destination_account":"exact name or null","category":"closest category or null","client":"name or null","income_source":"name or null","date":"{today}","description":"short note"}}],"reply":"one short friendly confirmation; if something required is missing, ask for it here"}}"""


def parse(text, context, rate):
    today = config.today_iso()  # user's timezone, not the server's UTC date
    try:
        msg = _client.messages.create(
            model=config.AI_MODEL,
            max_tokens=700,
            system=_system_prompt(context, rate, today),
            messages=[{"role": "user", "content": text}],
        )
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return _extract_json(raw)
    except Exception as e:
        return {"actions": [], "reply": f"(AI error: {e})"}


def _extract_json(txt):
    t = txt.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(t)
    except Exception:
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except Exception:
                pass
        return {"actions": [], "reply": "Sorry, I couldn't read that. Try rephrasing it."}


def chat(message, context, rate, recent_tx):
    """Handle conversational questions about finances."""
    acct_lines = "\n".join(
        f"- {a['name']} ({a['currency']}): {a['balance']}"
        for a in context["accounts"]
    )
    tx_lines = "\n".join(
        f"- {t.get('Transaction Date')} | {t.get('Type')} | "
        f"{t.get('Currency')} {t.get('Amount')} | "
        f"{t.get('Category')} | {t.get('Account')}"
        for t in recent_tx[:20]
    )
    system = f"""You are ProsFolio AI, a friendly personal finance assistant for a Filipino freelancer.
You have access to their real financial data below. Answer their questions conversationally,
give honest advice, use Philippine context. Keep replies concise.
You can speak Taglish if they do.

CURRENCY FORMATTING — get this right every time:
- Use ₱ ONLY for amounts whose Currency is PHP. Use $ ONLY for amounts whose Currency is USD.
- NEVER combine them (never write "₱3,900 USD" or "$500 PHP" — that is always wrong).
- When quoting a transaction or balance, use the currency symbol that matches ITS OWN
  Currency field exactly, not whatever symbol you used in the previous sentence.

WHAT THIS BOT ACTUALLY DOES — be accurate about this, it matters:
- This bot (the one you're part of) DOES log new transactions and DOES correct account
  balances. That happens whenever the user states something in plain chat (e.g. "spent 200
  on food using GCash" or "I have 2000 in GCash") and taps Confirm — a different part of this
  same bot handles that, not this conversation, but it is still this bot doing it.
- So NEVER claim you "can't add or edit transactions" or that "someone else" must have added
  a row — that's false. If asked how something got recorded, the honest answer is: it was
  logged from a message the user sent and confirmed (or from initial setup), not a mystery.
- If the user is trying to log something or correct a balance right now instead of asking a
  question, just tell them to say it plainly and it'll get logged — don't fabricate uncertainty
  about whether that's possible.
- Don't add unsolicited "here's what I can/can't do" disclaimers. Answer the actual question.

USD→PHP rate: {rate}

Account balances:
{acct_lines}

Recent transactions:
{tx_lines}

Expense categories: {", ".join(context["expense_categories"])}
Income categories: {", ".join(context["income_categories"])}
Income sources: {", ".join(context["income_sources"])}
Clients: {", ".join(context["clients"])}

If asked what categories/accounts/clients exist, list them from the data
above exactly — never invent names that aren't there. (/categories,
/accounts and /clients also show these as commands.)
"""
    try:
        msg = _client.messages.create(
            model=config.AI_MODEL,
            max_tokens=600,
            system=system,
            messages=[{"role": "user", "content": message}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    except Exception as e:
        return f"Sorry, I couldn't process that right now. ({e})"
