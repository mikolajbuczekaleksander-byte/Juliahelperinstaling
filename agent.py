"""
Instaling Helper — agent tłumaczeń i uczenia się.

Priorytet:
1. Potwierdzone odpowiedzi z pamięci.
2. Odpowiedzi ze słownika.
3. Tłumacz internetowy.

Do pamięci jako poprawne trafiają wyłącznie odpowiedzi
potwierdzone przez platformę.
"""

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
    return " ".join(str(text or "").strip().lower().split())


def answer_key(text):
    return normalize(text).strip(".,!?;:\"'()[]{}")


def word_count(text):
    text = normalize(text)
    return len(text.split()) if text else 0


def add_candidate(candidates, answer, source, verified=False):
    answer = str(answer or "").strip()

    if not answer:
        return

    key = answer_key(answer)

    for item in candidates:
        if answer_key(item["answer"]) == key:
            if verified and not item["verified"]:
                item["answer"] = answer
                item["verified"] = True
                item["source"] = source
            return

    candidates.append({
        "answer": answer,
        "source": str(source or "unknown"),
        "verified": bool(verified),
    })


def analyze_word(polish, context="", word_count_required=None):
    """
    Najpierw szuka odpowiedzi w pamięci i słowniku.
    Internet wykorzystuje dopiero wtedy, gdy brakuje
    odpowiedzi w lokalnych źródłach.
    """

    polish = str(polish or "").strip()
    context = str(context or "").strip()

    if not polish:
        return {
            "status": "error",
            "translations": [],
            "needs_translation": True,
        }

    cleanup_expired_words()

    candidates = []

    # 1. Pamięć odpowiedzi potwierdzonych przez platformę.
    known = get_known_answers(
        polish,
        context=context,
        word_count=word_count_required,
    ) or {}

    rejected = {
        answer_key(x)
        for x in known.get("rejected_answers", [])
    }

    for answer in known.get("accepted_answers", []):
        if (
            word_count_required is None
            or word_count(answer) == word_count_required
        ):
            add_candidate(
                candidates,
                answer,
                "memory",
                verified=True,
            )

    # 2. Lokalny słownik.
    entry = get_word(polish)

    if entry:
        for item in entry.get("translations", []):
            answer = str(item.get("english", "")).strip()

            if not answer:
                continue

            if (
                word_count_required is not None
                and word_count(answer) != word_count_required
            ):
                continue

            add_candidate(
                candidates,
                answer,
                item.get("source", "vocabulary"),
                item.get("verified", False),
            )

    # Odrzucone, niepotwierdzone odpowiedzi nie powinny
    # być ponownie proponowane.
    candidates = [
        item for item in candidates
        if not (
            answer_key(item["answer"]) in rejected
            and not item["verified"]
        )
    ]

    # Jeśli istnieje odpowiedź z pamięci lub słownika,
    # nie trzeba pytać tłumacza internetowego.
    if not candidates:
        translated = find_translations(
            polish,
            context=context,
            word_count=word_count_required,
        ) or {}

        for item in translated.get("translations", []):
            answer = str(item.get("answer", "")).strip()

            if not answer:
                continue

            if (
                word_count_required is not None
                and word_count(answer) != word_count_required
            ):
                continue

            add_candidate(
                candidates,
                answer,
                item.get("source", "translator"),
                item.get("verified", False),
            )

    # Najpierw odpowiedzi potwierdzone.
    candidates.sort(
        key=lambda item: (
            not item["verified"],
            item["source"] != "memory",
        )
    )

    # Zapis propozycji w słowniku. Niepotwierdzone
    # tłumaczenia pozostają oznaczone jako niepotwierdzone.
    for item in candidates:
        add_word(
            polish,
            translations=[{
                "english": item["answer"],
                "verified": item["verified"],
            }],
            source=item["source"],
        )

    return {
        "status": "translations_found" if candidates else "translation_needed",
        "polish": polish,
        "context": context,
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
    feedback_type=None,
):
    """
    Zapisuje odpowiedź na podstawie wyniku platformy.

    accepted=True:
        Platforma potwierdziła odpowiedź.

    correction:
        Platforma ujawniła prawidłową odpowiedź.
    """

    polish = str(polish or "").strip()
    answer = str(answer or "").strip()
    correction = str(correction or "").strip() or None

    if not polish or not answer:
        raise ValueError("Brakuje słowa lub odpowiedzi.")

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

    # Odpowiedź wpisana przez użytkownika jest potwierdzona
    # tylko wtedy, gdy platforma ją zaakceptowała.
    if accepted:
        add_word(
            polish,
            translations=[{
                "english": answer,
                "verified": True,
            }],
            source="verified_feedback",
        )

    # Jeżeli platforma podała poprawną odpowiedź,
    # zapisujemy ją jako potwierdzoną.
    if correction:
        add_word(
            polish,
            translations=[{
                "english": correction,
                "verified": True,
            }],
            source="verified_correction",
        )

    return {
        "polish": polish,
        "answer": answer,
        "correction": correction,
        "result": result,
        "feedback_type": feedback_type or result,
        "saved": True,
    }


def get_best_candidate(polish, context="", word_count=None):
    result = analyze_word(
        polish,
        context=context,
        word_count_required=word_count,
    )

    translations = result.get("translations", [])

    return translations[0] if translations else None


if __name__ == "__main__":
    while True:
        polish = input("Polskie słowo (koniec = wyjście): ").strip()

        if normalize(polish) == "koniec":
            break

        context = input("Kontekst (opcjonalnie): ").strip()

        result = analyze_word(polish, context=context)

        for item in result["translations"]:
            print(
                f"- {item['answer']} | "
                f"źródło={item['source']} | "
                f"potwierdzone={item['verified']}"
            )

        if not result["translations"]:
            print("Nie znaleziono tłumaczenia.")
