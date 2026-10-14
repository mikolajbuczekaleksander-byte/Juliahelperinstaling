"""
Instaling Helper — automatyzacja sesji.

Funkcje:
- logowanie przy użyciu GitHub Secrets;
- otwieranie sesji;
- rozpoznawanie zadań;
- wyszukiwanie tłumaczeń przez agenta;
- wpisywanie propozycji odpowiedzi;
- zapisywanie obserwacji;
- rozpoznawanie informacji zwrotnych;
- zapisywanie potwierdzonych odpowiedzi;
- ograniczona liczba zadań w jednym uruchomieniu.

Selektory i komunikaty należy zweryfikować
na podstawie rzeczywistego ekranu InstaLing.
"""

import os
import re
import json
import logging

from pathlib import Path
from datetime import datetime, timezone

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from agent import analyze_word, learn_from_feedback
from memory import (
    start_session,
    finish_session,
    record_attempt,
)


INSTALING_URL = "https://instaling.pl/"

ARTIFACTS_DIR = Path("artifacts")
VIDEO_DIR = ARTIFACTS_DIR / "video"
DATA_DIR = Path("data")

# Limit zabezpiecza przed niekontrolowaną pętlą.
MAX_TEST_TASKS = 3

# Bez potwierdzenia selektora przycisku sprawdzania
# program nie wysyła odpowiedzi automatycznie.
AUTO_SUBMIT = False


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat()


def normalize(text):
    return " ".join(str(text).strip().lower().split())


