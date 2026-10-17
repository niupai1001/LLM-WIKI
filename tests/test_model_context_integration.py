import os
import unittest
from unittest.mock import Mock, patch

from station import compiler, knowledge_synthesis as synthesis
from station.model_context import ContextBudgetExceeded


OFFICIAL = {'LLM_BASE_URL': 'https://api.deepseek.com', 'LLM_MODEL': 'deepseek-flash'}


class ProviderContextIntegrationTests(unittest.TestCase):
    def test_official_alignment_can_receive_complete_large_input_and_larger_plan_output(self):
        context = {'knowledge_points': 'evidence ' * 22000}
        with patch.dict(os.environ, OFFICIAL), patch('station.wiki.call_llm', return_value={'concepts': []}) as model:
            result = synthesis._invoke('align', 'Use all supplied points.', context, None)
        self.assertEqual(result, {'concepts': []})
        self.assertEqual(model.call_args.kwargs, {'max_tokens': 65536})
        self.assertIn(context['knowledge_points'], model.call_args.args[0][-1]['content'])

    def test_custom_adapter_keeps_its_original_limit_even_with_official_environment(self):
        model = Mock()
        with patch.dict(os.environ, OFFICIAL), self.assertRaises(synthesis.ContextLimitError):
            synthesis._invoke('align', 'Instructions', {'text': 'x' * 170000}, model)
        model.assert_not_called()

    def test_compiler_review_keeps_complete_evidence_with_verified_provider_limit(self):
        context = {'originals': 'source evidence ' * 13000}
        with patch.dict(os.environ, OFFICIAL), patch('station.wiki.call_llm', return_value={'reviews': []}) as model:
            result = compiler._request('verify', 'Check the originals.', context, None)
        self.assertEqual(result, {'reviews': []})
        self.assertIn(context['originals'], model.call_args.args[0][-1]['content'])

    def test_complete_utf8_byte_budget_is_checked_before_any_provider_request(self):
        context = {'text': '证' * 300000}
        with patch.dict(os.environ, OFFICIAL), patch('station.wiki.call_llm') as model:
            with self.assertRaises(synthesis.ContextLimitError):
                synthesis._invoke('align', 'Instructions', context, None)
            with self.assertRaises(ContextBudgetExceeded):
                compiler._request('verify', 'Instructions', context, None)
        model.assert_not_called()
