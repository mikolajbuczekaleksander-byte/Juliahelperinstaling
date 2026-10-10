from translator import find_translations
from vocabulary import (
    add_word,
    get_word,
    cleanup_expired_words,
)


def normalize(text):
    return " ".join(str(text).strip().lower().split())


def get_word_count(text):
    return len(text.split())


def analyze_word(polish, context="", word_count=None):
    """
    Analizuje słowo, sprawdza pamięć i słownik
    oraz zapisuje nowe informacje.

    Nie zgaduje odpowiedzi, jeśli nie ma tłumaczenia.
    """
    polish = normalize(polish)

    if not polish:
        return {
            "status": "error",
            "message": "Nie podano polskiego słowa.",
            "translations": [],
        }

    # Usuwanie słówek nieużywanych od ponad 30 dni.
    cleanup_expired_words()

    # Odczyt wcześniej zapisanego słowa.
    existing = get_word(polish)

    candidates = []

    # Wcześniej zapisane tłumaczenia.
    if existing:
        for item in existing.get("translations", []):
            answer = item.get("english", "")

            if not answer:
                continue

            if (
                word_count is not None
                and get_word_count(answer) != word_count
            ):
                continue

            candidates.append({
                "answer": answer,
                "source": item.get("source", "vocabulary"),
                "verified": item.get("verified", False),
            })

    # Pamięć odpowiedzi oraz słownik początkowy.
    result = find_translations(
        polish,
        context=context,
        word_count=word_count,
    )

    for item in result["translations"]:
        answer = item["answer"]

        if normalize(answer) in {
            normalize(candidate["answer"])
            for candidate in candidates
        }:
            continue

        candidates.append({
            "answer": answer,
            "source": item["source"],
            "verified": item["verified"],
        })

    # Zapisujemy znalezione tłumaczenia.
    # Nie oznaczamy propozycji jako potwierdzonych bez dowodu.
    for candidate in candidates:
        add_word(
            polish,
            translations=[{
                "english": candidate["answer"],
                "verified": candidate["verified"],
            }],
            source=candidate["source"],
        )

    # Rejestrujemy także nowe słowo, dla którego
    # nie udało się jeszcze znaleźć tłumaczenia.
    if not candidates:
        add_word(
            polish,
            translations=[],
            source="awaiting_translation",
        )

    if candidates:
        status = "translations_found"
    else:
        status = "translation_needed"

    return {
        "status": status,
        "polish": polish,
        "context": context,
        "word_count": word_count,
        "translations": candidates,
        "needs_translation": not bool(candidates),
    }


if __name__ == "__main__":
    polish = input("Podaj polskie słowo: ").strip()

    context = input(
        "Kontekst (opcjonalnie): "
    ).strip()

    count_input = input(
        "Wymagana liczba członów (opcjonalnie): "
    ).strip()

    word_count = (
        int(count_input)
        if count_input.isdigit()
        else None
    )

    result = analyze_word(
        polish,
        context=context,
        word_count=word_count,
    )

    print("\nWynik analizy:")

    if result["status"] == "error":
        print(result["message"])
    else:
        print("Status:", result["status"])

        if result["translations"]:
            for candidate in result["translations"]:
                print(
                    "-",
                    candidate["answer"],
                    "| źródło:",
                    candidate["source"],
                    "| potwierdzone:",
                    candidate["verified"],
                )
        else:
            print("Brak znanego tłumaczenia.")
            print("Potrzebne jest dodatkowe źródło.")
