import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from memory import get_known_answers

logger = logging.getLogger(__name__)

DICTIONARY_FILE = Path(__file__).with_name("dictionary.json")
MYMEMORY_URL = "https://api.mymemory.translated.net/get"
REQUEST_TIMEOUT = 8


def normalize(text):
    """Normalize text for matching dictionary and memory entries."""
    return " ".join(str(text or "").strip().lower().split())


def load_dictionary():
    """Load dictionary.json; return an empty dictionary if it is unavailable."""
    try:
        with DICTIONARY_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, dict):
            return data
        logger.warning("dictionary.json powinien zawierać obiekt JSON.")
    except FileNotFoundError:
        logger.info("Nie znaleziono dictionary.json: %s", DICTIONARY_FILE)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Nie można odczytać dictionary.json: %s", exc)
    return {}


def translate_online(polish, context=""):
    """
    Translate Polish text to English with MyMemory.
    Returns the translated text or None on failure.
    """
    text = str(polish or "").strip()
    if not text:
        return None

    query_text = text
    if context:
        query_text = f"{text}. Kontekst: {context}"

    params = urllib.parse.urlencode({
        "q": query_text,
        "langpair": "pl|en",
    })
    request = urllib.request.Request(
        f"{MYMEMORY_URL}?{params}",
        headers={"User-Agent": "InstalingHelper/1.0"},
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            raw_payload = response.read().decode("utf-8")
        payload = json.loads(raw_payload)

        response_data = payload.get("responseData") or {}
        answer = str(response_data.get("translatedText") or "").strip()
        if not answer:
            logger.warning("MyMemory nie zwróciło tłumaczenia dla: %s", text)
            return None

        if payload.get("responseStatus") not in (None, 200, "200"):
            logger.warning(
                "MyMemory API status: %s",
                payload.get("responseStatus"),
            )
            return None

        # API may return HTML entities in translated text.
        from html import unescape
        answer = unescape(answer).strip()
        return answer or None

    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        logger.warning("Internetowy tłumacz jest niedostępny: %s", exc)
        return None


def find_translations(polish, context="", word_count=None):
    """
    Kolejność źródeł:
    1. pamięć zaakceptowanych odpowiedzi,
    2. lokalny dictionary.json,
    3. internetowy tłumacz jako awaryjne źródło.

    Tłumaczenie online jest propozycją, a nie gwarancją akceptacji przez InstaLing.
    """
    known = get_known_answers(
        polish,
        context=context,
        word_count=word_count,
    )
    if not isinstance(known, dict):
        known = {}

    translations = []
    seen = set()

    def add_translation(answer, source, verified):
        answer = str(answer or "").strip()
        if not answer:
            return

        if word_count is not None and len(answer.split()) != word_count:
            return

        key = normalize(answer)
        if not key or key in seen:
            return

        seen.add(key)
        translations.append({
            "answer": answer,
            "source": source,
            "verified": verified,
        })

    # 1. Previously accepted answers.
    accepted = known.get("accepted_answers", [])
    if isinstance(accepted, list):
        for answer in accepted:
            add_translation(answer, "memory", True)

    # 2. Local dictionary.
    dictionary = load_dictionary()
    entry = dictionary.get(normalize(polish), [])

    if isinstance(entry, (str, dict)):
        entry = [entry]
    elif not isinstance(entry, list):
        entry = []

    for item in entry:
        if isinstance(item, str):
            answer = item
        elif isinstance(item, dict):
            answer = item.get("english", "")
        else:
            continue
        add_translation(answer, "dictionary", False)

    # Remove answers previously rejected by the user.
    rejected_values = known.get("rejected_answers", [])
    if not isinstance(rejected_values, list):
        rejected_values = []
    rejected = {normalize(answer) for answer in rejected_values}

    translations = [
        item for item in translations
        if normalize(item["answer"]) not in rejected
    ]

    # 3. Online fallback only if memory and dictionary have no usable answer.
    if not translations:
        online_answer = translate_online(polish, context=context)
        if online_answer and normalize(online_answer) not in rejected:
            add_translation(online_answer, "online", False)

    return {
        "known": bool(known.get("known", False)),
        "translations": translations,
        "needs_translation": len(translations) == 0,
    }


if __name__ == "__main__":
    import sys

    polish = " ".join(sys.argv[1:]).strip()
    if not polish:
        polish = input("Podaj polskie słowo: ").strip()

    result = find_translations(polish)
    print(json.dumps(result, ensure_ascii=False, indent=2))
