"""
Instaling Helper — automatyczny agent sesji.

Funkcje:
- logowanie przez zmienne środowiskowe;
- otwieranie InstaLing;
- odczytywanie polskiego słowa bezpośrednio z HTML;
- obsługa elementu #question_translation;
- wyszukiwanie tłumaczeń przez agent.py;
- wpisywanie i sprawdzanie odpowiedzi;
- rozpoznawanie informacji zwrotnych;
- zapisywanie potwierdzonych odpowiedzi;
- przechodzenie do kolejnych zadań;
- obsługa wielu sesji;
- zapisywanie obserwacji i logów;
- zapisywanie zrzutów ekranu i nagrań;
- bezpieczne zatrzymywanie przy niepewnym wyniku.

Wymagane pliki:
    main.py
    agent.py
    memory.py

Wymagane pakiety:
    pip install playwright
    playwright install chromium

UWAGA:
Selektory przycisków logowania, wysyłania odpowiedzi
i przechodzenia dalej mogą wymagać dostosowania do
aktualnego interfejsu InstaLing.
"""

import os
import re
import json
import logging
import traceback

from pathlib import Path
from datetime import datetime, timezone

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from agent import (
    analyze_word,
    learn_from_feedback,
)

from memory import (
    start_session,
    finish_session,
)


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

# Najważniejszy selektor — polskie słowo z HTML.
QUESTION_TRANSLATION_SELECTOR = "#question_translation"

# Pole wpisywania odpowiedzi.
ANSWER_SELECTOR = "#answer"

PAGE_TIMEOUT_MS = 15000
QUESTION_TIMEOUT_MS = 5000


# ============================================================
# NARZĘDZIA
# ============================================================

def utc_timestamp():
    return datetime.now(timezone.utc).isoformat()


def normalize(text):
    return " ".join(
        str(text or "").strip().lower().split()
    )


