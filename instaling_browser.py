"""
Instaling Helper — automatyzacja obserwacji sesji.

Funkcje:
- logowanie przez GitHub Secrets;
- nagrywanie przeglądarki;
- rozpoznawanie etapów otwierania sesji;
- diagnostyka ekranu zadania;
- pobieranie propozycji z agent.py;
- zapisywanie obserwacji i wyników;
- rozpoznawanie komunikatów zwrotnych;
- zapisywanie potwierdzonych odpowiedzi.

Uwaga:
Selektory i rozpoznawanie tekstu należy zweryfikować
na podstawie rzeczywistego ekranu InstaLing.
"""

import os
import re
import json
import logging

from pathlib import Path
from datetime import datetime

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from agent import analyze_word, learn_from_feedback
from memory import start_session, finish_session


INSTALING_URL = "https://instaling.pl/"

ARTIFACTS_DIR = Path("artifacts")
VIDEO_DIR = ARTIFACTS_DIR / "video"
DATA_DIR = Path("data")

MAX_TEST_TASKS = 3


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

        self.session_id = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )

        self.task_count = 0
        self.session_started = False

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

        self.page.screenshot(
            path=str(path),
            full_page=True,
        )

        self.logger.info(
            "Zapisano zrzut ekranu: %s",
            path,
        )

    def inspect_page(self):
        """Pobiera widoczny tekst strony."""

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
        """Rozpoznaje dostępne przyciski sesji."""

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

                description = (
                    f"{text} {value}"
                ).casefold()

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

    def open_session(self):
        """Przechodzi przez dostępne ekrany sesji."""

        for attempt in range(5):
            state = self.inspect_page()
            text = state["text"].casefold()

            self.logger.info(
                "Etap sesji %s:\n%s",
                attempt + 1,
                state["text"][:2000],
            )

            answer = self.page.locator("#answer")

            if answer.count() and answer.first.is_visible():
                self.logger.info(
                    "Wykryto pole odpowiedzi."
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

        answer = self.page.locator("#answer")

        return bool(
            answer.count() and answer.first.is_visible()
        )

    def read_task(self):
        """Odczytuje ekran zadania."""

        answer = self.page.locator("#answer")

        if not answer.count() or not answer.first.is_visible():
            return None

        text = self.page.locator("body").inner_text()

        return {
            "text": text[:12000],
            "url": self.page.url,
            "answer_selector": "#answer",
        }

    def extract_polish_word(self, task):
        """
        Próbuje wyodrębnić polskie słowo.

        Jeśli układ strony nie pasuje do znanych wzorców,
        zwraca None zamiast zgadywać.
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

    def inspect_answer_result(self):
        """Odczytuje widoczny komunikat po sprawdzeniu odpowiedzi."""

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
        """Odczytuje poprawkę wyłącznie z jawnego komunikatu."""

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

    def process_task(self):
        """
        Analizuje zadanie i przygotowuje tłumaczenie.

        Ta wersja nie klika automatycznie przycisku
        sprawdzania. Najpierw należy zweryfikować
        rozpoznawanie zadania i komunikatów.
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
                "Nie udało się pewnie odczytać polskiego słowa."
            )

            self.save_screenshot("word_not_detected.png")

            self.save_observation({
                "session_id": self.session_id,
                "result": "word_not_detected",
                "task_text": task["text"],
                "timestamp": datetime.now().isoformat(),
            })

            return False

        result = analyze_word(
            polish,
            context=task["text"],
        )

        candidates = result.get("translations", [])

        if not candidates:
            self.logger.warning(
                "Brak propozycji tłumaczenia dla: %s",
                polish,
            )

            self.save_observation({
                "session_id": self.session_id,
                "polish": polish,
                "result": "translation_needed",
                "task_text": task["text"],
                "timestamp": datetime.now().isoformat(),
            })

            return False

        verified = [
            candidate
            for candidate in candidates
            if candidate.get("verified")
        ]

        candidate = verified[0] if verified else candidates[0]

        self.logger.info("Polskie słowo: %s", polish)
        self.logger.info(
            "Propozycja: %s | źródło=%s | potwierdzona=%s",
            candidate["answer"],
            candidate.get("source"),
            candidate.get("verified", False),
        )

        self.save_observation({
            "session_id": self.session_id,
            "polish": polish,
            "context": task["text"],
            "candidate": candidate["answer"],
            "source": candidate.get("source"),
            "verified": candidate.get("verified", False),
            "result": "candidate_prepared",
            "timestamp": datetime.now().isoformat(),
        })

        self.save_screenshot("task_detected.png")

        self.logger.info(
            "Zadanie przeanalizowane. "
            "Automatyczne wysyłanie odpowiedzi pozostaje wyłączone "
            "do czasu potwierdzenia selektorów strony."
        )

        return True

    def run_test(self):
        """Wykonuje ograniczony test rozpoznawania zadań."""

        for index in range(MAX_TEST_TASKS):
            self.logger.info(
                "Zadanie testowe %s/%s",
                index + 1,
                MAX_TEST_TASKS,
            )

            answer = self.page.locator("#answer")

            if not (
                answer.count() and answer.first.is_visible()
            ):
                if not self.open_session():
                    self.logger.info(
                        "Nie znaleziono aktywnego zadania."
                    )
                    break

            if not self.process_task():
                self.logger.info(
                    "Zatrzymano test z powodu niepewnego "
                    "rozpoznania zadania."
                )
                break

            # Nie przechodzimy automatycznie dalej.
            break

        self.save_screenshot("test_finished.png")

    def close(self):
        """Zamyka kontekst i finalizuje nagranie."""

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
