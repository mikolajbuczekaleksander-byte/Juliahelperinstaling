"""
Instaling Helper — automatyczny agent sesji.

Funkcje:
- logowanie przez zmienne środowiskowe;
- otwieranie sesji InstaLing;
- rozpoznawanie zadań;
- wyszukiwanie tłumaczeń przez agent.py;
- wpisywanie i sprawdzanie odpowiedzi;
- rozpoznawanie informacji zwrotnych;
- zapisywanie potwierdzonych odpowiedzi;
- przechodzenie do kolejnych zadań;
- wykonywanie dwóch pełnych sesji;
- zapisywanie obserwacji;
- zapisywanie nagrań i zrzutów ekranu;
- bezpieczne zatrzymywanie w przypadku niepewnego wyniku.

UWAGA:
Selektory strony oraz komunikaty zwrotne należy
zweryfikować na rzeczywistym ekranie InstaLing.
"""

import os
import re
import json
import logging

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

# Ile pełnych sesji ma wykonać program.
TARGET_SESSIONS = 2

# Maksymalna liczba zadań w jednej sesji.
MAX_TASKS_PER_SESSION = 100

# Maksymalna liczba prób rozpoznania ekranu sesji.
MAX_SESSION_START_ATTEMPTS = 8

# Automatyczne wpisywanie i wysyłanie odpowiedzi.
AUTO_SUBMIT = True

# Nieznany wynik oznacza zatrzymanie automatyzacji.
STOP_ON_UNKNOWN_FEEDBACK = True

# Selektor pola odpowiedzi z dotychczasowego kodu.
ANSWER_SELECTOR = "#answer"

