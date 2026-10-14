"""
Instaling Helper — agent tłumaczeń i uczenia się.

Funkcje:
- wyszukiwanie odpowiedzi w pamięci i słowniku;
- pobieranie propozycji tłumaczeń;
- uwzględnianie kontekstu zdania;
- zachowywanie oryginalnej pisowni, w tym wielkości liter;
- zapisywanie zaakceptowanych odpowiedzi;
- zapisywanie odrzuconych odpowiedzi i poprawek;
- rozróżnianie odpowiedzi potwierdzonych i niepotwierdzonych;
- obsługa synonimów;
- unikanie uznawania niepewnych wyników za poprawne.

Moduł nie steruje przeglądarką.
Integracja z instaling_browser.py następuje osobno.
"""

import re

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
    """
    Normalizuje tekst do porównywania.

    Nie należy używać tej funkcji do zmiany pisowni
    odpowiedzi wpisywanej na platformie.
    """
    return " ".join(
        str(text or "").strip().lower().split()
    )


def get_word_count(text):
    """Zwraca liczbę wyrazów w odpowiedzi."""
    normalized = normalize(text)

    return len(normalized.split()) if normalized else 0


def _answer_key(text):
    """
    Przygotowuje klucz porównawczy.

    Wielkość liter jest ignorowana wyłącznie przy wyszukiwaniu
    duplikatów. Oryginalna odpowiedź pozostaje bez zmian.
    """
    return normalize(text).strip(
        ".,!?;:\"'()[]{}"
    )


def _add_candidate(
    candidates,
    answer,
    source,
    verified=False,
):
    """Dodaje propozycję tłumaczenia bez duplikatów."""

    answer = str(answer or "").strip()

    if not answer:
        return

    key = _answer_key(answer)

    for candidate in candidates:
        if _answer_key(candidate["answer"]) == key:

            if verified:
                candidate["verified"] = True
                candidate["source"] = source

                # Zachowujemy dokładną pisownię
                # potwierdzonej odpowiedzi.
                candidate["answer"] = answer

            return

    candidates.append({
        "answer": answer,
        "source": str(source or "unknown"),
        "verified": bool(verified),
    })


def _filter_by_word_count(candidates, word_count):
    """Odrzuca propozycje o niewłaściwej liczbie wyrazów."""

    if word_count is None:
        return candidates

    return [
        candidate
        for candidate in candidates
        if get_word_count(candidate["answer"]) == word_count
    ]


def analyze_word(
    polish,
    context="",
    word_count=None,
):
    """
    Znajduje możliwe tłumaczenia polskiego słowa.

    Priorytet:
    1. Odpowiedzi potwierdzone w pamięci.
    2. Odpowiedzi zapisane w słowniku.
    3. Propozycje translatora.

    Zwrócone tłumaczenie nie jest automatycznie uznawane
    za poprawne na InstaLing. Potwierdzenie musi pochodzić
    z rzeczywistego wyniku platformy.
    """

    polish = str(polish or "").strip()
    context = str(context or "").strip()

    if not polish:
        return {
            "status": "error",
            "message": "Nie podano polskiego słowa.",
            "polish": "",
            "context": context,
            "word_count": word_count,
            "translations": [],
            "needs_translation": True,
        }

    if word_count is not None:
        if (
            not isinstance(word_count, int)
            or isinstance(word_count, bool)
            or word_count < 1
        ):
            return {
                "status": "error",
                "message": (
                    "Liczba wyrazów musi być "
                    "dodatnią liczbą całkowitą."
                ),
                "polish": polish,
                "context": context,
                "word_count": word_count,
                "translations": [],
                "needs_translation": True,
            }

    cleanup_expired_words()

    known = get_known_answers(
        polish,
        context=context,
        word_count=word_count,
    ) or {}

    accepted_answers = known.get(
        "accepted_answers",
        [],
    )

    rejected_answers = known.get(
        "rejected_answers",
        [],
    )

    rejected = {
        _answer_key(answer)
        for answer in rejected_answers
    }

    candidates = []

    # 1. Odpowiedzi wcześniej zaakceptowane.
    for answer in accepted_answers:
        answer = str(answer or "").strip()

        if not answer:
            continue

        if (
            word_count is not None
            and get_word_count(answer) != word_count
        ):
            continue

        _add_candidate(
            candidates,
            answer,
            source="memory",
            verified=True,
        )

    # 2. Istniejący słownik.
    existing = get_word(polish)

    if existing:
        for item in existing.get("translations", []):

            answer = str(
                item.get("english", "")
            ).strip()

            if not answer:
                continue

            if (
                word_count is not None
                and get_word_count(answer) != word_count
            ):
                continue

            _add_candidate(
                candidates,
                answer,
                source=item.get(
                    "source",
                    "vocabulary",
                ),
                verified=item.get(
                    "verified",
                    False,
                ),
            )

    # 3. Propozycje translatora.
    translated = find_translations(
        polish,
        context=context,
        word_count=word_count,
    ) or {}

    for item in translated.get(
        "translations",
        [],
    ):

        answer = str(
            item.get("answer", "")
        ).strip()

        if not answer:
            continue

        if (
            word_count is not None
            and get_word_count(answer) != word_count
        ):
            continue

        _add_candidate(
            candidates,
            answer,
            source=item.get(
                "source",
                "translator",
            ),
            verified=item.get(
                "verified",
                False,
            ),
        )

    # Odrzucone odpowiedzi bez potwierdzenia
    # nie powinny być ponownie traktowane jak zwykłe
    # propozycje.
    filtered = []

    for candidate in candidates:

        key = _answer_key(candidate["answer"])

        if key in rejected and not candidate["verified"]:
            continue

        filtered.append(candidate)

    # Odpowiedzi potwierdzone wcześniej mają pierwszeństwo.
    filtered.sort(
        key=lambda item: (
            not item["verified"],
            item["source"] != "memory",
        )
    )

    # Zapisujemy propozycje w istniejącym słowniku.
    # Nie oznaczamy niepotwierdzonych tłumaczeń jako pewnych.
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
        "status": (
            "translations_found"
            if filtered
            else "translation_needed"
        ),
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
    feedback_type=None,
):
    """
    Zapisuje wynik odpowiedzi.

    accepted=True:
        Platforma potwierdziła poprawność.

    accepted=False i correction:
        Platforma odrzuciła odpowiedź i ujawniła poprawkę.

    accepted=False bez correction:
        Odpowiedź odrzucono, ale poprawna forma nie jest znana.

    feedback_type:
        Opcjonalny rodzaj informacji zwrotnej, np.:
        "correct", "incorrect", "synonym",
        "capitalization", "unknown".

    UWAGA:
    Synonim nie jest automatycznie traktowany jako poprawna
    odpowiedź. Dokładne zachowanie zależy od komunikatu
    rzeczywiście odczytanego z platformy.
    """

    polish = str(polish or "").strip()
    answer = str(answer or "").strip()
    correction = str(
        correction or ""
    ).strip() or None

    context = str(context or "").strip()

    if not polish:
        raise ValueError(
            "Polskie słowo nie może być puste."
        )

    if not answer:
        raise ValueError(
            "Odpowiedź nie może być pusta."
        )

    if accepted:
        result = "accepted"

    elif correction:
        result = "rejected_with_correction"

    else:
        result = "uncertain"

    # Zachowujemy istniejący interfejs memory.py.
    record_attempt(
        polish=polish,
        answer=answer,
        result=result,
        correction=correction,
        context=context,
        word_count=word_count,
        session_id=session_id,
    )

    # Do słownika jako potwierdzone trafiają wyłącznie
    # odpowiedzi rzeczywiście zaakceptowane albo poprawki
    # wyraźnie ujawnione przez platformę.
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
        "feedback_type": feedback_type or result,
        "saved": True,
    }


