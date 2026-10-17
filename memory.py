"""
Trwała pamięć Instaling Helper.

Zapamiętuje odpowiedzi w kontekście zdania i usuwa wpisy,
które nie wystąpiły przez 30 dni.
"""

import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path


DATA_DIR = Path("data")
MEMORY_FILE = DATA_DIR / "memory.json"
RETENTION_DAYS = 30


def now_dt():
    return datetime.now(timezone.utc)


def now():
    return now_dt().isoformat()


def parse_timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def normalize(text):
    # Do kluczy wyszukiwania normalizujemy wielkość liter.
    # Oryginalna pisownia odpowiedzi jest zachowywana osobno.
    return " ".join(str(text or "").strip().lower().split())


def load_memory():
    if not MEMORY_FILE.exists():
        return {"version": 2, "words": {}, "sessions": {}}

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
    fd, temporary_name = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_name, MEMORY_FILE)
    finally:
        if os.path.exists(temporary_name):
            os.remove(temporary_name)


def task_key(polish, context="", word_count=None):
    # Klucz zawiera kontekst, żeby np. „koncert” i „festiwal”
    # w różnych zdaniach nie dzieliły jednej odpowiedzi.
    return json.dumps(
        {
            "polish": normalize(polish),
            "context": normalize(context),
            "word_count": word_count,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def cleanup_expired_memory(retention_days=RETENTION_DAYS):
    """Usuwa hasła nieobserwowane przez określoną liczbę dni."""
    data = load_memory()
    cutoff = now_dt() - timedelta(days=retention_days)
    removed = 0

    for key, entry in list(data["words"].items()):
        last_seen = parse_timestamp(
            entry.get("last_seen_at")
            or entry.get("last_used_at")
            or entry.get("created_at")
        )

        # Stare wpisy z poprzedniej wersji mogą nie mieć daty.
        # Daty ostatniej próby używamy jako bezpiecznego fallbacku.
        if last_seen is None:
            attempts = entry.get("attempts", [])
            for attempt in reversed(attempts):
                last_seen = parse_timestamp(attempt.get("timestamp"))
                if last_seen:
                    break

        if last_seen is not None and last_seen < cutoff:
            del data["words"][key]
            removed += 1

    if removed:
        save_memory(data)
    return removed


def mark_word_seen(polish, context="", word_count=None):
    """Odświeża datę wystąpienia hasła, gdy zadanie pojawi się na stronie."""
    if not normalize(polish):
        return

    cleanup_expired_memory()
    data = load_memory()
    key = task_key(polish, context, word_count)
    entry = data["words"].get(key)

    if entry is None:
        entry = {
            "polish": str(polish).strip(),
            "context": str(context or "").strip(),
            "word_count": word_count,
            "accepted_answers": [],
            "rejected_answers": [],
            "attempts": [],
            "created_at": now(),
        }
        data["words"][key] = entry

    entry["last_seen_at"] = now()
    entry["context"] = str(context or "").strip()
    entry["word_count"] = word_count
    save_memory(data)


def record_attempt(
    polish,
    answer,
    result,
    correction=None,
    context="",
    word_count=None,
    session_id=None,
    feedback_type=None,
):
    allowed = {"accepted", "rejected_with_correction", "uncertain"}
    if result not in allowed:
        raise ValueError("Nieznany rodzaj wyniku.")
    if not normalize(polish) or not normalize(answer):
        raise ValueError("Brakuje słowa lub odpowiedzi.")

    correction = str(correction or "").strip() or None
    if result == "rejected_with_correction" and not correction:
        raise ValueError("Wymagana jest prawidłowa odpowiedź.")

    cleanup_expired_memory()
    data = load_memory()
    key = task_key(polish, context, word_count)

    if key not in data["words"]:
        data["words"][key] = {
            "polish": str(polish).strip(),
            "context": str(context or "").strip(),
            "word_count": word_count,
            "accepted_answers": [],
            "rejected_answers": [],
            "attempts": [],
            "created_at": now(),
        }

    entry = data["words"][key]
    entry.setdefault("accepted_answers", [])
    entry.setdefault("rejected_answers", [])
    entry.setdefault("attempts", [])
    entry["context"] = str(context or "").strip()
    entry["word_count"] = word_count
    entry["last_seen_at"] = now()

    entry["attempts"].append({
        "answer": answer,
        "result": result,
        "correction": correction,
        "feedback_type": feedback_type,
        "context": context,
        "session_id": session_id,
        "timestamp": now(),
    })

    if result == "accepted":
        accepted = entry["accepted_answers"]
        if answer not in accepted:
            accepted.append(answer)

    elif result == "rejected_with_correction":
        rejected = entry["rejected_answers"]
        if answer not in rejected:
            rejected.append(answer)

        # Korekta platformy jest zapisywana jako odpowiedź wskazana
        # przez Instaling, ale wywołujący może oznaczyć jej źródło.
        accepted = entry["accepted_answers"]
        if correction not in accepted:
            accepted.append(correction)

    save_memory(data)


def get_known_answers(polish, context="", word_count=None):
    cleanup_expired_memory()
    data = load_memory()
    key = task_key(polish, context, word_count)
    entry = data["words"].get(key)

    if entry is None:
        return {
            "known": False,
            "accepted_answers": [],
            "rejected_answers": [],
            "attempts": [],
        }

    # Odczyt zadania również odświeża datę jego wystąpienia.
    entry["last_seen_at"] = now()
    save_memory(data)

    return {
        "known": True,
        "accepted_answers": entry.get("accepted_answers", []),
        "rejected_answers": entry.get("rejected_answers", []),
        "attempts": entry.get("attempts", []),
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
        1 for session in data["sessions"].values()
        if session.get("status") == "completed"
    )


if __name__ == "__main__":
    print("Pamięć działa.")
    print("Usunięto starych haseł:", cleanup_expired_memory())
    print("Ukończone sesje:", completed_sessions())
