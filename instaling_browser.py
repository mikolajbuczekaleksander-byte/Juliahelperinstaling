"""
Instaling Helper — logowanie i analiza strony.

Funkcje:
- logowanie za pomocą GitHub Secrets,
- odczytywanie treści strony,
- rozpoznawanie potencjalnych przycisków sesji,
- analiza pól formularza,
- zapisywanie zrzutów ekranu.

Automatyczne rozwiązywanie zadań nie jest jeszcze zaimplementowane.
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
        """Uruchamia przeglądarkę i otwiera Instaling."""

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
        )

        self.page.goto(
            INSTALING_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        self.page.wait_for_timeout(1500)

        return self.page.url

    def save_screenshot(self):
        """Zapisuje zrzut ekranu do artifacts/instaling.png."""

        if self.page is None:
            return

        ARTIFACTS_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.page.screenshot(
            path=str(ARTIFACTS_DIR / "instaling.png"),
            full_page=True,
        )

        print("Zapisano zrzut ekranu.")

    def inspect_page(self):
        """Odczytuje tekst strony, pola formularzy i przyciski."""

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
            "button, input[type=submit], "
            "input[type=button], a, [role=button]"
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
            ).inner_text()[:7000],
            "inputs": inputs,
            "buttons": buttons,
        }

    def login(self):
        """Loguje się za pomocą sekretów GitHub Actions."""

        login = os.environ.get(
            "INSTALING_LOGIN",
            "",
        ).strip()

        password = os.environ.get(
            "INSTALING_PASSWORD",
            "",
        )

        if not login or not password:
            raise RuntimeError(
                "Brak danych logowania. Sprawdź sekrety "
                "INSTALING_LOGIN i INSTALING_PASSWORD."
            )

        page = self.page

        password_fields = page.locator(
            'input[type="password"]'
        )

        # Jeżeli formularza nie ma, szukamy linku logowania.
        if password_fields.count() == 0:

            login_links = page.get_by_role(
                "link",
                name=re.compile(
                    r"zaloguj|logowanie",
                    re.IGNORECASE,
                ),
            )

            if login_links.count() > 0:
                login_links.first.click()

                try:
                    page.wait_for_load_state(
                        "domcontentloaded",
                        timeout=15000,
                    )
                except PlaywrightTimeoutError:
                    pass

                page.wait_for_timeout(1000)

        password_fields = page.locator(
            'input[type="password"]'
        )

        if password_fields.count() == 0:
            self.save_screenshot()

            print(
                "Nie znaleziono formularza hasła."
            )

            print(
                "Aktualny adres:",
                page.url,
            )

            print(
                "Tytuł:",
                page.title(),
            )

            print(
                "Treść strony:",
                page.locator("body").inner_text()[:4000],
            )

            raise RuntimeError(
                "Nie rozpoznano formularza logowania."
            )

        # Szukamy pola loginu.
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
                str(
                    field.get_attribute(attribute) or ""
                )
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

        # Jeśli nazwa pola jest nietypowa, używamy pierwszego
        # widocznego i aktywnego pola tekstowego.
        if login_field is None:

            for index in range(possible_fields.count()):

                field = possible_fields.nth(index)

                if field.is_visible() and field.is_enabled():
                    login_field = field
                    break

        if login_field is None:
            self.save_screenshot()

            raise RuntimeError(
                "Nie znaleziono pola loginu."
            )

        password_field = password_fields.first

        login_field.fill(login)
        password_field.fill(password)

        # Wysyłamy formularz.
        submit = page.locator(
            'button[type="submit"], '
            'input[type="submit"]'
        )

        if submit.count() > 0 and submit.first.is_visible():

            submit.first.click()

        else:

            login_buttons = page.get_by_role(
                "button",
                name=re.compile(
                    r"zaloguj|logowanie|login",
                    re.IGNORECASE,
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

        result = self.inspect_page()
        text_lower = result["text"].lower()

        # Nie uznajemy samego kliknięcia za potwierdzenie sukcesu.
        error_phrases = (
            "nieprawidłowe hasło",
            "nieprawidlowe haslo",
            "błędne hasło",
            "bledne haslo",
            "nieprawidłowy login",
            "nieprawidlowy login",
            "niepoprawne hasło",
        )

        if any(
            phrase in text_lower
            for phrase in error_phrases
        ):
            self.save_screenshot()

            raise RuntimeError(
                "Instaling zgłosił błąd danych logowania."
            )

        if page.locator(
            'input[type="password"]'
        ).count() > 0:

            self.save_screenshot()

            raise RuntimeError(
                "Formularz hasła nadal jest widoczny. "
                "Nie potwierdzono logowania."
            )

        print("Formularz logowania został opuszczony.")
        print("Aktualny adres:", result["url"])
        print("Tytuł strony:", result["title"])
        print("Treść strony:")
        print(result["text"][:4000])

        return result

    def find_session_action(self):
        """
        Wyszukuje elementy, które mogą prowadzić do sesji.

        Nie wymaga dokładnego dopasowania nazwy przycisku.
        Analizuje tekst, adres linku i kontekst.
        """

        page = self.page

        elements = page.locator(
            "a, button, input[type='button'], "
            "input[type='submit'], [role='button']"
        )

        candidates = []

        session_words = (
            "sesj",
            "session",
            "start",
            "rozpoc",
            "zaczn",
            "kontynu",
            "dokończ",
            "dokonc",
            "ćwicz",
            "cwicz",
            "zadani",
            "continue",
            "begin",
            "practice",
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

                aria_label = str(
                    element.get_attribute("aria-label") or ""
                ).strip()

                title = str(
                    element.get_attribute("title") or ""
                ).strip()

                href = str(
                    element.get_attribute("href") or ""
                ).strip()

                description = " ".join(
                    (
                        text,
                        value,
                        aria_label,
                        title,
                        href,
                    )
                ).lower()

                if not description.strip():
                    continue

                score = 0

                for word in session_words:
                    if word in description:
                        score += 2

                if "student" in href.lower():
                    score += 1

                if "session" in href.lower():
                    score += 3

                if "logout" in description or "wyloguj" in description:
                    score -= 10

                if score > 0:

                    candidates.append({
                        "element": element,
                        "text": text or value or aria_label or title,
                        "href": href,
                        "score": score,
                    })

            except Exception:
                continue

        candidates.sort(
            key=lambda item: item["score"],
            reverse=True,
        )

        print("\nPotencjalne elementy sesji:")

        for candidate in candidates[:10]:

            print(
                f"- Ocena: {candidate['score']} | "
                f"Tekst: {candidate['text'][:150]} | "
                f"Link: {candidate['href'][:200]}"
            )

        if not candidates:
            print(
                "Nie znaleziono elementów pasujących "
                "do znanych cech sesji."
            )

            return None

        return candidates[0]["element"]

    def open_session(self):
        """Analizuje panel i próbuje otworzyć sesję."""

        page = self.page

        print("\nAnalizuję panel ucznia...")

        before = self.inspect_page()

        print("Adres:", before["url"])
        print("Tytuł:", before["title"])
        print("Treść panelu:")
        print(before["text"][:4000])

        action = self.find_session_action()

        if action is None:
            self.save_screenshot()

            print(
                "Nie znaleziono odpowiedniego elementu. "
                "Nie klikam przypadkowych przycisków."
            )

            return False

        print("\nKlikam najlepszego kandydata.")

        try:
            action.click(timeout=5000)
            page.wait_for_timeout(2000)

        except Exception as error:
            print(
                "Nie udało się otworzyć elementu:",
                str(error),
            )

            self.save_screenshot()

            return False

        after = self.inspect_page()

        print("\nAdres po kliknięciu:", after["url"])
        print("Tytuł po kliknięciu:", after["title"])
        print("Treść nowego ekranu:")
        print(after["text"][:5000])

        changed = (
            before["url"] != after["url"]
            or before["text"] != after["text"]
        )

        if changed:
            print("\nWykryto zmianę strony.")
        else:
            print("\nNie wykryto zmiany strony.")

        print("\nPola formularza:")

        for field in after["inputs"]:
            print(field)

        print("\nWidoczne przyciski i linki:")

        for button in after["buttons"]:
            print(button)

        self.save_screenshot()

        return changed

    def close(self):
        """Zamyka przeglądarkę."""

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

        print("\nZakończono próbę logowania.")
        print("Adres po logowaniu:", result["url"])

        # Próbujemy otworzyć sesję.
        browser.open_session()

    except Exception as error:

        print(
            "Błąd przeglądarki:",
            str(error),
        )

        raise

    finally:
        browser.close()


if __name__ == "__main__":
    main()
