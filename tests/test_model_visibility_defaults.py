"""Continuum first-launch model visibility.

Locks leftover 1.9.0 `DEFAULT_ENABLED_MODELS` / `nsfw_only` / LTX avatar
defaults to Continuum curated symbols. Continuum never hid `animate` or
`mmaudio_nsfw` behind a mature-only catalog gate, and avatar does not
default to LTX-2.3 Distilled.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STORE_PATH = ROOT / "ui" / "src" / "stores" / "useStore.ts"


def _store_source() -> str:
    return STORE_PATH.read_text(encoding="utf-8")


def _typescript_block(source: str, declaration: str) -> str:
    return source.split(declaration, 1)[1].split("])\n", 1)[0]


class ModelVisibilityDefaultsTests(unittest.TestCase):
    def test_fresh_defaults_keep_continuum_curated_families(self):
        source = _store_source()
        defaults = _typescript_block(
            source,
            "const DEFAULT_ENABLED_MODELS = new Set([",
        )
        self.assertIn("'minimax_h3'", defaults)
        self.assertIn("'ltx2_22B_distilled_1_1'", defaults)
        self.assertIn("'mmaudio_v2'", defaults)
        self.assertIn("'mmaudio_nsfw'", defaults)
        self.assertIn("'animate'", defaults)
        self.assertIn("const DEFAULTS_VERSION = 9", source)
        self.assertNotIn("const DEFAULTS_VERSION = 10", source)

    def test_mmaudio_nsfw_catalog_entry_has_no_mature_only_gate(self):
        source = _store_source()
        match = re.search(
            r"\{ model_type: 'mmaudio_nsfw'.*?\}",
            source,
        )
        self.assertIsNotNone(match)
        self.assertNotIn("nsfw_only: true", match.group(0))
        self.assertIn("architecture: 'mmaudio'", match.group(0))

    def test_mode_defaults_prefer_continuum_h3_and_empty_avatar_fallback(self):
        source = _store_source()
        defaults = source.split(
            "const modeDefaultModel: Record<GenerationMode, string> = {",
            1,
        )[1].split("}\n", 1)[0]
        self.assertIn("video: 'minimax_h3'", defaults)
        self.assertIn("image: 'flux2_klein_9b'", defaults)
        self.assertIn("audio: 'kugelaudio_0_open'", defaults)
        self.assertIn("avatar: '',", defaults)
        self.assertNotIn("avatar: 'ltx2_22B_distilled_1_1'", defaults)
        enabled = _typescript_block(
            source,
            "const DEFAULT_ENABLED_MODELS = new Set([",
        )
        self.assertIn("'ltx2_22B_distilled_1_1'", enabled)


if __name__ == "__main__":
    unittest.main()
