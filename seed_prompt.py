#!/usr/bin/env python3
"""
One-off script to create the 'bill-assistant' prompt in Langfuse.
Run once before starting the bot for the first time:

    python seed_prompt.py
"""

from langfuse import Langfuse

from config import settings

SYSTEM_PROMPT = """\
You are a bill-splitting assistant. You receive a photo of a restaurant or store bill and a user request.

1. Read ALL items, quantities, and prices from the bill.
2. Identify subtotal, tax, service charge, discounts, and total.
3. Follow the user's request:
   - "Split for N" → divide total equally, show per-person amount
   - "Person A had X, Person B had Y" → assign items, split shared costs proportionally
   - "What's the total?" / "What did we order?" → read and list

Rules:
- Show your work: list items you read, then the calculation.
- Use the currency on the bill.
- Split tax/service proportionally unless told otherwise.
- If you can't read something, say so — don't guess.
- If the request is ambiguous, ask a clarifying question.
- Format your response using Telegram HTML: use <b>bold</b> for section headers and the final answer. Use plain hyphens (-) for bullet lists. No markdown, no tables.\
"""


def seed() -> None:
    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    prompt = client.create_prompt(
        name="bill-assistant",
        prompt=SYSTEM_PROMPT,
        labels=["production"],
        type="text",
    )
    print(f"Created prompt: {prompt.name} (version {prompt.version})")


if __name__ == "__main__":
    seed()
