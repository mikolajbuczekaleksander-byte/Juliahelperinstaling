"""
Instaling Helper — przeglądarka, nagrywanie i testowa obsługa zadań.

Integracja:
- agent.analyze_word()
- agent.learn_from_feedback()

Tryb testowy:
- maksymalnie 3 zadania;
- brak losowego zgadywania odpowiedzi;
- zapis wyników i logów;
- nagrywanie przeglądarki.
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

    def start(self):
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

    def save_screenshot(self, name="instaling.png"):
        if self.page is None:
            return

        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

        destination = ARTIFACTS_DIR / name

        self.page.screenshot(
            path=str(destination),
            full_page=True,
        )

        self.logger.info(
            "Zapisano zrzut ekranu: %s",
            destination,
        )

    def inspect_page(self):
        return {
            "url": self.page.url,
            "title": self.page.title(),
            "text": self.page.locator(
                "body"
            ).inner_text()[:12000],
        }

    def login(self):
        login = os.environ.get(
            "INSTALING_LOGIN", ""
        ).strip()

        password = os.environ.get(
            "INSTALING_PASSWORD", ""
        )

        if not login or not password:
            raise RuntimeError(
                "Brak sekretów INSTALING_LOGIN lub "
                "INSTALING_PASSWORD."
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
        self.logger.info("Adres: %s", result["url"])

        self.save_screenshot("after_login.png")

        return result

    def find_session_action(self):
        """
        Rozpoznaje przyciski prowadzące do sesji.

        Dodatkowo obsługuje ekran pośredni, na którym
        trzeba ponownie nacisnąć „Rozpocznij sesję”.
        """

        elements = self.page.locator(
            "a, button, input[type=button], "
            "input[type=submit], [role=button]"
        )

        candidates = []

        priorities = (
            ("kontynuuj sesję", 100),
            ("kontynuuj sesje", 100),
            ("dokończ sesję", 95),
            ("dokonc", 95),
            ("rozpocznij kolejną sesję", 90),
            ("rozpocznij kolejna sesje", 90),
            ("rozpocznij sesję", 85),
            ("rozpocznij sesje", 85),
            ("następna sesja", 70),
            ("nastepna sesja", 70),
            ("kontynuuj", 60),
        )

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

                href = str(
                    element.get_attribute("href") or ""
                ).strip()

                description = (
                    f"{text} {value} {href}"
                ).casefold()

                score = 0

                for word, priority in priorities:
                    if word in description:
                        score = max(score, priority)

                if "session" in href.casefold():
                    score += 10

                if score:
                    candidates.append(
                        (score, element, text or value or href)
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
                "Kandydat na przycisk sesji: %s; ocena=%s",
                description[:150],
                score,
            )

        return candidates[0][1] if candidates else None

    def open_session(self):
        """
        Otwiera sesję i obsługuje ekran pośredni.

        Po każdym kliknięciu sprawdza, czy pojawił się
        ekran zadania. Jeśli nie, szuka kolejnego przycisku.
        """

        for attempt in range(4):
            state = self.inspect_page()
            text = state["text"].casefold()

            self.logger.info(
                "Stan strony, etap %s:\n%s",
                attempt + 1,
                state["text"][:2500],
            )

            if self.page.locator("#answer").is_visible():
                self.logger.info(
                    "Wykryto pole odpowiedzi."
                )
                self.save_screenshot("session_ready.png")
                return True

            if (
                "koniec sesji" in text
                or "powrót na stronę główną" in text
            ):
                self.logger.info(
                    "Wykryto ekran końca sesji."
                )
                return False

            action = self.find_session_action()

            if action is None:
                self.save_screenshot(
                    f"session_step_{attempt + 1}.png"
                )

                self.logger.warning(
                    "Nie znaleziono kolejnego przycisku sesji."
                )
                return False

            try:
                self.logger.info(
                    "Klikam element prowadzący do sesji."
                )

                action.click(timeout=5000)
                self.page.wait_for_timeout(1500)

            except Exception as error:
                self.logger.warning(
                    "Kliknięcie nie powiodło się: %s",
                    error,
                )
                return False

        return self.page.locator("#answer").is_visible()

    def read_task(self):
        """
        Odczytuje tekst ekranu.

        Uwaga: konkretne polskie słowo nie zawsze da się
        pewnie wyodrębnić z samego tekstu strony.
        """

        answer = self.page.locator("#answer")

        if not answer.count() or not answer.first.is_visible():
            return None

        text = self.page.locator("body").inner_text()

        task = {
            "text": text[:12000],
            "answer_selector": "#answer",
            "url": self.page.url,
        }

        self.logger.info(
            "Odczytano ekran zadania:\n%s",
            task["text"][:3000],
        )

        return task

    def extract_polish_word(self, task):
        """
        Próbuje znaleźć polskie słowo na podstawie etykiet.

        Jeśli nie ma wystarczająco pewnego wyniku,
        nie zgaduje.
        """

        text = task["text"]

        patterns = (
            r"(?:przetłumacz|przetlumacz)\s*:?\s*(.+)",
            r"(?:po polsku|polskie słowo|polskie slowo)\s*:?\s*(.+)",
        )

        for pattern in patterns:
            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                candidate = match.group(1).splitlines()[0].strip()

                if candidate and len(candidate) < 100:
                    return candidate

        return None

    def inspect_answer_result(self):
        """
        Rozpoznaje wynik tylko na podstawie widocznego
        komunikatu platformy.
        """

        text = self.page.locator("body").inner_text()
        normalized = text.casefold()

        if re.search(
            r"\bsynonim\b|\bsynonimem\b",
            normalized,
        ):
            result = "synonym"

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
        """
        Próbuje odczytać odpowiedź z jawnego komunikatu.
        Nie wymyśla poprawnego tłumaczenia.
        """

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
        """
        Zapisuje wynik do osobnego pliku JSONL.

        Nie zastępuje to pamięci modułu memory.py.
        """

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
        Analizuje jedno zadanie.

        Na tym etapie nie wysyła odpowiedzi, jeżeli
        nie udało się pewnie odczytać polskiego słowa.
        """

        task = self.read_task()

        if task is None:
            self.logger.warning(
                "Brak aktywnego pola odpowiedzi."
            )
            return False

        polish = self.extract_polish_word(task)

        if not polish:
            self.logger.warning(
                "Nie udało się pewnie wyodrębnić polskiego słowa. "
                "Nie wysyłam odpowiedzi."
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
                "Brak tłumaczenia dla: %s",
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

        # W pierwszej kolejności wybieramy tłumaczenie
        # wcześniej potwierdzone przez platformę.
        verified = [
            item for item in candidates
            if item.get("verified")
        ]

        candidate = (
            verified[0] if verified else candidates[0]
        )

        answer = candidate["answer"]

        self.logger.info(
            "Polskie słowo: %s",
            polish,
        )

        self.logger.info(
            "Wybrana odpowiedź: %s; źródło=%s; "
            "potwierdzona=%s",
            answer,
            candidate.get("source"),
            candidate.get("verified"),
        )

        # Celowo nie wysyłamy odpowiedzi w tej wersji.
        # Najpierw trzeba potwierdzić selektory oraz
        # format tekstu zadania na rzeczywistym ekranie.
        self.save_screenshot("task_detected.png")

        self.save_observation({
            "session_id": self.session_id,
            "polish": polish,
            "context": task["text"],
            "candidate": answer,
            "source": candidate.get("source"),
            "verified": candidate.get("verified", False),
            "result": "candidate_prepared",
            "timestamp": datetime.now().isoformat(),
        })

        self.logger.info(
            "Kandydat przygotowany. "
            "Automatyczne wysyłanie odpowiedzi jest wyłączone "
            "do czasu potwierdzenia odczytu zadania."
        )

        return True

    def run_test(self):
        for index in range(MAX_TEST_TASKS):
            self.logger.info(
                "Test zadania %s/%s",
                index + 1,
                MAX_TEST_TASKS,
            )

            if not self.page.locator("#answer").is_visible():
                if not self.open_session():
                    self.logger.info(
                        "Nie znaleziono aktywnego zadania."
                    )
                    break

            if not self.process_task():
                self.logger.info(
                    "Zatrzymano test, ponieważ zadanie "
                    "nie mogło zostać bezpiecznie przetworzone."
                )
                break

            # Nie przechodzimy automatycznie dalej,
            # dopóki nie potwierdzimy rozpoznawania zadań.
            break

        self.save_screenshot("test_finished.png")

    def close(self):
        """
        Zamyka kontekst i przeglądarkę.

        Zamknięcie kontekstu finalizuje nagranie.
        """

        if self.context:
            try:
                self.context.close()
                self.logger.info(
                    "Zamknięto kontekst i sfinalizowano nagranie."
                )
            except Exception as error:
                self.logger.warning(
                    "Błąd zamykania kontekstu: %s",
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