def setup_logging():
    ARTIFACTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

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
        self.completed_sessions_this_run = 0

    # ========================================================
    # URUCHAMIANIE
    # ========================================================

    def start(self):
        """Uruchamia Chromium i otwiera InstaLing."""

        ARTIFACTS_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        VIDEO_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        DATA_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

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

        self.page.set_default_timeout(
            PAGE_TIMEOUT_MS
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

        self.save_screenshot("initial_page.png")

        return self.page.url

    # ========================================================
    # DIAGNOSTYKA
    # ========================================================

    def save_screenshot(self, name):
        """Zapisuje zrzut ekranu."""

        if not self.page:
            return

        path = ARTIFACTS_DIR / name

        try:
            self.page.screenshot(
                path=str(path),
                full_page=True,
            )

            self.logger.info(
                "Zapisano zrzut ekranu: %s",
                path,
            )

        except Exception as error:
            self.logger.warning(
                "Nie udało się zapisać zrzutu: %s",
                error,
            )

    def inspect_page(self):
        """Pobiera informacje o aktualnej stronie."""

        try:
            title = self.page.title()
        except Exception:
            title = ""

        try:
            text = self.page.locator(
                "body"
            ).inner_text()[:12000]
        except Exception:
            text = ""

        return {
            "url": self.page.url,
            "title": title,
            "text": text,
        }

    def save_observation(self, observation):
        """Dopisuje obserwację do pliku JSONL."""

        DATA_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        path = DATA_DIR / "observations.jsonl"

        try:
            with path.open(
                "a",
                encoding="utf-8",
            ) as file:

                file.write(
                    json.dumps(
                        observation,
                        ensure_ascii=False,
                    ) + "\n"
                )

        except Exception as error:
            self.logger.warning(
                "Nie udało się zapisać obserwacji: %s",
                error,
            )

    # ========================================================
    # LOGOWANIE
    # ========================================================

    def login(self):
        """Loguje się za pomocą zmiennych środowiskowych."""

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
                "Brakuje INSTALING_LOGIN lub "
                "INSTALING_PASSWORD w zmiennych środowiskowych."
            )

        page = self.page

        password_fields = page.locator(
            'input[type="password"]'
        )

        if password_fields.count() == 0:

            links = page.get_by_role(
                "link",
                name=re.compile(
                    r"zaloguj|logowanie|login",
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

            try:
                if not field.is_visible():
                    continue

                if not field.is_enabled():
                    continue

                description = " ".join(
                    str(
                        field.get_attribute(attr) or ""
                    )
                    for attr in (
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

            except Exception:
                continue

        if login_field is None:

            for index in range(fields.count()):

                field = fields.nth(index)

                try:
                    if (
                        field.is_visible()
                        and field.is_enabled()
                    ):
                        login_field = field
                        break

                except Exception:
                    continue

        if login_field is None:

            self.save_screenshot("login_error.png")

            raise RuntimeError(
                "Nie znaleziono pola loginu."
            )

        login_field.fill(login)
        password_fields.first.fill(password)

        submit = page.locator(
            'button[type="submit"], '
            'input[type="submit"]'
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
                timeout=15000,
            )
        except PlaywrightTimeoutError:
            pass

        page.wait_for_timeout(2000)

        result = self.inspect_page()
        text = normalize(result["text"])

        errors = (
            "nieprawidłowe hasło",
            "nieprawidlowe haslo",
            "błędne hasło",
            "bledne haslo",
            "nieprawidłowy login",
            "niepoprawne hasło",
        )

        if any(
            normalize(message) in text
            for message in errors
        ):

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

        self.save_screenshot("after_login.png")

        return result

    # ========================================================
    # ODCZYT POLSKIEGO SŁOWA Z HTML
    # ========================================================

    def extract_polish_word(self, task):
        """
        Odczytuje polskie słowo bezpośrednio z elementu HTML.

        Przykładowy HTML:

        <div class="translation" id="question_translation">
            wystawiać (sztukę, przedstawienie)
        </div>

        Wartość zwracana:
            wystawiać (sztukę, przedstawienie)

        Nie próbujemy zgadywać słowa z całej strony,
        jeśli docelowy element nie istnieje.
        """

        selector = QUESTION_TRANSLATION_SELECTOR

        try:
            question = self.page.locator(selector)

            # Czekamy na pojawienie się elementu.
            try:
                question.first.wait_for(
                    state="visible",
                    timeout=QUESTION_TIMEOUT_MS,
                )

            except PlaywrightTimeoutError:
                self.logger.warning(
                    "Nie pojawił się widoczny element %s.",
                    selector,
                )

            # Sprawdzamy, czy element istnieje.
            if question.count() > 0:

                # Odczytujemy tekst bezpośrednio z HTML.
                if question.first.is_visible():

                    polish = question.first.inner_text().strip()

                    polish = re.sub(
                        r"\s+",
                        " ",
                        polish,
                    )

                    if polish:

                        self.logger.info(
                            "ODCZYTANO POLSKIE SŁOWO Z HTML: %s",
                            polish,
                        )

                        self.save_observation({
                            "session_id": self.session_id,
                            "result": "word_detected",
                            "polish": polish,
                            "selector": selector,
                            "url": self.page.url,
                            "timestamp": utc_timestamp(),
                        })

                        return polish

                    self.logger.warning(
                        "Element %s istnieje, ale jest pusty.",
                        selector,
                    )

        except Exception as error:

            self.logger.warning(
                "Błąd odczytu elementu %s: %s",
                selector,
                error,
            )

        # Diagnostyka, gdy nie udało się odczytać słowa.
        text = ""

        try:
            text = self.page.locator(
                "body"
            ).inner_text()[:12000]
        except Exception:
            pass

        self.logger.error(
            "Nie udało się odczytać polskiego słowa "
            "z elementu %s.",
            selector,
        )

        self.logger.info(
            "Tekst widoczny na stronie:\n%s",
            text[:3000],
        )

        self.save_observation({
            "session_id": self.session_id,
            "result": "word_not_detected",
            "task_text": text,
            "selector": selector,
            "url": self.page.url,
            "timestamp": utc_timestamp(),
        })

        self.save_screenshot("word_not_detected.png")

        return None

    # ========================================================
    # POLE ODPOWIEDZI
    # ========================================================

    def has_answer_field(self):
        """Sprawdza, czy pole odpowiedzi jest dostępne."""

        try:
            answer = self.page.locator(
                ANSWER_SELECTOR
            )

            return bool(
                answer.count()
                and answer.first.is_visible()
                and answer.first.is_enabled()
            )

        except Exception:
            return False

    # ========================================================
    # WYSZUKIWANIE PRZYCISKÓW
    # ========================================================

    def find_action_by_text(self, patterns):
        """
        Znajduje widoczny, aktywny przycisk lub link,
        którego tekst odpowiada jednemu ze wzorców.
        """

        elements = self.page.locator(
            "button, a, "
            "[role='button'], "
            "input[type='button'], "
            "input[type='submit']"
        )

        candidates = []

        for index in range(elements.count()):

            element = elements.nth(index)

            try:
                if not element.is_visible():
                    continue

                if not element.is_enabled():
                    continue

                text = str(
                    element.inner_text(
                        timeout=1000
                    ) or ""
                ).strip()

                value = str(
                    element.get_attribute("value") or ""
                ).strip()

                aria = str(
                    element.get_attribute("aria-label") or ""
                ).strip()

                title = str(
                    element.get_attribute("title") or ""
                ).strip()

                description = normalize(
                    " ".join(
                        (
                            text,
                            value,
                            aria,
                            title,
                        )
                    )
                )

                if not description:
                    continue

                for pattern, score in patterns:

                    if re.search(
                        pattern,
                        description,
                        re.IGNORECASE,
                    ):

                        candidates.append(
                            (
                                score,
                                element,
                                description,
                            )
                        )

                        break

            except Exception:
                continue

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        for score, _, description in candidates[:5]:

            self.logger.info(
                "Znaleziono akcję: %s | wynik=%s",
                description[:100],
                score,
            )

        return (
            candidates[0][1]
            if candidates
            else None
        )

    # ========================================================
    # ROZPOZNAWANIE KOŃCA SESJI
    # ========================================================

    def is_session_complete(self):
        """Rozpoznaje tekstowe oznaki zakończenia sesji."""

        try:
            text = normalize(
                self.page.locator(
                    "body"
                ).inner_text()
            )
        except Exception:
            return False

        patterns = (
            r"przećwiczyłeś",
            r"przećwiczyłaś",
            r"przecwiczyles",
            r"przecwiczylas",
            r"sesja została zakończona",
            r"sesja zakonczona",
            r"zakończyłeś sesję",
            r"zakonczyles sesje",
            r"zakończyłaś sesję",
            r"zakonczylas sesje",
            r"powrót do menu głównego",
            r"powrot do menu glownego",
            r"wróć do menu",
            r"wroc do menu",
        )

        return any(
            re.search(pattern, text)
            for pattern in patterns
        )

    def is_main_menu(self):
        """Sprawdza, czy prawdopodobnie jesteśmy w menu."""

        if self.has_answer_field():
            return False

        try:
            text = normalize(
                self.page.locator(
                    "body"
                ).inner_text()
            )
        except Exception:
            return False

        menu_phrases = (
            "rozpocznij sesję",
            "rozpocznij sesje",
            "kontynuuj sesję",
            "kontynuuj sesje",
            "rozpocznij naukę",
            "rozpocznij nauke",
        )

        return any(
            phrase in text
            for phrase in menu_phrases
        )

    # ========================================================
    # PRZYCISK ROZPOCZĘCIA SESJI
    # ========================================================

    def find_session_action(self):

        patterns = (
            (
                r"kontynuuj sesję|kontynuuj sesje",
                110,
            ),
            (
                r"dokończ sesję|dokoncz sesje",
                105,
            ),
            (
                r"rozpocznij kolejną sesję|"
                r"rozpocznij kolejna sesje",
                100,
            ),
            (
                r"rozpocznij sesję|rozpocznij sesje",
                95,
            ),
            (
                r"rozpocznij naukę|rozpocznij nauke",
                90,
            ),
            (
                r"następna sesja|nastepna sesja",
                80,
            ),
        )

        return self.find_action_by_text(patterns)

    # ========================================================
    # POWRÓT DO MENU
    # ========================================================

    def return_to_menu(self):

        patterns = (
            (
                r"wróć do menu głównego|"
                r"wroc do menu glownego",
                120,
            ),
            (
                r"powrót do menu głównego|"
                r"powrot do menu glownego",
                115,
            ),
            (
                r"wróć do menu|wroc do menu",
                110,
            ),
            (
                r"menu główne|menu glowne",
                90,
            ),
            (
                r"powrót|powrot",
                50,
            ),
        )

        action = self.find_action_by_text(patterns)

        if action is None:

            self.save_screenshot(
                "return_to_menu_error.png"
            )

            raise RuntimeError(
                "Sesja się zakończyła, ale nie znaleziono "
                "przycisku powrotu do menu."
            )

        action.click(timeout=5000)

        self.page.wait_for_timeout(1500)

        self.logger.info(
            "Kliknięto przycisk powrotu do menu."
        )

        self.save_screenshot("main_menu.png")

    # ========================================================
    # OTWIERANIE SESJI
    # ========================================================

    def open_session(self):
        """
        Otwiera sesję i czeka na pole odpowiedzi.

        Kliknięcie przycisku samo w sobie nie oznacza,
        że sesja została poprawnie otwarta.
        """

        for attempt in range(
            MAX_SESSION_START_ATTEMPTS
        ):

            if self.has_answer_field():

                if not self.session_started:

                    self.session_id = datetime.now(
                        timezone.utc
                    ).strftime(
                        "%Y%m%d_%H%M%S"
                    )

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

                self.logger.warning(
                    "Wykryto ekran końca sesji "
                    "podczas otwierania."
                )

                return False

            self.logger.info(
                "Otwieranie sesji: próba %s/%s",
                attempt + 1,
                MAX_SESSION_START_ATTEMPTS,
            )

            state = self.inspect_page()

            self.logger.info(
                "Aktualny ekran:\n%s",
                state["text"][:1500],
            )

            action = self.find_session_action()

            if action is None:

                self.save_screenshot(
                    f"session_start_{attempt + 1}.png"
                )

                self.logger.warning(
                    "Nie znaleziono przycisku rozpoczęcia sesji."
                )

                return False

            try:
                action.click(timeout=5000)
                self.page.wait_for_timeout(1500)

            except Exception as error:

                self.logger.warning(
                    "Nie udało się otworzyć sesji: %s",
                    error,
                )

                return False

        return self.has_answer_field()

    # ========================================================
    # ODCZYT ZADANIA
    # ========================================================

    def read_task(self):
        """Odczytuje aktualne zadanie."""

        if not self.has_answer_field():
            return None

        state = self.inspect_page()

        return {
            "text": state["text"],
            "url": state["url"],
            "answer_selector": ANSWER_SELECTOR,
        }

    # ========================================================
    # PRZYCISK SPRAWDZANIA
    # ========================================================

    def find_submit_button(self):

        patterns = (
            (r"^sprawdź$|^sprawdz$", 120),
            (r"^zatwierdź$|^zatwierdz$", 110),
            (r"^odpowiedz$", 100),
            (r"^dalej$", 50),
        )

        return self.find_action_by_text(patterns)

    # ========================================================
    # ODCZYT INFORMACJI ZWROTNEJ
    # ========================================================

    def inspect_answer_result(self):
        """
        Odczytuje widoczny wynik odpowiedzi.

        Wynik:
            capitalization
            incorrect
            synonym
            correct
            unknown
        """

        try:
            text = self.page.locator(
                "body"
            ).inner_text()
        except Exception:
            text = ""

        normalized = normalize(text)

        if re.search(
            r"zła wielkość liter|"
            r"zla wielkosc liter|"
            r"wielkość liter|"
            r"wielkosc liter",
            normalized,
        ):

            result = "capitalization"

        elif re.search(
            r"niepoprawnie|"
            r"niepoprawna odpowiedź|"
            r"niepoprawna odpowiedz|"
            r"zła odpowiedź|"
            r"zla odpowiedz",
            normalized,
        ):

            result = "incorrect"

        elif re.search(
            r"\bsynonim\b|"
            r"\bsynonimem\b|"
            r"\bsynonimiczna odpowiedź\b",
            normalized,
        ):

            result = "synonym"

        elif re.search(
            r"\bdobrze\b|"
            r"\bpoprawnie\b|"
            r"\bprawidłowo\b|"
            r"\bprawidlowo\b",
            normalized,
        ):

            result = "correct"

        else:

            result = "unknown"

        return {
            "result": result,
            "text": text[:12000],
        }

    # ========================================================
    # ODCZYT POPRAWNEJ ODPOWIEDZI
    # ========================================================

    def find_correct_answer(self, text):

        patterns = (
            r"poprawna odpowiedź\s*:\s*([^\n]+)",
            r"poprawna odpowiedz\s*:\s*([^\n]+)",
            r"powinno być\s*:\s*([^\n]+)",
            r"powinno byc\s*:\s*([^\n]+)",
            r"prawidłowa odpowiedź\s*:\s*([^\n]+)",
            r"prawidlowa odpowiedz\s*:\s*([^\n]+)",
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

    # ========================================================
    # PRZEJŚCIE DO NASTĘPNEGO ZADANIA
    # ========================================================

    def find_next_task_button(self):

        patterns = (
            (
                r"^następne$|^nastepne$",
                120,
            ),
            (
                r"^następne słowo$|^nastepne slowo$",
                120,
            ),
            (
                r"^kolejne słowo$|^kolejne slowo$",
                115,
            ),
            (
                r"^dalej$",
                100,
            ),
            (
                r"^kontynuuj$",
                90,
            ),
            (
                r"^następne zadanie$|^nastepne zadanie$",
                110,
            ),
        )

        return self.find_action_by_text(patterns)

    def go_to_next_task(self):
        """Przechodzi do kolejnego zadania."""

        if self.is_session_complete():
            return "session_complete"

        action = self.find_next_task_button()

        if action is None:

            self.save_screenshot(
                "next_task_button_missing.png"
            )

            self.logger.warning(
                "Nie znaleziono przycisku następnego zadania."
            )

            return "unknown"

        try:
            action.click(timeout=5000)
            self.page.wait_for_timeout(1000)

        except Exception as error:

            self.logger.warning(
                "Nie udało się przejść dalej: %s",
                error,
            )

            return "unknown"

        if self.is_session_complete():
            return "session_complete"

        if self.has_answer_field():
            return "next_task"

        self.save_screenshot(
            "next_task_unrecognized.png"
        )

        return "unknown"

    # ========================================================
    # PRZETWARZANIE ZADANIA
    # ========================================================

    def process_task(self):
        """
        Odczytuje polskie słowo z HTML, pobiera tłumaczenie,
        wpisuje odpowiedź i sprawdza informację zwrotną.

        Wynik:
            next_task
            session_complete
            stop
        """

        if self.is_session_complete():

            self.logger.info(
                "Rozpoznano koniec sesji."
            )

            return "session_complete"

        task = self.read_task()

        if task is None:

            self.logger.warning(
                "Nie znaleziono aktywnego pola odpowiedzi."
            )

            self.save_screenshot(
                "answer_field_missing.png"
            )

            return "stop"

        # ----------------------------------------------------
        # NAJWAŻNIEJSZY FRAGMENT:
        # ODCZYT POLSKIEGO SŁOWA Z #question_translation
        # ----------------------------------------------------

        polish = self.extract_polish_word(task)

        if not polish:

            self.logger.error(
                "Nie odczytano słowa z #question_translation. "
                "Zatrzymuję zadanie, aby uniknąć błędnej odpowiedzi."
            )

            return "stop"

        self.logger.info(
            "Rozpoznane polskie słowo: %s",
            polish,
        )

        # ----------------------------------------------------
        # WYSZUKIWANIE TŁUMACZENIA
        # ----------------------------------------------------

        try:
            result = analyze_word(
                polish,
                context=task["text"],
            )

        except Exception as error:

            self.logger.exception(
                "Błąd agenta: %s",
                error,
            )

            return "stop"

        if not isinstance(result, dict):

            self.logger.error(
                "Agent zwrócił nieoczekiwany wynik: %r",
                result,
            )

            return "stop"

        candidates = result.get(
            "translations",
            [],
        )

        if not candidates:

            self.logger.warning(
                "Brak tłumaczenia dla: %s",
                polish,
            )

            self.save_observation({
                "session_id": self.session_id,
                "polish": polish,
                "context": task["text"],
                "result": "translation_needed",
                "timestamp": utc_timestamp(),
            })

            return "stop"

        verified = [
            candidate
            for candidate in candidates
            if (
                isinstance(candidate, dict)
                and candidate.get("verified")
            )
        ]

        candidate = (
            verified[0]
            if verified
            else candidates[0]
        )

        if not isinstance(candidate, dict):

            self.logger.error(
                "Nieprawidłowy format tłumaczenia."
            )

            return "stop"

        answer = str(
            candidate.get("answer", "")
        ).strip()

        if not answer:

            self.logger.warning(
                "Agent zwrócił pustą odpowiedź."
            )

            return "stop"

        self.logger.info(
            "Wybrana odpowiedź: %s | źródło=%s | "
            "wcześniej potwierdzona=%s",
            answer,
            candidate.get("source"),
            candidate.get("verified", False),
        )

        self.save_observation({
            "session_id": self.session_id,
            "polish": polish,
            "context": task["text"],
            "candidate": answer,
            "source": candidate.get("source"),
            "verified": candidate.get("verified", False),
            "result": "candidate_prepared",
            "timestamp": utc_timestamp(),
        })

        # ----------------------------------------------------
        # WPISYWANIE ODPOWIEDZI
        # ----------------------------------------------------

        answer_field = self.page.locator(
            ANSWER_SELECTOR
        ).first

        try:
            answer_field.fill(answer)

        except Exception as error:

            self.logger.exception(
                "Nie udało się wpisać odpowiedzi: %s",
                error,
            )

            return "stop"

        self.logger.info("Wpisano odpowiedź.")

        self.save_screenshot("answer_filled.png")

        if not AUTO_SUBMIT:

            self.logger.info(
                "AUTO_SUBMIT=False — zatrzymano "
                "przed wysłaniem odpowiedzi."
            )

            return "stop"

        submit_button = self.find_submit_button()

        if submit_button is None:

            self.logger.warning(
                "Nie znaleziono przycisku sprawdzania."
            )

            self.save_screenshot(
                "submit_button_missing.png"
            )

            return "stop"

        # ----------------------------------------------------
        # SPRAWDZANIE ODPOWIEDZI
        # ----------------------------------------------------

        try:
            submit_button.click(timeout=5000)
            self.page.wait_for_timeout(1500)

        except Exception as error:

            self.logger.exception(
                "Nie udało się sprawdzić odpowiedzi: %s",
                error,
            )

            return "stop"

        feedback = self.inspect_answer_result()
        feedback_type = feedback["result"]

        self.logger.info(
            "Wynik platformy: %s",
            feedback_type,
        )

        self.save_screenshot("answer_feedback.png")

        # ----------------------------------------------------
        # NIEZNANY WYNIK
        # ----------------------------------------------------

        if feedback_type == "unknown":

            self.logger.warning(
                "Nie rozpoznano informacji zwrotnej. "
                "Nie zapisuję odpowiedzi jako poprawnej."
            )

            self.save_observation({
                "session_id": self.session_id,
                "polish": polish,
                "candidate": answer,
                "result": "feedback_unknown",
                "feedback": feedback["text"],
                "timestamp": utc_timestamp(),
            })

            if STOP_ON_UNKNOWN_FEEDBACK:
                return "stop"

            return "stop"

        # ----------------------------------------------------
        # ZAPIS WYNIKU W PAMIĘCI
        # ----------------------------------------------------

        correction = self.find_correct_answer(
            feedback["text"]
        )

        try:

            if feedback_type in (
                "correct",
                "synonym",
            ):

                learn_from_feedback(
                    polish=polish,
                    answer=answer,
                    accepted=True,
                    context=task["text"],
                    session_id=self.session_id,
                    feedback_type=feedback_type,
                )

                self.logger.info(
                    "Zapisano zaakceptowaną odpowiedź: "
                    "%s -> %s",
                    polish,
                    answer,
                )

            elif feedback_type in (
                "incorrect",
                "capitalization",
            ):

                learn_from_feedback(
                    polish=polish,
                    answer=answer,
                    accepted=False,
                    correction=correction,
                    context=task["text"],
                    session_id=self.session_id,
                    feedback_type=feedback_type,
                )

                if correction:

                    self.logger.info(
                        "Zapisano poprawkę: %s -> %s",
                        polish,
                        correction,
                    )

                else:

                    self.logger.warning(
                        "Odpowiedź odrzucona, ale platforma "
                        "nie pokazała jednoznacznej poprawki."
                    )

        except Exception as error:

            self.logger.exception(
                "Nie udało się zapisać informacji zwrotnej: %s",
                error,
            )

            return "stop"

        self.save_observation({
            "session_id": self.session_id,
            "polish": polish,
            "context": task["text"],
            "candidate": answer,
            "result": feedback_type,
            "correction": correction,
            "feedback": feedback["text"],
            "timestamp": utc_timestamp(),
        })

        self.task_count += 1

        self.logger.info(
            "Liczba przetworzonych zadań: %s",
            self.task_count,
        )

        # ----------------------------------------------------
        # KONIEC SESJI LUB KOLEJNE ZADANIE
        # ----------------------------------------------------

        if self.is_session_complete():

            self.logger.info(
                "Rozpoznano zakończenie sesji."
            )

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

    # ========================================================
    # WYKONANIE JEDNEJ SESJI
    # ========================================================

    def run_one_session(self):
        """
        Wykonuje jedną sesję.

        Za ukończoną uznaje ją dopiero po wykryciu
        ekranu końca sesji.
        """

        if not self.open_session():

            self.logger.error(
                "Nie udało się otworzyć sesji."
            )

            return False

        self.logger.info(
            "Rozpoczęto wykonywanie sesji."
        )

        for index in range(
            MAX_TASKS_PER_SESSION
        ):

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
                    "Sesja zatrzymana, ponieważ nie udało się "
                    "bezpiecznie rozpoznać lub obsłużyć zadania."
                )

                self.save_screenshot(
                    "session_stopped.png"
                )

                return False

            if outcome != "next_task":

                self.logger.warning(
                    "Nieznany wynik przetwarzania: %s",
                    outcome,
                )

                return False

        if not self.session_completed:

            self.logger.error(
                "Sesja nie została ukończona w limicie %s zadań.",
                MAX_TASKS_PER_SESSION,
            )

            self.save_screenshot(
                "session_limit_reached.png"
            )

            return False

        # Zapisujemy zakończenie dopiero po potwierdzeniu.
        try:
            finish_session(self.session_id)

        except Exception as error:

            self.logger.exception(
                "Nie udało się zapisać zakończenia sesji: %s",
                error,
            )

            return False

        self.logger.info(
            "Zakończono sesję: %s",
            self.session_id,
        )

        self.completed_sessions_this_run += 1

        self.save_screenshot("session_completed.png")

        # Pozwalamy na rozpoczęcie kolejnej sesji.
        self.session_started = False
        self.session_completed = False
        self.session_id = None

        return True

    # ========================================================
    # WYKONANIE DWÓCH SESJI
    # ========================================================

    def run_two_sessions(self):
        """Wykonuje skonfigurowaną liczbę sesji."""

        for session_number in range(
            1,
            TARGET_SESSIONS + 1,
        ):

            self.logger.info(
                "========================================"
            )

            self.logger.info(
                "SESJA %s/%s",
                session_number,
                TARGET_SESSIONS,
            )

            self.logger.info(
                "========================================"
            )

            if not self.run_one_session():

                self.logger.error(
                    "Nie udało się ukończyć sesji %s. "
                    "Program zatrzymał działanie.",
                    session_number,
                )

                return False

            self.return_to_menu()

            if session_number < TARGET_SESSIONS:

                self.logger.info(
                    "Sesja ukończona. Przygotowanie następnej."
                )

                self.page.wait_for_timeout(1000)

        self.logger.info(
            "Ukończono wszystkie %s sesje.",
            TARGET_SESSIONS,
        )

        return True

    # ========================================================
    # ZAMKNIĘCIE PRZEGLĄDARKI
    # ========================================================

    def close(self):
        """Zamyka przeglądarkę i finalizuje nagranie."""

        if self.context:

            try:
                self.context.close()

                self.logger.info(
                    "Zamknięto kontekst przeglądarki."
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

            except Exception as error:

                self.logger.warning(
                    "Błąd zamykania przeglądarki: %s",
                    error,
                )

            self.browser = None

        if self.playwright:

            try:
                self.playwright.stop()

            except Exception as error:

                self.logger.warning(
                    "Błąd zatrzymywania Playwright: %s",
                    error,
                )

            self.playwright = None

        self.logger.info(
            "Zakończono działanie przeglądarki."
        )


# ============================================================
# MAIN
# ============================================================

def main():

    # False otwiera widoczne okno Chromium.
    # Ułatwia to sprawdzenie, czy słowo jest odczytywane.
    browser = InstalingBrowser(
        headless=False,
    )

    success = False

    try:

        url = browser.start()

        browser.logger.info(
            "Otwarto stronę: %s",
            url,
        )

        login_result = browser.login()

        browser.logger.info(
            "Zalogowano. Adres: %s",
            login_result["url"],
        )

        success = browser.run_two_sessions()

        if success:

            browser.logger.info(
                "SUKCES: ukończono wszystkie sesje."
            )

        else:

            browser.logger.error(
                "Program nie ukończył wszystkich sesji. "
                "Sprawdź artifacts/run.log i zrzuty ekranu."
            )

    except KeyboardInterrupt:

        browser.logger.warning(
            "Przerwano działanie programu przez użytkownika."
        )

    except Exception as error:

        browser.logger.error(
            "Wystąpił nieoczekiwany błąd: %s",
            error,
        )

        browser.logger.error(
            "%s",
            traceback.format_exc(),
        )

    finally:

        browser.close()

    return success


if __name__ == "__main__":
    main()
