"""Stable category fixtures independent of the user's live subscriptions."""
from pathlib import Path
from unittest.mock import patch

CATEGORIES = Path(__file__).parent / 'fixtures' / 'legacy-categories.json'
CHILD_CONFIG = "from pathlib import Path as _FixturePath\nimport station.knowledge as _fixture_knowledge\n_fixture_knowledge._CONFIG = _FixturePath(" + repr(str(CATEGORIES)) + ")\n"

def category_fixture():
    return patch('station.knowledge._CONFIG', CATEGORIES)
