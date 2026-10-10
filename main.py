from agent import analyze_word, learn_from_feedback
from memory import get_known_answers, completed_sessions
from vocabulary import cleanup_expired_words


def main():
    print("=" * 40)
    print(" INSTALING HELPER — TEST PAMIĘCI")
    print("=" * 40)

    removed = cleanup_expired_words()
    print(f"Usunięte słówka: {len(removed)}")

    polish = "słowo testowe pamięci"
    answer = "memory test word"

    print("\nTest 1: zapisywanie poprawnej odpowiedzi")

    learn_from_feedback(
        polish=polish,
        answer=answer,
        accepted=True,
        context="test automatyczny",
        session_id="test-session-001",
    )

    print("Zapisano odpowiedź.")

    print("\nTest 2: odczyt zapamiętanej odpowiedzi")

    known = get_known_answers(
        polish=polish,
        context="test automatyczny",
    )

    print("Czy słowo jest zapamiętane:", known["known"])
    print("Zaakceptowane odpowiedzi:", known["accepted_answers"])

    if answer in known["accepted_answers"]:
        print("WYNIK: pamięć działa poprawnie.")
    else:
        print("WYNIK: błąd odczytu pamięci.")

    print("\nTest 3: sprawdzenie agenta")

    result = analyze_word(
        polish,
        context="test automatyczny",
    )

    print("Status:", result["status"])

    for candidate in result["translations"]:
        print(
            "Tłumaczenie:",
            candidate["answer"],
            "| źródło:",
            candidate["source"],
            "| potwierdzone:",
            candidate["verified"],
        )

    print("\nUkończone sesje:", completed_sessions())
    print("\nTest zakończony.")


if __name__ == "__main__":
    main()
