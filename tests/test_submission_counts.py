"""Unit tests for ``EMLYUserActionsTable.submission_counts``.

The widget reads this on mount to drive the per-form "N of M
submissions remaining" footnote and the post-limit engagement bubble.
The aggregation has to be bot-scoped (no cross-tenant counts), user-
scoped, and limited to ``action_name == "form_submit"`` so generic
actions (link clicks, etc.) don't inflate it.

Tests run against the conftest sqlite DB; ``isolated_db`` would also
work but we don't need transactional rollback because each test inserts
unique fixture ids.
"""
from __future__ import annotations

from datetime import datetime

import pytest


@pytest.fixture
def fresh_actions():
    """Wipe and recreate the tables we touch so each test starts clean."""
    from db.db import DB
    from models.bots import Bot
    from models.emly_user_action import EMLYUserActions
    from models.emly_users import EMLYUser

    DB.connect(reuse_if_open=True)
    DB.create_tables([Bot, EMLYUser, EMLYUserActions], safe=True)
    for model in (EMLYUserActions, EMLYUser, Bot):
        try:
            model.delete().execute()
        except Exception:
            pass
    yield
    for model in (EMLYUserActions, EMLYUser, Bot):
        try:
            model.delete().execute()
        except Exception:
            pass


def _seed_bot(bot_id: str):
    from models.bots import Bot
    now = datetime.now()
    Bot.create(
        id=bot_id,
        slug=f"slug-{bot_id}",
        name=f"Bot {bot_id}",
        is_active=True,
        is_deleted=False,
        created_at=now,
        updated_at=now,
    )


# Note: we don't seed EMLYUser rows. SQLite's FK enforcement is off by
# default, and the count query filters by FK column values (strings),
# not joined relations. Skipping seed keeps the test minimal.


def _insert_action(bot_id: str, user_id: str, action_name: str, action_value: str):
    """Low-level insert that skips ``to_dict`` (which dereferences the
    EMLYUser FK and fails if we didn't seed a matching user row)."""
    import uuid as _uuid
    from models.emly_user_action import EMLYUserActions
    now = datetime.now()
    EMLYUserActions.create(
        id=str(_uuid.uuid4()),
        bot=bot_id,
        user=user_id,
        action_name=action_name,
        action_value=action_value,
        created_on=now,
        updated_on=now,
    )


def test_empty_when_no_submissions(fresh_actions):
    from models.emly_user_action import USER_ACTIONS
    _seed_bot("bot-a")
    assert USER_ACTIONS.submission_counts("bot-a", "user-1") == {}


def test_groups_by_action_value(fresh_actions):
    from models.emly_user_action import USER_ACTIONS
    _seed_bot("bot-a")
    _insert_action("bot-a", "user-1", "form_submit", "callback_form")
    _insert_action("bot-a", "user-1", "form_submit", "callback_form")
    _insert_action("bot-a", "user-1", "form_submit", "lead_form")
    assert USER_ACTIONS.submission_counts("bot-a", "user-1") == {
        "callback_form": 2,
        "lead_form": 1,
    }


def test_excludes_other_action_names(fresh_actions):
    from models.emly_user_action import USER_ACTIONS
    _seed_bot("bot-a")
    _insert_action("bot-a", "user-1", "form_submit", "callback_form")
    _insert_action("bot-a", "user-1", "link_click", "callback_form")  # noise
    assert USER_ACTIONS.submission_counts("bot-a", "user-1") == {"callback_form": 1}


def test_isolates_across_bots(fresh_actions):
    # Same visitor id can land on two different bots (the X-Emly-UserID
    # header is browser-scoped, not bot-scoped). The query must not
    # leak counts across that boundary. SQLite doesn't enforce FK
    # uniqueness here so we can seed both rows with id="user-1".
    from models.emly_user_action import USER_ACTIONS
    _seed_bot("bot-a")
    _seed_bot("bot-b")
    _insert_action("bot-a", "user-1", "form_submit", "callback_form")
    _insert_action("bot-b", "user-1", "form_submit", "callback_form")
    _insert_action("bot-b", "user-1", "form_submit", "callback_form")
    assert USER_ACTIONS.submission_counts("bot-a", "user-1") == {"callback_form": 1}
    assert USER_ACTIONS.submission_counts("bot-b", "user-1") == {"callback_form": 2}


def test_isolates_across_users(fresh_actions):
    from models.emly_user_action import USER_ACTIONS
    _seed_bot("bot-a")
    _insert_action("bot-a", "user-1", "form_submit", "callback_form")
    _insert_action("bot-a", "user-2", "form_submit", "callback_form")
    _insert_action("bot-a", "user-2", "form_submit", "callback_form")
    assert USER_ACTIONS.submission_counts("bot-a", "user-1") == {"callback_form": 1}
    assert USER_ACTIONS.submission_counts("bot-a", "user-2") == {"callback_form": 2}
