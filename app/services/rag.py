import re
from sqlalchemy.orm import Session
from sqlalchemy import desc
from sqlalchemy.exc import OperationalError

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_postgres import PGVector
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser

from app.core.config import settings
from app.db.models import ChatLog

# Single opening message (first message / greeting) – no duplicate text
OPENING_MESSAGE_NL = (
    "Hoi! 👋 Ik ben de Eet als een Atleet-assistent. Ik beantwoord graag al je vragen over sportvoeding, herstel, gezonde voeding en recept inspiratie. "
    "Verwacht praktische tips, evidence-based advies en ideeën die je meteen kunt toepassen in je keuken en sport voorbereiding! "
    "Nieuwsgierig of ik jouw perfecte buddy ben? Je kunt me gratis 15 vragen stellen!\n\n"
    "De antwoorden worden automatisch gegenereerd en zijn enkel en alleen gebaseerd op de inhoud van het boek. "
    "Wees er bewust van dat AI fouten kan maken en weet dat wij nooit medische adviezen zullen geven."
)
OPENING_MESSAGE_EN = (
    "Hi! 👋 I'm the Eat like an Athlete assistant. I'm happy to answer your questions about sports nutrition, recovery, healthy eating and recipe inspiration. "
    "Expect practical tips, evidence-based advice and ideas you can use straight away in your kitchen and training. "
    "Curious if I'm your perfect buddy? You can ask me 15 questions for free!\n\n"
    "Answers are generated automatically and are based solely on the book content. "
    "Please be aware that AI can make mistakes and we will never give medical advice."
)

# Welcome intro for first *question* (when first message is not a greeting)
WELCOME_INTRO_NL = (
    "Ik beantwoord graag al je vragen over sportvoeding, herstel, gezonde voeding en recept inspiratie. "
    "Verwacht praktische tips, evidence-based advies en ideeën die je meteen kunt toepassen in je keuken en sport voorbereiding!"
)
WELCOME_INTRO_EN = (
    "I'm happy to answer your questions about sports nutrition, recovery, healthy eating and recipe inspiration. "
    "Expect practical tips, evidence-based advice and ideas you can use straight away in your kitchen and training."
)

# Out-of-context reply (language-aware: NL, EN, or both only when user mixes languages)
REFUSAL_MESSAGE_NL = "Helaas kan ik je bij deze vraag niet helpen. Wel help ik je graag verder met vragen over sportvoeding!"
REFUSAL_MESSAGE_EN = "Unfortunately, I can't help you with this question. However, I'm happy to help with questions about sports nutrition!"
REFUSAL_MESSAGE = (
    REFUSAL_MESSAGE_EN + "\n\n" + REFUSAL_MESSAGE_NL
)  # fallback bilingual


_REFUSAL_SENTENCE_PATTERNS = [
    r"Unfortunately,?\s*I (?:can(?:'|’)?t|cannot) help you with this question[^.]*\.",
    r"However,?\s*I(?:'|’)?m happy to help (?:you )?with questions about sports nutrition!?",
    r"Helaas kan ik je bij deze vraag niet helpen[^.]*\.",
    r"Wel help ik je graag (?:verder )?met vragen over sportvoeding!?",
    r"I don(?:'|’)?t know\.?\s*This is outside the book(?:'|’)?s context\.?",
    r"Ik weet het niet\.?\s*Dit is buiten de context van het boek\.?",
]


def _strip_refusal_from_answer(answer: str) -> str:
    """
    Remove refusal phrases that the model may have appended despite having content,
    plus clean 'page number not in index' artifacts. Idempotent.
    Keeps the substantive parts of the answer intact.
    """
    if not answer:
        return answer
    out = answer
    # Sweep all known refusal sentences anywhere in the text (not just trailing).
    for pat in _REFUSAL_SENTENCE_PATTERNS:
        out = re.sub(pat, "", out, flags=re.IGNORECASE)
    # Clean stale "page number not in index" artifacts (never shown to user)
    for bad in (
        "(zie pagina nummer niet in index)",
        "(page number not in index)",
        "pagina nummer niet in index",
        " page number not in index",
    ):
        if bad in out:
            out = out.replace(bad, "")
    # Tidy: collapse triple+ newlines, double spaces, dangling empty parens
    out = re.sub(r"\(\s*\)\.?", "", out)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = out.strip()
    # If we accidentally stripped everything (refusal-only message), return original
    return out if out else answer


