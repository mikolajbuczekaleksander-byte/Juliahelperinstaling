            return None

        if payload.get("responseStatus") not in (None, 200):
            logger.warning("MyMemory API status: %s", payload.get("responseStatus"))
            return None

        return answer

    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Internetowy tłumacz jest niedostępny: %s", exc)
        return None


def find_translations(polish, context="", word_count=None):
    """
    Kolejność źródeł:
    1. pamięć zaakceptowanych odpowiedzi,
    2. lokalny dictionary.json,
    3. internetowy tłumacz jako awaryjne źródło.

    Propozycja z tłumacza internetowego nie jest gwarancją, że InstaLing
    zaakceptuje daną odpowiedź.
    """
    known = get_known_answers(
        polish,
        context=context,
        word_count=word_count,
    )

    translations = []
    seen = set()

    def add_translation(answer, source, verified):
        answer = str(answer or "").strip()
        if not answer:
            return

        count = len(answer.split())
        if word_count is not None and count != word_count:
            return

        key = normalize(answer)
        if key in seen:
            return

        seen.add(key)
        translations.append({
            "answer": answer,
            "source": source,
            "verified": verified,
        })

    # 1. Pamięć — najwyższy priorytet.
    for answer in known.get("accepted_answers", []):
        add_translation(answer, "memory", True)

    # 2. Słownik lokalny.
    dictionary = load_dictionary()
    entry = dictionary.get(normalize(polish), [])

    if isinstance(entry, str):
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

    # Odfiltruj wcześniej odrzucone odpowiedzi przed decyzją o tłumaczeniu online.
    rejected = {
        normalize(answer)
        for answer in known.get("rejected_answers", [])
    }
    translations = [
        item for item in translations
        if normalize(item["answer"]) not in rejected
    ]

    # 3. Internet dopiero wtedy, gdy pamięć i słownik nie dały odpowiedzi.
    if not translations:
        online_answer = translate_online(polish, context=context)
        if online_answer and normalize(online_answer) not in rejected:
            add_translation(online_answer, "online", False)

    return {
        "known": known.get("known", False),
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
