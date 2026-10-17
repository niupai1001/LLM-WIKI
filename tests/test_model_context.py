import unittest
from unittest.mock import patch

from station import model_context as context


OFFICIAL = {"LLM_BASE_URL": "https://api.deepseek.com", "LLM_MODEL": "deepseek-flash"}


class VerifiedContextBudgetTests(unittest.TestCase):
    def test_official_profile_records_actual_model_and_documented_basis(self):
        with patch.dict("os.environ", OFFICIAL):
            profile = context.official_profile()
        self.assertEqual(profile["documented_model"], "DeepSeek-V4.1-Flash")
        self.assertEqual(profile["documentation_checked_at"], "2026-10-16")
        self.assertEqual(profile["documentation_url"], context.PROVIDER_DOCUMENTATION)
        self.assertEqual(profile["max_context_characters"], 500000)
        self.assertEqual(profile["max_request_utf8_bytes"], 800000)

    def test_custom_adapters_never_inherit_official_context_allowance(self):
        with patch.dict("os.environ", OFFICIAL):
            self.assertIsNone(context.official_profile(model=lambda _: {}))
            self.assertEqual(context.context_character_limit(model=object()), 160000)
            self.assertEqual(context.context_character_limit(default=12000), 500000)

    def test_similar_host_alternative_model_and_nonofficial_url_are_not_eligible(self):
        cases = [("https://api.deepseek.com.example.org", "deepseek-flash"),
                 ("https://api.deepseek.com", "deepseek-chat"), ("http://api.deepseek.com", "deepseek-flash"),
                 ("https://user:secret@api.deepseek.com", "deepseek-flash"),
                 ("https://api.deepseek.com?api_key=secret", "deepseek-flash")]
        for base, model in cases:
            with self.subTest(base=base, model=model), patch.dict("os.environ", {"LLM_BASE_URL": base, "LLM_MODEL": model}):
                self.assertIsNone(context.official_profile())
                self.assertEqual(context.context_character_limit(default=9000), 9000)

    def test_context_extension_is_bounded_and_does_not_truncate(self):
        source = {"all_original": "x" * 200000}
        with patch.dict("os.environ", OFFICIAL):
            self.assertGreater(context.check_context(source)["characters"], 200000)
            with self.assertRaises(context.ContextBudgetExceeded):
                context.check_context(source, model=object())
            with self.assertRaises(context.ContextBudgetExceeded):
                context.check_context({"all_original": "x" * 500000})
        self.assertEqual(len(source["all_original"]), 200000)

    def test_full_message_utf8_budget_includes_system_and_unicode_bytes(self):
        messages = [{"role": "system", "content": "汉" * 270000}, {"role": "user", "content": "small context"}]
        with patch.dict("os.environ", OFFICIAL):
            with self.assertRaisesRegex(context.ContextBudgetExceeded, "Complete request.*UTF-8 bytes"):
                context.check_messages(messages)
            stats = context.check_messages(messages, model=object())
        self.assertGreater(stats["utf8_bytes"], 800000)
        self.assertIsNone(stats["byte_limit"])


if __name__ == "__main__":
    unittest.main()