def _dedupe_paragraphs(text: str) -> str:
    """
    Remove consecutive identical (or near-identical) paragraphs and identical
    consecutive sentences. Defends against the 'same answer twice' issue.
    """
    if not text:
        return text
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    deduped_paras = []
    for p in paragraphs:
        norm = re.sub(r"\s+", " ", p).strip().lower()
        if deduped_paras:
            prev_norm = re.sub(r"\s+", " ", deduped_paras[-1]).strip().lower()
            if norm == prev_norm:
                continue
        deduped_paras.append(p)
    out = "\n\n".join(deduped_paras)
    # Sentence-level dedupe within each paragraph
    def _dedupe_sentences(par: str) -> str:
        sents = re.split(r"(?<=[.!?])\s+", par)
        seen_consecutive = None
        kept = []
        for s in sents:
            key = re.sub(r"\s+", " ", s).strip().lower()
            if key and key == seen_consecutive:
                continue
            seen_consecutive = key
            kept.append(s)
        return " ".join(kept)
    out = "\n\n".join(_dedupe_sentences(p) for p in out.split("\n\n"))
    return out.strip()


def _message_suggests_dutch(user_message: str) -> bool:
    """True if the user's message content suggests Dutch (for citations only; no DEFAULT_LANGUAGE)."""
    if not user_message or not user_message.strip():
        return False
    msg = user_message.lower().strip()
    dutch_cues = [
        "pagina", "welke", "waar", "bladzijde", "vind", "staat", "recept", "het boek",
        "een vraag", "van de", "op welke", "welke pagina", "kunt u", "kun je",
        "graag", "alsjeblieft", "dank", "bedankt", "hoeveel", "waarom", "wanneer",
        "hoi", "halloo", "hallo", "hoe ", "hoe werkt", "wie ben", "vertellen", "mij ",
        "jij ", "dit ", "werkt", "gedaan", "dankjewel", "dank je",
        "goedemorgen", "goedemiddag", "goedenmiddag", "goedenavond", "goedendag",
        "goede morgen", "goede middag", "goede avond", "goede dag", "dag ", "dag!",
    ]
    english_cues = [
        "what", "which", "where", "how", "when", "why", "best", "before", "training",
        "eat", "tell me", "thank", "thanks", "please", "hello", "hi ", "hey",
        "give", "reference", "references", "source", "page",
    ]
    has_dutch = any(c in msg for c in dutch_cues)
    has_english = any(c in msg for c in english_cues)
    return has_dutch and not has_english


def _use_dutch_page_word(user_message: str) -> bool:
    """True if we should use Dutch for greeting/refusal/welcome (message or DEFAULT_LANGUAGE)."""
    if not user_message or not user_message.strip():
        lang = getattr(settings, "DEFAULT_LANGUAGE", "") or ""
        return "dutch" in lang.lower() or lang.lower() == "nl"
    lang = getattr(settings, "DEFAULT_LANGUAGE", "") or ""
    if "dutch" in lang.lower() or lang.lower() == "nl":
        return True
    msg = user_message.lower().strip()
    dutch_cues = [
        "pagina", "welke", "waar", "bladzijde", "vind", "staat", "recept", "het boek",
        "een vraag", "van de", "op welke", "welke pagina", "kunt u", "kun je",
        "graag", "alsjeblieft", "dank", "bedankt", "hoeveel", "waarom", "wanneer",
        "hoi", "halloo", "hallo", "hoe ", "hoe werkt", "wie ben", "vertellen", "mij ",
        "jij ", "dit ", "werkt", "gedaan", "dankjewel", "dank je",
        "goedemorgen", "goedemiddag", "goedenmiddag", "goedenavond", "goedendag",
        "goede morgen", "goede middag", "goede avond", "goede dag", "dag ", "dag!",
    ]
    return any(c in msg for c in dutch_cues)


