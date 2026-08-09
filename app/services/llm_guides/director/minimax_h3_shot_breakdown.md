VIDEO PROMPT (video_prompt) — for MiniMax H3:

MiniMax H3 generates synchronized picture and stereo sound. A Director shot may
be text-only (FL2VA) or use soft image/audio references (Ref2VA); neither path
guarantees a fixed start frame. Every video prompt therefore stands on its own.

SELF-CONTAINED SHOT RULES:
- Describe the finished target shot, not instructions to copy, animate, replace,
  or edit a reference.
- Include the setting, composition, every visible subject, identity/appearance,
  wardrobe, action, camera behavior, lighting, dialogue, ambience, effects, and
  music that must exist in the result.
- Follow the model-aware CHARACTER NAMING block in the surrounding Director
  instructions. In prompt-only/direct-reference mode, preserve every recognizable
  proper identity and its series, film, franchise, or performer exactly as
  supplied and pair reference labels with useful visible traits. When generated
  shot images are enabled, follow the supplied name-to-description conversion
  instead so the image and video prompts stay aligned.
- Do not invent names, dialogue, franchises, or scene details absent from the
  screenplay or concept.
- References are guidance. Do not emit guessed <Picture N>, <Video N>, or
  <Audio N> tags; Maestro maps the per-shot references after planning.

HIGHEST PRIORITY REQUEST-FACT LEDGER
- Before writing, silently make a private request-fact ledger containing every
  authored identity; concrete object and its qualifiers or color; action;
  location; event; sound; requested silence; and music. Preserve which subject,
  time, place, and record each fact belongs to. Never output, label, quote, or
  mention this ledger.
- Copy every ledger fact into the finished prompt without deletion,
  substitution, generalization, reassignment, or relocation. Incidental details
  are still mandatory: `yellow leaf` cannot become `leaf` or disappear.
- A format repair may change only canonical wrappers and record-boundary syntax.
  It must preserve every request fact and association unchanged. Never invent a
  fact, qualifier, identity, relationship, action, intensity, or escalation.

CONTINUITY WITHOUT A FIXED START IMAGE:
- Treat wardrobe and blocking as explicit shot state. For every visible person,
  repeat the complete head-to-toe clothing and exact first-frame position:
  screen-left/center/right, depth, pose, facing direction, and nearby props.
- State the same opening composition in video_prompt; names alone do not carry
  appearance, clothing, or position into a new text-only generation.
- Give each uninterrupted place/time one continuity_group. Before an ordinary
  same-scene cut, visibly move each person into the next shot's opening position.
- Use continuity_strategy=extend_previous only for a literal same-composition
  continuation that should inherit the preceding generated final frame. Use
  continuity_strategy=continuous for normal cuts within the same scene.

CONTEXT-IR FORMAT:
- Structure video_prompt with exactly these labeled sections:
  integrated_multimodal_description, overall_soundscape, non_diegetic_music.
- Put integrated_multimodal_description on its own line. Write every internal
  shot as exactly one physical-line record in this exact shape:

  [Shot N] [STARTs-ENDs] shot_name: SHORT NAME | audiovisual_description: VISIBLE ACTION, CAMERA, LIGHT, AND SYNCHRONIZED SOUND | dialogue_and_vocalizations: EXACT <d> BLOCKS AND REQUESTED VOCALIZATIONS, OR none

- Begin with [Shot 1] at 0.00 and end the final record at the enclosing
  structured duration_sec. Number records sequentially. START and END are
  explicit seconds on one global timeline within video_prompt; every END is
  greater than START, each next START equals the previous END, and ranges are
  strictly ordered, contiguous, disjoint, and have no gaps.
- Preserve every user-authored global timestamp's numeric value, precision,
  order, cut association, and shot number. Add only the canonical trailing `s`
  unit wrapper when placing that value in `[STARTs-ENDs]`; do not round,
  reformat, or otherwise change it. Infer only missing boundaries.
- Every user-authored event timestamp is a mandatory record boundary, even when
  it marks an action, line, sound, or state change rather than a camera cut.
  The preceding record must end at that exact value and the next record must
  start at that exact value, with the timestamped event in the next record.
  A record boundary does not imply a visual cut; preserve visual continuity
  unless the user authored a cut. Never omit or round the timestamp, and never
  invent a cut, event, or timestamp.
