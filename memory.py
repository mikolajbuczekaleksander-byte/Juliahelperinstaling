"""
Trwała pamięć Instaling Helper.
"""

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
    return " ".join(str(text or "").strip().lower().split())


def load_memory():
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

    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(
            f"Nie można odczytać pamięci: {error}. "
            "Plik nie został nadpisany."
        ) from error


def save_memory(data):
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
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary_name, MEMORY_FILE)

    finally:
        if os.path.exists(temporary_name):
            os.remove(temporary_name)


def task_key(polish, context="", word_count=None):
    # Nie uzależniamy wyszukiwania odpowiedzi od całego
    # zdania. Kontekst zapisujemy osobno w historii prób.
    return json.dumps(
        {
            "polish": normalize(polish),
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
    allowed = {
        "accepted",
        "rejected_with_correction",
        "uncertain",
    }

    if result not in allowed:
        raise ValueError("Nieznany rodzaj wyniku.")

    if not normalize(polish) or not normalize(answer):
        raise ValueError("Brakuje słowa lub odpowiedzi.")

    correction = str(correction or "").strip() or None

    if result == "rejected_with_correction" and not correction:
        raise ValueError(
            "Wymagana jest prawidłowa odpowiedź."
        )

    data = load_memory()
    key = task_key(polish, context, word_count)

    if key not in data["words"]:
        data["words"][key] = {
            "polish": polish,
            "accepted_answers": [],
            "rejected_answers": [],
            "attempts": [],
        }

    entry = data["words"][key]

    entry.setdefault("accepted_answers", [])
    entry.setdefault("rejected_answers", [])
    entry.setdefault("attempts", [])

    entry["attempts"].append({
        "answer": answer,
        "result": result,
        "correction": correction,
        "context": context,
        "session_id": session_id,
        "timestamp": now(),
    })

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
        "accepted_answers": entry.get("accepted_answers", []),
        "rejected_answers": entry.get("rejected_answers", []),
    }


def start_session(session_id):
    data = load_memory()

    data["sessions"][session_id] = {
        "started_at": now(),
        "completed_at": None,
        "status": "started",
    }

    save_memory(data)


def finish_session(session_id):
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
    data = load_memory()

    return sum(
        1
        for session in data["sessions"].values()
        if session.get("status") == "completed"
    )


if __name__ == "__main__":
    print("Pamięć działa.")
    print("Ukończone sesje:", completed_sessions())