def _has_english_cues(user_message: str) -> bool:
    """True if the message clearly contains English (for welcome/refusal language choice)."""
    if not user_message or not user_message.strip():
        return False
    msg = user_message.lower().strip()
    english_cues = [
        "hello", "hi ", "hey", "what", "which", "where", "how", "when", "why",
        "can you", "could you", "tell me", "common", "mistake", "athletes", "training",
        "thank", "thanks", "please", "help", "book", "nutrition", "recipe", "recipes",
    ]
    return any(c in msg for c in english_cues)


def _detect_language(user_input: str, chat_history: list | None = None) -> str:
    """
    Decide reply language: 'nl' or 'en'. Single source of truth so we never mix
    or stack languages. Order:
      1. Strong cues in current message
      2. Most recent user message in chat history with clear cues
      3. settings.DEFAULT_LANGUAGE (defaults to Dutch since the book is Dutch)
    """
    msg = (user_input or "").lower().strip()
    if msg:
        nl = _use_dutch_page_word(user_input)
        en = _has_english_cues(user_input)
        if nl and not en:
            return "nl"
        if en and not nl:
            return "en"
    # Walk recent chat history (newest last) looking for an unambiguous cue
    if chat_history:
        for m in reversed(chat_history):
            content = getattr(m, "content", "") or ""
            if not content:
                continue
            # Only consider human messages (HumanMessage class) — fall back gracefully
            if m.__class__.__name__ != "HumanMessage":
                continue
            nl = _use_dutch_page_word(content)
            en = _has_english_cues(content)
            if nl and not en:
                return "nl"
            if en and not nl:
                return "en"
    default = (getattr(settings, "DEFAULT_LANGUAGE", "") or "").lower()
    if default in ("nl", "dutch", "nederlands"):
        return "nl"
    if default in ("en", "english", "engels"):
        return "en"
    # Book is Dutch — safer default than bilingual stacking
    return "nl"


def _refusal_for_language(user_input: str, chat_history: list | None = None) -> str:
    """Return refusal in a SINGLE language. Never bilingual."""
    return REFUSAL_MESSAGE_NL if _detect_language(user_input, chat_history) == "nl" else REFUSAL_MESSAGE_EN


def _user_asks_for_reference(user_message: str) -> bool:
    """True if the user is asking for a reference, source, or page number (EN or NL)."""
    if not user_message or not user_message.strip():
        return False
    msg = user_message.lower().strip()
    cues = [
        "reference", "referentie", "referenties", "source", "bron", "cite",
        "page", "pagina", "bladzijde", "which page", "welke pagina", "welke bladzijde",
        "where to find", "waar vind ik", "where can i find", "give me the page",
        "with reference", "with source", "met bron", "met referentie",
        "include reference", "include source", "give reference", "in the book",
    ]
    return any(c in msg for c in cues)


def _format_references_line(used_docs: list, use_dutch: bool) -> str:
    """Build a single references line from excerpt metadata (page or section)."""
    if not used_docs:
        return ""
    seen = set()
    parts = []
    for meta in used_docs:
        page = meta.get("page")
        chunk = meta.get("chunk_index", "?")
        if page is not None and str(page).strip() and str(page) != "N/A":
            key = ("p", page)
            if key not in seen:
                seen.add(key)
                if use_dutch:
                    parts.append(f"pagina {page}")
                else:
                    parts.append(f"page {page}")
        else:
            key = ("s", chunk)
            if key not in seen:
                seen.add(key)
                if use_dutch:
                    parts.append(f"sectie {chunk}")
                else:
                    parts.append(f"section {chunk}")
    if not parts:
        return ""
    if use_dutch:
        return "Referenties: " + ", ".join(parts)
    return "References: " + ", ".join(parts)


def _answer_has_page_reference(answer: str) -> bool:
    """True if answer already contains a page/pagina number (e.g. page 42 or pagina 42)."""
    if not answer:
        return False
    return bool(re.search(r"\b(?:page|pagina)\s+\d+", answer, re.IGNORECASE))


