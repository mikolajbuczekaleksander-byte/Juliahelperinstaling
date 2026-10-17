"""
Instaling Helper — agent tłumaczeń i uczenia się.

Priorytet:
1. Potwierdzone odpowiedzi dla tego samego kontekstu.
2. Tłumaczenia uwzględniające kontekst.
3. Lokalny słownik jako dodatkowa podpowiedź.

Odpowiedź z pamięci jest kontekstowa. Informacja zwrotna
Instalinga ma pierwszeństwo przed niepotwierdzonym tłumaczeniem.
"""

from translator import find_translations
from vocabulary import add_word, get_word, cleanup_expired_words
from memory import get_known_answers, record_attempt, mark_word_seen


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
    Analizuje polską podpowiedź w kontekście całego zdania.
    Jeśli istnieje potwierdzona odpowiedź dla dokładnego kontekstu,
    zwraca ją jako najwyższy priorytet.
    """
    polish = str(polish or "").strip()
    context = str(context or "").strip()

    if not polish:
        return {
            "status": "error",
            "polish": polish,
            "context": context,
            "translations": [],
            "needs_translation": True,
        }

    cleanup_expired_words()
    mark_word_seen(polish, context=context, word_count=word_count_required)

    known = get_known_answers(
        polish,
        context=context,
        word_count=word_count_required,
    ) or {}

    rejected = {answer_key(x) for x in known.get("rejected_answers", [])}
    candidates = []

    # Potwierdzone odpowiedzi tylko dla tego kontekstu.
    for answer in known.get("accepted_answers", []):
        if (
            word_count_required is None
            or word_count(answer) == word_count_required
        ):
            add_candidate(candidates, answer, "memory", verified=True)

    # Jeśli mamy odpowiedź potwierdzoną dla dokładnego kontekstu,
    # nie pozwalamy ogólnemu tłumaczeniu jej nadpisać.
    has_context_verified_answer = any(item["verified"] for item in candidates)

    # W przeciwnym razie pytamy tłumacza z kontekstem, nawet jeśli
    # lokalny słownik zawiera ogólne tłumaczenie tego polskiego hasła.
    if not has_context_verified_answer:
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

    # Lokalny słownik jest dodatkowym źródłem, a nie dowodem,
    # że tłumaczenie pasuje do aktualnego zdania.
    entry = get_word(polish)
    if entry and not has_context_verified_answer:
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

    # Odpowiedzi odrzucone w tym kontekście nie powinny być proponowane,
    # chyba że są oznaczone jako potwierdzone w pamięci.
    candidates = [
        item for item in candidates
        if item["verified"] or answer_key(item["answer"]) not in rejected
    ]

    candidates.sort(
        key=lambda item: (
            not item["verified"],
            item["source"] != "memory",
            item["source"] != "translator",
        )
    )

    # Przechowujemy podpowiedzi w słowniku, ale status verified
    # pozostaje prawdziwy tylko dla odpowiedzi potwierdzonych.
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
    Zapisuje informację zwrotną Instalinga.
    feedback_type może zawierać np. „Synonim”, „Literówka”
    lub „Zła wielkość liter”.
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
        feedback_type=feedback_type,
    )

    # Odpowiedź wpisana przez użytkownika jest potwierdzona tylko
    # wtedy, gdy platforma zaakceptowała ją w tym konkretnym kontekście.
    if accepted:
        add_word(
            polish,
            translations=[{
                "english": answer,
                "verified": True,
            }],
            source="verified_feedback",
        )

    # Korekta ze strony jest zapisywana jako informacja wskazana
    # przez platformę. Przy kolejnej próbie kontekstowa pamięć ją zwróci.
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
        context = input("Zdanie/kontekst (opcjonalnie): ").strip()
        result = analyze_word(polish, context=context)
        for item in result["translations"]:
            print(
                f"- {item['answer']} | źródło={item['source']} | "
                f"potwierdzone={item['verified']}"
            )
        if not result["translations"]:
            print("Nie znaleziono tłumaczenia.")
