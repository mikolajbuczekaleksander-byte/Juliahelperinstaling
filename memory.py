import json
import os
import tempfile

from datetime import datetime, timezone
from pathlib import Path


DATA_DIR = Path("data")
MEMORY_FILE = DATA_DIR / "memory.json"


def now():
    return datetime.now(timezone.utc).isoformat()


def normalize(text):
    return " ".join(str(text).strip().lower().split())


def load_memory():
    """Wczytuje pamięć albo tworzy pustą."""
    if not MEMORY_FILE.exists():
        return {
            "version": 1,
            "words": {},
            "sessions": {},
        }

    try:
        with MEMORY_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        data.setdefault("version", 1)
        data.setdefault("words", {})
        data.setdefault("sessions", {})

        return data

    except (json.JSONDecodeError, OSError):
        raise RuntimeError(
            "Nie można odczytać pamięci. "
            "Plik nie został automatycznie nadpisany."
        )


def save_memory(data):
    """Zapisuje dane bezpiecznie, aby ograniczyć ryzyko uszkodzenia."""
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

        os.replace(temporary_name, MEMORY_FILE)

    finally:
        if os.path.exists(temporary_name):
            os.remove(temporary_name)


def task_key(polish, context="", word_count=None):
    """Rozróżnia znaczenia i wskazówki dotyczące długości odpowiedzi."""
    return json.dumps(
        {
            "polish": normalize(polish),
            "context": normalize(context),
            "word_count": word_count,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def record_attempt(
    polish,
    answer,
    result,
    correction=None,
    context="",
    word_count=None,
    session_id=None,
):
    """
    Zapisuje wynik próby.

    result:
      accepted
      rejected_with_correction
      uncertain
    """
    allowed_results = {
        "accepted",
        "rejected_with_correction",
        "uncertain",
    }

    if result not in allowed_results:
        raise ValueError("Nieznany rodzaj wyniku.")

    if not normalize(polish):
        raise ValueError("Brakuje polskiego słowa.")

    if not normalize(answer):
        raise ValueError("Brakuje odpowiedzi.")

    if result == "rejected_with_correction":
        if not correction or not normalize(correction):
            raise ValueError(
                "Przy tym wyniku trzeba podać poprawną odpowiedź."
            )

    data = load_memory()

    key = task_key(polish, context, word_count)

    if key not in data["words"]:
        data["words"][key] = {
            "polish": polish,
            "context": context,
            "word_count": word_count,
            "accepted_answers": [],
            "rejected_answers": [],
            "attempts": [],
        }

    entry = data["words"][key]

    attempt = {
        "answer": answer,
        "result": result,
        "correction": correction,
        "session_id": session_id,
        "timestamp": now(),
    }

    entry["attempts"].append(attempt)

    if result == "accepted":
        accepted = entry["accepted_answers"]

        if normalize(answer) not in {
            normalize(item) for item in accepted
        }:
            accepted.append(answer)

    elif result == "rejected_with_correction":
        rejected = entry["rejected_answers"]

        if normalize(answer) not in {
            normalize(item) for item in rejected
        }:
            rejected.append(answer)

        accepted = entry["accepted_answers"]

        if normalize(correction) not in {
            normalize(item) for item in accepted
        }:
            accepted.append(correction)

    save_memory(data)


def get_known_answers(
    polish,
    context="",
    word_count=None,
):
    """Zwraca informacje zapamiętane dla danego zadania."""
    data = load_memory()

    key = task_key(polish, context, word_count)
    entry = data["words"].get(key)

    if entry is None:
        return {
            "known": False,
            "accepted_answers": [],
            "rejected_answers": [],
        }

    return {
        "known": True,
        "accepted_answers": entry["accepted_answers"],
        "rejected_answers": entry["rejected_answers"],
    }


def start_session(session_id):
    """Rejestruje rozpoczęcie sesji."""
    data = load_memory()

    data["sessions"][session_id] = {
        "started_at": now(),
        "completed_at": None,
        "status": "started",
    }

    save_memory(data)


def finish_session(session_id):
    """
    Oznacza sesję jako ukończoną.
    Wywołuj dopiero po potwierdzeniu zakończenia.
    """
    data = load_memory()

    session = data["sessions"].get(session_id)

    if session is None:
        raise ValueError("Nie znaleziono rozpoczętej sesji.")

    if session["status"] == "completed":
        return

    session["status"] = "completed"
    session["completed_at"] = now()

    save_memory(data)


def completed_sessions():
    """Zwraca liczbę zapisanych, ukończonych sesji."""
    data = load_memory()

    return sum(
        1
        for session in data["sessions"].values()
        if session["status"] == "completed"
    )


if __name__ == "__main__":
    print("Test pamięci agenta")

    print(
        "Liczba zapisanych ukończonych sesji:",
        completed_sessions(),
    )

    print("Pamięć działa.")
