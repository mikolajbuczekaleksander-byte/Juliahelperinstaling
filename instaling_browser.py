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

    def is_session_complete(self, allow_menu_signal=True):
        """Nie uznaje samego ukrytego nagłówka za koniec sesji."""

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

        if self.has_answer_field():
            if heading_count:
                self.logger.info(
                    "Nagłówek 'Koniec sesji' istnieje w DOM, ale "
                    "pole odpowiedzi jest aktywne; sesja trwa dalej."
                )
            return False

        try:
            menu_buttons = self.page.locator(SESSION_BUTTON_SELECTOR)
            menu_count = menu_buttons.count()
            menu_texts = [
                normalize(value)
                for value in menu_buttons.all_inner_texts()
            ] if menu_count else []
        except Exception:
            menu_count = 0
            menu_texts = []

        resumable_labels = (
            "kontynuuj sesję",
            "kontynuuj sesje",
            "dokończ sesję",
            "dokoncz sesje",
        )

        if any(
            label in text
            for text in menu_texts
            for label in resumable_labels
        ):
            self.logger.info(
                "Wykryto możliwość kontynuowania sesji: %r",
                menu_texts,
            )
            return False

        if menu_count and not allow_menu_signal:
            self.logger.debug(
                "Przycisk menu %r; podczas otwierania sesji "
                "traktuję go jako akcję do kliknięcia.",
                menu_texts,
            )
            return False

        text = normalize(self.page_text())

        completion_patterns = (
            r"sesja została zakończona",
            r"sesja zostala zakonczona",
            r"sesja zakończona",
            r"sesja zakonczona",
            r"zakończyłeś sesję",
            r"zakonczyles sesje",
            r"zakończyłaś sesję",
            r"zakonczylas sesje",
            r"powrót do menu głównego",
            r"powrot do menu glownego",
        )

        explicit_completion_text = any(
            re.search(pattern, text)
            for pattern in completion_patterns
        )

        next_session_labels = (
            "rozpocznij kolejną sesję",
            "rozpocznij kolejna sesje",
            "rozpocznij następną sesję",
            "rozpocznij nastepna sesje",
        )

        next_session_in_menu = any(
            label in menu_text
            for menu_text in menu_texts
            for label in next_session_labels
        )

        if heading_count:
            try:
                current_url = self.page.url
            except Exception:
                current_url = "<nieznany>"

            try:
                all_headings = self.page.locator(
                    "h1, h2, h3"
                ).all_inner_texts()
            except Exception:
                all_headings = []

            try:
                page_text = self.page_text()
            except Exception:
                page_text = "<nie udało się odczytać tekstu strony>"

            for secret in (
                os.environ.get("INSTALING_LOGIN", "").strip(),
                os.environ.get("INSTALING_PASSWORD", ""),
            ):
                if secret:
                    page_text = page_text.replace(
                        secret,
                        "[REDACTED]",
                    )

            self.logger.info(
                "DIAGNOSTYKA nagłówka 'Koniec sesji': url=%s; "
                "nagłówki=%r; przyciski_menu=%r; "
                "jawny_tekst_końca=%s; przycisk_kolejnej_sesji=%s; "
                "tekst_strony=%r",
                current_url,
                all_headings,
                menu_texts,
                explicit_completion_text,
                next_session_in_menu,
                page_text[:12000],
            )

            self.save_screenshot(
                "session_end_heading_diagnostic.png"
            )

        complete = (
            explicit_completion_text
            or next_session_in_menu
        )

        if complete:
            self.logger.info(
                "Potwierdzono koniec sesji: tekst końca=%s, "
                "przycisk kolejnej sesji=%s.",
                explicit_completion_text,
                next_session_in_menu,
            )
        elif heading_count:
            self.logger.info(
                "Sam nagłówek 'Koniec sesji' nie wystarczył "
                "do potwierdzenia końca."
            )

        return complete
            def find_session_action(self):
        """Znajduje akcję sesji, w tym przycisk „Kontynuuj sesję”."""
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
                tag_name = element.evaluate(
                    "el => el.tagName.toLowerCase()"
                )

                if text not in accepted_session_names:
                    continue

                if tag_name == "a" and href and "/app/session/app.php" not in href:
                    continue
                if tag_name == "a" and not href:
                    continue

                self.logger.info(
                    "Znaleziono przycisk sesji HTML: %s | tag=%s | href=%s",
                    text,
                    tag_name,
                    href or "(brak; przycisk HTML)",
                )
                return element

            except Exception as error:
                self.logger.debug(
                    "Błąd odczytu przycisku sesji HTML: %s",
                    error,
                )

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

                candidates.append(
                    (score, element, description, href or "")
                )

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
        """Sprawdza, czy widoczny jest przycisk „Nie znam”."""
        button = self.page.locator("#dont_know_new").first
        try:
            return (
                button.count() > 0
                and button.is_visible()
                and button.is_enabled()
            )
        except Exception:
            return False

    def handle_dont_know_screen(self):
        """Kliknie „Nie znam”, potem „Pomiń”."""
        button = self.page.locator("#dont_know_new").first

        try:
            if (
                not button.count()
                or not button.is_visible()
                or not button.is_enabled()
            ):
                return "not_present"

            try:
                sentence = self.page.locator(
                    "#question_usage"
                ).inner_text(timeout=1000)
            except Exception:
                sentence = ""

            self.logger.info(
                "Wykryto ekran specjalny (#dont_know_new). "
                "Zdanie: %r. Klikam 'Nie znam', a następnie 'Pomiń'.",
                sentence,
            )

            button.click(timeout=5000)

            skip = self.page.locator("#skip").first
            skip.wait_for(state="visible", timeout=5000)

            if not skip.is_enabled():
                self.logger.error(
                    "Przycisk #skip jest widoczny, ale nieaktywny."
                )
                self.save_screenshot("dont_know_skip_disabled.png")
                return "unknown"

            skip.click(timeout=5000)
            self.logger.info("Kliknięto #skip po ekranie 'Nie znam'.")

            deadline = time.monotonic() + 10

            while time.monotonic() < deadline:
                if self.is_session_complete():
                    self.logger.info(
                        "Po pominięciu ekranu 'Nie znam' "
                        "wykryto koniec sesji."
                    )
                    return "session_complete"

                if self.has_answer_field():
                    self.logger.info(
                        "Po pominięciu ekranu 'Nie znam' "
                        "załadowano zwykłe zadanie."
                    )
                    return "next_task"

                if self.is_dont_know_screen():
                    self.logger.info(
                        "Po pominięciu pojawił się kolejny "
                        "ekran 'Nie znam'."
                    )
                    return "next_task"

                self.page.wait_for_timeout(150)

            self.logger.warning(
                "Po kliknięciu #skip nie pojawiło się zwykłe "
                "zadanie ani koniec sesji."
            )
            self.save_screenshot("dont_know_after_skip_timeout.png")
            return "unknown"

        except Exception as error:
            self.logger.warning(
                "Nie udało się obsłużyć ekranu "
                "'Nie znam' / 'Pomiń': %s",
                error,
            )
            self.save_screenshot("dont_know_screen_error.png")
            return "unknown"

    def open_session(self):
        """Otwiera lub wznawia sesję, także przez „Kontynuuj sesję”."""

        def mark_session_started():
            if self.session_started:
                return

            self.session_id = datetime.now(timezone.utc).strftime(
                "%Y%m%d_%H%M%S"
            )
            start_session(self.session_id)
            self.session_started = True
            self.session_completed = False
            self.task_count = 0

            self.logger.info(
                "Rozpoczęto lub wznowiono obsługę sesji: %s",
                self.session_id,
            )

        for attempt in range(MAX_SESSION_START_ATTEMPTS):
            special_result = self.handle_dont_know_screen()

            if special_result == "unknown":
                self.logger.error(
                    "Nie udało się obsłużyć ekranu 'Nie znam' "
                    "podczas otwierania sesji."
                )
                return False

            if special_result == "session_complete":
                self.logger.info(
                    "Podczas otwierania sesji wykryto koniec sesji."
                )
                return False

            if self.has_answer_field() or self.is_dont_know_screen():
                mark_session_started()
                return True

            if self.is_session_complete(allow_menu_signal=False):
                self.logger.info(
                    "Nie otwieram sesji: strona potwierdza jej zakończenie."
                )
                return False

            action = self.find_session_action()

            if action is None:
                self.save_screenshot(
                    f"session_start_{attempt + 1}.png"
                )
                self.logger.error(
                    "Nie znaleziono przycisku rozpoczęcia/"
                    "kontynuowania sesji. Sprawdź, czy strona pokazuje "
                    "'Rozpocznij', 'Dokończ' lub 'Kontynuuj sesję'."
                )
                return False

            try:
                action_text = normalize(
                    action.inner_text(timeout=1000)
                )
            except Exception:
                action_text = "(nie udało się odczytać tekstu przycisku)"

            self.logger.info(
                "Klikam akcję sesji: %r (próba %s/%s).",
                action_text,
                attempt + 1,
                MAX_SESSION_START_ATTEMPTS,
            )

            try:
                action.click(timeout=5000)
            except Exception as error:
                self.logger.warning(
                    "Kliknięcie akcji sesji %r nie powiodło się: %s",
                    action_text,
                    error,
                )
                self.save_screenshot(
                    f"session_start_error_{attempt + 1}.png"
                )
                self.page.wait_for_timeout(250)
                continue

            deadline = time.monotonic() + 8
            next_screen_ready = False

            while time.monotonic() < deadline:
                if self.has_answer_field() or self.is_dont_know_screen():
                    next_screen_ready = True
                    break

                if self.is_session_complete(allow_menu_signal=False):
                    self.logger.info(
                        "Po kliknięciu %r strona potwierdziła koniec sesji.",
                        action_text,
                    )
                    return False

                next_action = self.find_session_action()

                if next_action is not None:
                    try:
                        next_text = normalize(
                            next_action.inner_text(timeout=1000)
                        )
                    except Exception:
                        next_text = ""

                    if next_text and next_text != action_text:
                        self.logger.info(
                            "Po kliknięciu %r pojawiła się kolejna "
                            "akcja sesji %r; spróbuję ją obsłużyć.",
                            action_text,
                            next_text,
                        )
                        break

                self.page.wait_for_timeout(150)

            if next_screen_ready:
                mark_session_started()
                return True

            if time.monotonic() >= deadline:
                self.logger.warning(
                    "Po kliknięciu %r w ciągu 8 s nie pojawiło się "
                    "zadanie, ekran 'Nie znam' ani potwierdzenie końca "
                    "sesji; ponawiam rozpoznanie stanu strony.",
                    action_text,
                )

        if self.has_answer_field() or self.is_dont_know_screen():
            mark_session_started()
            return True

        self.save_screenshot("session_start_attempts_exhausted.png")
        self.logger.error(
            "Nie udało się otworzyć sesji po %s próbach.",
            MAX_SESSION_START_ATTEMPTS,
        )
        return False

    # --------------------------------------------------------
    # ROZPOZNAWANIE ZADANIA
    # --------------------------------------------------------

    def read_task(self):
        """Odczytuje bieżące zdanie i polską podpowiedź."""
        if not self.has_answer_field():
            return None

        sentence = ""
        polish = ""

        usage = self.page.locator("#question_usage").first
        try:
            usage.wait_for(state="attached", timeout=3000)
            sentence = usage.evaluate(r"""el => {
                const clone = el.cloneNode(true);
                clone.querySelectorAll('.visually-hidden').forEach(n => n.remove());
                return (clone.innerText || clone.textContent || '').replace(/\s+/g, ' ').trim();
            }""")
            sentence = str(sentence or "").strip()
        except Exception as error:
            self.logger.warning(
                "Nie udało się odczytać #question_usage: %s",
                error,
            )

        translation = self.page.locator("#question_translation").first
        try:
            translation.wait_for(state="attached", timeout=3000)
            polish = (
                translation.inner_text(timeout=2000) or ""
            ).strip()
        except Exception as error:
            self.logger.warning(
                "Nie udało się odczytać #question_translation: %s",
                error,
            )

        return {
            "text": self.page_text()[:12000],
            "sentence": sentence,
            "polish": polish,
            "url": self.page.url,
        }

    def extract_polish_word(self, task):
        """Najpierw czyta #question_translation, potem używa zapasowych wzorców."""
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
        """Odczytuje komunikat z #sr_status."""
        status = self.page.locator(STATUS_SELECTOR)

        try:
            if status.count() > 0:
                message = status.first.inner_text().strip()
                if message:
                    return message
        except Exception:
            pass

        capitalization = self.page.locator(CAPITALIZATION_SELECTOR)
        try:
            if (
                capitalization.count() > 0
                and capitalization.first.is_visible()
            ):
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
        elif re.fullmatch(
            r"(dobrze|poprawnie|prawidłowo|prawidlowo)[!. ]*",
            value,
        ):
            result = "correct"
        else:
            result = "unknown"

        self.logger.info(
            "Klasyfikacja statusu: tekst=%r | wynik=%s",
            str(text or ""),
            result,
        )
        return result

    def find_correct_answer(self, text):
        """Odczytuje odpowiedź podaną jawnie przez platformę."""
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
                answer = match.group(1).strip().splitlines()[0].strip()
                if answer:
                    return answer

        return None

    def wait_for_feedback_change(self, previous_status):
        deadline = time.monotonic() + (
            FEEDBACK_TIMEOUT_MS / 1000
        )

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
            "Brak zmiany statusu po %s ms: poprzedni=%r | "
            "aktualny=%r | przyczyna=%s",
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
                main()
