"""
LTX Audio-to-Video Renderer — lean and timing-aware.

Used when uploaded audio or dialogue is the timing anchor.
Keeps prompts leaner than T2V. Prioritizes staging, physical performance,
camera framing, and environment. Lets audio drive timing.

Pass 1: assembles essential staging fields into a tagged draft
Pass 2: LLM rewrites into a lean, performance-focused prompt
"""

from __future__ import annotations
from typing import Optional

from ..schema import ShotPlan, ProductionPlan
from ..policies import resolve_visual_style
from .base import BaseRenderer


class LtxA2VRenderer(BaseRenderer):
    mode = "a2v"

    def _refinement_system_prompt(self, shot: ShotPlan, **context) -> str:
        extra = " Preserve exact quoted dialogue." if shot.audio_plan.lip_sync_critical else ""
        return f"Rewrite into 2-4 lean sentences, present tense. Focus on staging, performance, camera.{extra} No names. Output ONLY the prompt."

    def render(
        self,
        shot: ShotPlan,
        plan: Optional[ProductionPlan] = None,
        **context,
    ) -> str:
        """Pass 1: assemble tagged draft for A2V."""
        parts = []

        # Shot setup
        visual_style = resolve_visual_style(
            shot.visual_style,
            has_visual_reference=bool(context.get("has_reference", False)),
        )
        setup = [
            value for value in (visual_style, shot.camera_plan.framing)
            if value
        ]
        if shot.environment:
            setup.append(shot.environment)
        if shot.lighting:
            setup.append(shot.lighting)
        parts.append(f"SETUP: {', '.join(setup)}")

        # Character staging
        subjects = self._subjects_text(shot, plan)
        if subjects:
            spatial = f", {shot.spatial_setup}" if shot.spatial_setup else ""
            parts.append(f"SUBJECTS: {subjects}{spatial}")

        # Key action beats (limited)
        if shot.action_beats:
            key_beats = shot.action_beats[:3]
            parts.append(f"ACTION: {'. '.join(key_beats)}")

        # Dialogue (critical for lip sync)
        if shot.audio_plan.lip_sync_critical and shot.dialogue_beats:
            dialogue = self._dialogue_text(shot, plan)
            if dialogue:
                parts.append(f"DIALOGUE: {dialogue}")

        # Camera
        if shot.camera_plan.movement:
            parts.append(f"CAMERA: {shot.camera_plan.movement}")

        # Ending beat
        if shot.ending_beat:
            parts.append(f"ENDING: {shot.ending_beat}")

        return " | ".join(parts)
