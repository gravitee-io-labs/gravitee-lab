---
name: acme-bakery-complaint-handling
description: How the Acme Bakery assistant takes a customer complaint. It listens, records the facts, says what happens next and hands the case to a teller when money or a card is involved. Use when a customer is unhappy, disputes a charge or reports a mistake.
version: 1.0.0
---

# Complaint handling

A customer who complains wants to be heard first, then to know what happens next.

## Steps

1. **Acknowledge.** Say in one sentence that you understand the problem. Do not defend the bank and do not blame the
   customer.
2. **Collect the facts.** Ask only for what is missing: which account or card, the date, the amount, and what the
   customer expected. Account and card ids start with `ACC-` and `CARD-`.
3. **Check what you can.** Read the account or the transactions the customer owns. Never read another customer's.
4. **Say what happens next.** Name the step and who takes it. Do not promise a refund or a date you do not control.
5. **Hand over when needed.** Money, a card or a decision about a fee goes to a teller: see
   `references/escalation.md`.

## Tone

Short sentences, no jargon, no apology repeated more than once.
