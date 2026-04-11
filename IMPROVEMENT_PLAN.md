# Conversation Quality Improvement Plan

This plan addresses 14 client-reported issues with the WhatsApp "Eet als een Atleet" RAG buddy. The bot is **already running on Render and serving real users**, so all changes are scoped to:

- `app/services/rag.py` (prompts + control flow)
- `app/core/config.py` (only adding optional new settings with safe defaults)

**No changes** to:

- DB schema (`app/db/models.py`)
- WhatsApp webhook plumbing (`app/api/whatsapp.py`)
- Payment / credit logic (`app/services/payment_logic.py`)
- Vector store / ingestion (`scripts/ingest_book.py`)
- Function signatures of `get_response(...)`, `init_rag_components()` (drop-in compatible)

---

## 1. Issue → Root Cause → Fix matrix

| # | Client issue | Root cause in code | Fix |
|---|---|---|---|
| 1 | "What is the disadvantage of saturated fat?" → refused | `SIMILARITY_THRESHOLD=1.0` filter is strict; rewrite_chain reformulation may miss the topic; refusal triggers as soon as no docs ≤ threshold | Always fall back to top-K when no doc clears threshold (already partially done — extend so we **never refuse on in-domain nutrition topics** when we have *any* retrieved chunks). Also broaden the rewrite query for known topics like fats/proteins/carbs. |
| 2 | "How does it work again with (un)healthy oils?" → got intro + refusal | The first-message intro is prepended *and* the answer is a refusal, producing a confusing collage. Plus the bilingual refusal is concatenated. | (a) When the bot refuses, do **not** also prepend the welcome intro on the first message — replace it with a single coherent message. (b) Pick **one** language for the refusal, never both, by falling back to `DEFAULT_LANGUAGE` (Dutch) when language is ambiguous instead of bilingual stack. |
| 3 | Half English / half Dutch answers | `answer_chain` system prompt says "answer in the same language as user" but doesn't *enforce* it; LangChain context excerpts are in Dutch (book is Dutch) which biases the model to mix. | Detect user language **once** in `get_response`, pass it explicitly into the answer prompt as `{language}`, and instruct the model: *"Write the entire answer in {language}. Do not include any text in any other language, even if the source excerpts are in another language."* |
| 4 | Refusal + answer in same reply | `_strip_refusal_from_answer` only handles a fixed set of tails | Expand strip patterns and add a final pass: if answer contains both substantive content and a refusal sentence anywhere, drop the refusal sentence(s). |
| 5 | Same answer twice for one question | Most likely cause is the **dead** multi-question split path (`questions = [user_input]` always returns 1, so the multi-question branch is unreachable today) — but the safety net is to also dedupe identical paragraphs in the answer post-processor. There's no double-send in `whatsapp.py` itself (only one `send_whatsapp_message` per question + an *optional* trial warning). | Add a deduplication pass that removes consecutive identical sentences/paragraphs from the final answer before sending. |
| 6 | Way too much information (magnesium, calcium, 500ml soy, etc. when user just asked about protein) | `answer_chain` prompt says "summarize the actual content" and "give the substance from the excerpts so the user gets a complete answer" — encourages dumping full excerpt. Also doesn't tell the model to scale portions to person/context. | Rewrite the prompt to: (a) answer **only** the specific question asked, (b) give **realistic, single-person portions**, (c) only mention nutrients the user asked about, (d) keep answers under ~120 words unless the user explicitly asks for a meal plan. |
| 7 | Follow-up to earlier answer fails | Chat history *is* loaded (last 5 logs), but the rewrite_chain prompt doesn't tell the model "if the question is a follow-up, expand it using prior context". So "and what about for older athletes?" gets rewritten to literally that, retrieves nothing. | Improve rewrite_chain prompt to **resolve pronouns and continuations** using chat history before querying the vector store. |
| 8 | "Recipe inspiration" advertised but bot refuses, and contradictions | Two issues: (a) the rewrite query for "recipes" may not align with how the book labels them; (b) when chat history says "avoid protein" in one turn and "take protein" in another, the model has no rule to reconcile. | (a) Add specific rewrite hints for "recipe / recept / inspiratie / inspiration / dagmenu" → "recipes daily menu meal plan". (b) In the answer prompt, instruct: *"If your previous answers in the chat history contradict your current answer, briefly acknowledge and clarify the difference (e.g., 'before training vs after training')."* |
| 9 | "Protein for an 80-year-old" → refused as "only for athletes" | Refusal is too eager to block anything that doesn't sound like a young athlete; but the book is about nutrition broadly. | Drop the implicit "athletes only" stance from the system prompt. The book covers general sports nutrition — older recreational athletes are in scope. Make the refusal *only* fire for clearly off-topic queries (politics, medical diagnosis, code, etc.), not for any nutrition question with retrieved content. |
| 10 | User wanted reference links | Already supported (`_user_asks_for_reference`, `_format_references_line`). Issue may be that users don't know to ask. | At the end of substantive answers, optionally append a one-liner: *"💡 Tip: ask 'op welke pagina?' for the page reference."* — gated behind a new `INVITE_REFERENCE_TIP` setting (default off so it doesn't spam existing users). |
| 11 | More concrete advice with examples | Same as #6 but in the opposite direction — for **meal/portion** questions, the user *wants* specific examples but properly sized for one person. | The new prompt (see #6) explicitly requires: when user asks "how much / what to eat / example / sample", give **one** concrete example with realistic single-person portions. |
| 12 | Buddy should ask a clarifying question first | Currently the bot answers in one shot. Add a lightweight rule: if the question is highly context-dependent (e.g., "what should I eat?" with no info on training type, time of day, body weight, goal), ask **one** focused clarifying question before answering — but only if no clarifying info exists in chat history. | Add a "clarify-first" rule into the answer prompt with strict guardrails (max 1 question, only when truly ambiguous, never on greetings/thanks/clear questions). |
| 13 | One-way Q&A, no continuation | Same root cause as #12. Combined fix: at the end of an answer, the bot may **optionally** offer a relevant follow-up suggestion ("Wil je dat ik dit toepas op jouw trainingsdag?") — gated so it doesn't fire on every reply. | Allow the answer prompt to add **at most one** short follow-up suggestion at the end, only when the topic naturally invites more depth. |
| 14 | Personal data (weight, height, HR) for tailored advice | No DB schema change is allowed. But the bot already loads the last 5 chat logs as context. We can simply instruct the bot: *"If the user shares personal info (age, weight, height, sport, training frequency), remember it from chat history and apply it to subsequent answers."* The existing 5-message history window already enables this for short sessions. | Update answer prompt + bump `MAX_CHAT_LOG_MESSAGES` default from 5 → 10 so the personal info window is longer. (Setting is already in config; raising the default is safe.) |