def _localize_page_citations(user_message: str, answer: str) -> str:
    """Match citation language to user message: Dutch -> 'pagina'; English -> 'page'. Fix model output if it used the wrong language."""
    if not answer:
        return answer
    if _message_suggests_dutch(user_message):
        # User wrote in Dutch: use "pagina"
        answer = re.sub(r"\bpage\s+(\d+)", r"pagina \1", answer, flags=re.IGNORECASE)
        answer = re.sub(r"\bpages\s+", "pagina's ", answer, flags=re.IGNORECASE)
    else:
        # User wrote in English: ensure citations are in English (model sometimes outputs "Zie pagina")
        answer = re.sub(r"\bZie pagina\b", "See page", answer, flags=re.IGNORECASE)
        answer = re.sub(r"\bReferenties:\s*pagina\b", "References: page", answer, flags=re.IGNORECASE)
        answer = re.sub(r"\bpagina\s+(\d+)", r"page \1", answer, flags=re.IGNORECASE)
    return answer


def _prepend_welcome_if_first(reply: str, is_first_message: bool, user_input: str = "") -> str:
    """
    Prepend welcome intro only on the user's first message.
    Language: Dutch only, English only, or both only when the user's message mixes both.
    """
    if not is_first_message:
        return reply
    if not reply:
        reply = WELCOME_INTRO_NL
    dutch = _use_dutch_page_word(user_input or "")
    english = _has_english_cues(user_input or "")
    if dutch and not english:
        intro = WELCOME_INTRO_NL
    elif english and not dutch:
        intro = WELCOME_INTRO_EN
    else:
        intro = WELCOME_INTRO_EN + "\n\n" + WELCOME_INTRO_NL
    return intro + "\n\n" + reply


def _is_refusal_response(answer: str) -> str:
    """Return 'refused' only if the answer is essentially the refusal (no substantive content)."""
    a = answer.strip()
    if not a:
        return "refused"
    # If it's mostly the refusal message, count as refused
    if "Unfortunately, I can't help" in a and "Helaas kan ik je" in a:
        if len(a) < 280:  # roughly the length of the bilingual refusal
            return "refused"
    if "Helaas kan ik je bij deze vraag niet helpen" in a and len(a) < 200:
        return "refused"
    if "Unfortunately, I can't help you with this question" in a and len(a) < 200:
        return "refused"
    # If answer contains refusal but also substantive content (e.g. section 13, page 5), treat as answered
    if "chunk" in a.lower() or "excerpt" in a.lower() or "page" in a.lower() or "section" in a.lower():
        return "answered"
    if "I don't know" in a or "Ik weet het niet" in a or "buiten de context" in a.lower():
        return "refused"
    return "answered"


# -----------------------------
# GLOBALS
# -----------------------------
llm = None
retriever = None
intent_chain = None
split_questions_chain = None
rewrite_chain = None
answer_chain = None