# Maksymalny czas oczekiwania na zmianę ekranu.
PAGE_TIMEOUT_MS = 15000


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

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

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

        self.logger.info(
            "Uruchomiono Chromium."
        )

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

    # --------------------------------------------------------
    # DIAGNOSTYKA
    # --------------------------------------------------------

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

        return {
            "url": self.page.url,
            "title": self.page.title(),
            "text": self.page.locator(
                "body"
            ).inner_text()[:12000],
        }

    def save_observation(self, observation):
        """Dopisuje obserwację do pliku JSONL."""

        DATA_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        path = DATA_DIR / "observations.jsonl"

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

    # --------------------------------------------------------
    # LOGOWANIE
    # --------------------------------------------------------

    def login(self):
        """Loguje się przez zmienne środowiskowe."""

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

            self.save_screenshot(
                "login_error.png"
            )

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

        if login_field is None:

            for index in range(fields.count()):

                field = fields.nth(index)

                if (
                    field.is_visible()
                    and field.is_enabled()
                ):

                    login_field = field
                    break

        if login_field is None:

            self.save_screenshot(
                "login_error.png"
            )

            raise RuntimeError(
                "Nie znaleziono pola loginu."
            )

        login_field.fill(login)

        password_fields.first.fill(password)

        submit = page.locator(
            'button[type="submit"], '
            'input[type="submit"]'
        )

        if (
            submit.count()
            and submit.first.is_visible()
        ):

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

                self.save_screenshot(
                    "login_error.png"
                )

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

            self.save_screenshot(
                "login_error.png"
            )

            raise RuntimeError(
                "Platforma zgłosiła błąd logowania."
            )

        visible_password_fields = page.locator(
            'input[type="password"]:visible'
        )

        if visible_password_fields.count():

            self.save_screenshot(
                "login_error.png"
            )

            raise RuntimeError(
                "Nie potwierdzono zakończenia logowania."
            )

        self.logger.info(
            "Logowanie zakończone."
        )

        self.save_screenshot(
            "after_login.png"
        )

        return result

    # --------------------------------------------------------
    # ROZPOZNAWANIE POLA ODPOWIEDZI
    # --------------------------------------------------------

    def has_answer_field(self):
        """Sprawdza, czy pole odpowiedzi jest dostępne."""

        answer = self.page.locator(
            ANSWER_SELECTOR
        )

        return bool(
            answer.count()
            and answer.first.is_visible()
            and answer.first.is_enabled()
        )

    # --------------------------------------------------------
    # PRZYCISKI
    # --------------------------------------------------------

    def find_action_by_text(self, patterns):
        """
        Znajduje widoczny, aktywny przycisk lub link
        odpowiadający jednemu z podanych wzorców.
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
                    element.get_attribute(
                        "value"
                    ) or ""
                ).strip()

                aria = str(
                    element.get_attribute(
                        "aria-label"
                    ) or ""
                ).strip()

                title = str(
                    element.get_attribute(
                        "title"
                    ) or ""
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

    # --------------------------------------------------------
    # ROZPOZNAWANIE EKRANU SESJI
    # --------------------------------------------------------

    def is_session_complete(self):
        """
        Rozpoznaje ekran końca sesji.

        Wzorce trzeba zweryfikować na rzeczywistym ekranie.
        """

        text = normalize(
            self.page.locator(
                "body"
            ).inner_text()
        )

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

        text = normalize(
            self.page.locator(
                "body"
            ).inner_text()
        )

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

    def find_session_action(self):
        """Znajduje przycisk rozpoczęcia lub kontynuowania sesji."""

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

        return self.find_action_by_text(
            patterns
        )

    def return_to_menu(self):
        """Wraca do menu po zakończeniu sesji."""

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

        action = self.find_action_by_text(
            patterns
        )

        if action is None:

            self.save_screenshot(
                "return_to_menu_error.png"
            )

            raise RuntimeError(
                "Sesja się zakończyła, ale nie "
                "znaleziono przycisku powrotu do menu."
            )

        action.click(timeout=5000)

        self.page.wait_for_timeout(1500)

        self.logger.info(
            "Kliknięto przycisk powrotu do menu."
        )

        self.save_screenshot(
            "main_menu.png"
        )

    # --------------------------------------------------------
    # OTWIERANIE SESJI
    # --------------------------------------------------------

    def open_session(self):
        """
        Otwiera sesję i czeka na pole odpowiedzi.

        Nie uznaje samego kliknięcia za potwierdzenie,
        że sesja została otwarta.
        """

        for attempt in range(
            MAX_SESSION_START_ATTEMPTS
        ):

            if self.has_answer_field():

                if not self.session_started:

                    self.session_id = (
                        datetime.now(
                            timezone.utc
                        ).strftime(
                            "%Y%m%d_%H%M%S"
                        )
                    )

                    start_session(
                        self.session_id
                    )

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
                    "Nie znaleziono przycisku sesji."
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

    # --------------------------------------------------------
    # ODCZYT ZADANIA
    # --------------------------------------------------------

    def read_task(self):
        """Odczytuje aktualny ekran zadania."""

        if not self.has_answer_field():
            return None

        return {
            "text": self.page.locator(
                "body"
            ).inner_text()[:12000],
            "url": self.page.url,
            "answer_selector": ANSWER_SELECTOR,
        }

    # --------------------------------------------------------
    # ROZPOZNAWANIE POLSKIEGO SŁOWA
    # --------------------------------------------------------

    def extract_polish_word(self, task):
        """
        Rozpoznaje słowo na podstawie tekstu zadania.

        Jeśli nie potrafi jednoznacznie znaleźć słowa,
        zwraca None zamiast zgadywać.
        """

        text = str(
            task.get("text", "") or ""
        ).strip()

        if not text:
            self.logger.warning(
                "Brak tekstu zadania."
            )
            return None

        self.logger.info(
            "TEKST EKRANU ZADANIA:\n%s",
            text[:4000],
        )

        patterns = (
            (
                r"(?:przetłumacz|przetlumacz)"
                r"\s*:?\s*([^\r\n]+)"
            ),
            (
                r"(?:po polsku|polskie słowo|"
                r"polskie slowo)"
                r"\s*:?\s*([^\r\n]+)"
            ),
        )

        for pattern in patterns:
            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if not match:
                continue

            candidate = re.sub(
                r"\s+",
                " ",
                match.group(1),
            ).strip()

            # Odrzucamy puste lub podejrzanie długie wyniki.
            if not candidate or len(candidate) >= 100:
                continue

            self.logger.info(
                "ROZPOZNANO SŁOWO: %s",
                candidate,
            )

            return candidate

        self.logger.warning(
            "Nie rozpoznano słowa. "
            "Tekst zadania zapisano w logach."
        )

        self.save_observation({
            "session_id": self.session_id,
            "result": "word_not_detected",
            "task_text": text,
            "url": task.get("url"),
            "timestamp": utc_timestamp(),
        })

        self.save_screenshot(
            "word_not_detected.png"
        )

        return None

    # --------------------------------------------------------
    # WYSZUKIWANIE PRZYCISKU SPRAWDZANIA
    # --------------------------------------------------------

    def find_submit_button(self):
        """
        Wyszukuje przycisk sprawdzania odpowiedzi.

        Wybiera wyłącznie widoczny i aktywny element.
        """

        patterns = (
            (r"^sprawdź$|^sprawdz$", 120),
            (r"^zatwierdź$|^zatwierdz$", 110),
            (r"^odpowiedz$", 100),
            (r"^dalej$", 50),
        )

        return self.find_action_by_text(
            patterns
        )

    # --------------------------------------------------------
    # ODCZYT WYNIKU
    # --------------------------------------------------------

    def inspect_answer_result(self):
        """
        Odczytuje widoczny wynik odpowiedzi.

        Priorytet:
        1. Wielkość liter.
        2. Niepoprawna odpowiedź.
        3. Synonim.
        4. Poprawna odpowiedź.
        """

        text = self.page.locator(
            "body"
        ).inner_text()

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

    def find_correct_answer(self, text):
        """
        Odczytuje poprawną odpowiedź wyłącznie
        z jawnego komunikatu.
        """

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

    # --------------------------------------------------------
    # PRZEJŚCIE DO NASTĘPNEGO ZADANIA
    # --------------------------------------------------------

    def find_next_task_button(self):
        """
        Wyszukuje przycisk przejścia dalej.

        Nie klika przycisku, jeśli nie jest pewien.
        """
        patterns = (
            (r"^następne$|^nastepne$", 120),
            (r"^dalej$", 110),
            (r"^kontynuuj$", 100),
        )
        return self.find_action_by_text(patterns)
