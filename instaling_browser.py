"""
Instaling Helper — przeglądarka, nagrywanie i obserwacja sesji.

Funkcje:
- logowanie przez GitHub Secrets;
- nagrywanie przeglądarki do pliku wideo;
- zapisywanie zrzutów ekranu;
- zapisywanie logów działania;
- rozpoznawanie panelu ucznia i ekranu sesji;
- rozpoznawanie pola odpowiedzi;
- odczytywanie komunikatów zwrotnych;
- bezpieczne rozpoznawanie wyników zadań.

Automatyczne rozwiązywanie zadań nie jest jeszcze włączone.
"""

import os
import re
import logging
from pathlib import Path

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


INSTALING_URL = "https://instaling.pl/"
ARTIFACTS_DIR = Path("artifacts")
VIDEO_DIR = ARTIFACTS_DIR / "video"


def setup_logging():
    """Konfiguruje logowanie do konsoli i pliku."""

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
        self.page = None
        self.logger = setup_logging()

    def start(self):
        """Uruchamia Chromium z nagrywaniem."""

        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)

        self.playwright = sync_playwright().start()

        self.browser = self.playwright.chromium.launch(
            headless=self.headless
        )

        self.page = self.browser.new_page(
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
        """Zapisuje zrzut ekranu."""

        if self.page is None:
            return

        ARTIFACTS_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

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
        """Odczytuje tekst, pola formularza i przyciski."""

        if self.page is None:
            raise RuntimeError(
                "Przeglądarka nie została uruchomiona."
            )

        inputs = self.page.locator("input").evaluate_all(
            """
            elements => elements
                .filter(el => {
                    const s = getComputedStyle(el);
                    return s.display !== 'none'
                        && s.visibility !== 'hidden';
                })
                .map(el => ({
                    type: el.type,
                    name: el.name,
                    id: el.id,
                    placeholder: el.placeholder,
                    ariaLabel: el.getAttribute('aria-label')
                }))
            """
        )

        buttons = self.page.locator(
            "button, input[type=submit], input[type=button], "
            "a, [role=button]"
        ).evaluate_all(
            """
            elements => elements
                .filter(el => {
                    const s = getComputedStyle(el);
                    return s.display !== 'none'
                        && s.visibility !== 'hidden';
                })
                .map(el => ({
                    tag: el.tagName,
                    text: (
                        el.innerText
                        || el.value
                        || el.getAttribute('aria-label')
                        || ''
                    ).trim(),
                    href: el.getAttribute('href')
                }))
                .filter(el => el.text || el.href)
                .slice(0, 100)
            """
        )

        return {
            "url": self.page.url,
            "title": self.page.title(),
            "text": self.page.locator(
                "body"
            ).inner_text()[:10000],
            "inputs": inputs,
            "buttons": buttons,
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
                "Brak sekretów INSTALING_LOGIN lub "
                "INSTALING_PASSWORD. Sprawdź ustawienia "
                "GitHub Actions."
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

            if links.count() > 0:
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

        possible_fields = page.locator(
            'input:not([type="password"]):'
            'not([type="hidden"]):'
            'not([type="submit"]):'
            'not([type="button"])'
        )

        login_field = None

        for index in range(possible_fields.count()):
            field = possible_fields.nth(index)

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
            for index in range(possible_fields.count()):
                field = possible_fields.nth(index)

                if field.is_visible() and field.is_enabled():
                    login_field = field
                    break

        if login_field is None:
            self.save_screenshot("login_error.png")

            raise RuntimeError(
                "Nie znaleziono widocznego pola loginu."
            )

        login_field.fill(login)
        password_fields.first.fill(password)

        submit = page.locator(
            'button[type="submit"], input[type="submit"]'
        )

        if submit.count() > 0 and submit.first.is_visible():
            submit.first.click()
        else:
            buttons = page.get_by_role(
                "button",
                name=re.compile(
                    r"zaloguj|logowanie|login",
                    re.IGNORECASE,
                ),
            )

            if buttons.count() == 0:
                self.save_screenshot("login_error.png")

                raise RuntimeError(
                    "Nie znaleziono przycisku logowania."
                )

            buttons.first.click()

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
        ).count() > 0:
            self.save_screenshot("login_error.png")

            raise RuntimeError(
                "Nie potwierdzono zakończenia logowania."
            )

        self.logger.info(
            "Formularz logowania został opuszczony."
        )
        self.logger.info("Adres: %s", result["url"])
        self.logger.info("Tytuł: %s", result["title"])

        self.save_screenshot("after_login.png")

        return result

    def find_session_action(self):
        """Wyszukuje przycisk kontynuowania lub rozpoczęcia sesji."""

        elements = self.page.locator(
            "a, button, input[type=button], "
            "input[type=submit], [role=button]"
        )

        candidates = []

        # Dłuższe i bardziej konkretne opisy mają wyższy priorytet.
        words = (
            "kontynuuj sesję",
            "kontynuuj sesje",
            "dokończ sesję",
            "dokonc",
            "rozpocznij kolejną sesję",
            "rozpocznij kolejna sesje",
            "rozpocznij sesję",
            "rozpocznij sesje",
            "zacznij",
            "kontynuuj",
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

                for word in words:
                    if word in description:
                        score += 3

                if "session" in href.casefold():
                    score += 2

                if score:
                    candidates.append(
                        (
                            score,
                            element,
                            text or value or href,
                        )
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
        """Otwiera rozpoznany ekran sesji."""

        before = self.inspect_page()

        self.logger.info("Analizuję panel ucznia.")
        self.logger.info(
            "Tekst panelu:\n%s",
            before["text"][:3000],
        )

        action = self.find_session_action()

        if action is None:
            self.save_screenshot("session_not_found.png")

            self.logger.warning(
                "Nie znaleziono przycisku sesji."
            )

            return False

        try:
            action.click(timeout=5000)
            self.page.wait_for_timeout(1500)

        except Exception as error:
            self.logger.error(
                "Nie udało się otworzyć sesji: %s",
                error,
            )

            self.save_screenshot("session_error.png")

            return False

        after = self.inspect_page()

        self.logger.info(
            "Ekran po kliknięciu:\n%s",
            after["text"][:4000],
        )

        self.save_screenshot("session.png")

        return True

    def read_task(self):
        """Odczytuje zadanie i sprawdza obecność pola odpowiedzi."""

        page = self.page
        answer = page.locator("#answer")

        if answer.count() == 0 or not answer.first.is_visible():
            self.logger.warning(
                "Nie znaleziono widocznego pola #answer."
            )

            return None

        task = {
            "text": page.locator(
                "body"
            ).inner_text()[:10000],
            "answer_selector": "#answer",
            "url": page.url,
        }

        self.logger.info("Rozpoznano ekran zadania.")
        self.logger.info(
            "Treść zadania:\n%s",
            task["text"][:4000],
        )

        return task

    def inspect_answer_result(self):
        """
        Rozpoznaje komunikat zwrotny.

        correct:
            wyraźny komunikat o poprawnej odpowiedzi;

        incorrect:
            wyraźny komunikat o błędzie;

        synonym:
            wyraźny komunikat o synonimie;

        unknown:
            wynik nie został pewnie rozpoznany.
        """

        text = self.page.locator("body").inner_text()
        normalized = text.casefold()

        # Najpierw sprawdzamy, czy platforma mówi o synonimie.
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

        observation = {
            "result": result,
            "text": text[:10000],
        }

        self.logger.info(
            "Wynik obserwacji: %s",
            result,
        )

        self.logger.info(
            "Treść komunikatu:\n%s",
            text[:3000],
        )

        return observation

    def run_test(self):
        """
        Tryb testowy.

        Nie wpisuje odpowiedzi i nie klika przycisku Sprawdź.
        """

        task = self.read_task()

        if task is None:
            self.logger.warning(
                "Nie potwierdzono ekranu zadania."
            )

            self.save_screenshot("task_not_found.png")

            return False

        self.save_screenshot("task_detected.png")

        self.logger.info(
            "Test zakończony: pole odpowiedzi rozpoznane. "
            "Automatyczne rozwiązywanie jest wyłączone."
        )

        return True

    def close(self):
        """Zamyka przeglądarkę i kończy nagrywanie."""

        if self.page:
            try:
                self.page.close()
                self.logger.info(
                    "Zamknięto stronę; nagranie powinno zostać zapisane."
                )
            except Exception as error:
                self.logger.warning(
                    "Problem podczas zamykania strony: %s",
                    error,
                )

            self.page = None

        if self.browser:
            try:
                self.browser.close()
            except Exception as error:
                self.logger.warning(
                    "Problem podczas zamykania przeglądarki: %s",
                    error,
                )

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
            "Zakończono próbę logowania. Adres: %s",
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
