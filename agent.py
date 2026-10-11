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
    """Normalizuje tekst do porównywania odpowiedzi."""
    return " ".join(str(text or "").strip().lower().split())


def get_word_count(text):
    """Liczba wyrazów w angielskiej odpowiedzi."""
    normalized = normalize(text)
    return len(normalized.split()) if normalized else 0


def _answer_key(text):
    """Klucz porównawczy odpowiedzi."""
    return normalize(text).strip(".,!?;:\"'()[]{}")


def _add_candidate(candidates, answer, source, verified=False):
    """Dodaje odpowiedź bez tworzenia duplikatów."""
    answer = str(answer or "").strip()

    if not answer:
        return

    key = _answer_key(answer)

    for candidate in candidates:
        if _answer_key(candidate["answer"]) == key:
            if verified:
                candidate["verified"] = True
                candidate["source"] = source
            return

    candidates.append({
        "answer": answer,
        "source": source,
        "verified": bool(verified),
    })


def analyze_word(polish, context="", word_count=None):
    """
    Znajduje możliwe tłumaczenia.

    Kolejność:
    1. Odpowiedzi wcześniej zaakceptowane.
    2. Odpowiedzi ze słownika.
    3. Odpowiedzi znalezione przez translator.py.

    Nie traktuje niepotwierdzonych propozycji jako poprawnych.
    """
    polish = normalize(polish)
    context = str(context or "").strip()

    if not polish:
        return {
            "status": "error",
            "message": "Nie podano polskiego słowa.",
            "translations": [],
            "needs_translation": True,
        }

    if word_count is not None:
        if not isinstance(word_count, int) or word_count < 1:
            return {
                "status": "error",
                "message": "Liczba wyrazów musi być dodatnią liczbą całkowitą.",
                "translations": [],
                "needs_translation": True,
            }

    cleanup_expired_words()

    known = get_known_answers(
        polish,
        context=context,
        word_count=word_count,
    )

    rejected = {
        _answer_key(answer)
        for answer in known.get("rejected_answers", [])
    }

    candidates = []

    # Najwyższy priorytet: odpowiedzi zaakceptowane wcześniej.
    for answer in known.get("accepted_answers", []):
        if word_count is not None and get_word_count(answer) != word_count:
            continue

        _add_candidate(
            candidates,
            answer,
            source="memory",
            verified=True,
        )

    # Kolejny priorytet: zapisane tłumaczenia.
    existing = get_word(polish)

    if existing:
        for item in existing.get("translations", []):
            answer = item.get("english", "").strip()

            if not answer:
                continue

            if word_count is not None and get_word_count(answer) != word_count:
                continue

            _add_candidate(
                candidates,
                answer,
                source=item.get("source", "vocabulary"),
                verified=item.get("verified", False),
            )

    # Na końcu propozycje z translator.py.
    result = find_translations(
        polish,
        context=context,
        word_count=word_count,
    )

    for item in result.get("translations", []):
        answer = item.get("answer", "").strip()

        if not answer:
            continue

        if word_count is not None and get_word_count(answer) != word_count:
            continue

        _add_candidate(
            candidates,
            answer,
            source=item.get("source", "translator"),
            verified=item.get("verified", False),
        )

    # Odrzucone odpowiedzi nie powinny być proponowane ponownie
    # jako zwykłe, niepotwierdzone propozycje.
    filtered = []

    for candidate in candidates:
        key = _answer_key(candidate["answer"])

        if key in rejected and not candidate["verified"]:
            continue

        filtered.append(candidate)

    # Potwierdzone odpowiedzi na początku.
    filtered.sort(
        key=lambda item: (
            not item["verified"],
            item["source"] != "memory",
            item["answer"].lower(),
        )
    )

    # Zapisujemy znalezione propozycje w trwałym słowniku.
    for candidate in filtered:
        add_word(
            polish,
            translations=[{
                "english": candidate["answer"],
                "verified": candidate["verified"],
            }],
            source=candidate["source"],
        )

    if not filtered:
        add_word(
            polish,
            translations=[],
            source="awaiting_translation",
        )

    return {
        "status": "translations_found" if filtered else "translation_needed",
        "polish": polish,
        "context": context,
        "word_count": word_count,
        "translations": filtered,
        "needs_translation": not bool(filtered),
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
    Zapisuje wynik próby i wiarygodną informację zwrotną.

    accepted=True:
        odpowiedź została zaakceptowana.

    accepted=False z correction:
        odpowiedź odrzucono i podano poprawkę.

    accepted=False bez correction:
        wynik jest niepewny.
    """
    polish = normalize(polish)
    answer = str(answer or "").strip()
    correction = str(correction or "").strip() or None

    if not polish:
        raise ValueError("Polskie słowo nie może być puste.")

    if not answer:
        raise ValueError("Odpowiedź nie może być pusta.")

    if accepted:
        result = "accepted"
    elif correction:
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

    if accepted:
        add_word(
            polish,
            translations=[{
                "english": answer,
                "verified": True,
            }],
            source="verified_feedback",
        )

    elif correction:
        add_word(
            polish,
            translations=[{
                "english": correction,
                "verified": True,
            }],
            source="verified_feedback",
        )

    return {
        "polish": polish,
        "answer": answer,
        "result": result,
        "correction": correction,
        "saved": True,
    }


if __name__ == "__main__":
    print("Instaling Helper — tryb ręczny")
    print("Wpisz 'koniec', aby zakończyć.")

    while True:
        polish = input("\nPolskie słowo: ").strip()

        if normalize(polish) == "koniec":
            break

        context = input("Kontekst (opcjonalnie): ").strip()
        count_input = input(
            "Wymagana liczba wyrazów (Enter = dowolna): "
        ).strip()

        word_count = int(count_input) if count_input.isdigit() else None

        result = analyze_word(
            polish,
            context=context,
            word_count=word_count,
        )

        print("\nStatus:", result["status"])

        for index, candidate in enumerate(result["translations"], start=1):
            print(
                f"{index}. {candidate['answer']} "
                f"| źródło: {candidate['source']} "
                f"| potwierdzone: {candidate['verified']}"
            )

        if not result["translations"]:
            print("Nie znaleziono tłumaczeń.")
            continue

        answer = input(
            "\nKtórą odpowiedź sprawdzono? Wpisz ją dokładnie: "
        ).strip()

        accepted_input = input(
            "Czy Instaling zaakceptował odpowiedź? (t/n): "
        ).strip().lower()

        if accepted_input == "t":
            learn_from_feedback(
                polish=polish,
                answer=answer,
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
                polish=polish,
                answer=answer,
                accepted=False,
                correction=correction or None,
                context=context,
                word_count=word_count,
            )
            print("Zapisano wynik próby.")
