"""`SessionControl` (`docs/contracts.md` -> Core enums, Judge contracts ->
Session controls).

Lives apart from `tests/core/test_contracts.py` (owned by another WP in this
wave) because this WP (M1 fix F5) only adds the enum itself.
"""

from mana_leak_core.contracts.enums import SessionControl


def test_session_control_members_match_contract() -> None:
    assert {member.value for member in SessionControl} == {
        "answer",
        "new_question",
        "end_session",
    }


def test_session_control_is_a_str_enum() -> None:
    assert SessionControl.end_session == "end_session"
    assert isinstance(SessionControl.end_session, str)
