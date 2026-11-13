from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot


class GenerateCopyTests(unittest.IsolatedAsyncioTestCase):
    async def test_copy_request_keeps_the_pre_card_call_contract(self) -> None:
        note = "Una asociación local organiza una recogida solidaria."
        output = {
            "tono": "festivo",
            "copy": "Recogida solidaria 🍎",
        }
        create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(output, ensure_ascii=False))
                    )
                ]
            )
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            with patch.object(bot, "AsyncGroq", return_value=client):
                copy_text, tone = await bot.generate_copy(note)

        self.assertEqual((copy_text, tone), (output["copy"], output["tono"]))
        request = create.await_args.kwargs
        self.assertEqual(request["model"], bot.GROQ_MODEL)
        self.assertEqual(request["temperature"], 0.7)
        self.assertEqual(request["max_tokens"], 1024)
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertEqual(request["messages"][0]["content"], bot.COPY_SYSTEM_PROMPT)
        self.assertNotIn("reasoning_effort", request)

    async def test_card_metadata_is_a_separate_low_reasoning_request(self) -> None:
        note = (
            "Limpieza del canal del río Águeda. "
            "Los trabajos retiran maleza y sedimentos junto al puente romano."
        )
        output = {
            "titular": "Limpieza del canal del río Águeda",
            "resaltado": "río Águeda",
            "subtitulo": "Los trabajos retiran maleza y sedimentos junto al puente romano.",
            "categoria": "MEDIO AMBIENTE",
            "lugar": "rio-agueda",
        }
        create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(output, ensure_ascii=False))
                    )
                ]
            )
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            with patch.object(bot, "AsyncGroq", return_value=client):
                metadata = await bot.generate_card_metadata(note)

        self.assertEqual(metadata, output)
        request = create.await_args.kwargs
        self.assertEqual(request["model"], bot.GROQ_MODEL)
        self.assertEqual(request["temperature"], 0.3)
        self.assertEqual(request["reasoning_effort"], "low")
        self.assertEqual(request["max_tokens"], 1024)
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertEqual(request["messages"][0]["content"], bot.CARD_METADATA_INSTRUCTIONS)
        self.assertNotIn('"copy":', request["messages"][0]["content"])
        self.assertNotIn('"tono":', request["messages"][0]["content"])
