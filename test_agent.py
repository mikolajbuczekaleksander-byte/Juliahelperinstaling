import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import memory
import vocabulary
from agent import analyze_word, learn_from_feedback


class TestInstalingHelper(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.data_dir = Path(self.temp_dir.name)

        self.memory_file = self.data_dir / "memory.json"
        self.vocabulary_file = self.data_dir / "vocabulary.json"

        # Przekierowanie pamięci i słownika do katalogu testowego.
        self.memory_dir_patch = patch.object(
            memory,
            "DATA_DIR",
            self.data_dir,
        )
        self.memory_file_patch = patch.object(
            memory,
            "MEMORY_FILE",
            self.memory_file,
        )

        self.vocabulary_dir_patch = patch.object(
            vocabulary,
            "DATA_DIR",
            self.data_dir,
        )
        self.vocabulary_file_patch = patch.object(
            vocabulary,
            "VOCABULARY_FILE",
            self.vocabulary_file,
        )

        for patcher in (
            self.memory_dir_patch,
            self.memory_file_patch,
            self.vocabulary_dir_patch,
            self.vocabulary_file_patch,
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_accepted_answer_is_remembered(self):
        learn_from_feedback(
            polish="słowo testowe",
            answer="test word",
            accepted=True,
            context="test kontekstu",
        )

        known = memory.get_known_answers(
            polish="słowo testowe",
            context="test kontekstu",
        )

        self.assertIn("test word", known["accepted_answers"])

    def test_rejected_answer_and_correction_are_saved(self):
        learn_from_feedback(
            polish="słowo testowe",
            answer="wrong answer",
            accepted=False,
            correction="correct answer",
            context="test poprawki",
        )

        known = memory.get_known_answers(
            polish="słowo testowe",
            context="test poprawki",
        )

        self.assertIn("wrong answer", known["rejected_answers"])
        self.assertIn("correct answer", known["accepted_answers"])

    def test_vocabulary_keeps_multiple_translations(self):
        vocabulary.add_word(
            "słowo",
            translations=[
                {"english": "word", "verified": False},
            ],
            source="test",
        )

        vocabulary.add_word(
            "słowo",
            translations=[
                {"english": "term", "verified": False},
            ],
            source="test",
        )

        entry = vocabulary.get_word("słowo")
        translations = {
            item["english"]
            for item in entry["translations"]
        }

        self.assertIn("word", translations)
        self.assertIn("term", translations)

    def test_vocabulary_marks_verified_translation(self):
        vocabulary.add_word(
            "słowo",
            translations=[
                {"english": "word", "verified": False},
            ],
            source="test",
        )

        vocabulary.add_word(
            "słowo",
            translations=[
                {"english": "word", "verified": True},
            ],
            source="verified_feedback",
        )

        entry = vocabulary.get_word("słowo")

        verified = [
            item for item in entry["translations"]
            if item["english"] == "word"
        ]

        self.assertTrue(verified[0]["verified"])

    def test_memory_survives_reload(self):
        learn_from_feedback(
            polish="trwałe słowo",
            answer="persistent word",
            accepted=True,
            context="trwałość",
        )

        with self.memory_file.open(
            "r",
            encoding="utf-8",
        ) as file:
            saved_data = json.load(file)

        self.assertTrue(saved_data["words"])

    def test_empty_word_returns_error(self):
        result = analyze_word("   ")

        self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main(verbosity=2)
