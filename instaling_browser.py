"""
Instaling Helper — przeglądarka i logowanie.

Dane logowania pobierane są ze zmiennych środowiskowych.
Program nie wypisuje loginu ani hasła w logach.
"""

import os
import re
from pathlib import Path

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


INSTALING_URL = "https://instaling.pl/"
ARTIFACTS_DIR = Path("artifacts")


class InstalingBrowser:
    def __init__(self, headless=True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.page = None

    def start(self):
        """Uruchamia Chromium i otwiera stronę Instaling."""
        self.playwright = sync_playwright().start()

        self.browser = self.playwright.chromium.launch(
            headless=self.headless
        )

        self.page = self.browser.new_page(
            locale="pl-PL",
            viewport={"width": 1365, "height": 900},
        )

        self.page.goto(
            INSTALING_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        self.page.wait_for_timeout(1500)

        return self.page.url

    def save_screenshot(self):
        """Zapisuje zrzut ekranu do artefaktów GitHub Actions."""
        if self.page is None:
            return

        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

        self.page.screenshot(
            path=str(ARTIFACTS_DIR / "instaling.png"),
            full_page=True,
        )

        print("Zapisano zrzut ekranu: artifacts/instaling.png")

    def inspect_page(self):
        """Odczytuje tekst strony i widoczne elementy formularza."""
        if self.page is None:
            raise RuntimeError("Przeglądarka nie została uruchomiona.")

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
            "button, input[type=submit], a"
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
                    text: (el.innerText || el.value || '').trim()
                }))
                .filter(el => el.text)
                .slice(0, 60)
            """
        )

        return {
            "url": self.page.url,
            "title": self.page.title(),
            "text": self.page.locator("body").inner_text()[:7000],
            "inputs": inputs,
            "buttons": buttons,
        }

    def login(self):
        """Próbuje zalogować się przy użyciu GitHub Secrets."""
        login = os.environ.get("INSTALING_LOGIN", "").strip()
        password = os.environ.get("INSTALING_PASSWORD", "")

        if not login or not password:
            raise RuntimeError(
                "Brak danych logowania. Sprawdź sekrety "
                "INSTALING_LOGIN i INSTALING_PASSWORD "
                "oraz konfigurację kroku w daily.yml."
            )

        page = self.page

        # Strona główna może wymagać przejścia do formularza.
        password_fields = page.locator('input[type="password"]')

        if password_fields.count() == 0:
            login_links = page.get_by_role(
                "link",
                name=re.compile(r"zaloguj|logowanie", re.I),
            )

            if login_links.count() > 0:
                login_links.first.click()
                page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=20000,
                )
                page.wait_for_timeout(1000)

        # Wyszukujemy pole hasła.
        password_fields = page.locator('input[type="password"]')

        if password_fields.count() == 0:
            self.save_screenshot()
            print("Nie rozpoznano formularza logowania.")
            print("URL:", page.url)
            print("Tytuł:", page.title())
            print(
                "Widoczny tekst:",
                page.locator("body").inner_text()[:5000],
            )
            raise RuntimeError(
                "Nie znaleziono pola hasła. "
                "Potrzebna jest analiza formularza."
            )

        password_field = password_fields.first

        # Preferujemy pola identyfikowane jako login, email lub użytkownik.
        possible_login_fields = page.locator(
            'input:not([type="password"]):not([type="hidden"])'
        )

        login_field = None

        for index in range(possible_login_fields.count()):
            field = possible_login_fields.nth(index)

            if not field.is_visible() or not field.is_enabled():
                continue

            description = " ".join(
                str(field.get_attribute(attribute) or "")
                for attribute in (
                    "name",
                    "id",
                    "placeholder",
                    "aria-label",
                    "type",
                )
            ).lower()

            if any(
                word in description
                for word in (
                    "login",
                    "email",
                    "user",
                    "użytkownik",
                    "identyfikator",
                )
            ):
                login_field = field
                break

        # Niektóre formularze nie mają opisowych nazw pól.
        if login_field is None:
            for index in range(possible_login_fields.count()):
                field = possible_login_fields.nth(index)

                if field.is_visible() and field.is_enabled():
                    login_field = field
                    break

        if login_field is None:
            self.save_screenshot()
            raise RuntimeError(
                "Nie znaleziono widocznego pola loginu."
            )

        login_field.fill(login)
        password_field.fill(password)

        # Próbujemy znaleźć przycisk wysyłający formularz.
        submit = page.locator(
            'button[type="submit"], input[type="submit"]'
        )

        if submit.count() > 0 and submit.first.is_visible():
            submit.first.click()
        else:
            login_buttons = page.get_by_role(
                "button",
                name=re.compile(
                    r"zaloguj|logowanie|login",
                    re.I,
                ),
            )

            if login_buttons.count() == 0:
                self.save_screenshot()
                raise RuntimeError(
                    "Nie znaleziono przycisku logowania."
                )

            login_buttons.first.click()

        try:
            page.wait_for_load_state(
                "domcontentloaded",
                timeout=15000,
            )
        except PlaywrightTimeoutError:
            pass

        page.wait_for_timeout(2000)

        page_data = self.inspect_page()
        text_lower = page_data["text"].lower()

        # Nie wypisujemy zawartości pól formularza ani haseł.
        self.save_screenshot()

        error_phrases = (
            "nieprawidłowe hasło",
            "nieprawidlowe haslo",
            "błędne hasło",
            "bledne haslo",
            "nieprawidłowy login",
            "nieprawidlowy login",
            "niepoprawne hasło",
        )

        if any(phrase in text_lower for phrase in error_phrases):
            raise RuntimeError(
                "Instaling zgłosił błąd danych logowania."
            )

        # Nie zakładamy, że samo kliknięcie oznacza sukces.
        still_has_password = (
            page.locator('input[type="password"]').count() > 0
        )

        if still_has_password:
            print(
                "UWAGA: formularz hasła nadal jest widoczny. "
                "Logowanie nie zostało potwierdzone."
            )
            print("Aktualny adres:", page.url)
            print("Widoczny tekst:", page_data["text"][:3000])

            raise RuntimeError(
                "Nie udało się potwierdzić logowania. "
                "Sprawdź zrzut ekranu."
            )

        print("Formularz logowania został opuszczony.")
        print("Aktualny adres:", page.url)
        print("Tytuł strony:", page_data["title"])
        print("Widoczny tekst:", page_data["text"][:4000])

        return page_data

    def close(self):
        """Zamyka przeglądarkę i zwalnia zasoby."""
        if self.browser:
            self.browser.close()
            self.browser = None

        if self.playwright:
            self.playwright.stop()
            self.playwright = None


def main():
    browser = InstalingBrowser(headless=True)

    try:
        url = browser.start()
        print("Otwarto stronę:", url)

        result = browser.login()

        print("Zakończono próbę logowania.")
        print("Adres po próbie:", result["url"])

    except Exception as error:
        print("Błąd przeglądarki:", str(error))
        raise

    finally:
        browser.close()


if __name__ == "__main__":
    main()
