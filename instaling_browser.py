"""
Instaling Helper — moduł automatyzacji przeglądarki.

Odpowiada za:
- otwieranie Instaling,
- obserwowanie aktualnego ekranu,
- przekazywanie treści zadania do istniejącego agenta.

Nie wykonuje jeszcze automatycznego logowania ani
nie wysyła odpowiedzi. Najpierw przygotowujemy stabilną
warstwę komunikacji z przeglądarką.
"""

import os
from playwright.sync_api import sync_playwright


INSTALING_URL = "https://instaling.pl/"


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
            locale="pl-PL"
        )

        self.page.goto(
            INSTALING_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        return self.page.url

    def inspect_page(self):
        """Zwraca aktualny adres i widoczny tekst strony."""
        if self.page is None:
            raise RuntimeError(
                "Najpierw uruchom przeglądarkę metodą start()."
            )

        return {
            "url": self.page.url,
            "title": self.page.title(),
            "text": self.page.locator("body").inner_text(
                timeout=15000
            ),
        }

    def close(self):
        """Zamyka przeglądarkę i zwalnia zasoby."""
        if self.browser:
            self.browser.close()

        if self.playwright:
            self.playwright.stop()


def main():
    browser = InstalingBrowser(headless=True)

    try:
        url = browser.start()
        print("Otwarto stronę:", url)

        page_data = browser.inspect_page()

        print("Tytuł:", page_data["title"])
        print("\nWidoczny tekst strony:")
        print(page_data["text"][:5000])

    except Exception as error:
        print("Nie udało się otworzyć Instaling:", error)
        raise

    finally:
        browser.close()


if __name__ == "__main__":
    main()
