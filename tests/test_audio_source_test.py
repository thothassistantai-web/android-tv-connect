"""Tests for line-by-line audio source test helpers."""

from __future__ import annotations

import sys
import unittest

sys.path.insert(0, ".")
from android_tv_connect.audio_source_test import (
    build_audio_test_queue,
    next_queue_index,
)
from android_tv_connect.media_enumeration import AudioSourceOption


class AudioSourceTestTests(unittest.TestCase):
    def test_build_queue_uses_enumerated_sources(self) -> None:
        sources = [
            AudioSourceOption(name="source.a", description="Input A"),
            AudioSourceOption(name="source.b", description="Input B"),
        ]
        queue = build_audio_test_queue(sources)
        self.assertEqual([item.name for item in queue], ["source.a", "source.b"])

    def test_build_queue_adds_manual_and_auto_once(self) -> None:
        sources = [AudioSourceOption(name="source.a", description="Input A")]
        queue = build_audio_test_queue(
            sources,
            manual_name="manual.source",
            include_auto_resolved="source.b",
            auto_label="Auto",
        )
        self.assertEqual(len(queue), 3)
        self.assertEqual(queue[0].name, "source.b")
        self.assertEqual(queue[0].label, "Auto")
        self.assertEqual(queue[1].name, "source.a")
        self.assertEqual(queue[2].name, "manual.source")

    def test_next_queue_index_advances_and_stops(self) -> None:
        queue = build_audio_test_queue(
            [
                AudioSourceOption(name="a", description="A"),
                AudioSourceOption(name="b", description="B"),
            ]
        )
        self.assertEqual(next_queue_index(queue, "a"), 1)
        self.assertIsNone(next_queue_index(queue, "b"))
        self.assertEqual(next_queue_index(queue, "missing"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