# -----------------------------
# INIT RAG COMPONENTS
# -----------------------------
def init_rag_components():
    global llm, retriever, intent_chain, split_questions_chain, rewrite_chain, answer_chain

    embeddings = OpenAIEmbeddings(
        model=settings.OPENAI_EMBEDDING_MODEL
    )

    llm = ChatOpenAI(
        model=settings.OPENAI_MODEL,
        temperature=settings.OPENAI_TEMPERATURE,
        max_tokens=settings.MAX_TOKENS_RESPONSE
    )

    vectorstore = PGVector(
        embeddings=embeddings,
        collection_name="book_chunks",
        connection=settings.DATABASE_URL,
        use_jsonb=True,
    )

    retriever = vectorstore

    # 1. Intent detection
    intent_chain = (
        ChatPromptTemplate.from_messages([
            ("system", "Classify the user message. Return ONLY one word: GREETING, THANKS, or QUESTION. "
             "GREETING: hello, hi, hey, who are you, how does this work, hoe werkt dit, wie ben jij, "
             "hallo, hoi, goedemorgen, goedemiddag, goedenavond, goedendag, dag (when used as greeting). "
             "THANKS: thanks, thank you, bedankt, dank je, dankjewel (not GREETING). "
             "Anything else that is a real question = QUESTION."),
            ("human", "{input}")
        ])
        | llm
        | StrOutputParser()
    )

    # 2. Split multiple questions (one message can contain several questions)
    split_questions_chain = (
        ChatPromptTemplate.from_messages([
            (
                "system",
                "The user may have asked one or more questions in a single message. "
                "Split the message into separate questions. Return ONLY the questions, one per line. "
                "Do not number them. Do not add labels. If there is exactly one question, return that one line. "
                "If the message is not a question (e.g. greeting), return it as-is on one line. "
                "Example: 'What is X? And what about Y?' -> 'What is X?' then new line 'What about Y?'",
            ),
            ("human", "{input}"),
        ])
        | llm
        | StrOutputParser()
    )

    # 3. Rewrite query (standalone query using chat history)
    rewrite_chain = (
        ChatPromptTemplate.from_messages([
            (
                "system",
                "Rewrite the user message into a standalone search query for finding relevant book content. "
                "Use chat history for context. Output the query in English only. Return ONLY the rewritten query, nothing else.\n\n"
                "FOLLOW-UP HANDLING (critical): If the user message is a follow-up — it uses pronouns ('it','that','this','die','het','dat','deze','dit'), or starts with 'and …'/'en …'/'wat dan met …'/'and what about …', or is short and clearly continues the previous topic — you MUST resolve it against the most recent topic in chat history before producing the query. Example: previous turn was about protein for older adults and the user now writes 'En voor jongeren?' → rewrite to 'protein needs for young athletes'.\n\n"
                "TOPIC NORMALIZATION (use the same English terms regardless of source language so retrieval is consistent):\n"
                "  • verzadigd vet / saturated fat → 'saturated fat health effects'\n"
                "  • onverzadigd vet / gezonde oliën / unsaturated / healthy oils → 'unsaturated fats healthy oils'\n"
                "  • eiwit / proteïne / protein → 'protein intake recovery'\n"
                "  • koolhydraten / carbs → 'carbohydrates endurance'\n"
                "  • herstel / recovery → 'recovery nutrition after training'\n"
                "  • recept / recepten / recipe / recipes / inspiratie → 'recipes meal ideas'\n"
                "  • dagmenu / daily menu / sample day → 'daily menu sample meal plan'\n"
                "  • wedstrijd / competition → 'competition day nutrition'\n"
                "  • hydratatie / hydration / drinken → 'hydration fluids athletes'\n"
                "  • oudere / 80 / senior / older athlete → 'protein nutrition older adults'\n"
                "Preserve any specific numbers, ages, weights, or sport names from the user's message in the query."
            ),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}")
        ])
        | llm
        | StrOutputParser()
    )

    # 4. Answer generation — friendly, concise, single-language
    max_words = getattr(settings, "ANSWER_MAX_WORDS", 120)
    enable_clarify = getattr(settings, "ENABLE_CLARIFY_QUESTION", True)
    enable_followup = getattr(settings, "ENABLE_FOLLOWUP_SUGGESTIONS", True)

    answer_system_prompt = (
        f"You are the friendly buddy of the book \"{settings.BOOK_TITLE}\". You speak like a knowledgeable, warm friend who happens to know the book inside out. Not a textbook, not a lecturer.\n\n"
        "You will receive excerpts from the book labeled like [page N] or [section N], plus the recent chat history.\n\n"
        "## HARD RULES (in priority order)\n"
        "1. **LANGUAGE LOCK.** Write the ENTIRE reply in {language} ('nl' = Dutch, 'en' = English). Never mix languages. If the source excerpts are in another language, translate them. Do NOT include any sentence in the other language, not even a refusal or disclaimer.\n"
        "2. **ANSWER ONLY WHAT WAS ASKED.** If the user asks about protein, talk about protein. Do NOT volunteer magnesium, calcium, vitamins, fiber, hydration tips, or any nutrient/topic the user did not ask about. Resist the urge to give a 'complete picture'.\n"
        f"3. **BE CONCISE.** Target {max_words} words or fewer. Only go longer when the user explicitly asks for a meal plan, sample day, or detailed example.\n"
        "4. **REALISTIC PORTIONS FOR ONE PERSON, ONE MOMENT.** When you mention amounts, give them for a single person and a single meal/snack. Do NOT stack a full day's worth of food into one example. Match portions to the user's stated context (age, weight, training type) when known from chat history.\n"
        "5. **PERSONALIZE FROM HISTORY.** If the user has shared personal info in earlier messages (age, weight, sport, training frequency, goals), use it. If they ask about 'protein for me', use what you know about them.\n"
        "6. **CONSISTENCY WITH PRIOR ANSWERS.** If your earlier answers in this same chat differ from what you'd say now (e.g. 'avoid protein right before training' vs 'take protein after training'), call out the distinction in one short sentence so the user is not confused.\n"
        + (
            "7. **CLARIFY WHEN TRULY VAGUE.** If the question is too vague to answer well (e.g. 'what should I eat?' with zero context) AND chat history has no clarifying info (sport, time of day, weight, goal), ask exactly ONE short clarifying question and stop. Never ask more than one. Never ask if the question is already specific enough or if the answer is obvious from context.\n"
            if enable_clarify else ""
        )
        + (
            "8. **OPTIONAL FOLLOW-UP.** After a substantive answer, you MAY end with ONE short suggestion of a natural follow-up the user might want next (e.g. 'Wil je dat ik dit toepas op jouw trainingsdag?'). Only when the topic naturally invites depth — never on greetings, thanks, refusals, or when you just asked a clarifying question.\n"
            if enable_followup else ""
        )
        + "9. **REFERENCES.** When the user asks for a reference / source / page (words like 'page','pagina','bladzijde','bron','referentie','where','waar','source'), include the page/section numbers from the excerpt labels. Use 'page N' in English, 'pagina N' in Dutch. Otherwise do NOT add page numbers.\n"
        "10. **DISCLAIMER.** Only when you give specific nutrient amounts or portion sizes, add ONE short sentence in {language}: Dutch: 'Dit is algemene informatie; voor persoonlijk advies kun je een (sport)diëtist raadplegen.' English: 'This is general information; for personal advice you can consult a (sports) dietitian.'\n"
        "11. **REFUSE ONLY WHEN TRULY OFF-TOPIC.** Off-topic = politics, code, medical diagnosis, anything unrelated to food, eating, sport, recovery, recipes, hydration, age groups, body composition. Anything about nutrition — including for older adults, beginners, casual exercisers, or people with general health goals — is IN scope. The book covers sports nutrition broadly; do NOT refuse just because the user is not a competitive athlete.\n"
        "12. **REFUSAL FORMAT.** When (and only when) you must refuse, reply with EXACTLY one of these single lines and nothing else:\n"
        "    - Dutch: \"Helaas kan ik je bij deze vraag niet helpen. Wel help ik je graag verder met vragen over sportvoeding!\"\n"
        "    - English: \"Unfortunately, I can't help you with this question. However, I'm happy to help you with questions about sports nutrition!\"\n"
        "    Use the line that matches {language}. Never output both. Never combine refusal with substantive content in the same reply.\n"
        "13. **NO META.** Never mention 'excerpts', 'chunks', 'context', 'the system', or that you have 'no information'. If you can't answer, use the refusal in rule 12.\n"
    )

    answer_chain = (
        ChatPromptTemplate.from_messages([
            ("system", answer_system_prompt),
            ("system", "Context excerpts from the book:\n{context}"),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}")
        ])
        | llm
        | StrOutputParser()
    )

    print("✅ RAG components initialized")


