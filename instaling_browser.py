"""
Instaling Helper — przeglądarka i obsługa sesji.

Funkcje:
- logowanie przez zmienne środowiskowe;
- odczytywanie zadania ze strony;
- korzystanie z agenta tłumaczeń;
- sprawdzanie odpowiedzi przez #sr_status;
- rozpoznawanie poprawności, literówek i błędów;
- zapisywanie potwierdzonych odpowiedzi;
- zapisywanie korekt ujawnionych przez platformę;
- nagrywanie i zrzuty ekranu;
- bezpieczne zatrzymywanie przy nieznanym wyniku.

Wymaga istniejących modułów:
agent.py, memory.py, vocabulary.py, translator.py.
"""

import os
import re
import json
import logging
import time

from pathlib import Path
from datetime import datetime, timezone

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from agent import analyze_word, learn_from_feedback
from memory import start_session, finish_session


# ============================================================
# KONFIGURACJA
# ============================================================

INSTALING_URL = "https://instaling.pl/"

ARTIFACTS_DIR = Path("artifacts")
VIDEO_DIR = ARTIFACTS_DIR / "video"
DATA_DIR = Path("data")

TARGET_SESSIONS = 2
MAX_TASKS_PER_SESSION = 100
MAX_SESSION_START_ATTEMPTS = 8

AUTO_SUBMIT = True
STOP_ON_UNKNOWN_FEEDBACK = True

ANSWER_SELECTOR = "#answer"
STATUS_SELECTOR = "#sr_status"
CAPITALIZATION_SELECTOR = ".blue"
SESSION_BUTTON_SELECTOR = ".btn-start-session"
RETURN_BUTTON_SELECTOR = "#return_mainpage"

PAGE_TIMEOUT_MS = 15000
FEEDBACK_TIMEOUT_MS = 8000


# ============================================================
# POMOCNICZE
# ============================================================

def utc_timestamp():
    return datetime.now(timezone.utc).isoformat()


def normalize(text):
    return " ".join(str(text or "").strip().lower().split())


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


# ============================================================
# PRZEGLĄDARKA
# ============================================================

