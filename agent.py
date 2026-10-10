from translator import find_translations
from vocabulary import (
    add_word,
    get_word,
    cleanup_expired_words,
)
from memory import (
    get_known_answers,
    record_attempt,
)


def normalize(text):
    return " ".join(str(text).strip().lower().split())


def get_word_count(text):
    return len(str(text).split())


def analyze_word(polish, context="", word_count=None):
    """
    Analizuje słowo z wykorzystaniem słownika i pamięci.
    Nie zgaduje, jeśli nie ma znanego tłumaczenia.
    """
    polish = normalize(polish)

    if not polish:
        return {
            "status": "error",
            "message": "Nie podano polskiego słowa.",
            "translations": [],
        }

    cleanup_expired_words()

    known = get_known_answers(
        polish,
        context=context,
        word_count=word_count,
    )

    existing = get_word(polish)
    candidates = []

    # Najpierw odpowiedzi wcześniej zaakceptowane.
    for answer in known["accepted_answers"]:
        if (
            word_count is not None
            and get_word_count(answer) != word_count
        ):
            continue

        candidates.append({
            "answer": answer,
            "source": "memory",
            "verified": True,
        })

    # Następnie odpowiedzi zapisane w słowniku.
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

            if normalize(answer) in {
                normalize(candidate["answer"])
                for candidate in candidates
            }:
                continue

            candidates.append({
                "answer": answer,
                "source": item.get("source", "vocabulary"),
                "verified": item.get("verified", False),
            })

    # Na końcu tłumaczenia znalezione przez translator.py.
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

    # Zapisujemy propozycje, ale nie oznaczamy ich
    # jako potwierdzonych bez wiarygodnej informacji zwrotnej.
    for candidate in candidates:
        add_word(
            polish,
            translations=[{
                "english": candidate["answer"],
                "verified": candidate["verified"],
            }],
            source=candidate["source"],
        )

    if not candidates:
        add_word(
            polish,
            translations=[],
            source="awaiting_translation",
        )

    return {
        "status": (
            "translations_found"
            if candidates
            else "translation_needed"
        ),
        "polish": polish,
        "context": context,
        "word_count": word_count,
        "translations": candidates,
        "needs_translation": not bool(candidates),
    }


def learn_from_feedback(
    polish,
    answer,
    accepted,
    correction=None,
    context="",
    word_count=None,
    session_id=None,
):
    """
    Zapisuje wynik odpowiedzi.

    accepted=True:
        odpowiedź została zaakceptowana.

    accepted=False i correction:
        odpowiedź odrzucono, a platforma pokazała poprawną.

    accepted=False bez correction:
        wynik pozostaje niepewny.
    """
    if accepted:
        result = "accepted"
    elif correction and normalize(correction):
        result = "rejected_with_correction"
    else:
        result = "uncertain"

    record_attempt(
        polish=polish,
        answer=answer,
        result=result,
        correction=correction,
        context=context,
        word_count=word_count,
        session_id=session_id,
    )

    # Zapamiętujemy odpowiedź zaakceptowaną lub poprawkę
    # przekazaną przez platformę.
    if accepted:
        add_word(
            polish,
            translations=[{
                "english": answer,
                "verified": True,
            }],
            source="verified_feedback",
        )

    elif correction and normalize(correction):
        add_word(
            polish,
            translations=[{
                "english": correction,
                "verified": True,
            }],
            source="verified_feedback",
        )

    return {
        "polish": normalize(polish),
        "answer": answer,
        "result": result,
        "correction": correction,
        "saved": True,
    }


if __name__ == "__main__":
    print("Instaling Helper — test agenta")
    print("Wpisz polskie słowo, aby sprawdzić tłumaczenia.")
    print("Aby zakończyć, wpisz: koniec")

    while True:
        polish = input("\nPolskie słowo: ").strip()

        if normalize(polish) == "koniec":
            break

        context = input("Kontekst (opcjonalnie): ").strip()
        count_input = input(
            "Wymagana liczba członów (Enter = dowolna): "
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

        print("\nWynik:", result["status"])

        for candidate in result["translations"]:
            print(
                "-",
                candidate["answer"],
                "| źródło:",
                candidate["source"],
                "| potwierdzone:",
                candidate["verified"],
            )

        if not result["translations"]:
            print("Nie znaleziono tłumaczenia.")
            continue

        answer = input(
            "\nKtórą odpowiedź sprawdzono? "
            "(wpisz ją dokładnie): "
        ).strip()

        accepted_input = input(
            "Czy Instaling zaakceptował odpowiedź? (t/n): "
        ).strip().lower()

        if accepted_input == "t":
            learn_from_feedback(
                polish,
                answer,
                accepted=True,
                context=context,
                word_count=word_count,
            )
            print("Zapisano zaakceptowaną odpowiedź.")

        elif accepted_input == "n":
            correction = input(
                "Poprawna odpowiedź pokazana przez platformę "
                "(Enter, jeśli jej nie pokazano): "
            ).strip()

            learn_from_feedback(
                polish,
                answer,
                accepted=False,
                correction=correction or None,
                context=context,
                word_count=word_count,
            )

            print("Zapisano wynik próby.")