---

## 2. Concrete code changes

### 2.1 `app/services/rag.py`

**A. New helper: `_detect_language(user_message, chat_history)`**

- Returns `"nl"` or `"en"`.
- Checks current message first; if ambiguous, walks recent chat history; falls back to `settings.DEFAULT_LANGUAGE`.
- Replaces ad-hoc `_use_dutch_page_word` / `_has_english_cues` decisions in the response path (the existing helpers stay for backward compat with `whatsapp.py` which imports `_use_dutch_page_word`).

**B. `_refusal_for_language(user_input)`**

- Stop returning bilingual refusal. Use the detected language; default Dutch.
- This kills issue #2's stacked refusal.

**C. `_strip_refusal_from_answer(answer)`**

- Add a regex sweep that removes any standalone refusal sentence even if surrounded by content. Idempotent.
- Also dedupes identical consecutive paragraphs (issue #5).

**D. `answer_chain` system prompt — full rewrite**

New rules (in order):

1. You are a friendly, conversational Dutch sports-nutrition buddy based on the book *"Eet als een Atleet"*. Speak like a knowledgeable friend, not a textbook.
2. **Language lock**: Write the entire reply in `{language}`. Never mix languages, even if excerpts are in another language. Translate where needed.
3. **Answer only what was asked.** Do not volunteer extra nutrients, vitamins, minerals, or unrelated nutrition trivia.
4. **Be concise.** Default length: 60–120 words. Only longer when the user explicitly asks for a meal plan, sample day, or detailed example.
5. **Personalize from history.** If the user previously shared age, weight, sport, or goal, factor that in.
6. **Realistic portions.** When you give amounts, give them for **one** person and one meal/snack — not the bot's idea of a full daily intake stacked into one moment.
7. **Clarify when necessary.** If the question is too vague to answer well *and* no clarifying info exists in chat history, ask **one** short clarifying question and stop. Never ask more than one.
8. **Continuation.** When the topic naturally invites more depth, you may end with **one** short suggestion of a follow-up (e.g., "Wil je dat ik hier een voorbeelddag bij maak?"). Optional, not on every reply.
9. **Consistency with prior turns.** If your earlier answers in this chat differ from what you'd say now (e.g., "before vs after training"), call out the distinction in one sentence so the user isn't confused.
10. **Refuse only when truly off-topic.** "Off-topic" means: politics, code, medical diagnosis, things unrelated to food/nutrition/sport. Anything about food, eating, sport, recovery, recipes, age groups, body composition, hydration → answer it.
11. **Refusal format.** When you must refuse, output exactly the single-line refusal in `{language}` and nothing else.
12. **References.** Only add page numbers when the user asked for them (existing rule kept).
13. **Disclaimer.** Keep the existing "general info, consult a (sports) dietitian" one-liner when giving specific intake amounts.

**E. `rewrite_chain` system prompt — small additions**

- Add: "If the user message is a follow-up (uses pronouns like 'it', 'that', 'this', 'die', 'het', or asks 'and X?', 'wat dan met X?'), resolve it using the most recent topic in chat history before producing the search query."
- Add specific topic mappings: `verzadigde vetten/saturated fat`, `onverzadigde vetten/unsaturated/healthy oils`, `recepten/dagmenu/recipes/daily menu`, `eiwit/protein for older adults/recovery`.

**F. `_split_into_questions` — keep returning a single question.** (Already neutralized; the dead multi-question branch is left in place to minimize diff risk, but is unreachable.)

**G. `get_response` flow update**

- Detect language **once**, pass to `answer_chain.invoke({..., "language": "Dutch"|"English"})`.
- On refusal path, do **not** call `_prepend_welcome_if_first` (avoids issue #2 collage).
- Add post-processing step: dedupe paragraphs, then strip refusals, then localize citations.

### 2.2 `app/core/config.py`

Add (all with safe defaults so nothing currently in production changes behavior unless flipped):

```python
MAX_CHAT_LOG_MESSAGES: int = 10        # was 5 — longer personal-info memory
ANSWER_MAX_WORDS: int = 120            # soft target enforced via prompt
ENABLE_FOLLOWUP_SUGGESTIONS: bool = True
ENABLE_CLARIFY_QUESTION: bool = True
INVITE_REFERENCE_TIP: bool = False     # off by default
```

These are read from env, so they're overridable on Render without a code push.

---

## 3. Why this won't break production

- **No DB migrations** — `MAX_CHAT_LOG_MESSAGES` already exists and is just used to LIMIT a query. Increasing it from 5 to 10 only changes how many rows are loaded.
- **No new function signatures** — all helpers added are private (`_detect_language`, dedupe pass).
- **Backward-compat helpers** — `_use_dutch_page_word` is kept (it's imported by `app/api/whatsapp.py`).
- **Prompt changes are runtime-only** — new RAG components are re-built on first request via `init_rag_components()`. Hot-reload friendly.
- **New settings have safe defaults** that match current behavior; new behaviors are *enabled* via prompt rules, not via flags, so no env change is required for the fix to land.

---

## 4. Validation checklist (manual, post-deploy)

Test these prompts on the live bot after deploying:

1. NL: "Wat is het nadeel van verzadigd vet?" → expect substantive Dutch answer, no refusal.
2. NL: "Hoe zit het ook alweer met (on)gezonde oliën?" → substantive Dutch answer, no English text, no welcome+refusal sandwich.
3. EN: "How much protein does an 80-year-old recreational runner need?" → substantive English answer that does **not** say "this is for athletes only".
4. NL: "Geef recept inspiratie voor herstel na hardlopen." → real recipe-style answer, page refs only if asked.
5. NL: "Hoeveel eiwit heb ik nodig en kun je dit vertalen naar een maaltijd?" → one realistic single-person example, no random magnesium/calcium dump.
6. NL follow-up: "En voor een dag zonder training?" (immediately after #5) → resolves "voor een dag zonder training" against the previous protein topic.
7. NL: "Wat moet ik eten?" with no context → bot asks **one** clarifying question (sport? time of day? weight?).
8. EN: "Thanks!" → friendly acknowledgement, no refusal.
9. NL: "Wie heeft de verkiezingen gewonnen in 2024?" → polite refusal **in Dutch only**, one line.
10. Sanity: send "hi" first then a real question → opening message, then a clean answer (not collage).

---

## 5. Out of scope (not included in this PR-equivalent change)

- Adding a `user_profile` table to durably store weight/height/HR → would require DB migration. Can be added later if the client wants persistent personal data.
- Linking external recipe URLs → the book is the only source of truth right now and the vector store only contains book chunks. Would need a separate retriever.
- Multi-turn voice / image understanding.
- Switching from `gpt-4o-mini` to a stronger model (cost trade-off; client decision).
