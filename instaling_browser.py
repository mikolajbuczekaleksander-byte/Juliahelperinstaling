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

        # Zapobiega ponownemu klikaniu przycisku
        # sprawdzania, jeśli nie można ustalić wyniku.
        self.last_feedback = None

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

        if page.locator('input[type="password"]').count():
            self.save_screenshot("login_error.png")
            raise RuntimeError(
                "Nie potwierdzono zakończenia logowania."
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

        return any(re.search(pattern, text) for pattern in patterns)

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

        # Awaryjnie zachowujemy oryginalne wyszukiwanie po tekście.
        return self.find_action_by_text((
            (r"kontynuuj sesję|kontynuuj sesje", 120),
            (r"dokończ sesję|dokonc sesje", 115),
            (r"rozpocznij kolejną sesję|rozpocznij kolejna sesje", 110),
            (r"rozpocznij sesję|rozpocznij sesje", 100),
            (r"rozpocznij naukę|rozpocznij nauke", 90),
            (r"następna sesja|nastepna sesja", 80),
        ))

    def open_session(self):
        for attempt in range(MAX_SESSION_START_ATTEMPTS):
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
        """
        Zwraca:
            correct
            synonym
            typo
            incorrect
            unknown
        """

        raw = str(text or "").strip()
        value = normalize(raw)

        if not value:
            return "unknown"

        # Najpierw sprawdzamy błąd i literówkę.
        # W przeciwnym razie komunikat zawierający
        # dodatkowe słowo "Dobrze" mógłby być źle oceniony.

        if re.search(
            r"\bniepoprawnie\b|\bniepoprawna odpowiedź\b|"
            r"\bniepoprawna odpowiedz\b",
            value,
        ):
            return "incorrect"

        if re.search(
            r"\bliterówka\b|\bliterowka\b|"
            r"\bliterówkę\b|\bliterowke\b",
            value,
        ):
            return "typo"

        if re.search(
            r"\bsynonim\b|\bsynonimem\b|"
            r"\bpoprawny synonim\b",
            value,
        ):
            return "synonym"

        if re.search(
            r"^dobrze[!. ]*$|"
            r"^poprawnie[!. ]*$|"
            r"^prawidłowo[!. ]*$|"
            r"^prawidlowo[!. ]*$",
            value,
        ):
            return "correct"

        return "unknown"

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
        """
        Czeka na zmianę #sr_status po wysłaniu odpowiedzi.
        Dzięki temu nie wykorzystujemy komunikatu
        pozostawionego po poprzednim zadaniu.
        """

        deadline = time.monotonic() + (
            FEEDBACK_TIMEOUT_MS / 1000
        )

        while time.monotonic() < deadline:
            current = self.read_status_element()

            if current and current != previous_status:
                return current

            # Jeśli platforma czyści status przed odpowiedzią,
            # a następnie ustawia ten sam komunikat,
            # akceptujemy go dopiero po zaobserwowaniu pustego
            # statusu lub po ponownym pojawieniu się komunikatu.
            self.page.wait_for_timeout(150)

        current = self.read_status_element()

        if current and current != previous_status:
            return current

        return ""

    # --------------------------------------------------------
    # PRZYCISKI ODPOWIEDZI
    # --------------------------------------------------------

    def find_submit_button(self):
        return self.find_action_by_text((
            (r"^sprawdź$|^sprawdz$", 120),
            (r"^zatwierdź$|^zatwierdz$", 110),
            (r"^odpowiedz$", 100),
        ))

    def find_next_task_button(self):
        return self.find_action_by_text((
            (r"^następne słowo$|^nastepne slowo$", 120),
            (r"^kolejne słowo$|^kolejne slowo$", 115),
            (r"^następne zadanie$|^nastepne zadanie$", 110),
            (r"^następne$|^nastepne$", 100),
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
            self.page.wait_for_timeout(500)
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
                self.session_completed = True
                break

            outcome = self.process_task()

            if outcome == "session_complete":
                self.session_completed = True
                break

            if outcome == "stop":
                self.logger.warning(
                    "Sesja zatrzymana z powodu niepewnego wyniku."
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
        self.page.wait_for_timeout(1000)
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

        self.logger.info(
            "Ukończono wszystkie %s sesje.",
            TARGET_SESSIONS,
        )

        return True

    # --------------------------------------------------------
    # ZAMKNIĘCIE
    # --------------------------------------------------------

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
