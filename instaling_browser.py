"""
Instaling Helper — przeglądarka, nagrywanie i obserwacja sesji.

Funkcje:
- logowanie przez GitHub Secrets;
- nagrywanie przeglądarki do pliku wideo;
- zapisywanie zrzutów ekranu;
- zapisywanie logów działania;
- rozpoznawanie panelu ucznia i ekranów pośrednich;
- obsługa różnych przycisków rozpoczynania sesji;
- rozpoznawanie pola odpowiedzi;
- odczytywanie komunikatów zwrotnych.

Automatyczne rozwiązywanie zadań jest na razie wyłączone.
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

# Ograniczenie liczby kliknięć podczas przechodzenia
# przez ekrany pośrednie.
MAX_SESSION_STEPS = 6


def setup_logging():
    """Konfiguruje logowanie do konsoli i pliku."""

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("instaling_helper")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

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

    def start(self):
        """Uruchamia Chromium z nagrywaniem wideo."""

        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)

        self.playwright = sync_playwright().start()

        self.browser = self.playwright.chromium.launch(
            headless=self.headless
        )

        # Nagrywanie konfiguruje się na poziomie kontekstu.
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
        """Zapisuje zrzut ekranu."""

        if self.page is None:
            return

        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

        destination = ARTIFACTS_DIR / name

        try:
            self.page.screenshot(
                path=str(destination),
                full_page=True,
                timeout=15000,
            )

            self.logger.info(
                "Zapisano zrzut ekranu: %s",
                destination,
            )

        except Exception as error:
            self.logger.warning(
                "Nie udało się zapisać zrzutu: %s",
                error,
            )

    def inspect_page(self):
        """Odczytuje tekst strony, pola i widoczne przyciski."""

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
            ).casefold()

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

    @staticmethod
    def normalize_label(value):
        """Normalizuje etykietę przycisku."""

        return " ".join(
            (value or "").casefold().split()
        )

    def get_visible_session_actions(self):
        """
        Zwraca widoczne przyciski mogące prowadzić do sesji.

        Nie klika automatycznie linków niezwiązanych z sesją.
        """

        page = self.page

        elements = page.locator(
            "button, a, [role=button], "
            "input[type=button], input[type=submit]"
        )

        candidates = []

        patterns = [
            (
                re.compile(
                    r"rozpocznij.*sesj|"
                    r"rozpocznij sesj",
                    re.IGNORECASE,
                ),
                100,
            ),
            (
                re.compile(
                    r"kontynuuj.*sesj",
                    re.IGNORECASE,
                ),
                90,
            ),
            (
                re.compile(
                    r"dokończ.*sesj|dokonc.*sesj",
                    re.IGNORECASE,
                ),
                80,
            ),
            (
                re.compile(
                    r"wznów.*sesj|wznow.*sesj",
                    re.IGNORECASE,
                ),
                80,
            ),
        ]

        for index in range(elements.count()):
            element = elements.nth(index)

            try:
                if not element.is_visible():
                    continue

                if not element.is_enabled():
                    continue

                label = (
                    element.inner_text(timeout=1000)
                    or element.get_attribute("value")
                    or element.get_attribute("aria-label")
                    or ""
                ).strip()

                href = (
                    element.get_attribute("href") or ""
                ).strip()

                normalized = self.normalize_label(label)

                if not normalized:
                    continue

                priority = None

                for pattern, score in patterns:
                    if pattern.search(normalized):
                        priority = score
                        break

                # Dodatkowa wskazówka dla linków prowadzących
                # do strony sesji.
                if priority is None and "session" in href.casefold():
                    if "dokoncz" in normalized or "dokończ" in normalized:
                        priority = 75

                if priority is not None:
                    candidates.append(
                        {
                            "priority": priority,
                            "element": element,
                            "label": label,
                            "href": href,
                        }
                    )

            except Exception as error:
                self.logger.debug(
                    "Pominięto element: %s",
                    error,
                )

        candidates.sort(
            key=lambda item: item["priority"],
            reverse=True,
        )

        return candidates

    def open_session(self):
        """
        Przechodzi przez ekrany pośrednie aż do ekranu zadania.

        Po każdym kliknięciu ponownie sprawdza stronę.
        """

        page = self.page

        self.logger.info(
            "Rozpoczynam rozpoznawanie ekranu sesji."
        )

        for step in range(1, MAX_SESSION_STEPS + 1):
            page.wait_for_timeout(1200)

            try:
                data = self.inspect_page()
            except Exception:
                self.logger.exception(
                    "Nie udało się odczytać aktualnej strony."
                )
                self.save_screenshot("session_inspect_error.png")
                return False

            text = data["text"].casefold()

            self.logger.info(
                "Krok %s/%s | URL: %s",
                step,
                MAX_SESSION_STEPS,
                page.url,
            )

            # Najpierw sprawdzamy, czy pojawiło się pole odpowiedzi.
            answer = page.locator("#answer")

            if (
                answer.count() > 0
                and answer.first.is_visible()
                and answer.first.is_enabled()
            ):
                self.logger.info(
                    "Wykryto aktywne pole odpowiedzi."
                )

                self.save_screenshot("session_ready.png")
                return True

            # Nie rozpoczynamy kolejnej sesji, jeśli bieżąca
            # już się zakończyła.
            if re.search(
                r"\bkoniec sesji\b",
                text,
            ):
                self.logger.info(
                    "Wykryto ekran końca sesji."
                )

                self.save_screenshot("session_finished.png")
                return False

            # Na ekranie początkowym i pośrednim szukamy
            # przycisków związanych z sesją.
            candidates = self.get_visible_session_actions()

            if not candidates:
                self.logger.warning(
                    "Nie znaleziono rozpoznanego przycisku sesji."
                )

                self.logger.info(
                    "Widoczny tekst:\n%s",
                    data["text"][:4000],
                )

                self.save_screenshot(
                    "session_action_not_found.png"
                )

                return False

            clicked = False

            for candidate in candidates:
                label = candidate["label"]
                element = candidate["element"]

                try:
                    self.logger.info(
                        "Krok %s: klikam '%s'.",
                        step,
                        label,
                    )

                    element.click(timeout=5000)

                    page.wait_for_timeout(1500)

                    self.save_screenshot(
                        f"session_step_{step}.png"
                    )

                    clicked = True
                    break

                except Exception as error:
                    self.logger.warning(
                        "Nie udało się kliknąć '%s': %s",
                        label,
                        error,
                    )

            if not clicked:
                self.logger.error(
                    "Nie udało się kliknąć żadnego "
                    "rozpoznanego przycisku sesji."
                )

                self.save_screenshot(
                    "session_click_failed.png"
                )

                return False

        # Po wykorzystaniu limitu sprawdzamy ekran jeszcze raz.
        page.wait_for_timeout(1000)

        answer = page.locator("#answer")

        if (
            answer.count() > 0
            and answer.first.is_visible()
            and answer.first.is_enabled()
        ):
            self.logger.info(
                "Sesja gotowa — pole odpowiedzi jest widoczne."
            )

            self.save_screenshot("session_ready.png")
            return True

        self.logger.warning(
            "Osiągnięto limit przejść bez potwierdzenia "
            "ekranu zadania."
        )

        self.save_screenshot("session_not_ready.png")
        return False

    def read_task(self):
        """
        Odczytuje aktualne zadanie.

        Na tym etapie nie wpisuje odpowiedzi.
        """

        page = self.page
        answer = page.locator("#answer")

        if (
            answer.count() == 0
            or not answer.first.is_visible()
        ):
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

        self.logger.info(
            "Rozpoznano ekran zadania."
        )

        self.logger.info(
            "Treść strony:\n%s",
            task["text"][:4000],
        )

        return task

    def inspect_answer_result(self):
        """
        Rozpoznaje komunikat po sprawdzeniu odpowiedzi.

        Wyniki:
        - correct;
        - incorrect;
        - synonym;
        - unknown.
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
        Testuje rozpoznawanie ekranu zadania.

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
            "Test zakończony. Pole odpowiedzi rozpoznane; "
            "automatyczne rozwiązywanie pozostaje wyłączone."
        )

        return True

    def close(self):
        """Zamyka stronę i przeglądarkę, kończąc nagrywanie."""

        if self.page is not None:
            try:
                self.page.close()

                self.logger.info(
                    "Zamknięto stronę."
                )

            except Exception as error:
                self.logger.warning(
                    "Problem podczas zamykania strony: %s",
                    error,
                )

            self.page = None

        if self.context is not None:
            try:
                # Zamknięcie kontekstu finalizuje nagrania wideo.
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

        if self.browser is not None:
            try:
                self.browser.close()

            except Exception as error:
                self.logger.warning(
                    "Problem podczas zamykania przeglądarki: %s",
                    error,
                )

            self.browser = None

        if self.playwright is not None:
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
