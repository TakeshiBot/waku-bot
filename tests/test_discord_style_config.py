import pytest
from pydantic import ValidationError

from waku.config import _AppConfig


@pytest.mark.parametrize("interval", [None, 0, 5])
def test_discord_reaction_interval_does_not_change_telegram_interval(interval):
    config = _AppConfig(
        token="offline-token", owners=[], agent_periodic_reaction_interval=7,
        discord_periodic_reaction_interval=interval,
    )
    assert config.agent_periodic_reaction_interval == 7
    assert config.discord_periodic_reaction_interval == interval


def test_negative_discord_reaction_interval_is_rejected():
    with pytest.raises(ValidationError):
        _AppConfig(token="offline-token", owners=[], discord_periodic_reaction_interval=-1)
