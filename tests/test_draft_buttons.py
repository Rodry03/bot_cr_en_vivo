from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot


class FakeStatusMessage:
    def __init__(self) -> None:
        self.text = ""
        self.reply_markup = None

    async def edit_text(self, text: str, reply_markup=None, **kwargs) -> None:
        self.text = text
        self.reply_markup = reply_markup


class FakeQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = SimpleNamespace(chat=SimpleNamespace(id=123))
        self.answers = []
        self.edits = []

    async def answer(self, *args, **kwargs) -> None:
        self.answers.append((args, kwargs))

    async def edit_message_text(self, text: str, **kwargs) -> None:
        self.edits.append(text)

    async def edit_message_reply_markup(self, **kwargs) -> None:
        pass


def _token_from(status: FakeStatusMessage) -> str:
    callback_data = status.reply_markup.inline_keyboard[0][0].callback_data
    action, token = callback_data.split(":")
    assert action == "approve"
    return token


class DraftButtonsTests(unittest.IsolatedAsyncioTestCase):
    async def _show_two_drafts(self, context) -> tuple[str, str]:
        first_status, second_status = FakeStatusMessage(), FakeStatusMessage()
        with patch(
            "bot.generate_copy",
            new=AsyncMock(
                side_effect=[("Copy de la feria", "festivo"), ("Copy del accidente", "sobrio")]
            ),
        ):
            await bot.generate_and_show_draft(first_status, context, "Nota de la feria.")
            await bot.generate_and_show_draft(second_status, context, "Nota del accidente.")
        return _token_from(first_status), _token_from(second_status)

    async def test_approve_uses_the_draft_of_its_own_message(self) -> None:
        context = SimpleNamespace(user_data={}, bot_data={})
        first_token, second_token = await self._show_two_drafts(context)
        self.assertNotEqual(first_token, second_token)

        query = FakeQuery(f"approve:{first_token}")
        await bot.handle_callback(SimpleNamespace(callback_query=query), context)

        self.assertIn("Copy de la feria", query.edits[-1])
        self.assertIn(f"<b>{bot.TONE_DISPLAY['festivo']}</b>", query.edits[-1])
        self.assertNotIn("accidente", query.edits[-1])

        # La segunda noticia sigue intacta y se aprueba con su propio copy.
        second_query = FakeQuery(f"approve:{second_token}")
        await bot.handle_callback(SimpleNamespace(callback_query=second_query), context)
        self.assertIn("Copy del accidente", second_query.edits[-1])

    async def test_regenerate_uses_the_note_of_its_own_message(self) -> None:
        context = SimpleNamespace(user_data={}, bot_data={})
        first_token, _ = await self._show_two_drafts(context)

        query = FakeQuery(f"regenerate:{first_token}")
        with patch(
            "bot.generate_copy",
            new=AsyncMock(return_value=("Otro copy de la feria", "festivo")),
        ) as generate_copy:
            await bot.handle_callback(SimpleNamespace(callback_query=query), context)

        generate_copy.assert_awaited_once_with("Nota de la feria.", temperature=0.9)
        self.assertIn("Otro copy de la feria", query.edits[-1])

        # Aprobar tras regenerar publica la variante nueva de esa misma noticia.
        approve_query = FakeQuery(f"approve:{first_token}")
        await bot.handle_callback(SimpleNamespace(callback_query=approve_query), context)
        self.assertIn("Otro copy de la feria", approve_query.edits[-1])

    async def test_approve_still_works_after_choosing_card_without_photo(self) -> None:
        context = SimpleNamespace(user_data={}, bot_data={})
        first_token, _ = await self._show_two_drafts(context)

        await bot.handle_callback(
            SimpleNamespace(callback_query=FakeQuery(f"sin_foto:{first_token}")), context
        )
        query = FakeQuery(f"approve:{first_token}")
        await bot.handle_callback(SimpleNamespace(callback_query=query), context)

        self.assertIn("Copy de la feria", query.edits[-1])

    async def test_unknown_token_shows_alert_instead_of_wrong_draft(self) -> None:
        context = SimpleNamespace(user_data={}, bot_data={})
        await self._show_two_drafts(context)

        query = FakeQuery("approve:0000000000000000")
        await bot.handle_callback(SimpleNamespace(callback_query=query), context)

        self.assertEqual(query.edits, [])
        self.assertTrue(query.answers[-1][1]["show_alert"])


if __name__ == "__main__":
    unittest.main()
