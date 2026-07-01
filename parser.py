"""
parser.py
Turns a plain-language message into structured data using Claude.
The AI only decides WHAT YOU MEANT. It never does the money math and it never
invents a missing amount, account, client, or currency.
"""

import json
import datetime
from anthropic import Anthropic

import config

_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _system_prompt(context, rate, today):
    accts = "\n".join(f"- {a['name']} ({a['currency']})" for a in context["accounts"])
    return f"""You read short money messages (English or Taglish) for a Filipino freelancer and turn them into structured ledger actions.

Today is {today}. The USD->PHP rate is {rate}.

Accounts (use the exact name):
{accts}

Expense categories: {", ".join(context["expense_categories"])}
Income categories: {", ".join(context["income_categories"])}
Income sources: {", ".join(context["income_sources"])}
Clients: {", ".join(context["clients"])}

Rules:
- type is one of: income, expense, transfer.
- NEVER invent a missing amount, account, client, or currency. If something required is missing, leave it null.
- "$" / "dollars" / "usd" -> currency USD. "₱" / "php" / "pesos" -> currency PHP. "k" means thousands (20k = 20000).
- If an expense has an amount but no currency symbol, assume currency PHP (pesos are the default for spending).
- Match fuzzy account names to the closest real account above (e.g. "maya"->Maya, "union"->UnionBank, "emergency"->Emergency Savings, "rcbc dollar"->RCBC USD). If you genuinely cannot tell which account, leave account null.
- For income, fill income_source and/or client only if the message clearly names them.
- A message may contain several actions; return all of them.

Reply with ONLY a JSON object, no markdown, no backticks:
{{"actions":[{{"type":"income|expense|transfer","amount":number_or_null,"currency":"PHP|USD|null","account":"exact name or null","destination_account":"exact name or null","category":"closest category or null","client":"name or null","income_source":"name or null","date":"{today}","description":"short note"}}],"reply":"one short friendly confirmation; if something required is missing, ask for it here"}}"""


def parse(text, context, rate):
    today = datetime.date.today().isoformat()
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
