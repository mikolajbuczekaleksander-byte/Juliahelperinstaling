import json
from pathlib import Path

from memory import get_known_answers


DICTIONARY_FILE = Path("dictionary.json")


def normalize(text):
    return " ".join(str(text).strip().lower().split())


def load_dictionary():
    """Wczytuje lokalny słownik."""
    if not DICTIONARY_FILE.exists():
        return {}

    with DICTIONARY_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def find_translations(polish, context="", word_count=None):
    """
    Zwraca możliwe tłumaczenia.

    Zapamiętane odpowiedzi i słownik to źródła propozycji,
    a nie gwarancja, że Instaling zaakceptuje daną odpowiedź.
    """
    known = get_known_answers(
        polish,
        context=context,
        word_count=word_count,
    )

    translations = []

    for answer in known["accepted_answers"]:
        translations.append({
            "answer": answer,
            "source": "memory",
            "verified": True,
        })

    dictionary = load_dictionary()

    entry = dictionary.get(normalize(polish), [])

    if isinstance(entry, str):
        entry = [entry]

    for item in entry:
        if isinstance(item, str):
            answer = item
            count = len(answer.split())
        else:
            answer = item.get("english", "")
            count = item.get("words", len(answer.split()))

        if not answer:
            continue

        if word_count is not None and count != word_count:
            continue

        if normalize(answer) not in {
            normalize(item["answer"]) for item in translations
        }:
            translations.append({
                "answer": answer,
                "source": "dictionary",
                "verified": False,
            })

    rejected = {
        normalize(answer)
        for answer in known["rejected_answers"]
    }

    translations = [
        item for item in translations
        if normalize(item["answer"]) not in rejected
    ]

    return {
        "known": known["known"],
        "translations": translations,
        "needs_translation": len(translations) == 0,
    }


if __name__ == "__main__":
    import sys

    polish = " ".join(sys.argv[1:]).strip()

    if not polish:
        polish = input("Podaj polskie słowo: ").strip()

    result = find_translations(polish)

    print(json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
    ))
