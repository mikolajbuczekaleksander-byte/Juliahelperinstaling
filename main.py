from agent import analyze_word
from vocabulary import cleanup_expired_words


def main():
    print("================================")
    print(" INSTALING HELPER — TEST AGENTA")
    print("================================")

    removed = cleanup_expired_words()
    print(f"Usunięte nieużywane słówka: {len(removed)}")

    test_words = [
        "pokaz mody",
        "festiwal",
        "żongler",
        "trapez",
    ]

    for polish in test_words:
        print(f"\nAnalizuję: {polish}")

        result = analyze_word(polish)

        print("Status:", result["status"])

        for translation in result["translations"]:
            print(
                "Tłumaczenie:",
                translation["answer"],
                "| potwierdzone:",
                translation["verified"],
            )

        if result["needs_translation"]:
            print("Brak tłumaczenia — potrzebne dodatkowe źródło.")

    print("\nTest zakończony.")


if __name__ == "__main__":
    main()
