"""
Instaling Helper — przeglądarka i obsługa zadań.

Funkcje:
- logowanie przez GitHub Secrets;
- analiza strony i formularzy;
- rozpoznawanie ekranu sesji;
- odczytywanie zadania;
- bezpieczne przygotowanie odpowiedzi;
- sprawdzanie wyniku;
- zapisywanie zrzutów ekranu;
- ograniczony tryb testowy.

Integracja z agent.py zostanie aktywowana po potwierdzeniu
dokładnego interfejsu modułu tłumaczącego.
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

# Na początek analizujemy tylko jedno zadanie.
MAX_TEST_TASKS = 1


class InstalingBrowser:

    def __init__(self, headless=True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.page = None

    def start(self):
        """Uruchamia Chromium i otwiera InstaLing."""

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

    def save_screenshot(self, name="instaling.png"):
        """Zapisuje zrzut ekranu."""

        if self.page is None:
            return

        ARTIFACTS_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.page.screenshot(
            path=str(ARTIFACTS_DIR / name),
            full_page=True,
        )

        print(f"Zapisano zrzut ekranu: {name}")

    def inspect_page(self):
        """Zwraca tekst strony, widoczne pola i przyciski."""

        if self.page is None:
            raise RuntimeError("Przeglądarka nie jest uruchomiona.")

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
            "text": self.page.locator("body").inner_text()[:7000],
            "inputs": inputs,
            "buttons": buttons,
        }

    def login(self):
        """Loguje się za pomocą sekretów GitHub Actions."""

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
            raise RuntimeError("Nie znaleziono pola loginu.")

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
        text = result["text"].lower()

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

        print("Formularz logowania został opuszczony.")
        print("Adres:", result["url"])
        print("Tytuł:", result["title"])

        return result

    def find_session_action(self):
        """Wyszukuje widoczne elementy prowadzące do sesji."""

        elements = self.page.locator(
            "a, button, input[type=button], "
            "input[type=submit], [role=button]"
        )

        candidates = []

        words = (
            "kontynuuj",
            "dokończ sesję",
            "dokonc",
            "rozpocznij sesję",
            "rozpocznij sesje",
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
                ).lower()

                score = sum(
                    3 for word in words
                    if word in description
                )

                if "session" in href.lower():
                    score += 2

                if score:
                    candidates.append(
                        (score, element, text or value or href)
                    )

            except Exception:
                continue

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        for score, _, description in candidates[:5]:
            print(
                f"Element sesji: {description[:150]} "
                f"(ocena {score})"
            )

        return candidates[0][1] if candidates else None

    def open_session(self):
        """Otwiera najlepszy rozpoznany element sesji."""

        before = self.inspect_page()

        print("\nAnalizuję panel ucznia...")
        print(before["text"][:2500])

        action = self.find_session_action()

        if action is None:
            self.save_screenshot("session_not_found.png")
            print("Nie znaleziono przycisku sesji.")
            return False

        try:
            action.click(timeout=5000)
            self.page.wait_for_timeout(1500)
        except Exception as error:
            print("Nie udało się otworzyć sesji:", str(error))
            self.save_screenshot("session_error.png")
            return False

        after = self.inspect_page()

        print("\nEkran po kliknięciu:")
        print(after["text"][:4000])

        self.save_screenshot("session.png")

        return True

    def read_task(self):
        """
        Odczytuje aktualny ekran zadania.

        Nie zakłada, że każde zadanie jest pojedynczym słowem.
        Na tym etapie tylko zbiera informacje — nie zgaduje
        tłumaczenia i nie wysyła odpowiedzi.
        """

        page = self.page
        answer = page.locator("#answer")

        if answer.count() == 0 or not answer.first.is_visible():
            print("Nie znaleziono widocznego pola #answer.")
            return None

        task = {
            "text": page.locator("body").inner_text()[:7000],
            "answer_selector": "#answer",
            "url": page.url,
        }

        print("\nRozpoznano ekran zadania.")
        print("Treść strony:")
        print(task["text"][:3000])

        return task

    def inspect_answer_result(self):
        """
        Odczytuje wynik sprawdzenia odpowiedzi.

        Wynik 'unknown' oznacza, że nie znaleziono
        wystarczająco pewnego komunikatu.
        """

        text = self.page.locator("body").inner_text()
        normalized = text.casefold()

        if re.search(r"\bniepoprawnie\b|\bniepoprawna odpowiedź\b", normalized):
            result = "incorrect"

        elif re.search(r"\bdobrze!\b|\bpoprawnie\b", normalized):
            result = "correct"

        else:
            result = "unknown"

        return {
            "result": result,
            "text": text[:7000],
        }

    def run_test(self):
        """
        Bezpieczny test rozpoznawania zadania.

        Celowo nie wpisuje odpowiedzi, nie klika 'Sprawdź'
        i nie przechodzi automatycznie do następnego zadania.
        """

        task = self.read_task()

        if task is None:
            print(
                "Nie potwierdzono ekranu zadania. "
                "Nie wykonuję dalszych akcji."
            )
            self.save_screenshot("task_not_found.png")
            return False

        self.save_screenshot("task_detected.png")

        print(
            "\nTest zakończony: rozpoznano pole odpowiedzi. "
            "Automatyczne rozwiązywanie nie zostało jeszcze "
            "włączone."
        )

        return True

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

        print("\nLogowanie zakończone.")
        print("Adres:", result["url"])

        if browser.open_session():
            browser.run_test()

    except Exception as error:
        print("Błąd przeglądarki:", str(error))
        raise

    finally:
        browser.close()


if __name__ == "__main__":
    main()
