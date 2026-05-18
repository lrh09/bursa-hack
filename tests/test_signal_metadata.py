"""All Strategy subclasses must declare the mandatory bank-metadata fields."""
from __future__ import annotations

import pytest

# Import side-effect: registers every Strategy subclass.
import bursahack.signals  # noqa: F401
from bursahack.signals.registry import all_strategies


MANDATORY = ("DISPLAY_NAME", "SHAPE_KEYS", "SHORT_BLURB",
             "DEFINITION_MD", "SOURCE_FILE", "ADDED")


@pytest.mark.parametrize("cls", all_strategies(), ids=lambda c: c.__name__)
def test_subclass_has_mandatory_metadata(cls):
    missing = []
    for f in MANDATORY:
        value = getattr(cls, f, "")
        if value == "" or value is None:
            missing.append(f)
    assert not missing, f"{cls.__name__} missing mandatory metadata: {missing}"


def test_at_least_six_strategies_registered():
    assert len(all_strategies()) >= 6
