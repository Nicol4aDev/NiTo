from types import SimpleNamespace

from nito.config import Secrets, config_from_dict
from nito.telegram_bot import authorized, build_application


class Msg:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


def _context(allowed):
    cfg = config_from_dict({}, Secrets(allowed_user_ids=frozenset(allowed)))
    return SimpleNamespace(application=SimpleNamespace(bot_data={"cfg": cfg}))


def _update(user_id):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_message=Msg(),
        callback_query=None,
    )


async def test_unauthorized_user_is_rejected():
    called = []

    @authorized
    async def handler(update, context):
        called.append(update.effective_user.id)

    upd = _update(999)
    await handler(upd, _context({123}))
    assert called == []
    assert "Non autorizzato" in upd.effective_message.replies[0]
    assert "999" in upd.effective_message.replies[0]

    await handler(_update(123), _context({123}))
    assert called == [123]


def test_application_builds_without_network():
    cfg = config_from_dict({}, Secrets(telegram_token="123:abc", allowed_user_ids=frozenset({1})))
    app = build_application(cfg)
    commands = {c for h in app.handlers[0] for c in getattr(h, "commands", ())}
    assert {"status", "panic", "pause", "resume", "scan", "positions", "stats"} <= commands
