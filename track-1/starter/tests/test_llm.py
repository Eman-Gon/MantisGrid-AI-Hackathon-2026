from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

STARTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STARTER))

import llm


class LLMConfigurationTests(unittest.TestCase):
    def test_client_disables_sdk_retries_and_bounds_timeout(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "FEATHERLESS_API_KEY": "test-only-key",
                    "FEATHERLESS_TIMEOUT_S": "17.5",
                },
                clear=True,
            ),
            patch.object(llm, "OpenAI") as client,
        ):
            llm.LLM()

        client.assert_called_once_with(
            api_key="test-only-key",
            base_url=llm.DEFAULT_BASE_URL,
            timeout=17.5,
            max_retries=0,
        )

    def test_timeout_must_be_positive(self) -> None:
        with patch.dict(
            "os.environ",
            {"FEATHERLESS_API_KEY": "test-only-key", "FEATHERLESS_TIMEOUT_S": "0"},
            clear=True,
        ), self.assertRaisesRegex(ValueError, "must be positive"):
            llm.LLM()


if __name__ == "__main__":
    unittest.main()