class InstalingBrowser:

    def __init__(self, headless=True):
        self.headless = headless

        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

        self.logger = setup_logging()

        self.session_id = None
        self.session_started = False
        self.session_completed = False

        self.task_count = 0

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    def start(self):
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        self.playwright = sync_playwright().start()

        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
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

        self.page.set_default_timeout(PAGE_TIMEOUT_MS)

        self.logger.info("Uruchomiono Chromium.")

        self.page.goto(
            INSTALING_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        self.page.wait_for_timeout(1000)

        self.save_screenshot("initial_page.png")

        self.logger.info("Otwarto stronę: %s", self.page.url)

        return self.page.url

    # --------------------------------------------------------
    # DIAGNOSTYKA
    # --------------------------------------------------------

    def save_screenshot(self, name):
        if not self.page:
            return

        try:
            self.page.screenshot(
                path=str(ARTIFACTS_DIR / name),
                full_page=True,
            )
        except Exception as error:
            self.logger.warning(
                "Nie udało się zapisać zrzutu: %s",
                error,
            )

    def save_observation(self, observation):
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        with (DATA_DIR / "observations.jsonl").open(
            "a",
            encoding="utf-8",
        ) as file:
            file.write(
                json.dumps(
                    observation,
                    ensure_ascii=False,
                ) + "\n"
            )

    def page_text(self):
        try:
            return self.page.locator("body").inner_text()
        except Exception:
            return ""

    def inspect_page(self):
        return {
            "url": self.page.url,
            "title": self.page.title(),
            "text": self.page_text()[:12000],
        }

    # --------------------------------------------------------
    # LOGOWANIE
    # --------------------------------------------------------

    def login(self):
        username = os.environ.get(
            "INSTALING_LOGIN", ""
        ).strip()

        password = os.environ.get(
            "INSTALING_PASSWORD", ""
        )

        if not username or not password:
            raise RuntimeError(
                "Ustaw zmienne INSTALING_LOGIN i INSTALING_PASSWORD."
            )

        page = self.page

        password_field = page.locator(
            'input[type="password"]'
        )

        if password_field.count() == 0:
            links = page.get_by_role(
                "link",
                name=re.compile(
                    r"zaloguj|logowanie|login",
                    re.IGNORECASE,
                ),
            )

            if links.count():
                links.first.click()
                page.wait_for_timeout(500)

        password_field = page.locator(
            'input[type="password"]'
        )

        if password_field.count() == 0:
            self.save_screenshot("login_error.png")
            raise RuntimeError(
                "Nie znaleziono pola hasła. "
                "Sprawdź formularz logowania."
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
                token in description
                for token in (
                    "login",
                    "email",
                    "user",
                    "użytkownik",
                    "identyfikator",
                )
            ):
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
            raise RuntimeError("Nie znaleziono pola loginu.")

        login_field.fill(username)
        password_field.first.fill(password)

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
                timeout=10000,
            )
        except PlaywrightTimeoutError:
            pass

        page.wait_for_timeout(1000)

        password_after = page.locator('input[type="password"]')
        try:
            password_still_visible = (
                password_after.count() > 0
                and password_after.first.is_visible()
            )
        except Exception:
            password_still_visible = False

        page_text_after_login = normalize(self.page_text())
        login_error_patterns = (
            r"nieprawidłowe dane",
            r"nieprawidlowe dane",
            r"błędny login",
            r"bledny login",
            r"błędne hasło",
            r"bledne haslo",
            r"nieprawidłowe hasło",
            r"nieprawidlowe haslo",
        )
        login_error_visible = any(
            re.search(pattern, page_text_after_login)
            for pattern in login_error_patterns
        )

        if password_still_visible and login_error_visible:
            self.save_screenshot("login_error.png")
            raise RuntimeError(
                "Logowanie nie powiodło się: widoczny formularz "
                "oraz komunikat o błędzie."
            )

        if password_still_visible:
            self.logger.warning(
                "Pole hasła nadal jest widoczne po logowaniu; "
                "nie uznaję tego automatycznie za błąd."
            )

        if not self.has_answer_field() and login_error_visible:
            self.save_screenshot("login_error.png")
            raise RuntimeError(
                "Nie potwierdzono logowania — strona zawiera "
                "komunikat o błędzie."
            )

        self.logger.info("Zakończono logowanie.")
        self.save_screenshot("after_login.png")

        return self.inspect_page()

    # --------------------------------------------------------
    # ELEMENTY SESJI
    # --------------------------------------------------------

    def has_answer_field(self):
        field = self.page.locator(ANSWER_SELECTOR)

        try:
            return (
                field.count() > 0
                and field.first.is_visible()
                and field.first.is_enabled()
            )
        except Exception:
            return False

    def find_action_by_text(self, patterns):
        elements = self.page.locator(
            "button, a, [role='button'], "
            "input[type='button'], input[type='submit']"
        )

        candidates = []

        for index in range(elements.count()):
            element = elements.nth(index)

            try:
                if not element.is_visible() or not element.is_enabled():
                    continue

                text = str(
                    element.inner_text(timeout=1000) or ""
                ).strip()

                value = str(
                    element.get_attribute("value") or ""
                ).strip()

                aria = str(
                    element.get_attribute("aria-label") or ""
                ).strip()

                description = normalize(
                    " ".join((text, value, aria))
                )

                for pattern, score in patterns:
                    if re.search(
                        pattern,
                        description,
                        re.IGNORECASE,
                    ):
                        candidates.append(
                            (score, element, description)
                        )
                        break

            except Exception:
                continue

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        return candidates[0][1] if candidates else None

    def is_session_complete(self):
        """Rozpoznaje koniec sesji, nie myląc go z ukrytym, starym nagłówkiem."""
        heading = self.page.locator(
            "h2.visually-hidden",
            has_text="Koniec sesji",
        )

        try:
            heading_count = heading.count()
        except Exception as error:
            heading_count = 0
            self.logger.debug(
                "Nie udało się policzyć nagłówków końca sesji: %s",
                error,
            )

        if heading_count > 0:
            # Zapisujemy stan strony, aby można było ustalić, czy nagłówek
            # pochodzi z aktywnej sesji, czy pozostał w DOM po nawigacji.
            try:
                current_url = self.page.url
            except Exception:
                current_url = "<nieznany>"

            try:
                page_text = self.page_text()
            except Exception:
                page_text = "<nie udało się odczytać tekstu strony>"

            # Nie umieszczaj danych logowania w diagnostycznym tekście strony.
            for secret in (
                os.environ.get("INSTALING_LOGIN", "").strip(),
                os.environ.get("INSTALING_PASSWORD", ""),
            ):
                if secret:
                    page_text = page_text.replace(secret, "[REDACTED]")

            try:
                all_headings = self.page.locator("h1, h2, h3").all_inner_texts()
            except Exception:
                all_headings = []

            try:
                answer_count = self.page.locator(ANSWER_SELECTOR).count()
            except Exception:
                answer_count = -1

            try:
                menu_button = self.page.locator(SESSION_BUTTON_SELECTOR)
                menu_count = menu_button.count()
                menu_texts = menu_button.all_inner_texts() if menu_count else []
            except Exception:
                menu_count = -1
                menu_texts = []

            self.logger.warning(
                "DIAGNOSTYKA nagłówka 'Koniec sesji': url=%s; "
                "pasujące_nagłówki=%s; wszystkie_h1_h2_h3=%r; "
                "pola_odpowiedzi=%s; elementy_menu=%s; teksty_menu=%r; "
                "tekst_strony=%r",
                current_url,
                heading_count,
                all_headings,
                answer_count,
                menu_count,
                menu_texts,
                page_text[:12000],
            )
            self.save_screenshot("session_end_heading_diagnostic.png")

            # Pole odpowiedzi jest mocniejszym sygnałem aktywnego zadania niż
            # wizualnie ukryty nagłówek, który może pozostać w DOM.
            if self.has_answer_field():
                self.logger.warning(
                    "Nagłówek 'Koniec sesji' istnieje w DOM, ale pole %s "
                    "jest aktywne; traktuję nagłówek jako pozostałość i "
                    "nie kończę sesji.",
                    ANSWER_SELECTOR,
                )
                return False

            # Potwierdzenie menu wymaga charakterystycznego elementu menu,
            # a nie samej obecności ukrytego nagłówka.
            if menu_count > 0:
                self.logger.info(
                    "Potwierdzono koniec sesji: brak aktywnego pola odpowiedzi "
                    "i obecny element menu %s.",
                    SESSION_BUTTON_SELECTOR,
                )
                return True

            self.logger.warning(
                "Nagłówek 'Koniec sesji' jest w DOM, ale nie ma ani aktywnego "
                "pola odpowiedzi, ani elementu menu %s; sam nagłówek nie "
                "wystarcza do potwierdzenia końca sesji.",
                SESSION_BUTTON_SELECTOR,
            )

        # Tekstowe wzorce awaryjne sprawdzamy tylko wtedy, gdy nie ma aktywnego
        # pola odpowiedzi — w przeciwnym razie tekst mógł pozostać w DOM.
        if self.has_answer_field():
            return False

        text = normalize(self.page_text())
        patterns = (
            r"sesja została zakończona",
            r"sesja zakonczona",
            r"zakończyłeś sesję",
            r"zakonczyles sesje",
            r"zakończyłaś sesję",
            r"zakonczylas sesje",
            r"powrót do menu głównego",
            r"powrot do menu glownego",
        )
        complete = any(re.search(pattern, text) for pattern in patterns)
        if complete:
            self.logger.info(
                "Koniec sesji wykryty na podstawie tekstu strony; "
                "brak aktywnego pola odpowiedzi."
            )
        return complete

    def find_session_action(self):
        """Znajduje akcję sesji przez HTML, a potem przez tekst.

        Zachowuje dotychczasowe wzorce tekstowe i dodatkowo obsługuje
        elementy HTML z klasą .btn-start-session, w tym link
        „Rozpocznij codzienną sesję”.
        """
        # Obsłuż linki HTML do sesji, w tym rozpoczęcie i dokończenie.
        # Przykładowe teksty:
        # - „Rozpocznij codzienną sesję”
        # - „Dokończ sesję”
        # W obu przypadkach sprawdzamy też docelowy adres href.
        session_buttons = self.page.locator(SESSION_BUTTON_SELECTOR)
        accepted_session_names = {
            "rozpocznij codzienną sesję",
            "rozpocznij codzienna sesje",
            "dokończ sesję",
            "dokoncz sesje",
            "kontynuuj sesję",
            "kontynuuj sesje",
            "rozpocznij kolejną sesję",
            "rozpocznij kolejna sesje",
            "rozpocznij sesję",
            "rozpocznij sesje",
            "rozpocznij naukę",
            "rozpocznij nauke",
            "rozpocznij konfigurację sesji",
            "rozpocznij konfiguracje sesji",
            "następna sesja",
            "nastepna sesja",
        }
        for index in range(session_buttons.count()):
            element = session_buttons.nth(index)
            try:
                if not element.is_visible() or not element.is_enabled():
                    continue
                text = normalize(element.inner_text(timeout=1000))
                href = element.get_attribute("href") or ""

                if text not in accepted_session_names:
                    continue
                if "/app/session/app.php" not in href:
                    continue

                self.logger.info(
                    "Znaleziono przycisk sesji HTML: %s | href=%s",
                    text,
                    href,
                )
                return element
            except Exception as error:
                self.logger.debug("Błąd odczytu przycisku sesji HTML: %s", error)

        # Awaryjnie szukamy po tekście, ale dla linków sprawdzamy także href.
        fallback_patterns = (
            (r"kontynuuj sesję|kontynuuj sesje", 120),
            (r"dokończ sesję|dokonc sesje", 115),
            (r"rozpocznij kolejną sesję|rozpocznij kolejna sesje", 110),
            (r"rozpocznij sesję|rozpocznij sesje", 100),
            (r"rozpocznij naukę|rozpocznij nauke", 90),
            (r"następna sesja|nastepna sesja", 80),
        )

        elements = self.page.locator(
            "button, a, [role='button'], "
            "input[type='button'], input[type='submit']"
        )
        candidates = []

        for index in range(elements.count()):
            element = elements.nth(index)
            try:
                if not element.is_visible() or not element.is_enabled():
                    continue

                description = normalize(
                    " ".join((
                        str(element.inner_text(timeout=1000) or ""),
                        str(element.get_attribute("value") or ""),
                        str(element.get_attribute("aria-label") or ""),
                    ))
                )

                score = None
                for pattern, pattern_score in fallback_patterns:
                    if re.search(pattern, description, re.IGNORECASE):
                        score = pattern_score
                        break

                if score is None:
                    continue

                href = element.get_attribute("href")
                if href is not None and href.strip():
                    if "/app/session/app.php" not in href:
                        continue

                candidates.append((score, element, description, href or ""))
            except Exception:
                continue

        candidates.sort(key=lambda item: item[0], reverse=True)

        if candidates:
            score, element, description, href = candidates[0]
            self.logger.info(
                "Znaleziono awaryjny przycisk sesji: %s | href=%s",
                description,
                href,
            )
            return element

        return None

    def is_dont_know_screen(self):
        """Sprawdza, czy widoczny jest ekran z przyciskiem „Nie znam”."""
        button = self.page.locator("#dont_know_new").first
        try:
            return button.count() > 0 and button.is_visible() and button.is_enabled()
        except Exception:
            return False

    def handle_dont_know_screen(self):
        """Obsługuje ekran przykładowego zdania: „Nie znam”, a potem „Pomiń”.

        Zwraca: 'not_present', 'next_task', 'session_complete' albo 'unknown'.
        Nie zakłada konkretnej treści zdania — wykrywa ekran po selektorze.
        """
        button = self.page.locator("#dont_know_new").first
        try:
            if not button.count() or not button.is_visible() or not button.is_enabled():
                return "not_present"

            try:
                sentence = self.page.locator("#question_usage").inner_text(timeout=1000)
            except Exception:
                sentence = ""

            self.logger.info(
                "Wykryto ekran specjalny (#dont_know_new). Zdanie: %r. "
                "Klikam 'Nie znam', a następnie 'Pomiń'.",
                sentence,
            )
            button.click(timeout=5000)

            skip = self.page.locator("#skip").first
            skip.wait_for(state="visible", timeout=5000)
            if not skip.is_enabled():
                self.logger.error("Przycisk #skip jest widoczny, ale nieaktywny.")
                self.save_screenshot("dont_know_skip_disabled.png")
                return "unknown"

            skip.click(timeout=5000)
            self.logger.info("Kliknięto #skip po ekranie 'Nie znam'.")

            # Po pominięciu czekamy na zwykłe zadanie, kolejny ekran specjalny
            # albo potwierdzenie końca sesji. Ekran może pojawić się w dowolnym
            # miejscu sesji, więc nie opieramy się na treści konkretnego zdania.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if self.is_session_complete():
                    self.logger.info(
                        "Po pominięciu ekranu 'Nie znam' wykryto koniec sesji."
                    )
                    return "session_complete"
                if self.has_answer_field():
                    self.logger.info(
                        "Po pominięciu ekranu 'Nie znam' załadowano zwykłe zadanie."
                    )
                    return "next_task"
                if self.is_dont_know_screen():
                    # Kolejny taki ekran z rzędu obsłuży kolejna iteracja pętli.
                    self.logger.info(
                        "Po pominięciu pojawił się kolejny ekran 'Nie znam'."
                    )
                    return "next_task"
                self.page.wait_for_timeout(150)

            self.logger.warning(
                "Po kliknięciu #skip nie pojawiło się zwykłe zadanie ani koniec sesji."
            )
            self.save_screenshot("dont_know_after_skip_timeout.png")
            return "unknown"
        except Exception as error:
            self.logger.warning(
                "Nie udało się obsłużyć ekranu 'Nie znam' / 'Pomiń': %s",
                error,
            )
            self.save_screenshot("dont_know_screen_error.png")
            return "unknown"

    def open_session(self):
        for attempt in range(MAX_SESSION_START_ATTEMPTS):
            special_result = self.handle_dont_know_screen()
            if special_result == "unknown":
                return False
            if special_result == "session_complete":
                return False

            if self.has_answer_field():
                if not self.session_started:
                    self.session_id = datetime.now(
                        timezone.utc
                    ).strftime("%Y%m%d_%H%M%S")

                    start_session(self.session_id)

                    self.session_started = True
                    self.session_completed = False
                    self.task_count = 0

                    self.logger.info(
                        "Rozpoczęto sesję: %s",
                        self.session_id,
                    )

                return True

            if self.is_session_complete():
                return False

            action = self.find_session_action()

            if action is None:
                self.save_screenshot(
                    f"session_start_{attempt + 1}.png"
                )
                self.logger.error(
                    "Nie znaleziono przycisku rozpoczęcia sesji."
                )
                return False

            try:
                action.click(timeout=5000)
                self.page.wait_for_timeout(1000)
            except Exception as error:
                self.logger.error(
                    "Nie udało się rozpocząć sesji: %s",
                    error,
                )
                return False

        return self.has_answer_field()

    # --------------------------------------------------------
    # ROZPOZNAWANIE ZADANIA
    # --------------------------------------------------------

    def read_task(self):
        """Odczytuje bieżące zdanie i dowolną polską podpowiedź z InstaLing."""
        if not self.has_answer_field():
            return None

        sentence = ""
        polish = ""

        # Treść zadania może pojawić się chwilę po polu odpowiedzi, więc
        # krótko czekamy na aktualne elementy zamiast sprawdzać count() od razu.
        usage = self.page.locator("#question_usage").first
        try:
            usage.wait_for(state="attached", timeout=3000)
            # Usuwamy ukryty tekst dostępności (np. „luka”), ale zachowujemy
            # widoczne podkreślenie w miejscu brakującego słowa.
            sentence = usage.evaluate(r"""el => {
                const clone = el.cloneNode(true);
                clone.querySelectorAll('.visually-hidden').forEach(n => n.remove());
                return (clone.innerText || clone.textContent || '').replace(/\s+/g, ' ').trim();
            }""")
            sentence = str(sentence or "").strip()
        except Exception as error:
            self.logger.warning("Nie udało się odczytać #question_usage: %s", error)

        # Nie zakładamy żadnego konkretnego polskiego słowa. Odczytujemy
        # tekst aktualnego elementu #question_translation, niezależnie od tego,
        # czy to „występować”, „malarstwo”, „główny bohater” czy inna podpowiedź.
        translation = self.page.locator("#question_translation").first
        try:
            translation.wait_for(state="attached", timeout=3000)
            polish = (translation.inner_text(timeout=2000) or "").strip()
        except Exception as error:
            self.logger.warning("Nie udało się odczytać #question_translation: %s", error)

        return {
            "text": self.page_text()[:12000],
            "sentence": sentence,
            "polish": polish,
            "url": self.page.url,
        }

    def extract_polish_word(self, task):
        """Najpierw czyta #question_translation, a dopiero potem używa fallbacku."""
        direct = str(task.get("polish", "")).strip()
        if direct and len(direct) < 100:
            return direct

        text = task.get("text", "")
        patterns = (
            r"(?:przetłumacz|przetlumacz)\s*:?\s*([^\n]+)",
            r"(?:po polsku|polskie słowo|polskie slowo)\s*:?\s*([^\n]+)",
        )

        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                candidate = match.group(1).strip()
                if candidate and len(candidate) < 100:
                    return candidate

        return None

    # --------------------------------------------------------
    # HTML: WYNIK ODPOWIEDZI
    # --------------------------------------------------------

    def read_status_element(self):
        """
        Odczytuje wyłącznie #sr_status.

        Przykładowe komunikaty:
        Dobrze!
        Literówka
        Niepoprawnie Poprawna odpowiedź: audience
        """

        status = self.page.locator(STATUS_SELECTOR)

        try:
            if status.count() > 0:
                message = status.first.inner_text().strip()
                if message:
                    return message
        except Exception:
            pass

        # Dodatkowy komunikat HTML używany m.in. dla wielkości liter.
        capitalization = self.page.locator(CAPITALIZATION_SELECTOR)
        try:
            if capitalization.count() > 0 and capitalization.first.is_visible():
                return capitalization.first.inner_text().strip()
        except Exception:
            pass

        return ""

    def classify_feedback(self, text):
        value = re.sub(
            r"\s+",
            " ",
            str(text or "").strip().lower(),
        )
        if not value:
            result = "unknown"
        # Najpierw sprawdzamy błędy.
        elif re.search(
            r"niepoprawnie|niepoprawna odpowiedź|"
            r"niepoprawna odpowiedz",
            value,
        ):
            result = "incorrect"
        elif re.search(r"literówk|literowk", value):
            result = "typo"
        elif re.search(r"synonim", value):
            result = "synonym"
        # Akceptujemy wyłącznie znane krótkie komunikaty pozytywne.
        elif re.fullmatch(
            r"(dobrze|poprawnie|prawidłowo|prawidlowo)[!. ]*",
            value,
        ):
            result = "correct"
        else:
            result = "unknown"

        # Pełny status zapisujemy diagnostycznie, bez danych logowania.
        self.logger.info(
            "Klasyfikacja statusu: tekst=%r | wynik=%s",
            str(text or ""),
            result,
        )
        return result

    def find_correct_answer(self, text):
        """
        Odczytuje odpowiedź podaną jawnie przez platformę.
        """

        patterns = (
            r"poprawna odpowiedź\s*:\s*([^\n]+)",
            r"poprawna odpowiedz\s*:\s*([^\n]+)",
            r"prawidłowa odpowiedź\s*:\s*([^\n]+)",
            r"prawidlowa odpowiedz\s*:\s*([^\n]+)",
            r"powinno być\s*:\s*([^\n]+)",
            r"powinno byc\s*:\s*([^\n]+)",
        )

        for pattern in patterns:
            match = re.search(
                pattern,
                text or "",
                flags=re.IGNORECASE,
            )

            if match:
                answer = match.group(1).strip()

                # Usuwamy ewentualny dodatkowy tekst.
                answer = answer.splitlines()[0].strip()

                if answer:
                    return answer

        return None

    def wait_for_feedback_change(self, previous_status):
        deadline = time.monotonic() + (FEEDBACK_TIMEOUT_MS / 1000)

        while time.monotonic() < deadline:
            current = self.read_status_element()

            if current and current != previous_status:
                self.logger.info(
                    "Status zmienił się: poprzedni=%r | aktualny=%r",
                    previous_status,
                    current,
                )
                return current

            self.page.wait_for_timeout(100)

        current = self.read_status_element()
        self.logger.warning(
            "Brak zmiany statusu po %s ms: poprzedni=%r | aktualny=%r | przyczyna=%s",
            FEEDBACK_TIMEOUT_MS,
            previous_status,
            current,
            "brak komunikatu" if not current else "powtórzony komunikat",
        )

        if current and current != previous_status:
            return current

        return ""
    def find_submit_button(self):
        return self.find_action_by_text((
            (r"^sprawdź$|^sprawdz$", 120),
            (r"^zatwierdź$|^zatwierdz$", 110),
            (r"^odpowiedz$", 100),
        ))

    def find_next_task_button(self):
        button = self.page.locator("#next_word")

        try:
            if (
                button.count() > 0
                and button.first.is_visible()
                and button.first.is_enabled()
            ):
                return button.first
        except Exception:
            pass

        # Fallback dla innych wariantów strony.
        return self.find_action_by_text((
            (r"^następne$", 130),
            (r"^następne słowo$|^nastepne slowo$", 120),
            (r"^kolejne słowo$|^kolejne slowo$", 115),
            (r"^następne zadanie$|^nastepne zadanie$", 110),
            (r"^dalej$", 90),
            (r"^kontynuuj$", 80),
        ))

    def go_to_next_task(self):
        if self.is_session_complete():
            return "session_complete"

        action = self.find_next_task_button()

        if action is None:
            self.save_screenshot("next_task_button_missing.png")
            return "unknown"

        try:
            action.click(timeout=5000)

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if self.is_session_complete():
                    return "session_complete"
                if self.has_answer_field():
                    break
                if self.is_dont_know_screen():
                    self.logger.info(
                        "Po przejściu dalej pojawił się ekran specjalny #dont_know_new; "
                        "zostanie obsłużony przed zwykłym zadaniem."
                    )
                    return "next_task"
                self.page.wait_for_timeout(100)
            else:
                self.save_screenshot("next_task_timeout.png")
                self.logger.warning(
                    "Po kliknięciu przycisku następnego zadania nie pojawiło się "
                    "ani widoczne #answer, ani #dont_know_new."
                )
                return "unknown"
        except Exception as error:
            self.logger.warning(
                "Nie udało się przejść dalej: %s",
                error,
            )
            return "unknown"

        if self.is_session_complete():
            return "session_complete"

        return "next_task" if self.has_answer_field() else "unknown"

    # --------------------------------------------------------
    # ROZWIĄZYWANIE ZADANIA
    # --------------------------------------------------------

    def process_task(self):
        if self.is_session_complete():
            return "session_complete"

        if self.is_dont_know_screen():
            special_result = self.handle_dont_know_screen()
            if special_result in ("next_task", "session_complete"):
                return special_result
            if special_result == "unknown":
                return "stop"

        task = self.read_task()

        if task is None:
            self.logger.warning("Brak pola odpowiedzi.")
            self.save_screenshot("answer_field_missing.png")
            return "stop"

        polish = self.extract_polish_word(task)

        self.logger.info("URL zadania: %s", task.get("url", ""))
        self.logger.info("Odczytane zdanie (#question_usage): %r", task.get("sentence", ""))
        self.logger.info("Odczytana podpowiedź (#question_translation): %r", task.get("polish", ""))
        self.logger.info("Hasło przekazane do agenta: %r", polish)

        if not polish:
            self.logger.warning(
                "Nie udało się jednoznacznie rozpoznać słowa."
            )

            self.save_observation({
                "session_id": self.session_id,
                "result": "word_not_detected",
                "task_text": task["text"],
                "sentence": task.get("sentence", ""),
                "polish": task.get("polish", ""),
                "timestamp": utc_timestamp(),
            })

            self.save_screenshot("word_not_detected.png")
            return "stop"

        self.logger.info("Rozpoznane słowo: %s", polish)

        try:
            result = analyze_word(
                polish,
                context=(task.get("sentence") or task["text"]),
            )
        except Exception:
            self.logger.exception("Błąd agenta tłumaczeń.")
            return "stop"

        candidates = result.get("translations", [])

        if not candidates:
            self.logger.warning(
                "Agent nie zwrócił żadnych tłumaczeń dla: %s. "
                "Pole #answer nie zostanie wypełnione, bo nie ma odpowiedzi do wpisania.",
                polish,
            )
            self.save_screenshot("translation_missing.png")

            self.save_observation({
                "session_id": self.session_id,
                "polish": polish,
                "result": "translation_needed",
                "timestamp": utc_timestamp(),
            })

            return "stop"

        # Agent sortuje odpowiedzi tak, aby potwierdzone
        # odpowiedzi z pamięci miały pierwszeństwo.
        candidate = candidates[0]

        answer = str(candidate.get("answer", "")).strip()

        if not answer:
            return "stop"

        self.logger.info(
            "Wybrana odpowiedź: %s | źródło=%s | potwierdzona=%s",
            answer,
            candidate.get("source"),
            candidate.get("verified"),
        )

        answer_field = self.page.locator(ANSWER_SELECTOR).first

        # Zapamiętujemy poprzedni komunikat PRZED wpisaniem
        # odpowiedzi i kliknięciem przycisku.
        previous_status = self.read_status_element()

        try:
            answer_field.wait_for(state="visible", timeout=3000)
            if not answer_field.is_enabled():
                raise RuntimeError("Pole #answer jest wyłączone.")
            answer_field.fill(answer, timeout=5000)

            # Potwierdzamy odczytem wartości, że wpisanie rzeczywiście się udało.
            actual_value = answer_field.input_value(timeout=3000)
            if actual_value.strip() != answer:
                self.logger.error(
                    "Weryfikacja wpisania nie powiodła się: oczekiwano %r, pole zawiera %r",
                    answer, actual_value,
                )
                self.save_screenshot("answer_fill_failed.png")
                return "stop"

            self.logger.info("Potwierdzono wpisanie do #answer: %r", actual_value)
            self.save_screenshot("answer_filled.png")
        except Exception:
            self.logger.exception("Nie udało się wpisać lub zweryfikować odpowiedzi w #answer.")
            self.save_screenshot("answer_fill_failed.png")
            return "stop"

        if not AUTO_SUBMIT:
            self.logger.info(
                "AUTO_SUBMIT=False — odpowiedź nie została wysłana."
            )
            return "stop"

        submit_button = self.find_submit_button()

        if submit_button is None:
            self.logger.warning(
                "Nie znaleziono przycisku sprawdzania."
            )
            self.save_screenshot("submit_button_missing.png")
            return "stop"

        try:
            submit_button.click(timeout=5000)
        except Exception:
            self.logger.exception(
                "Nie udało się wysłać odpowiedzi."
            )
            return "stop"

        # Najważniejsza zmiana: czekamy na komunikat HTML.
        feedback_text = self.wait_for_feedback_change(
            previous_status
        )

        feedback_type = self.classify_feedback(feedback_text)

        self.logger.info(
            "HTML #sr_status: %r",
            feedback_text,
        )

        self.logger.info(
            "Rozpoznany wynik: %s",
            feedback_type,
        )

        self.save_screenshot("answer_feedback.png")

        correction = self.find_correct_answer(feedback_text)

        self.save_observation({
            "session_id": self.session_id,
            "polish": polish,
            "candidate": answer,
            "source": candidate.get("source"),
            "feedback_type": feedback_type,
            "feedback_text": feedback_text,
            "correction": correction,
            "timestamp": utc_timestamp(),
        })

        # Nieznany komunikat oznacza zatrzymanie.
        if feedback_type == "unknown":
            self.logger.warning(
                "Nie udało się zweryfikować odpowiedzi. "
                "Nie zapisuję jej jako poprawnej."
            )

            if STOP_ON_UNKNOWN_FEEDBACK:
                return "stop"

            return "stop"

        # ----------------------------------------------------
        # ZAPIS ODPOWIEDZI
        # ----------------------------------------------------

        if feedback_type == "correct":
            learn_from_feedback(
                polish=polish,
                answer=answer,
                accepted=True,
                context=task["text"],
                session_id=self.session_id,
                feedback_type="correct",
            )

            self.logger.info(
                "Potwierdzona odpowiedź zapisana: %s -> %s",
                polish,
                answer,
            )

        elif feedback_type == "synonym":
            # Synonim traktujemy jako zaakceptowany tylko
            # wtedy, gdy platforma wyraźnie tak informuje.
            learn_from_feedback(
                polish=polish,
                answer=answer,
                accepted=True,
                context=task["text"],
                session_id=self.session_id,
                feedback_type="synonym",
            )

            self.logger.info(
                "Zaakceptowany synonim zapisany: %s -> %s",
                polish,
                answer,
            )

        elif feedback_type == "incorrect":
            learn_from_feedback(
                polish=polish,
                answer=answer,
                accepted=False,
                correction=correction,
                context=task["text"],
                session_id=self.session_id,
                feedback_type="incorrect",
            )

            if correction:
                self.logger.info(
                    "Zapisano poprawną odpowiedź z HTML: %s -> %s",
                    polish,
                    correction,
                )
            else:
                self.logger.warning(
                    "Platforma odrzuciła odpowiedź, "
                    "ale nie podała poprawnej formy."
                )

        elif feedback_type == "typo":
            # "Literówka" nie zawiera automatycznie prawidłowej
            # formy. Nie zapisujemy wpisanej odpowiedzi
            # jako potwierdzonej.
            learn_from_feedback(
                polish=polish,
                answer=answer,
                accepted=False,
                correction=correction,
                context=task["text"],
                session_id=self.session_id,
                feedback_type="typo",
            )

            self.logger.warning(
                "Wykryto literówkę. Odpowiedź nie została "
                "zapisana jako potwierdzona."
            )

        self.task_count += 1

        self.logger.info(
            "Przetworzone zadania: %s",
            self.task_count,
        )

        if self.is_session_complete():
            return "session_complete"

        next_result = self.go_to_next_task()

        if next_result == "session_complete":
            return "session_complete"

        if next_result == "unknown":
            self.logger.warning(
                "Nie rozpoznano następnego ekranu."
            )
            return "stop"

        return "next_task"

    # --------------------------------------------------------
    # PEŁNA SESJA
    # --------------------------------------------------------

    def run_one_session(self):
        if not self.open_session():
            self.logger.error("Nie udało się otworzyć sesji.")
            return False

        for index in range(MAX_TASKS_PER_SESSION):
            self.logger.info(
                "Zadanie %s/%s",
                index + 1,
                MAX_TASKS_PER_SESSION,
            )

            if self.is_session_complete():
                self.logger.info(
                    "run_one_session kończy pętlę: wykryto koniec sesji przed process_task()."
                )
                self.session_completed = True
                break

            outcome = self.process_task()
            self.logger.info(
                "process_task() zwróciło: %r",
                outcome,
            )

            if outcome == "session_complete":
                self.logger.info(
                    "run_one_session kończy pętlę: process_task() zgłosiło koniec sesji."
                )
                self.session_completed = True
                break

            if outcome == "stop":
                self.logger.warning(
                    "run_one_session kończy się przez process_task() == 'stop', a nie przez wykrycie końca sesji."
                )
                self.save_screenshot("session_stopped.png")
                return False

            if outcome != "next_task":
                return False

        if not self.session_completed:
            self.logger.error(
                "Nie wykryto końca sesji w limicie zadań."
            )
            self.save_screenshot("session_limit_reached.png")
            return False

        finish_session(self.session_id)

        self.logger.info(
            "Potwierdzono zakończenie sesji: %s",
            self.session_id,
        )

        self.save_screenshot("session_completed.png")

        # Pozwalamy rozpocząć kolejną sesję z nowym ID.
        self.session_started = False

        return True

    # --------------------------------------------------------
    # POWRÓT DO MENU
    # --------------------------------------------------------

    def return_to_menu(self):
        # Obsługa konkretnego przycisku HTML z podanego przykładu.
        action = self.page.locator(RETURN_BUTTON_SELECTOR).first
        try:
            if not action.count() or not action.is_visible() or not action.is_enabled():
                action = None
        except Exception:
            action = None

        # Zachowaj dotychczasowe wzorce tekstowe jako mechanizm awaryjny.
        if action is None:
            action = self.find_action_by_text((
                (r"^wróć do menu głównego$|^wroc do menu glownego$", 120),
                (r"^powrót do menu głównego$|^powrot do menu glownego$", 115),
                (r"^wróć do menu$|^wroc do menu$", 100),
            ))

        if action is None:
            self.save_screenshot("return_to_menu_error.png")

            # Nie klikamy przypadkowego przycisku.
            if self.has_answer_field():
                return True

            raise RuntimeError(
                "Nie znaleziono przycisku powrotu do menu."
            )

        action.click(timeout=5000)

        # Powrót potwierdzamy elementem charakterystycznym dla menu.
        # state="attached" sprawdza obecność w DOM, bez wymogu widoczności.
        menu_entry = self.page.locator(SESSION_BUTTON_SELECTOR).first
        try:
            menu_entry.wait_for(
                state="attached",
                timeout=PAGE_TIMEOUT_MS,
            )
        except PlaywrightTimeoutError:
            self.logger.error(
                "Kliknięto powrót do menu, ale element menu %r nie pojawił się w DOM w ciągu %s ms.",
                SESSION_BUTTON_SELECTOR,
                PAGE_TIMEOUT_MS,
            )
            self.save_screenshot("return_to_menu_error.png")
            raise RuntimeError(
                "Nie potwierdzono powrotu do menu: brak elementu "
                f"{SESSION_BUTTON_SELECTOR!r} w DOM."
            )

        self.logger.info(
            "Potwierdzono powrót do menu: element %r jest obecny w DOM.",
            SESSION_BUTTON_SELECTOR,
        )
        self.save_screenshot("main_menu.png")
        return True

    # --------------------------------------------------------

    # WYKONANIE SESJI
    # --------------------------------------------------------

    def run_two_sessions(self):
        for session_number in range(1, TARGET_SESSIONS + 1):
            self.logger.info(
                "========== SESJA %s/%s ==========",
                session_number,
                TARGET_SESSIONS,
            )

            if not self.run_one_session():
                self.logger.error(
                    "Nie udało się ukończyć sesji %s.",
                    session_number,
                )
                return False

            if session_number < TARGET_SESSIONS:
                self.return_to_menu()
                self.page.wait_for_timeout(1000)

        self.logger.info("Ukończono wszystkie %s sesje.", TARGET_SESSIONS)
        return True
    def close(self):
        if self.context:
            try:
                self.context.close()
            except Exception:
                self.logger.exception(
                    "Błąd zamykania kontekstu."
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

        self.logger.info("Zamknięto przeglądarkę.")


# ============================================================
# MAIN
# ============================================================

def main():
    browser = InstalingBrowser(headless=True)

    try:
        browser.start()
        browser.login()

        success = browser.run_two_sessions()

        if success:
            browser.logger.info(
                "SUKCES: ukończono wszystkie sesje."
            )
        else:
            browser.logger.error(
                "Praca została przerwana. "
                "Sprawdź artifacts/run.log."
            )

    except Exception:
        browser.logger.exception(
            "Wystąpił nieoczekiwany błąd."
        )
        raise

    finally:
        browser.close()


if __name__ == "__main__":
    main()