# -----------------------------
# GET RESPONSE
# -----------------------------
def _split_into_questions(user_input: str) -> list:
    """
    Return the user message as a single question.
    The multi-question splitting chain is disabled for now to avoid confusing numbered answers
    (e.g. '1. refusal' + '2. real answer') when the model over-splits a single question.
    """
    return [user_input.strip() if user_input else ""]


def get_response(user_input: str, whatsapp_number: str, db: Session, is_first_message: bool = False):
    if not all([llm, retriever, intent_chain, split_questions_chain, rewrite_chain, answer_chain]):
        init_rag_components()

    # Load chat history early so language detection can use it for short follow-ups.
    past_logs = (
        db.query(ChatLog)
        .filter(ChatLog.whatsapp_number == whatsapp_number)
        .order_by(desc(ChatLog.created_at))
        .limit(settings.MAX_CHAT_LOG_MESSAGES)
        .all()
    )

    chat_history = []
    for log in reversed(past_logs):
        chat_history.append(HumanMessage(content=log.user_message))
        chat_history.append(AIMessage(content=log.bot_response))

    # Single source of truth for the reply language.
    language_code = _detect_language(user_input, chat_history)
    language_full = "Dutch" if language_code == "nl" else "English"

    # 1. Intent check
    intent = intent_chain.invoke({"input": user_input}).strip().upper()
    if "GREETING" in intent:
        reply = OPENING_MESSAGE_NL if language_code == "nl" else OPENING_MESSAGE_EN
        db.add(ChatLog(whatsapp_number=whatsapp_number, user_message=user_input, bot_response=reply, response_type="greeting", chunks_used=[], history_snapshot=[]))
        db.commit()
        return reply
    if "THANKS" in intent:
        if language_code == "nl":
            reply = "Graag gedaan! Stel gerust nog een vraag over het boek."
        else:
            reply = "You're welcome! Ask me anything else about the book."
        final = _prepend_welcome_if_first(reply, is_first_message, user_input)
        db.add(ChatLog(whatsapp_number=whatsapp_number, user_message=user_input, bot_response=final, response_type="thanks", chunks_used=[], history_snapshot=[]))
        db.commit()
        return final

    questions = _split_into_questions(user_input)

    if len(questions) > 1:
        # Multiple questions in one message: answer each and combine into one reply
        def _excerpt_label(meta):
            page = meta.get("page")
            chunk = meta.get("chunk_index", "?")
            section = meta.get("section")
            if page is not None and str(page).strip() and str(page) != "N/A":
                part = f"page {page}"
            else:
                part = f"section {chunk}"
            if section:
                part += f", {section}"
            return part
        parts = []
        all_used_docs = []
        for q in questions:
            if not q.strip():
                continue
            rewritten = rewrite_chain.invoke({"chat_history": [], "input": q})
            try:
                docs_with_scores = retriever.similarity_search_with_score(rewritten, k=settings.RETRIEVAL_TOP_K)
            except OperationalError:
                docs_with_scores = []
            relevant_docs = [(doc, s) for doc, s in docs_with_scores if s <= settings.SIMILARITY_THRESHOLD]
            if not relevant_docs and docs_with_scores:
                relevant_docs = list(docs_with_scores)[:3]
            if not relevant_docs:
                parts.append(_refusal_for_language(q, chat_history))
            else:
                context_text = "\n\n".join(
                    f"Excerpt [{_excerpt_label(doc.metadata)}]: {doc.page_content}"
                    for doc, _ in relevant_docs
                )
                part = answer_chain.invoke({"context": context_text, "chat_history": [], "input": q, "language": language_full})
                part = _strip_refusal_from_answer(part)
                part = _localize_page_citations(q, part)
                all_used_docs.extend(doc.metadata for doc, _ in relevant_docs)
                if _user_asks_for_reference(q) and not _answer_has_page_reference(part):
                    ref_line = _format_references_line([doc.metadata for doc, _ in relevant_docs], use_dutch=_message_suggests_dutch(q))
                    if ref_line:
                        part = (part.rstrip() + "\n\n" + ref_line).strip()
                parts.append(part)
        answer = "\n\n".join(f"{i+1}. {p}" for i, p in enumerate(parts))
        used_docs = all_used_docs
        response_type = "answered"
    else:
        # Single question
        # 3. Rewrite query
        rewritten_query = rewrite_chain.invoke({
            "chat_history": chat_history,
            "input": user_input
        })
        print(f"🔍 DEBUG Rewritten Query: {rewritten_query}")

        # 4. Vector retrieval (with retry on stale DB connection)
        def _do_retrieval():
            return retriever.similarity_search_with_score(
                rewritten_query,
                k=settings.RETRIEVAL_TOP_K
            )

        try:
            docs_with_scores = _do_retrieval()
        except OperationalError as e:
            if "SSL connection" in str(e) or "closed" in str(e).lower() or "connection" in str(e).lower():
                print("⚠️ DB connection stale, re-initializing RAG and retrying once...")
                init_rag_components()
                try:
                    docs_with_scores = _do_retrieval()
                except Exception as retry_e:
                    print(f"❌ Retry failed: {retry_e}")
                    answer = _refusal_for_language(user_input, chat_history)
                    db.add(ChatLog(whatsapp_number=whatsapp_number, user_message=user_input, bot_response=answer, response_type="refused", chunks_used=[], history_snapshot=[]))
                    db.commit()
                    return answer
            else:
                raise

        relevant_docs = [
            (doc, score)
            for doc, score in docs_with_scores
            if score <= settings.SIMILARITY_THRESHOLD
        ]
        # If nothing under threshold, use top 3 retrieved so we don't refuse borderline questions (e.g. wedstrijdperiodes)
        if not relevant_docs and docs_with_scores:
            relevant_docs = list(docs_with_scores)[:3]

        print(f"📊 DEBUG: Found {len(relevant_docs)} relevant chunks")

        # 5. Answer phase
        if not relevant_docs:
            answer = _refusal_for_language(user_input, chat_history)
            response_type = "refused"
            used_docs = []
        else:
            def _excerpt_label(meta):
                """Label excerpts: page when available, otherwise section N (so we always have a reference to give when asked)."""
                page = meta.get("page")
                chunk = meta.get("chunk_index", "?")
                section = meta.get("section")
                if page is not None and str(page).strip() and str(page) != "N/A":
                    part = f"page {page}"
                else:
                    part = f"section {chunk}"
                if section:
                    part += f", {section}"
                return part
            context_text = "\n\n".join(
                f"Excerpt [{_excerpt_label(doc.metadata)}]: {doc.page_content}"
                for doc, _ in relevant_docs
            )

            answer = answer_chain.invoke({
                "context": context_text,
                "chat_history": chat_history,
                "input": user_input,
                "language": language_full,
            })

            # If model wrongly appended refusal despite having relevant excerpts, strip it and keep the useful part
            answer = _strip_refusal_from_answer(answer)
            # Remove duplicated paragraphs/sentences (defends against the 'same answer twice' issue)
            answer = _dedupe_paragraphs(answer)
            # Use Dutch 'pagina' instead of 'page' when user wrote in Dutch
            answer = _localize_page_citations(user_input, answer)

            used_docs = [doc.metadata for doc, _ in relevant_docs]
            # If user asked for reference/source/page but the model didn't include one, append references from excerpts
            if _user_asks_for_reference(user_input) and not _answer_has_page_reference(answer):
                ref_line = _format_references_line(used_docs, use_dutch=_message_suggests_dutch(user_input))
                if ref_line:
                    answer = (answer.rstrip() + "\n\n" + ref_line).strip()

            # Only treat as refused if answer is essentially the refusal (no substantive content)
            response_type = _is_refusal_response(answer)

    # 6. Save chat log
    db.add(ChatLog(
        whatsapp_number=whatsapp_number,
        user_message=user_input,
        bot_response=answer,
        response_type=response_type,
        chunks_used=used_docs,
        history_snapshot=[
            {
                "role": "human" if isinstance(m, HumanMessage) else "ai",
                "content": m.content
            }
            for m in chat_history
        ]
    ))
    db.commit()

    # -----------------------------
    # 7. CLEANUP OLD CHAT LOGS (FIXED)
    # -----------------------------
    keep_ids = (
        db.query(ChatLog.id)
        .filter(ChatLog.whatsapp_number == whatsapp_number)
        .order_by(desc(ChatLog.created_at))
        .limit(settings.MAX_CHAT_LOG_MESSAGES)
        .all()
    )

    keep_ids = [id for (id,) in keep_ids]

    if keep_ids:
        (
            db.query(ChatLog)
            .filter(ChatLog.whatsapp_number == whatsapp_number)
            .filter(~ChatLog.id.in_(keep_ids))
            .delete(synchronize_session=False)
        )
        db.commit()

    # Do NOT stack the welcome intro on top of a refusal — that produces a confusing
    # "intro + we can't help" sandwich on the user's very first message.
    if response_type == "refused":
        return answer
    return _prepend_welcome_if_first(answer, is_first_message, user_input)
