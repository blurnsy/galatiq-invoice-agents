"""Approval agent: a simulated VP decides, a critic checks the reasoning, and the
VP gets one chance to revise. The validation rules always have the final say,
so the model can make a decision more cautious but never less.
"""

import json
import logging

from . import db as store

log = logging.getLogger(__name__)

LARGE_INVOICE = 10_000
ORDER = ["approved", "needs_review", "rejected"]  # least to most cautious

POLICY = """Acme accounts payable policy:
- Reject any invoice with a "reject" issue.
- Send any invoice with a "review" issue to a person.
- Invoices over $10,000 get extra scrutiny: check the stock yourself with the tool.
- Otherwise approve."""

VP_PROMPT = "You are the VP of Finance approving vendor invoices.\n" + POLICY + \
    "\nGive a decision and two or three sentences of reasoning that mention every issue."

CRITIC_PROMPT = "You review a VP's invoice decision against policy.\n" + POLICY + \
    "\nAgree only if the decision follows the policy and the reasoning addresses every issue."

VP_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["decision", "reasoning"],
    "properties": {"decision": {"type": "string", "enum": ["approve", "review", "reject"]},
                   "reasoning": {"type": "string"}},
}
CRITIC_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["agree", "feedback"],
    "properties": {"agree": {"type": "boolean"}, "feedback": {"type": "string"}},
}


def approve(invoice, issues, db, llm=None):
    rejects = [msg for level, msg in issues if level == "reject"]
    reviews = [msg for level, msg in issues if level == "review"]
    by_rules = "rejected" if rejects else "needs_review" if reviews else "approved"

    if llm is None:
        return {"decision": by_rules, "reasoning": explain(invoice, by_rules, rejects, reviews), "critique": []}

    try:
        vp, critique, critic_agreed = vp_with_critic(invoice, issues, db, llm)
    except Exception as exc:  # API down, bad response: the rules still work
        log.warning("Grok approval failed (%s); using the rules", exc)
        return {"decision": by_rules, "reasoning": explain(invoice, by_rules, rejects, reviews), "critique": []}

    # A model "reject" on an invoice the rules found nothing wrong with goes to a person.
    by_model = {"approve": "approved", "review": "needs_review", "reject": "needs_review"}[vp["decision"]]
    decision = by_rules if by_rules == "rejected" else max(by_rules, by_model, key=ORDER.index)
    if decision == "approved" and invoice["total"] > LARGE_INVOICE and not critic_agreed:
        decision = "needs_review"
    return {"decision": decision, "reasoning": vp["reasoning"], "critique": critique}


def vp_with_critic(invoice, issues, db, llm, rounds=2):
    def check_inventory(item):
        """Current stock and our unit price for an inventory item."""
        return store.find_item(db, item) or {"error": f"{item} is not in inventory"}

    tool = (check_inventory, {"type": "object", "additionalProperties": False, "required": ["item"],
                              "properties": {"item": {"type": "string"}}})
    facts = json.dumps({"invoice": invoice, "issues": [{"level": lv, "issue": m} for lv, m in issues]},
                       indent=2, default=str)
    messages = [{"role": "system", "content": VP_PROMPT}, {"role": "user", "content": facts}]
    critique = []

    for _ in range(rounds):
        vp = llm.ask(messages, VP_SCHEMA, "vp_decision", tools=[tool])
        review = llm.ask([{"role": "system", "content": CRITIC_PROMPT},
                          {"role": "user", "content": facts + "\n\nVP decision:\n" + json.dumps(vp)}],
                         CRITIC_SCHEMA, "critique")
        critique.append(review["feedback"])
        if review["agree"]:
            return vp, critique, True
        messages += [{"role": "assistant", "content": json.dumps(vp)},
                     {"role": "user", "content": "A reviewer pushed back: " + review["feedback"]
                      + "\nReconsider and give your final decision."}]
    return vp, critique, False


def explain(invoice, decision, rejects, reviews):
    """Reasoning for offline mode, built from the issues."""
    if decision == "rejected":
        text = "Rejected: " + " ".join(rejects)
        return text + (" Also noted: " + " ".join(reviews) if reviews else "")
    if decision == "needs_review":
        return "Needs a person: " + " ".join(reviews)
    text = "Approved: items are in stock, totals add up and the vendor is approved."
    if invoice["total"] > LARGE_INVOICE:
        text += " Over $10,000, so it needed a completely clean check, which it had."
    return text
