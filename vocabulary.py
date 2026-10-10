import json
import os
import tempfile

from datetime import datetime, timedelta, timezone
from pathlib import Path


DATA_DIR = Path("data")
VOCABULARY_FILE = DATA_DIR / "vocabulary.json"

EXPIRATION_DAYS = 30


def now():
    return datetime.now(timezone.utc)


def timestamp():
    return now().isoformat()


def normalize(text):
    return " ".join(str(text).strip().lower().split())


def load_vocabulary():
    """Wczytuje słownik. Tworzy pusty, jeśli jeszcze nie istnieje."""
    if not VOCABULARY_FILE.exists():
        return {}

    try:
        with VOCABULARY_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError("Nieprawidłowy format słownika.")

        return data

    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            "Nie można odczytać słownika. "
            "Plik nie został automatycznie nadpisany."
        ) from exc


def save_vocabulary(data):
    """Zapisuje słownik, ograniczając ryzyko uszkodzenia pliku."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    fd, temporary_name = tempfile.mkstemp(
        dir=DATA_DIR,
        suffix=".tmp",
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(temporary_name, VOCABULARY_FILE)

    finally:
        if os.path.exists(temporary_name):
            os.remove(temporary_name)


def add_word(polish, translations=None, source="unknown"):
    """
    Dodaje nowe słowo lub aktualizuje datę ostatniego wystąpienia.

    translations: lista angielskich propozycji.
    source: np. dictionary, web lub verified_feedback.
    """
    polish = normalize(polish)

    if not polish:
        raise ValueError("Polskie słowo nie może być puste.")

    data = load_vocabulary()
    key = polish

    if key not in data:
        data[key] = {
            "translations": [],
            "first_seen": timestamp(),
            "last_seen": timestamp(),
            "sources": [],
        }
    else:
        data[key]["last_seen"] = timestamp()

    entry = data[key]

    if source not in entry["sources"]:
        entry["sources"].append(source)

    for translation in translations or []:
        if isinstance(translation, str):
            english = translation.strip()
            verified = source == "verified_feedback"
        else:
            english = str(
                translation.get("english", "")
            ).strip()

            verified = bool(
                translation.get("verified", False)
            )

        if not english:
            continue

        existing = next(
            (
                item
                for item in entry["translations"]
                if normalize(item["english"]) == normalize(english)
            ),
            None,
        )

        if existing is None:
            entry["translations"].append({
                "english": english,
                "verified": verified,
                "source": source,
            })
        elif verified:
            existing["verified"] = True
            existing["source"] = source

    save_vocabulary(data)


def get_word(polish):
    """Zwraca zapisane informacje o słowie."""
    data = load_vocabulary()
    return data.get(normalize(polish))


def cleanup_expired_words():
    """
    Całkowicie usuwa wpisy, których nie widziano przez 30 dni.

    Brak prawidłowej daty oznacza, że wpis zostaje zachowany.
    """
    data = load_vocabulary()
    current_time = now()
    removed = []

    for polish, entry in list(data.items()):
        try:
            last_seen = datetime.fromisoformat(
                entry["last_seen"]
            )

            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(
                    tzinfo=timezone.utc
                )

        except (KeyError, ValueError, TypeError):
            continue

        if current_time - last_seen > timedelta(
            days=EXPIRATION_DAYS
        ):
            removed.append(polish)
            del data[polish]

    if removed:
        save_vocabulary(data)

    return removed


if __name__ == "__main__":
    removed = cleanup_expired_words()

    print("Słownik został sprawdzony.")
    print("Usunięte przeterminowane słówka:", len(removed))
    print("Pozostałe słówka:", len(load_vocabulary()))