def setup_logging():
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("instaling_helper")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    file_handler = logging.FileHandler(
        ARTIFACTS_DIR / "run.log",
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    logger.addHandler(console)
    logger.addHandler(file_handler)

    return logger


class InstalingBrowser:

    def __init__(self, headless=True):
        self.headless = headless

        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

        self.logger = setup_logging()

        self.session_id = datetime.now(
            timezone.utc
        ).strftime("%Y%m%d_%H%M%S")

        self.task_count = 0
        self.session_started = False
        self.session_completed = False

    def start(self):
        """Uruchamia przeglądarkę i nagrywanie."""

        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)

        self.playwright = sync_playwright().start()

        self.browser = self.playwright.chromium.launch(
            headless=self.headless
        )

        self.context = self.browser.new_context(
            locale="pl-PL",
            viewport={
                "width": 1365,
                "height": 900,
            },
            record_video_dir=str(VIDEO_DIR),
            record_video_size={
                "width": 1365,
                "height": 900,
            },
        )

        self.page = self.context.new_page()

        self.logger.info("Uruchomiono Chromium.")

        self.page.goto(
            INSTALING_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        self.page.wait_for_timeout(1500)

        self.logger.info(
            "Otwarto stronę: %s",
            self.page.url,
        )

        return self.page.url

    def save_screenshot(self, name):
        """Zapisuje zrzut ekranu."""

        if not self.page:
            return

        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

        path = ARTIFACTS_DIR / name

        try:
            self.page.screenshot(
                path=str(path),
                full_page=True,
            )

            self.logger.info(
                "Zapisano zrzut ekranu: %s",
                path,
            )

        except Exception as error:
            self.logger.warning(
                "Nie udało się zapisać zrzutu: %s",
                error,
            )

    def inspect_page(self):
        """Pobiera podstawowe informacje o stronie."""

        return {
            "url": self.page.url,
            "title": self.page.title(),
            "text": self.page.locator(
                "body"
            ).inner_text()[:12000],
        }

    def login(self):
        """Loguje się przy użyciu GitHub Secrets."""

        login = os.environ.get(
            "INSTALING_LOGIN", ""
        ).strip()

        password = os.environ.get(
            "INSTALING_PASSWORD", ""
        )

        if not login or not password:
            raise RuntimeError(
                "Brakuje sekretów INSTALING_LOGIN "
                "lub INSTALING_PASSWORD."
            )

        page = self.page

        password_fields = page.locator(
            'input[type="password"]'
        )

        if password_fields.count() == 0:
            links = page.get_by_role(
                "link",
                name=re.compile(
                    r"zaloguj|logowanie",
                    re.IGNORECASE,
                ),
            )

            if links.count():
                links.first.click()
                page.wait_for_timeout(1000)

        password_fields = page.locator(
            'input[type="password"]'
        )

        if password_fields.count() == 0:
            self.save_screenshot("login_error.png")

            raise RuntimeError(
                "Nie znaleziono formularza logowania."
            )

        fields = page.locator(
            'input:not([type="password"]):'
            'not([type="hidden"]):'
            'not([type="submit"]):'
            'not([type="button"])'
        )

        login_field = None

        for index in range(fields.count()):
            field = fields.nth(index)

            if not field.is_visible() or not field.is_enabled():
                continue

            description = " ".join(
                str(field.get_attribute(attr) or "")
                for attr in (
                    "name",
                    "id",
                    "placeholder",
                    "aria-label",
                    "type",
                )
            ).lower()

            if any(word in description for word in (
                "login",
                "email",
                "user",
                "użytkownik",
                "identyfikator",
            )):
                login_field = field
                break

        if login_field is None:
            for index in range(fields.count()):
                field = fields.nth(index)

                if field.is_visible() and field.is_enabled():
                    login_field = field
                    break

        if login_field is None:
            self.save_screenshot("login_error.png")

            raise RuntimeError(
                "Nie znaleziono pola loginu."
            )

        login_field.fill(login)
        password_fields.first.fill(password)

        submit = page.locator(
            'button[type="submit"], input[type="submit"]'
        )

        if submit.count() and submit.first.is_visible():
            submit.first.click()
        else:
            button = page.get_by_role(
                "button",
                name=re.compile(
                    r"zaloguj|logowanie|login",
                    re.IGNORECASE,
                ),
            )

            if not button.count():
                self.save_screenshot("login_error.png")

                raise RuntimeError(
                    "Nie znaleziono przycisku logowania."
                )

            button.first.click()

        try:
            page.wait_for_load_state(
                "domcontentloaded",
                timeout=15000,
            )
        except PlaywrightTimeoutError:
            pass

        page.wait_for_timeout(2000)

        result = self.inspect_page()
        text = result["text"].casefold()

        errors = (
            "nieprawidłowe hasło",
            "nieprawidlowe haslo",
            "błędne hasło",
            "bledne haslo",
            "nieprawidłowy login",
            "niepoprawne hasło",
        )

        if any(message in text for message in errors):
            self.save_screenshot("login_error.png")

            raise RuntimeError(
                "Platforma zgłosiła błąd logowania."
            )

        if page.locator(
            'input[type="password"]'
        ).count():
            self.save_screenshot("login_error.png")

            raise RuntimeError(
                "Nie potwierdzono zakończenia logowania."
            )

        self.logger.info("Logowanie zakończone.")

        self.save_screenshot("after_login.png")

        return result

    def find_session_action(self):
        """Wyszukuje przyciski przejścia do sesji."""

        elements = self.page.locator(
            "a, button, input[type=button], "
            "input[type=submit], [role=button]"
        )

        priorities = (
            ("kontynuuj sesję", 100),
            ("kontynuuj sesje", 100),
            ("dokończ sesję", 95),
            ("dokoncz sesje", 95),
            ("rozpocznij kolejną sesję", 90),
            ("rozpocznij kolejna sesje", 90),
            ("rozpocznij sesję", 85),
            ("rozpocznij sesje", 85),
            ("następna sesja", 70),
            ("nastepna sesja", 70),
            ("kontynuuj", 60),
        )

        candidates = []

        for index in range(elements.count()):
            element = elements.nth(index)

            try:
                if not element.is_visible():
                    continue

                if not element.is_enabled():
                    continue

                text = str(
                    element.inner_text(timeout=1000) or ""
                ).strip()

                value = str(
                    element.get_attribute("value") or ""
                ).strip()

                description = f"{text} {value}".casefold()

                score = 0

                for phrase, priority in priorities:
                    if phrase in description:
                        score = max(score, priority)

                if score:
                    candidates.append(
                        (score, element, text or value)
                    )

            except Exception as error:
                self.logger.debug(
                    "Pominięto element: %s",
                    error,
                )

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        for score, _, description in candidates[:5]:
            self.logger.info(
                "Przycisk sesji: %s | priorytet=%s",
                description[:120],
                score,
            )

        return candidates[0][1] if candidates else None

    def has_answer_field(self):
        """Sprawdza, czy widoczne jest pole odpowiedzi."""

        answer = self.page.locator("#answer")

        return bool(
            answer.count()
            and answer.first.is_visible()
            and answer.first.is_enabled()
        )

    def open_session(self):
        """Próbuje przejść do ekranu aktywnego zadania."""

        for attempt in range(5):
            state = self.inspect_page()

            self.logger.info(
                "Etap sesji %s:\n%s",
                attempt + 1,
                state["text"][:2000],
            )

            if self.has_answer_field():
                self.logger.info(
                    "Wykryto aktywne pole odpowiedzi."
                )

                self.save_screenshot("session_ready.png")

                if not self.session_started:
                    start_session(self.session_id)
                    self.session_started = True

                return True

            action = self.find_session_action()

            if action is None:
                self.save_screenshot(
                    f"session_step_{attempt + 1}.png"
                )

                self.logger.warning(
                    "Nie znaleziono przycisku sesji."
                )

                return False

            try:
                action.click(timeout=5000)
                self.page.wait_for_timeout(1500)

            except Exception as error:
                self.logger.warning(
                    "Nie udało się kliknąć przycisku: %s",
                    error,
                )

                return False

        return self.has_answer_field()

    def read_task(self):
        """Odczytuje aktualny ekran zadania."""

        if not self.has_answer_field():
            return None

        return {
            "text": self.page.locator(
                "body"
            ).inner_text()[:12000],
            "url": self.page.url,
            "answer_selector": "#answer",
        }

    def extract_polish_word(self, task):
        """
        Próbuje wyodrębnić polskie słowo.
        Nie zgaduje, jeśli nie rozpoznaje wzorca.
        """

        text = task["text"]

        patterns = (
            r"(?:przetłumacz|przetlumacz)\s*:?\s*([^\n]+)",
            r"(?:po polsku|polskie słowo|polskie slowo)"
            r"\s*:?\s*([^\n]+)",
        )

        for pattern in patterns:
            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                candidate = match.group(1).strip()

                if candidate and len(candidate) < 100:
                    return candidate

        return None

    def save_observation(self, observation):
        """Dopisuje obserwację do pliku JSONL."""

        DATA_DIR.mkdir(parents=True, exist_ok=True)

        path = DATA_DIR / "observations.jsonl"

        with path.open("a", encoding="utf-8") as file:
            file.write(
                json.dumps(
                    observation,
                    ensure_ascii=False,
                ) + "\n"
            )

    def find_submit_button(self):
        """
        Wyszukuje możliwy przycisk sprawdzania odpowiedzi.

        To diagnostyczny mechanizm. Nie używamy go
        do automatycznego wysyłania bez weryfikacji.
        """

        patterns = (
            r"^sprawdź$",
            r"^sprawdz$",
            r"^zatwierdź$",
            r"^zatwierdz$",
            r"^dalej$",
            r"^ok$",
        )

        for pattern in patterns:
            button = self.page.get_by_role(
                "button",
                name=re.compile(
                    pattern,
                    re.IGNORECASE,
                ),
            )

            if button.count():
                for index in range(button.count()):
                    candidate = button.nth(index)

                    if (
                        candidate.is_visible()
                        and candidate.is_enabled()
                    ):
                        return candidate

        elements = self.page.locator(
            'input[type="submit"], '
            'input[type="button"]'
        )

        for index in range(elements.count()):
            candidate = elements.nth(index)

            if not candidate.is_visible():
                continue

            label = " ".join(
                str(candidate.get_attribute(attr) or "")
                for attr in ("value", "aria-label", "title")
            ).casefold()

            if any(
                word in label
                for word in ("sprawdź", "sprawdz", "dalej")
            ):
                return candidate

        return None

    def inspect_answer_result(self):
        """Próbuje odczytać komunikat zwrotny."""

        text = self.page.locator("body").inner_text()
        normalized = text.casefold()

        if re.search(
            r"\bsynonim\b|\bsynonimem\b",
            normalized,
        ):
            result = "synonym"

        elif re.search(
            r"zła wielkość liter|zla wielkosc liter"
            r"|wielkość liter|wielkosc liter",
            normalized,
        ):
            result = "capitalization"

        elif re.search(
            r"\bniepoprawnie\b"
            r"|\bniepoprawna odpowiedź\b"
            r"|\bniepoprawna odpowiedz\b",
            normalized,
        ):
            result = "incorrect"

        elif re.search(
            r"\bdobrze\s*!?"
            r"|\bpoprawnie\b",
            normalized,
        ):
            result = "correct"

        else:
            result = "unknown"

        return {
            "result": result,
            "text": text[:12000],
        }

    def find_correct_answer(self, text):
        """Szuka jawnie podanej poprawnej odpowiedzi."""

        patterns = (
            r"poprawna odpowiedź\s*:\s*([^\n]+)",
            r"poprawna odpowiedz\s*:\s*([^\n]+)",
            r"powinno być\s*:\s*([^\n]+)",
            r"powinno byc\s*:\s*([^\n]+)",
        )

        for pattern in patterns:
            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                answer = match.group(1).strip()

                if answer:
                    return answer

        return None

    def process_task(self):
        """
        Rozpoznaje zadanie, pobiera propozycję
        i wpisuje ją do pola odpowiedzi.

        Automatyczne zatwierdzanie pozostaje wyłączone
        do czasu potwierdzenia selektorów strony.
        """

        task = self.read_task()

        if task is None:
            self.logger.warning(
                "Nie znaleziono aktywnego pola odpowiedzi."
            )
            return False

        polish = self.extract_polish_word(task)

        if not polish:
            self.logger.warning(
                "Nie udało się pewnie rozpoznać słowa."
            )

            self.save_screenshot("word_not_detected.png")

            self.save_observation({
                "session_id": self.session_id,
                "result": "word_not_detected",
                "task_text": task["text"],
                "timestamp": utc_timestamp(),
            })

            return False

        self.logger.info(
            "Rozpoznane polskie słowo: %s",
            polish,
        )

        try:
            result = analyze_word(
                polish,
                context=task["text"],
            )

        except Exception as error:
            self.logger.exception(
                "Agent nie potrafił przeanalizować słowa: %s",
                error,
            )
            return False

        candidates = result.get("translations", [])

        if not candidates:
            self.logger.warning(
                "Brak tłumaczenia dla: %s",
                polish,
            )

            self.save_observation({
                "session_id": self.session_id,
                "polish": polish,
                "result": "translation_needed",
                "task_text": task["text"],
                "timestamp": utc_timestamp(),
            })

            return False

        verified = [
            candidate
            for candidate in candidates
            if candidate.get("verified")
        ]

        candidate = (
            verified[0]
            if verified
            else candidates[0]
        )

        answer = str(
            candidate.get("answer", "")
        ).strip()

        if not answer:
            self.logger.warning(
                "Agent zwrócił pustą odpowiedź."
            )
            return False

        self.logger.info(
            "Propozycja: %s | źródło=%s | potwierdzona=%s",
            answer,
            candidate.get("source"),
            candidate.get("verified", False),
        )

        self.save_observation({
            "session_id": self.session_id,
            "polish": polish,
            "context": task["text"],
            "candidate": answer,
            "source": candidate.get("source"),
            "verified": candidate.get("verified", False),
            "result": "candidate_prepared",
            "timestamp": utc_timestamp(),
        })

        self.save_screenshot("task_detected.png")

        answer_field = self.page.locator("#answer").first

        try:
            answer_field.fill(answer)

        except Exception as error:
            self.logger.exception(
                "Nie udało się wpisać odpowiedzi: %s",
                error,
            )
            return False

        self.logger.info(
            "Wpisano odpowiedź do pola."
        )

        # Samo wpisanie odpowiedzi NIE oznacza,
        # że platforma uznała ją za poprawną.
        #
        # AUTO_SUBMIT pozostaje False do czasu
        # zweryfikowania prawdziwego przycisku.
        if not AUTO_SUBMIT:
            self.logger.info(
                "AUTO_SUBMIT=False: odpowiedź nie została wysłana."
            )

            self.save_observation({
                "session_id": self.session_id,
                "polish": polish,
                "candidate": answer,
                "result": "answer_filled_not_submitted",
                "timestamp": utc_timestamp(),
            })

            return True

        submit_button = self.find_submit_button()

        if submit_button is None:
            self.logger.warning(
                "Nie znaleziono przycisku sprawdzania."
            )
            return False

        before = self.inspect_answer_result()

        try:
            submit_button.click(timeout=5000)
            self.page.wait_for_timeout(1000)

        except Exception as error:
            self.logger.exception(
                "Nie udało się wysłać odpowiedzi: %s",
                error,
            )
            return False

        after = self.inspect_answer_result()

        if after["result"] == "unknown":
            self.logger.warning(
                "Nie udało się potwierdzić wyniku odpowiedzi."
            )

            self.save_observation({
                "session_id": self.session_id,
                "polish": polish,
                "candidate": answer,
                "result": "feedback_unknown",
                "feedback": after["text"],
                "timestamp": utc_timestamp(),
            })

            return False

        if after["result"] == "correct":
            record_attempt(
                polish=polish,
                answer=answer,
                result="accepted",
                context=task["text"],
                session_id=self.session_id,
            )

            learn_from_feedback(
                polish,
                answer,
                accepted=True,
                context=task["text"],
            )

            self.logger.info(
                "Zapisano potwierdzoną odpowiedź: %s -> %s",
                polish,
                answer,
            )

        elif after["result"] in (
            "incorrect",
            "capitalization",
            "synonym",
        ):
            correction = self.find_correct_answer(
                after["text"]
            )

            if correction:
                record_attempt(
                    polish=polish,
                    answer=answer,
                    result="rejected_with_correction",
                    correction=correction,
                    context=task["text"],
                    session_id=self.session_id,
                )

                learn_from_feedback(
                    polish,
                    answer,
                    accepted=False,
                    correction=correction,
                    context=task["text"],
                )

                self.logger.info(
                    "Zapisano informację zwrotną: %s -> %s",
                    polish,
                    correction,
                )

            else:
                record_attempt(
                    polish=polish,
                    answer=answer,
                    result="uncertain",
                    context=task["text"],
                    session_id=self.session_id,
                )

                self.logger.warning(
                    "Odpowiedź odrzucona, ale brak "
                    "jednoznacznej poprawki."
                )

        self.save_observation({
            "session_id": self.session_id,
            "polish": polish,
            "candidate": answer,
            "result": after["result"],
            "feedback": after["text"],
            "timestamp": utc_timestamp(),
        })

        self.task_count += 1

        return True

    def run_test(self):
        """Przetwarza ograniczoną liczbę zadań."""

        for index in range(MAX_TEST_TASKS):
            self.logger.info(
                "Zadanie %s/%s",
                index + 1,
                MAX_TEST_TASKS,
            )

            if not self.has_answer_field():
                if not self.open_session():
                    self.logger.warning(
                        "Nie znaleziono aktywnego zadania."
                    )
                    break

            if not self.process_task():
                self.logger.warning(
                    "Przerwano przetwarzanie z powodu "
                    "braku pewnego wyniku."
                )
                break

            if not AUTO_SUBMIT:
                # Nie przechodzimy do następnego zadania,
                # ponieważ nie zatwierdziliśmy bieżącego.
                break

        self.save_screenshot("test_finished.png")

        self.logger.info(
            "Przetworzono zadań: %s",
            self.task_count,
        )

    def close(self):
        """Zamyka przeglądarkę i finalizuje nagranie."""

        if self.context:
            try:
                self.context.close()

                self.logger.info(
                    "Zamknięto kontekst przeglądarki."
                )

            except Exception as error:
                self.logger.warning(
                    "Problem podczas zamykania kontekstu: %s",
                    error,
                )

            self.context = None

        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass

            self.browser = None

        if self.playwright:
            try:
                self.playwright.stop()
            except Exception:
                pass

            self.playwright = None

        self.logger.info(
            "Zakończono działanie przeglądarki."
        )


def main():
    browser = InstalingBrowser(headless=True)

    try:
        url = browser.start()

        browser.logger.info(
            "Otwarto stronę: %s",
            url,
        )

        result = browser.login()

        browser.logger.info(
            "Logowanie zakończone. Adres: %s",
            result["url"],
        )

        if browser.open_session():
            browser.run_test()

    except Exception:
        browser.logger.exception(
            "Wystąpił błąd działania programu."
        )
        raise

    finally:
        browser.close()


if __name__ == "__main__":
    main()
