import unittest
from unittest.mock import patch

from llm_provider import (
    DEFAULT_MODEL_DISPLAY_NAMES,
    _is_openai_text_model,
    discover_available_models,
    load_model_catalog,
    normalize_model_selection,
    resolve_model_selection,
)


class ModelCatalogTests(unittest.TestCase):
    def test_default_models_resolve_to_their_actual_api_ids(self):
        expected = {
            "Gemini 3.1 Pro": ("Gemini", "gemini-3.1-pro-preview"),
            "Claude Opus 4.8": ("Claude", "claude-opus-4-8"),
            "GPT-4o": ("OpenAI", "gpt-4o"),
            "GPT-5.6 Sol": ("OpenAI", "gpt-5.6-sol"),
            "GPT-5.6 Terra": ("OpenAI", "gpt-5.6-terra"),
            "GPT-5.6 Luna": ("OpenAI", "gpt-5.6-luna"),
        }

        self.assertEqual(set(DEFAULT_MODEL_DISPLAY_NAMES), set(expected))
        for display_name, (provider, model_id) in expected.items():
            model = resolve_model_selection(display_name)
            self.assertEqual((model.provider, model.model_id), (provider, model_id))

    def test_legacy_model_names_are_migrated_without_changing_actual_provider(self):
        self.assertEqual(normalize_model_selection("GPT-5.5"), "GPT-4o")
        self.assertEqual(normalize_model_selection("Claude Opus 4.8 높음"), "Claude Opus 4.8")
        self.assertEqual(normalize_model_selection("Gemini 3.1 Pro (확장모드)"), "Gemini 3.1 Pro")

    def test_unknown_model_is_rejected_instead_of_silently_using_another_model(self):
        with self.assertRaises(ValueError):
            resolve_model_selection("알 수 없는 모델")

    def test_recommended_catalog_is_loaded_from_the_json_file(self):
        models = load_model_catalog()
        gemini = next(model for model in models if model.display_name == "Gemini 3.1 Pro")

        self.assertEqual(gemini.status, "미리보기")
        self.assertTrue(gemini.recommended)
        self.assertEqual(resolve_model_selection("GPT-5.6 Terra").selection_key, "OpenAI|gpt-5.6-terra")

    def test_openai_discovery_filters_non_text_models(self):
        payload = {
            "data": [
                {"id": "gpt-5.6-sol"},
                {"id": "gpt-image-1"},
                {"id": "text-embedding-3-large"},
                {"id": "gpt-realtime-2"},
            ]
        }
        with patch("security_manager.SecurityManager.get_api_key", return_value="test-key"), patch(
            "llm_provider._http_json", return_value=payload
        ):
            models = discover_available_models("OpenAI")

        self.assertEqual([model.model_id for model in models], ["gpt-5.6-sol"])
        self.assertTrue(_is_openai_text_model("gpt-5.6-sol"))
        self.assertFalse(_is_openai_text_model("gpt-image-1"))


if __name__ == "__main__":
    unittest.main()
