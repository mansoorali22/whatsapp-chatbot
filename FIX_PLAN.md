# Fix Plan: Inconsistent Refusal for Identical Questions Across Users

## Problem

When different users ask the exact same question — "What is the disadvantage of saturated fat?" — some get a correct answer while others receive:

> "Unfortunately, I can't help you with this question. However, I'm happy to help you with questions about sports nutrition!"

The RAG retrieval works fine for all users (logs show "Found 8 relevant chunks" every time). The divergence happens during answer generation.

## Root Cause

The issue is in `app/services/rag.py`, lines 494–506 and 643–648. Chat history is loaded per user and injected into the `answer_chain` prompt. When a user's history contains prior refusals (stored as `bot_response` in `chat_logs`), the LLM sees its own past refusal patterns and is biased toward refusing again — even when it has 8 perfectly relevant book excerpts in front of it.

Concretely:

1. **Lines 503–506** build `chat_history` by appending every past `bot_response` as an `AIMessage`, including refusal messages.
2. **Line 643–648** pass that full `chat_history` into `answer_chain.invoke(...)`.
3. The LLM sees prior AI messages like *"Unfortunately, I can't help you with this question…"* and pattern-matches on them, producing another refusal even though the current context excerpts are highly relevant.

User A (923205038894) has a clean history (no prior refusals), so the model answers normally. User B's history likely contains one or more prior refusals, which poisons the model's behavior.

The existing `_strip_refusal_from_answer()` function (line 59) only cleans refusal text from the **output** after generation. It does nothing to prevent the history from biasing the model during generation.

## Fix: Filter Refusal Responses Out of Chat History

### Change 1 — Filter refusals when building `chat_history` (rag.py, lines 503–506)

**Current code:**
```python
chat_history = []
for log in reversed(past_logs):
    chat_history.append(HumanMessage(content=log.user_message))
    chat_history.append(AIMessage(content=log.bot_response))
```

**Fixed code:**
```python
chat_history = []
for log in reversed(past_logs):
    # Skip refused exchanges — they bias the LLM toward refusing future questions
    if log.response_type == "refused":
        continue
    chat_history.append(HumanMessage(content=log.user_message))
    chat_history.append(AIMessage(content=log.bot_response))
```

This is the primary fix. It removes the poisoned context before it ever reaches the LLM.

### Change 2 — Defense-in-depth: also filter on the response text (same location)

Not all old chat logs may have `response_type` set correctly (e.g., logs from before the field was added, or edge cases where `_is_refusal_response` misclassified). Add a text-based fallback:

```python
_REFUSAL_MARKERS = [
    "Unfortunately, I can't help you with this question",
    "Helaas kan ik je bij deze vraag niet helpen",
    "I don't know. This is outside the book",
    "Ik weet het niet. Dit is buiten de context",
]

chat_history = []
for log in reversed(past_logs):
    if log.response_type == "refused":
        continue
    if any(marker in (log.bot_response or "") for marker in _REFUSAL_MARKERS):
        continue
    chat_history.append(HumanMessage(content=log.user_message))
    chat_history.append(AIMessage(content=log.bot_response))
```

### Change 3 — Reinforce the system prompt against history bias (rag.py, answer_system_prompt)

Add a rule to the system prompt (inside `init_rag_components()`, after rule 13) that explicitly tells the model to ignore refusal patterns in history:

```
14. **HISTORY INDEPENDENCE.** Each question is independent. Even if previous answers in the chat
    history refused a topic, you MUST still answer the current question based on the current
    excerpts. Never refuse simply because a prior turn refused.
```

## Verification

After applying the fix:

1. Clear or ignore existing chat logs for a test user who previously received refusals.
2. Send "What is the disadvantage of saturated fat?" from that user.
3. Confirm the bot returns a substantive answer (not a refusal), since the RAG retrieval already finds 8 relevant chunks for this query.
4. Send several off-topic questions to generate refusals, then re-ask the saturated fat question — it should still answer correctly.

## Files to Change

| File | What changes |
|---|---|
| `app/services/rag.py` | Filter refusals from `chat_history` loop (lines 503–506); add `_REFUSAL_MARKERS` list; add rule 14 to `answer_system_prompt` |

No other files need changes. The retrieval, config, and webhook layers are all working correctly.