def get_best_candidate(
    polish,
    context="",
    word_count=None,
):
    """
    Zwraca najlepszą propozycję bez uznawania jej
    za poprawną przed sprawdzeniem przez platformę.

    Zwraca:
        dict z answer, source i verified;
        albo None, gdy nie ma propozycji.
    """

    result = analyze_word(
        polish,
        context=context,
        word_count=word_count,
    )

    candidates = result.get(
        "translations",
        [],
    )

    if not candidates:
        return None

    return candidates[0]


if __name__ == "__main__":

    print("Instaling Helper — tryb ręczny")
    print(
        "Wpisz 'koniec', aby zakończyć."
    )

    while True:

        polish = input(
            "\nPolskie słowo: "
        ).strip()

        if normalize(polish) == "koniec":
            break

        context = input(
            "Kontekst zdania (opcjonalnie): "
        ).strip()

        count_input = input(
            "Wymagana liczba wyrazów "
            "(Enter = dowolna): "
        ).strip()

        if count_input:
            try:
                word_count = int(count_input)

                if word_count < 1:
                    print(
                        "Liczba wyrazów musi być dodatnia."
                    )
                    continue

            except ValueError:
                print(
                    "Nieprawidłowa liczba wyrazów."
                )
                continue

        else:
            word_count = None

        result = analyze_word(
            polish,
            context=context,
            word_count=word_count,
        )

        print(
            "\nStatus:",
            result["status"],
        )

        for index, candidate in enumerate(
            result["translations"],
            start=1,
        ):
            print(
                f"{index}. {candidate['answer']} "
                f"| źródło: {candidate['source']} "
                f"| potwierdzone: "
                f"{candidate['verified']}"
            )

        if not result["translations"]:
            print(
                "Nie znaleziono tłumaczeń."
            )
            continue

        answer = input(
            "\nWpisz odpowiedź, którą sprawdzono: "
        ).strip()

        accepted_input = input(
            "Czy platforma zaakceptowała odpowiedź? "
            "(t/n): "
        ).strip().lower()

        if accepted_input == "t":

            feedback = learn_from_feedback(
                polish=polish,
                answer=answer,
                accepted=True,
                context=context,
                word_count=word_count,
                feedback_type="correct",
            )

            print(
                "Zapisano zaakceptowaną odpowiedź:",
                feedback["answer"],
            )

        elif accepted_input == "n":

            correction = input(
                "Poprawna odpowiedź pokazana przez "
                "platformę (Enter, jeśli nieznana): "
            ).strip()

            feedback_type = input(
                "Rodzaj wyniku "
                "(incorrect/synonym/capitalization/unknown): "
            ).strip().lower()

            feedback = learn_from_feedback(
                polish=polish,
                answer=answer,
                accepted=False,
                correction=correction or None,
                context=context,
                word_count=word_count,
                feedback_type=feedback_type or "incorrect",
            )

            print(
                "Zapisano wynik:",
                feedback["result"],
            )

        else:
            print(
                "Nieznany wybór. "
                "Nie zapisano wyniku jako poprawnego."
            )