- Authored-event boundary example — user: `Mara waits by the door with a yellow
  leaf on the floor. At 3.125 seconds, Mara opens the door, revealing a chipped
  blue cup on the sill. At 9.50 seconds, Theo says "Ready." Duration 12.75
  seconds.` Canonical records:
  `[Shot 1] [0.00s-3.125s] shot_name: Mara waits | audiovisual_description: Mara waits by the door; a yellow leaf lies on the floor. | dialogue_and_vocalizations: none`
  `[Shot 2] [3.125s-9.50s] shot_name: Door opens | audiovisual_description: Mara opens the door, revealing a chipped blue cup on the sill; the yellow leaf remains on the floor. | dialogue_and_vocalizations: none`
  `[Shot 3] [9.50s-12.75s] shot_name: Theo says Ready | audiovisual_description: The yellow leaf remains on the floor and the chipped blue cup remains on the sill as Theo speaks. | dialogue_and_vocalizations: Theo (S1) says: <d>[English] Ready.</d>`
- Never bury a shot boundary in prose such as "At 30 seconds, [Shot 2]
  begins."
  Do not place headings, bullets, commentary, or continuation prose between
  shot records. This record structure is inside the existing video_prompt
  string and does not add or change any Director JSON field.
- Invalid -> valid correction: `15.00-40.00: the door opens` and
  `[15.00s-40.00s] the door opens` are invalid because they omit the shot
  marker; write `[Shot 2] [15.00s-40.00s] shot_name: Door opens | audiovisual_description: The door opens. | dialogue_and_vocalizations: none`.
  Every timeline record must begin with `[Shot N] [`.
- For timing the screenplay or user did not author, treat boundaries as
  chronological narrative anchors, not a metronome. Infer naturally unequal
  boundaries from action, dialogue, reactions, and visual rhythm. Approximate,
  irregular boundaries are valid; do not routinely place changes at exact 5,
  10, 15, or 30-second intervals. Never alter a user-supplied timestamp.
- Give every record a concise shot_name, preserving any screenplay-authored
  shot or scene name verbatim. Keep all character names, exact <d> dialogue
  blocks, and requested laughs, cries, gasps, grunts, breaths, singing, or
  other vocalizations in the record where they occur; never move, summarize,
  or drop them.
- Compact canonical record: `[Shot 1] [0.00s-13.40s] shot_name: Exact reply | audiovisual_description: Mara remains screen-right as Theo enters screen-left. | dialogue_and_vocalizations: Theo (S1) says: <d>[English] I brought it.</d>`.
- Give each speaking person a stable ID such as (S1) or (S2).
- Literal speech uses <d>[English] Exact words.</d> (change the language tag
  when requested). Speaker identity, action, delivery, and voice are outside
  the dialogue tag. Preserve scripted dialogue verbatim.
- Every structured dialogue_beats entry must also appear exactly once in
  video_prompt. Never leave the actual spoken words only in the JSON field.
- When no dialogue is requested, explicitly keep mouths closed and omit voices
  or speech-like sounds. Explicitly forbid muttering, murmuring, improvised
  words, and gibberish; never fill unused time with invented speech.
- After the last spoken line, keep mouths closed and extend or hold only the
  requested state and atmosphere. Use reactions or motion only when requested
  or necessarily entailed by the authored event; never invent them as filler.
- overall_soundscape contains ambience, practical effects, and non-verbal human
  sounds. Do not repeat dialogue there.
- non_diegetic_music is audience-only music. Use N/A unless music is requested
  or the shot follows supplied driving music.

TIMING:
- Keep actions and dialogue realistic for the requested duration. Spoken text
  should generally stay at or below about two words per second.
- Do not put scheduling commands, references to a previous generated piece, or
  alternate storyboard trigger syntax inside video_prompt. Use only the
  required structured continuity fields outside video_prompt.
- For supplied driving audio, describe the visible performance, lip movement,
  rhythm, and action that synchronize to it; do not transcribe or replace its
  audible content.

Do not include negative prompts, model names, LoRA names, technical settings,
reference-index guesses, or explanatory prose in video_prompt.
