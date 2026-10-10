# Storyboard v2 and the M5 Edit room: design specification

Status: design for approval, 2026-10-10. Owner's brief: "the editing tool will be close to Adobe or CapCut, a professional look", with Storyflow, the DomoAI storyboard round-up and noahmorris.nl (NexLev, VidRush) as references.
Mockups: [mockup-storyboard.html](mockup-storyboard.html) and [mockup-editor.html](mockup-editor.html) (open them in Edge or Chrome at 1440 x 900). Summary for the owner: [README.md](README.md).

This document is the single design source for M5. It builds on `docs/PLAN.md` (sections 2, 3, 8), `docs/M1-M2-CONTRACT.md` section 6 (`storyboard.json`), `docs/M3-M4-CONTRACT.md` section 3 (`timeline.json`), `docs/M5A-CONTRACT.md` (routes, roles, audit), and `docs/TRANSITIONS-V2-CONTRACT.md` (the cinematic transition library and Motion v2). The preview engine mirrors two parity documents written by that wave: [MOTION-PARITY.md](MOTION-PARITY.md) (camera moves and 2.5D parallax) and `docs/design/TRANSITION-PARITY.md` (the cuts). Where this design needs a data or API change, section 10.7 lists it. Following the rule in `CLAUDE.md`, the contract changes first and the code second.

Contents

1. Goals, pitches, principles
2. The shared workspace (shell, routes, theme, type, panels)
3. Storyboard v2
4. The Edit room (M5 timeline editor)
5. The preview engine (animatic and live preview)
6. Keyboard shortcuts
7. States
8. The ten most common tasks, step by step
9. Accessibility and plain-English copy rules
10. Data flow (files, locks, regeneration, approvals, API, contract changes)
11. Technology choices and licences
12. Build plan M5.1 to M5.4 with acceptance criteria
13. Risks
14. Open questions for the owner

Appendix A: the 12-scene example used in the mockups. Appendix B: sources.

---

## 1. Goals, pitches, principles

### 1.1 Storyboard v2

**Pitch:** *See the whole video on one board, fix any scene where you see it, and play it as an animatic before the money is spent.*

Goals:
- Show the whole video as story sections and scene cards: picture or prompt, shot direction, narration, popup, motion and transition. Each card is readable at a glance, with no hidden tabs.
- Keep pictures consistent across scenes. Use "Cast & places", which are reusable characters and locations with reference images, plus shot directions (size, angle, light). This covers the "visual consistency" and "cinematic customisation" criteria of the DomoAI round-up, and the panel prompt, camera direction and reference pattern of Storyflow.
- Play the board as an animatic at every stage: estimated timing before the voice exists (an optional free scratch voice), real voice timing afterwards. Popups and captions are drawn in.
- Use one board for three moments: planning (stage 4, prompts), pictures review (stage 6, pictures with check results), and editing (stage 7 and later, the Storyboard view of the timeline).
- Approvals, locks and gates keep working as they do today. The board replaces the grid in `StoryboardBoard.tsx` without changing the approve contract.

### 1.2 Edit room (the M5 timeline editor)

**Pitch:** *A CapCut-style editor that already knows the story: every cut sits on the narration, every change previews instantly, and the final render matches what you saw.*

Goals:
- Reach a professional, Premiere and CapCut grade look and feel. That means a four-zone dark workspace, a precise timeline with a playhead, snapping and trim handles, a contextual inspector and pro shortcuts.
- Handle the common 80% of fixes without leaving the app (PLAN.md section 2): re-time cuts, re-split narration, swap or regenerate a picture, adjust motion with on-picture handles, change transitions, edit popups and captions, change the music and its ducking, shorten pauses, and render presets.
- Preview live in the browser, drawn from `timeline.json`, with the same motion and transition maths as the renderer. The final file is still made by FFmpeg.
- Hand off to Kdenlive, DaVinci Resolve (FCPXML) and CapCut drafts for anything deeper.

### 1.3 Non-goals (stay out of scope; use a hand-off instead)

These belong in a hand-off, not in M5:
- Arbitrary multi-track compositing, video clips, masks, colour grading, keyframes on any parameter other than the two camera rectangles, speed ramps, and audio effects other than gain, fades and ducking.
- Editing in a third-party format. `timeline.json` stays the only editable truth (PLAN.md section 2).
- Real-time collaboration. Projects live on one laptop; two windows on the same project are handled by revision checks (section 10.3).

### 1.4 Design principles

1. **The voice is the clock.** Scene cuts sit at sentence starts by default and can move to word boundaries. Nothing the editor does can move a word inside the narration. The only edits that change narration timing are explicit ones: shortening a pause, reordering scenes and re-recording a sentence. All three are reversible.
2. **One source of truth per question.** `storyboard.json` holds the plan (what each scene says and shows). `timing.json` holds the clock. `timeline.json` holds the cut that gets rendered. Each edit writes to a known file (section 10.2).
3. **What you see is what renders.** Motion and transitions use the renderer's exact formulas. Popups use the same Pillow bitmaps. Captions use the same fonts and the same line breaks. Anything only approximate says so on screen.
4. **Amber means "here, now, selected, do it".** `#FFC000` is used only for the playhead, the selection, the current time and primary buttons. Everything else stays in neutral graphite-navy, so amber always points at something.
5. **Your changes are kept.** Every human change is recorded and survives an AI rebuild. Everything can be undone, and history snapshots include the original AI version (the pattern VidRush uses).
6. **Plain English, pro speed.** Labels are written for video editors, not developers. Full keyboard control sits underneath, and every tooltip names its shortcut.

---

## 2. The shared workspace

Both screens are views of one workspace component: same shell, same tokens, same inspector parts and same preview engine. A Storyboard/Timeline switch in the top bar flips between them, as Descript's View menu does.

### 2.1 Routes and entry points

| Route | What opens | Notes |
|---|---|---|
| `/editing` | Edit room project picker (exists today) | Unchanged list of videos at edit, export or done |
| `/editing/:projectId` | Workspace, Timeline view | Full-bleed. Replaces today's `EditReview` page body and removes the "Timeline editor arrives in M5" strip |
| `/editing/:projectId?view=storyboard` | Workspace, Storyboard view | Same data as the timeline |
| `/automation/projects/:id/storyboard` | Workspace, Storyboard view at stage 4 (plan) or stage 6 (pictures) | Opened by "Open the storyboard" in the project's review panel |

**Full-bleed shell (`WorkspaceShell`).** The workspace must not inherit `AppShell`'s 240 px sidebar or its `max-w-[1440px] px-8 py-6` container. At 1280 CSS px that would leave about 1000 px for three columns and a timeline. The workspace layout route renders:
- a **48 px app rail** that replaces the sidebar: product mark, the four area icons, the review count badge, and the user chip at the bottom;
- a **44 px workspace top bar** that replaces the app top bar;
- `h-screen`, no page scroll, `min-width: 1100px`.

The project page keeps a compact review panel with an "Open in Edit room" or "Open the storyboard" button. `EditReview.tsx` and `StoryboardBoard.tsx` stay as fallbacks until M5.3 is accepted.

### 2.2 Editor theme tokens (dark only, added to `frontend/src/index.css`)

The dashboard's navy (`#0b1220` and `#111a2e`, about 46% HSL saturation) is too blue around a picture. Colour judgement suffers when a frame is surrounded by saturated colour. The workspace therefore uses a graphite-navy at about half that saturation. It keeps the brand undertone and the CapCut/Premiere step of roughly 2x luminance between gutters and panels. The monitor letterbox is pure black.

| Token | Value | Use |
|---|---|---|
| `--ed-canvas` | `#0A0D14` | gutters (4 px), top bar, app rail, empty timeline area |
| `--ed-panel` | `#141924` | panel bodies (library, monitor surround, inspector, timeline) |
| `--ed-panel-2` | `#181E2A` | panel headers, ruler, track headers, cards |
| `--ed-raised` | `#1E2533` | inputs, chips, toggles (off), tiles |
| `--ed-raised-hi` | `#283044` | hover on raised, toggles (on) |
| `--ed-track` | `#10141C` / `#121722` | lane backgrounds, alternating |
| `--ed-line` | `#232B3B` | 1 px separators |
| `--ed-line-strong` | `#2F3A50` | hover outlines, focus rails |
| `--ed-black` | `#000000` | monitor letterbox |
| ink | `--cfs-ink #E8EDF7`, `--cfs-ink-muted #96A3BC`, `--cfs-ink-faint #7C8BA3` | text (14.9:1, 6.9:1 and 5.0:1 on `--ed-panel`) |
| accent | `#FFC000` (hover `#FFD033`, ink `#0B1220`) | playhead, selection ring, current timecode, primary buttons only |
| status | ok `#22C55E`, warn `#F59E0B`, fail `#EF4444`, info `#60A5FA` | check results, tips, links |

Lane colours. All are muted, with white text at 6:1 or better. Hue plus lane position plus icon tells the lanes apart; colour is never the only signal.

| Lane | Clip body | Detail colour |
|---|---|---|
| Scenes | name bar `#24365C`, thumbnails below | cut separator `#0A0D14` 1 px |
| Popups | `#6B4FA8` | text `#FFFFFF` |
| Captions | `#8B4F41` | text `#FFFFFF` |
| Voice | `#0E5566` | waveform `#7FD6E6`, pauses hatched `#FFFFFF` 7% |
| Music | `#2F6B4F` | waveform `#9BE0B8` at 55%, volume line `#E8EDF7` |
| Markers | sentence ticks `#4A5878`, section flags `#1E2D4A` with text `#9CC3FA`, user markers `#60A5FA` (or chosen colour) | |
| Motion boxes (monitor) | start `#22C55E`, end `#F87171` (DaVinci Resolve's green/red Dynamic Zoom convention) | |

Amber never appears on a lane. When a clip colour is light (a user-chosen marker, for example), its waveform or text flips dark. This follows the Premiere lesson on bright label colours.

### 2.3 Type, density and controls

| Element | Spec |
|---|---|
| UI text | Inter 12 px / 16 px, weight 400 to 500 |
| Dense labels (track headers, ruler, chips) | Inter 11 px, weight 500, 0.01em tracking |
| Inputs | Inter 13 px, controls 24 px tall (Spectrum component-height-75); primary buttons 32 px |
| Section headers in panels | Inter 11 px semibold uppercase, 0.06em tracking, `--cfs-ink-muted` |
| Timecodes | IBM Plex Mono 13 px (monitor), 16 px semibold (timeline header), 10 px (ruler). Inter with `font-feature-settings: "tnum"` elsewhere, so numbers never jitter |
| Icons | lucide (already used), 16 px, stroke 1.75; 14 px inside chips |
| Radii | panels 6 px, cards 8 px, clips 4 px, chips 999 px, buttons 6 px |
| Shadows | none on panels (flat, CapCut style); popovers `0 12px 32px rgb(0 0 0 / .5)` with a 1 px `--ed-line-strong` border |
| Fonts offline | Bundle Inter and IBM Plex Mono (OFL) with the app. Today `index.html` loads Inter from Google Fonts, which falls back to system fonts offline. Noto fonts for previews come from `assets/fonts` |

Time format. The monitor and the timeline header use `HH:MM:SS:FF` at the timeline fps (30), for example `00:01:05:18`. Inspector fields and tooltips use `m:ss.t` (`1:05.6`) and durations in seconds (`10.4 s`). A setting, "Show times as frames / as seconds", switches the monitor and header.

### 2.4 Panels

- Panels resize with `react-resizable-panels`. Separators are 4 px gutters with a 1 px line that turns `--ed-line-strong` on hover. Each separator is a keyboard-focusable splitter: arrow keys move it 16 px.
- Sizes are kept per user in `localStorage` (`ccs-ws-layout-v1`). They are never written to `timeline.json`; storing them there would change the render key for no reason.
- Panels collapse to icon rails below their minimum. Double-clicking a separator restores the default.
- "Reset layout" lives in the top bar overflow menu.

---

## 3. Storyboard v2

### 3.1 Layout

ASCII view at 1440 x 900 (CSS px):

```
x: 0    48          288                                                1088                 1440
   +----+--------------------------------------------------------------------------------------+ y 0
   |rail| TOP BAR 44: < Why she paid for strangers v | Step 6 of 8 Pictures | [Storyboard|Timeline]|
   |    |          undo redo  Saved 16:58            Checks: 1 to fix   Regenerate v  [Approve]  |
   |    +-----------+-----------------------------------------------------+--------------------+ y 44
   |    | OUTLINE   | BOARD TOOLBAR 36: All 12 | Needs work 2 | Locked 2 |  INSPECTOR          |
   |    | 236 wide  |               Card size S M L | Show prompts       |  344 wide           |
   |    |           | STORY RIBBON 30: [1|2|3|4|5|6|7|8|9|10|11|12]      |  full height        |
   |    | Hook      | -- The reason -- 3 scenes - 30.6 s ------------    |                     |
   |    |  1  2     |  [card 7]---(Light leak)---[card 8]---( )---[card 9]|  Scene 7            |
   |    | Routine   | -- The town -- 3 scenes - 32.6 s --------------    |  Narration          |
   |    |  3  4     |  [card 10]---( )---[card 11]---( )---[card 12]     |  Picture and shot   |
   |    | ...       |   (board scrolls vertically)                       |  Popup              |
   |    | Cast and  +-----------------------------------------------------+  Motion             |
   |    | places    | ANIMATIC 206: [monitor 16:9] [mini timeline: scenes |  Transition out     |
   |    | Style     |   voice waveform, music, captions] play 01:05:18    |  Notes              |
   +----+-----------+-----------------------------------------------------+--------------------+ y 900
```

Pixel guidance. The outline and inspector keep their width when the window grows; the board takes the extra space.

| Region | 1440 x 900 | 1920 x 1080 | Rules |
|---|---|---|---|
| App rail | x 0, w 48, full height | same | fixed |
| Top bar | y 0, h 44 | h 44 | fixed |
| Outline | x 52, y 48, w 236, h 848 | w 236, h 1028 | min 200, max 320; collapses to a 56 px thumbnail rail below 1360 px width |
| Board | x 292, y 48, w 796, h 638 | w 1276, h 818 | flexible |
| Animatic strip | x 292, y 690, w 796, h 206 | h 206 | min 170, max 320, user-resizable; collapsible to a 36 px transport bar |
| Inspector | x 1092, y 48, w 344, h 848 | w 344, h 1028 | min 320, max 440 |
| Gutters | 4 px `--ed-canvas` | 4 px | |

Cards are 210 to 400 px wide (a 26 px header row, then the 16:9 frame). A section row holds as many cards as fit, at most 4. At 1440 that is 3 per row (about 220 px each); at 1920 the same rows have about 380 px cards, and sections with 4 or more scenes show 4 per row. For a Shorts project (9:16) cards turn portrait (frame 128 x 228) and hold 5 to 6 per row.

### 3.2 Panels and contents

**Top bar (44 px).**
- Left: back to the list, then the video title with a project switcher (`Why she paid for strangers`), with channel and format underneath (`Kind Ledger, Long video, 16:9`).
- Centre: the step chip with an 8-dot stepper (`Step 6 of 8 · Pictures to review`; below 1240 px only the dots and `Pictures`). Then undo/redo, the save state (`Saved 16:58`) and the Storyboard/Timeline switch. Timeline is disabled with the tooltip "The timeline is built at step 7 (Edit)" until `timeline.json` exists.
- Right:
  - the checks pill (`1 to fix, 1 tip`), which opens the checks list;
  - a Regenerate menu (whole storyboard with a note, unlocked scenes only, pictures that failed);
  - the primary button, which changes with the step: **Approve storyboard** at step 4, **Approve pictures** at step 6, **Open in Timeline** at step 7 and later.

**Outline (left).**
- The story sections from `script.json`. Each shows its colour dot, name, scene range, duration, and its scenes as 30 px rows: a 40 x 22 thumbnail, the number, a 2 to 3 word title from the shot description, a length, and state icons (lock, warning, generating).
- Clicking a row scrolls the board to the card and selects it. A bracket marks the rows currently visible on the board.
- Below the scenes:
  - **Cast & places**: one row per element, with its reference picture as the avatar (a coloured initial until a picture is set; round for people, rounded square for places), its name, and "N scenes". Clicking one filters the board to its scenes.
  - **Style**: the channel style guide sentence and the style sheet thumbnail (read-only; edited in Master Sheet, Channels).

**Board toolbar (36 px).**
- Filter chips: `All 12`, `Needs work 2` (warnings or failed checks), `No popup 4`, `Locked 2`.
- Card size S/M/L.
- Toggles: "Show prompts" (the prompt replaces the narration on cards) and "Show estimated times".
- Search by text across narration, prompts and popups.

**Story ribbon (30 px).**
- The whole video as one bar. Segments are proportional to length and tinted by section, with the scene number inside.
- A white bracket shows the part of the board on screen. The selected scene has a 2 px amber underline. Clicking a segment jumps to it.
- This keeps the whole video in view even when the board scrolls.

**Board.**
- Section rows start with a header line: name, number of scenes, length, and a "Play section" button.
- Cards follow in reading order, joined by connectors. Each connector carries the transition chip (Storyflow's "connections between panels so the sequence reads in order").
- The last card of a section carries a stub connector with the section's closing transition (for example `Dip to black 0.8 s`).
- Drag a card to reorder it; the drop indicator is a 3 px amber bar between cards.
- Drop a picture file or a library picture on a card to replace its picture (Storyflow's "mix real stills").

**Inspector (right).** Covered in section 3.5. It is the same component family as the Edit room inspector, and it edits plan fields.

**Animatic strip (bottom).**
- Left: a 16:9 monitor (about 280 x 158 at 1440).
- Right: a read-only mini timeline of the whole video. Lanes: ruler 14, scenes 34 (thumbnails), popups 8, captions 7, voice 26 (waveform), music 18 (with its ducking line).
- Header row: play/pause, timecode, loop, "Scratch voice / Real voice" (real when `timing.json` exists), "Open in Timeline".
- Clicking the mini timeline seeks. Selecting a card moves the playhead to that scene's start. Playing highlights the current card with a thin white outline (amber stays for the selection).

### 3.3 Card anatomy

```
+------------------------------ 210 to 400 px --------------------------------+
| [7] Why she paid                                             10.4 s [lock] |  header row 26 px: number, title,
+-----------------------------------------------------------------------------+  length, lock when locked
| +---------------------+                                    [Check passed]  |  frame, 16:9; status chip top right
| | Fifty years earlier |   popup drawn where it will appear (scaled)        |
| +---------------------+                                                    |
|                                             (hover: Play scene, centred)   |
| [Slow push in]                                                             |  motion chip bottom left
+-----------------------------------------------------------------------------+
| Close-up | Eye level | Warm window light                                    |  shot line, 10.5 px chips
| When he visited, she finally told him why. Fifty years earlier, a           |  narration, 12/17 px,
| stranger had paid for her groceries on the worst day of her life.           |  2 lines (3 at size L), ellipsis
| (purple dot) "Fifty years earlier"                                top left  |  popup line
| (R)(L)(H)                                               notes 1   locks 2   |  cast avatars, counters
+-----------------------------------------------------------------------------+
   connector 40 px wide: 1 px line + a 26 px round chip (transition family icon) and its length under it
```

| Part | Spec |
|---|---|
| Card | background `--ed-panel-2`, 1 px `--ed-line` border, radius 8; body padding 8 x 10; height about 265 px at size M (220 px wide) |
| Header row | 26 px: a 20 x 17 number tile, the 2 to 3 word scene title (semibold, ellipsis), the length (`10.4 s`, tabular figures) and a lock icon when locked. Labelling the frame above the picture follows Boords' frame label, and it keeps the picture itself clear for the popup preview |
| Frame | the scene picture (`object-fit: cover` of the cover region); at stage 4 a **prompt card** (dashed 1 px border, the prompt in 10.5 px, status "Picture not made yet"); the popup drawn at 1:1 scale in its real position; motion chip bottom-left; status chip top-right (`Check passed`, `Check failed`, `New picture 1 of 3`, `Uploaded`, `Approved`) |
| Hover | border `#3D4964`, the card lifts 1 px with a soft shadow; the frame shows a centred 34 px "Play scene" button and quick actions top right (New picture, Replace, More) in place of the status chip |
| Selected | 2 px amber outline with 2 px offset (so the layout does not move), the number tile in amber with navy text, and a faint amber tint on the header row |
| Playing | 1 px white outline (not amber) |
| Locked | lock icon in the header row; the lock counter in the footer; locked fields are read-only in the inspector until unlocked |
| Warning or tip | 3 px warn-colour top edge plus a warn chip in the body (for example "Still for 9.8 s: try a slow drift") |
| Check failed | 3 px fail-colour top edge, a faint red tint over the frame, a red status chip, and the reason plus a **New picture** button in the body ("Readable text in the picture") |
| Generating | the old picture dimmed with an animated sheen, a progress line along the bottom of the frame, and the chip "New picture 1 of 3"; Cancel in the inspector |
| Focus | 2 px `#E8EDF7` outline with 2 px offset (keyboard only) |

Connectors.
- A connector is 40 px of 1 px line (28 px stub after the last card of a section) with a 26 px round chip showing the transition family's icon, and the length under it (`0.8 s`).
- Hovering the chip names the transition ("Light leak (warm), 0.8 s") and plays its animated preview in a 160 x 90 tooltip, using the Transitions v2 `/transitions/<id>.webp`.
- Clicking the chip opens the transition picker for that cut (the pack's tiles, a length slider with the 25% limit tick, Apply to all cuts, Vary all).
- A connector whose transition repeats the previous cut gets a warn-colour dot.

### 3.4 Shot directions, Cast & places, references

These are new optional fields (section 10.7). They give the AI and the person the vocabulary professional storyboards use (Boords' Camera and Lighting fields, Storyflow's camera directions, LTX Studio's shot types and Elements).

| Field | Values | Feeds |
|---|---|---|
| `shot.size` | Wide, Medium, Close-up, Detail | prompt prefix ("close-up of ...") |
| `shot.angle` | Eye level, Low, High, Top-down, Over the shoulder | prompt |
| `shot.light` | free text with suggestions (Warm window light, Dusk, Cold blue, Lamp light, Morning) | prompt |
| `element_ids` | Cast & places chips | prompt (each element's description) and `reference_images` (each element's picture) |
| `references` | up to 4 pinned pictures (uploads or other scenes) | `reference_images` for this scene only |

The images stage composes each prompt from these parts:

```
style guide + shot (size, angle) + subject prompt + cast and places descriptions + light + negative rules
```

It passes the style sheet, element pictures and pins as reference images. Nano Banana 2.1 accepts up to 14 reference images (PLAN.md section 2). The board shows the composed prompt read-only under "What the picture tool gets".

### 3.5 Inspector fields (Storyboard view)

| Section | Field | Control | Writes | Lock group | Rules and copy |
|---|---|---|---|---|---|
| Header | Scene number, section, time range, length, sentence count | text | none | none | "Scene 7, The reason, 1:00.8 to 1:11.2, 10.4 s" |
| Header | Lock | toggle plus menu (lock all fields, lock picture only) | `locked.*` | all | "Locked: the AI will not change this scene" |
| Narration | Sentences with numbers and times | read-only list | none | `narration` | words come from the script stage |
| Narration | Split here, between two sentences | inline button on hover | `sentence_ids` of a new scene | `narration` | needs 2 or more sentences |
| Narration | Move first or last sentence to the neighbour | menu on a sentence | `sentence_ids` of both scenes | `narration` | keeps every scene at 1 or more sentences |
| Picture | Picture | thumbnail 16:9 full width, check result, attempts | `image` (images stage) | `image` (new) | "Check passed, 8 of 10"; failed reasons listed |
| Picture | New picture | button with note field | images stage job | none | cost hint: "Makes a new picture. About $0.03" |
| Picture | Upload / choose | button | `image` plus upload | `image` | "Your picture is used as it is and skips the check" (logged) |
| Picture | Prompt | auto-growing textarea | `image_prompt` | `image_prompt` | style guide shown as a fixed prefix chip; no words, signs or logos |
| Picture | Shot size, angle, light | chip groups | `shot.*` | `image_prompt` | |
| Picture | Cast & places | tag input with avatars | `element_ids` | `image_prompt` | |
| Picture | References | 4 slots | `references` | `image_prompt` | |
| Popup | Text | input plus counter `3 / 6 words` | `popup.text` | `popup` | at most 6 words, never the same as the narration; empty means no popup |
| Popup | Style | menu with drawn previews | `popup.style` | `popup` | styles from the channel plus defaults |
| Popup | Position | 5-point grid (corners and centre) | `popup.position` | `popup` | |
| Popup | Appears | "0.3 s after sentence 17 starts" | `popup.key_sentence_id` (new), `in_offset_s` | `popup` | shows for up to 4 s and leaves 0.2 s before the scene ends (builder rules) |
| Motion | Preset | tiles (8 today; Motion v2 adds Drift, Tilt up/down, Dolly in/out, Handheld) | `motion.preset` (resets the rectangles) | `motion` | warns when the neighbour has the same move |
| Motion | Framing | "Edit on picture" opens the start/end boxes on the animatic monitor | `motion.start_rect` / `end_rect` (fractions) | `motion` | |
| Motion | Ease | In and out, Out, Gentle, Snap, Linear (the Motion v2 curves `ease_in_out_cubic`, `ease_out_quart`, `ease_in_out_sine`, `snap`, `linear`) | `motion.ease` (new, optional) | `motion` | empty means the preset's own curve (MOTION-PARITY.md section 4) |
| Motion | Style | Ken Burns / 2.5D parallax, plus Depth strength for parallax | `motion.style`, `motion.strength` (Motion v2) | `motion` | parallax needs the depth model; without it the scene plays as Ken Burns and says so |
| Transition out | Type | chip that opens the picker | `transition_out.type` | `transition_out` | only the channel's pack; never the same as the previous cut |
| Transition out | Length | slider 0.1 to 3.0 s | `transition_out.duration_s` | `transition_out` | shows the builder's limit: "Longest here: 2.4 s (a quarter of the shorter scene)" |
| Notes | Notes | list plus add | `notes` | none | "Notes go to the AI when this scene is redone" |

Multi-select (Ctrl or Shift click on cards):
- Fields shared by all selected cards show their value. Fields that differ show `Mixed` with an em dash (the Premiere convention).
- Changing a field applies it to every selected card.
- Motion and transition choices offer "Vary across selection", which assigns different presets and transitions and obeys the variety rules.

### 3.6 Behaviour by stage

| Stage | Frame shows | Times | Animatic | Primary action |
|---|---|---|---|---|
| 4 Storyboard (plan) | prompt card, or a picture if uploaded | estimated from words and speaking rate, shown as `about 10 s` | estimated timing; optional **scratch voice** (Windows' built-in voice through the browser speech API, free and offline, marked "Scratch voice, timing is approximate"); popups and narration captions | Approve storyboard |
| 6 Pictures | pictures with check status, generating states | real, from `timing.json` | real voice, default music of the channel | Approve pictures |
| 7 Edit and later | pictures from `timeline.json` | timeline times | the live preview (section 5) | Open in Timeline |

At stage 7 and later the board is a view of `timeline.json`. Its edits go through the same commands as the Timeline view (section 10.2).

### 3.7 What changes from the current Storyboard Board

- `SceneCard.tsx` becomes `BoardCard.tsx` (read-mostly) plus the shared `SceneInspector.tsx`. The long in-card forms move to the inspector, so cards stay scannable at 60 to 80 scenes.
- HTML5 drag and drop becomes `@dnd-kit` (keyboard reorder, accessible announcements).
- `VarietyPanel.tsx` becomes the checks list behind the top bar pill. Its per-scene issues show on cards.
- Approve, override, notes and the redo dialogs keep today's API (`POST .../stage/storyboard/approve` with `edits.storyboard`).

---

## 4. The Edit room (M5 timeline editor)

### 4.1 Layout

ASCII view at 1440 x 900:

```
x: 0    48            356 360                                  1096 1100                  1440
   +----+----------------+------------------------------------------+------------------------+ y 0
   |rail| < Why she paid | undo redo  Saved 17:45  [Storyboard|Timeline]  [Hand off v][Render v]| Checks  (IA) |
   |    +----------------+------------------------------------------+------------------------+ y 44
   |    | LIBRARY 304    | MONITOR 736                              | INSPECTOR 336          |
   |    | Pictures Motion| Preview, Live  Full v  Fit v   [boxes][safe][compare]               |
   |    | Transitions    | +--------------------------------------+ | Scene 7, The reason    |
   |    | Text Music     | |  frame 712 x 400, black letterbox     | | Picture                |
   |    | Claude         | |  popup top-left, caption bottom       | | Motion  [tiles]        |
   |    |                | |  green start box, red end box         | |  Start | End  Swap     |
   |    | [tile][tile]   | +--------------------------------------+ |  Scale 100% > 115%     |
   |    | [tile][tile]   | 00:01:05:18 / 00:02:04:00  |< << <| > |> >> >| loop vol  | Transition out >  |
   |    |                |                                          | Popup >  Narration >   |
   |    +----------------+------------------------------------------+------------------------+ y 554
   |    | TOOLBAR 36: Select Blade | Split Merge Delete | Marker Popup |  Snap Link Skim | size | - zoom + Fit |
   |    | 00:01:05:18 (header) | OVERVIEW 28: whole video, bracket, playhead                  |
   |    |                      | RULER 24: 0:58 1:00 1:02 ... loop band, playhead head       |
   |    | Markers  16          | ticks at sentence starts, section flags, your markers        |
   |    | Popups   24          | [Fifty years earlier]          [Four dollars in her purse]  |
   |    | Captions 24          | [cue][cue][cue]...                                           |
   |    | Scenes   64          | [6 ...][7 Why she paid  10.4 s ====================][8 ...] |
   |    | Voice    52          | word bar + waveform + hatched pauses                         |
   |    | Music    36          | waveform + volume line dipping under the voice               |
   +----+----------------------+-------------------------------------------------------------+ y 900
```

Pixel guidance (CSS px, 4 px gutters, timeline across the full width as in CapCut and Premiere):

| Region | 1440 x 900 | 1920 x 1080 | Rules |
|---|---|---|---|
| App rail | x 0, w 48 | w 48 | fixed |
| Top bar | h 44 | h 44 | fixed |
| Workspace inner area | x 52 to 1436, y 48 to 896 (1384 x 848) | x 52 to 1916, y 48 to 1076 (1864 x 1028) | |
| Top row / timeline split | 506 / 338 (60 / 40 of the inner height) | 614 / 410 | timeline min 220, max 70% of the height |
| Library | x 52, w 304 | w 408 | 22%, min 260, max 420; becomes a 48 px icon rail with fly-outs below 1360 px width |
| Monitor | x 360, w 736 | x 464, w 1028 | flexible |
| Inspector | x 1100, w 336 | x 1496, w 420 | 24%, min 300, max 420 |
| Monitor header / transport | 32 / 44 | 32 / 44 | |
| Picture frame in the monitor | 712 x 400 (16:9, width-bound) | 956 x 538 (height-bound) | black letterbox; 9:16 projects centre a portrait frame |
| Timeline header column | 120 | 120 | track headers; the timecode sits above them |
| Lane area width | 1254 (1384 - 120 - 10 scrollbar) | 1734 | |
| Lane heights | default (16/24/24/64/52/36) | large (16/28/28/88/64/48) chosen automatically at 1000 px or more of height unless the user picked a size | compact (16/20/20/48/40/28) below 760 px of height |

Proportions come from measured references:
- CapCut desktop: media 36 to 39%, player 36 to 39%, properties 21 to 26% across; top row 53 to 66%, timeline 33 to 45% down.
- Premiere's Captions and Graphics workspace: about 31 / 46 / 22 and 58 / 40.
- OpenCut: 25 / 50 / 25 and 50 / 50.

CashCow's monitor gets more width than CapCut's because every clip is a still picture and the picture is what people judge.

**Smaller screens (1280 x 720 CSS px).** Windows often recommends 150% scaling on 1080p laptops. A maximised pywebview window then gives a viewport of about 1280 x 640.
- The workspace switches to the compact preset: top bar 40, library as an icon rail, inspector 300, overview strip hidden, compact lanes.
- That leaves a 462 x 260 picture and 256 px of timeline.
- All ten tasks in section 8 must work at this size. Below 1100 px of width the page scrolls sideways rather than squeezing panels.

### 4.2 Top bar

The bar is aligned with the panels below, like DaVinci Resolve's toolbar, so related actions sit above the panel they affect.

| Segment | Contents |
|---|---|
| Above the library | Back to the Edit room list; video title with switcher; channel and format under it |
| Above the monitor | Undo and redo with labels in tooltips ("Undo: move cut 7 to 8"); save state (`Saved 17:45`, `Saving...`, `Not saved: retrying`); the Storyboard/Timeline switch (centred); **Hand off** menu; **Render** (amber, primary) |
| Above the inspector | Checks pill (`All good`, `1 tip`, `1 to fix`), step chip (`Step 7 of 8: Edit`), Ask Claude button, overflow menu (History, Reset layout, Keyboard shortcuts, Report a problem) |

**Render menu (the "finish" menu).** It opens under the Render button, 332 px wide and about 300 px tall, over the right part of the monitor. It is kept short so the caption area of the picture stays visible. It contains:
1. A header: `Finish this video`, with "Renders run in the background" on the right.
2. Preset rows, 36 px each: 1080p, 720p, 4K. Each has a checkbox, a name and size, and a status chip: `Out of date · 3 changes`, `Rendered 17:12` with a Play button, or `Not rendered`. 4K is disabled with "Turn on in Settings" when not allowed. The Play button and the Renders page lead to the files and to Open folder.
3. Options on one line: Captions, Popups (both burned in), Speed (Balanced / Faster / Better quality, which maps to the x264 preset).
4. Buttons: **Render 1080p · about 6 min** (primary; the label names the ticked sizes and the estimate) and **Range only** when an in/out range is set.
5. A progress block while rendering: per-preset bar, phase ("Pictures 12 of 12, transitions, final mix"), time left, and Cancel.
6. A footer: **Approve and send to export**. It is enabled when at least one ticked size is rendered and up to date and nothing blocks. Otherwise the reason shows under it ("Render 1080p first: the last render is out of date. Approving as Imran."). An "Approve anyway" option appears when a check blocks (reason required, logged).

**Hand off menu.** It offers "Open in Kdenlive (free)", "Export for DaVinci Resolve (FCPXML and captions)" and "Export a CapCut draft (best effort)". Each line says what survives ("Resolve: camera moves kept, captions as an SRT file, popups burned into the pictures"). The files go to `07_edit/handoff/` and the folder opens.

### 4.3 Library (left)

Tabs are icons over 10 px labels in a 56 px row (CapCut style). The panel scrolls inside the tab.

| Tab | Contents | Main interactions |
|---|---|---|
| Pictures | "This video" (scene pictures with numbers), "Other tries" (alternates and rejected pictures per scene), "Uploads", "Channel references" | drag onto a scene clip or card to replace (Alt-drop swaps two scenes' pictures); double-click to view large; upload button |
| Motion | preset tiles that animate on hover (the 8 current presets plus Motion v2's Drift, Tilt up/down, Dolly in/out, Handheld), energy chip, "Motion style: Ken Burns / 2.5D parallax" switch | click applies to the selected scene(s); drag onto a clip |
| Transitions | pack filter (Cinematic, Clean, Dynamic, All) for the channel's pack, a search box, a grid of 16:9 tiles that play the real preview on hover (`/transitions/<id>.webp`, rendered by the renderer itself), label, length and energy dots; the tile used by the selected cut has an amber outline | click applies to the selected cut; drag onto any cut; "Apply to all cuts"; "Vary all (no repeats)" |
| Text | popup styles drawn as cards (Brand yellow box, Lower third, Highlight, Sticker), caption styles (Bold white, Yellow, Clean, **Active word**, **Single word pop**, **Boxed**) | "Add popup at playhead" (T); click a caption style to apply it to the whole video |
| Music | tracks from the channel's music folder: name, length, licence badge (`Licence on file` / `No licence file`), play preview (wavesurfer.js mini waveform) | "Use" or drag onto the music lane |
| Claude | Ask Claude: a prompt box at the bottom, suggestion chips ("Shorten every pause over a second", "Vary the transitions in the second half", "Make scenes 5 and 6 calmer"), proposals as change lists with **Apply** / **Discard** | an applied proposal is one undo step; anything that costs money (new pictures, re-recorded sentences) asks first |

### 4.4 Monitor

- **Header (32 px):** the `Preview` title and a `Live` chip with a green dot. Hovering the chip shows "Drawn from the timeline as you edit. Motion and transitions match the final render; text can differ by a pixel or two." Then quality (`Full`, `Half`, `Auto`) and zoom (`Fit`, `50%`, `100%`). On the right are toggles: **Motion boxes** (on when the Motion section is open), **Safe areas** (title-safe 90% and action-safe 93%, plus a 9:16 crop guide for 16:9 videos) and **Compare with render** (an A/B wipe against the last render of this range).
- **Viewport:** black, frame centred. Overlays drawn by the engine (not DOM):
  - **Motion boxes.** Green `Start` and red `End` boxes with corner handles. Their centres are joined by a dashed arrow; the current view is marked with a crosshair. Dragging inside a box moves it, corners resize it with the aspect locked, Shift resizes from the centre and arrow keys nudge by 1% (5% with Shift). The boxes are clamped to the picture.
  - **Popup editing.** Double-clicking a popup on the frame edits its text in place. Dragging it snaps to the 5 positions.
  - **Whole picture mode.** Hold Alt or press the toggle to see the full picture (the cover region) with both boxes and the current frame outline, as Resolve's Dynamic Zoom does.
- **Transport (44 px):**
  - Left: current time in amber (`00:01:05:18`) and total in muted ink (`/ 00:02:04:00`).
  - Centre: to start, previous cut, frame back, a 32 px play/pause button, frame forward, next cut, to end.
  - Right: loop (on/off; on shows the in/out range), scrub audio, volume, full screen.

### 4.5 Inspector (right)

The inspector follows the selection. Its header names the selection, and nothing is ever blank.

**Scene selected** (shown in the mockup, scene 7):

| Section | Fields | Writes (`timeline.json`, unless noted) |
|---|---|---|
| Header | "Scene 7, The reason", "1:00.8 to 1:11.2, 10.4 s, 2 sentences", lock toggle, More (Split at playhead, Merge with previous/next, Copy settings, Paste settings, Go to the storyboard card) | `scenes[i].locked` |
| Picture | 96 x 54 thumbnail, file and size (`scene_07.png, 2560 x 1440`), check result, **Replace...**, **New picture** (note field and cost hint), "Fit: cover" (read-only today) | `scenes[i].image`; images stage job |
| Motion | preset tiles; Start / End switch; Scale (start % and end %); Centre X / Y (%); Ease (In and out, Out, Gentle, Snap, Linear); Style (Ken Burns / 2.5D parallax) with Depth strength; **Swap start and end**; **Reset to preset**; "Show boxes on picture" | `scenes[i].motion.{preset,start_rect,end_rect,ease,style,strength}`. Rectangles stay in source pixels; % and centre are views of them, matching the reference's centre-and-size path (MOTION-PARITY.md) |
| Transition out | animated tile, name, pack and energy chips; Length slider with the limit tick and label ("Longest here: 2.4 s"); Apply to all cuts; Vary | `scenes[i].transition_out` |
| Popup | text with word counter; style; position grid; animation (Slide, Fade, Pop); In and Out times with "linked to scene 7"; Remove | `popups[j]`; PNG re-rendered |
| Narration | sentences with times; per-sentence **Re-record** (voice tool, cost hint); "Move the cut here" between sentences | voice stage job; `scenes` times |

Collapsed sections show a one-line summary (`Light leak (warm), 0.8 s`), so a 506 px tall panel shows the whole scene at once.

**Other selections:**

| Selection | Inspector |
|---|---|
| Cut (click a transition badge or between clips) | "Cut between scene 7 and scene 8" and the Transition section |
| Popup clip | Popup fields; "Belongs to scene 7"; Unlink (moves freely, `scene` updated on drop) |
| Caption cue | Words as editable text (each word keeps its time); split at the cursor (Enter); Merge with next; "Fix everywhere" (finds the same misspelling in all cues); timing; style is global (link to Video settings) |
| Voice sentence | Text (read-only), times, words; **Re-record this sentence**; pause before and after |
| Pause | "Pause after sentence 17, 1.6 s"; Shorten to: 0.3 s, 0.6 s (natural), or a typed value; "Shorten all pauses over 1.0 s to 0.6 s (saves 7.4 s)" |
| Music clip | Track (menu from the channel folder, licence badge), Start the track at (offset), Volume (`-18 dB`), Dip under the voice (`-12 dB`), Dip speed (attack 0.3 s, release 0.8 s), Fade in and out, licence status |
| Marker | Label, colour, time, "Use as a YouTube chapter" |
| Several scenes | Shared values or "Mixed"; changes apply to all; Vary motion and Vary transitions buttons |
| Nothing | "Scene under the playhead" summary (Descript's convention), plus the **Video** settings: Captions (on/off, style, size, position, active-word colour, one or two lines), Popups (on/off, default style), Music (as above), Output (aspect, sizes), Checks |

### 4.6 Timeline

#### 4.6.1 Toolbar (36 px)

- **Left:** Select (A) and Blade (B) tools. Then Split (S), Merge, Delete. Then Add marker (M) and Add popup (T).
- **Right:** toggles for Snapping (N), Link popups to scenes, Follow playhead and Skim. Then lane size (Compact, Default, Large), zoom out, a zoom slider and zoom in (= and -), and **Fit** (Shift+Z).
- Toggles that are on use white icons on `--ed-raised-hi`; toggles that are off use faint icons.
- Every button's tooltip names its shortcut.

#### 4.6.2 Header timecode, overview strip and ruler

- **Header timecode.** The 120 px corner above the track headers shows the playhead time in IBM Plex Mono 16 px semibold (`00:01:05:18`), as in DaVinci Resolve. Clicking it opens "Go to time".
- **Overview strip (28 px).**
  - The whole video at a fixed fit. Scene segments carry tiny thumbnails and numbers, with section tints on top.
  - A white 1.5 px bracket with 6% white fill shows the window visible below; drag it to scroll and drag its edges to zoom.
  - The playhead appears as a 1 px amber line and the selected scene as a 2 px amber underline.
  - This is the Resolve Cut page's upper timeline idea, so a 60 to 80 scene video never needs constant zooming. It replaces today's `SceneStrip`.
- **Ruler (24 px).**
  - Background `--ed-panel-2`, labels in IBM Plex Mono 10 px `--cfs-ink-faint`.
  - Major ticks are 8 px with labels. Their spacing comes from {0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300} s, so that labels are at least 64 px apart. Minor ticks are 4 px, with 4 or 5 per major tick.
  - Labels read `m:ss`, or `m:ss:ff` at 240 px/s and above.
  - The in/out (loop) range is a band in the ruler with 8% white fill, 1 px 50% white edges and bracket handles. The lanes under it get a 3% white tint.
  - Click to seek; drag to scrub.

#### 4.6.3 Lanes: order, colours, heights

| Order | Lane | Default / compact / large (px) | Clip colour | Shows | You can |
|---|---|---|---|---|---|
| 1 | Markers | 16 / 16 / 16 | see 4.6.6 | sentence-start ticks, section flags, your markers | add (M), move, rename, delete your markers |
| 2 | Popups | 24 / 20 / 28 | `#6B4FA8` | one clip per popup: text, animation glyph, fade ramps, link glyph | move, trim, edit, delete, add |
| 3 | Captions | 24 / 20 / 28 | `#8B4F41` | one clip per cue: text, word ticks for active-word styles | edit text, split, merge, trim to words |
| 4 | Scenes (main) | 64 / 48 / 88 | name bar `#24365C` with thumbnails | scene clips, cuts, transition badges and bands | move cuts, split, merge, reorder, swap picture, set transitions |
| 5 | Voice | 52 / 40 / 64 | `#0E5566`, waveform `#7FD6E6` | 14 px word bar, waveform, sentence lines, hatched pauses | select sentences and pauses, shorten pauses, re-record |
| 6 | Music | 36 / 28 / 48 | `#2F6B4F`, waveform `#9BE0B8` | waveform, volume line with the dips under the voice, fade handles | change track, offset, volume, dips, fades |

- Lanes are separated by 4 px gaps of `--ed-canvas`.
- **Track headers (120 px):** icon, name, and lock and eye (or mute) toggles at 16 px.
- **Order rationale:** text lanes sit above the pictures they cover, and sound sits below, as in every NLE. Popups and captions are near the scenes for linking.

#### 4.6.4 Clip anatomy

**Scene clip (64 px default):**

```
|<- 8 px trim hit area (shown when selected or hovered)            ->|
+--+-----------------------------------------------------------+--+   1 px separator (#0A0D14) on both sides
|  | 7  Why she paid  10.4 s         [slow push glyph] [lock] (!)|  |   name bar 16 px, #24365C, Inter 11 px 600, 85% white
|##|[tile][tile][tile][tile][tile][tile][tile]                  |##|   thumbnails 48 px: 3 to 7 crops along the camera move
|  |<>                                                        <> |  |   start and end diamonds 10 px (selected only)
+--+-----------------------------------------------------------+--+
 ^ visible handle 6 px with a 2 px grip (selected)        transition band centred on the cut <->
```

| Part | Spec |
|---|---|
| Body | radius 4; the thumbnails are crops of the picture at 0%, 25%, 50%, 75% and 100% of the move (computed in CSS from the two rectangles, no server work), so the strip shows the push-in or pan direction |
| Name bar | `7  Why she paid  10.4 s`, then the motion glyph, lock glyph and warning dot |
| Separator | a 1 px darker line between neighbouring clips, always visible. This avoids the problem Premiere 25.1 users reported: "you can't see the cuts between clips" |
| Selected | 2 px amber ring outside the clip; trim handles appear (8 px hit area, 6 px visible, 2 px grip); start/end diamonds |
| Hover | 1 px `#2F3A50` outline; handles at 60% |
| Locked | lock glyph plus a 45 degree hatch at 6% white over the thumbnails |
| Warning / failed check | 3 px top edge in warn or fail colour, dot in the name bar, tooltip |
| Generating | animated sheen over the thumbnails, a spinner in the name bar, a progress tooltip |
| Edited by you | small white dot after the name (tooltip "Changed by you, kept when the AI rebuilds") |

**Transition on a cut.**
- A 16 px badge sits on the cut line with an icon for the transition family.
- A shaded band (white at 10%, with diagonal lines) as long as the transition, centred on the cut, shows where the blend really happens. The Transitions v2 renderer centres every cut on the narration's cut point: `n // 2` frames before the boundary and the rest after (`render/assembly.py`). The first renderer (`xfade` in `render/ffmpeg.py`) started the blend at the cut; the band follows whatever the parity doc and the renderer say.
- Selecting the badge selects the cut. Dragging either edge of the band changes the length symmetrically. It stops at the builder's limit, a quarter of the shorter neighbouring scene (`config/render.yaml`), with a tooltip.

**Popup clip (24 px).** It shows the text in white 11 px, a glyph for slide, fade or pop, fade ramps at both ends (0.3 s, or a third of the length) and a link glyph while linked. Its edges snap to words.

**Caption clip (24 px).** It shows the cue text (`\N` line breaks shown as ` / `) and, for active-word styles, word ticks along the bottom edge.

**Voice (52 px).**
- **Word bar (14 px):** each word in a 10 px label box at its time. Labels show at 60 px/s and above; between 30 and 60 px/s only ticks show; below that the bar is hidden.
- **Waveform (38 px):** mirrored min/max peaks from the backend (10 ms resolution).
- Sentence boundaries are 1 px 30% white lines.
- **Pauses:** pauses longer than 0.35 s (`pause_break_s` in `config/captions.yaml`) are hatched blocks labelled with their length (`1.6 s pause`) when wider than 40 px.

**Music (36 px).**
- The waveform at 55%, drawn after ducking, so the dips are visible.
- The volume line is 1.5 px `#E8EDF7`. It is flat at the track volume and dips under speech by the ducking amount, with the attack and release slopes.
- Fade handles are 8 px triangles at the top corners. The clip label reads "Quiet Morning (piano), -18 dB, dips -12 dB, Licence on file".

#### 4.6.5 Editing rules: snapping, trims, splits, links

The voice clock forbids ripple edits, with one explicit exception: shortening a pause.

**Selection.**
- Click selects; Ctrl+click adds; Shift+click selects a range on the same lane; drawing a box on empty lane space selects everything it touches.
- Esc clears the selection. Selecting a scene also highlights its popups and captions with a 1 px white outline.

**Snapping** (on by default, N toggles; hold **Alt** while dragging to ignore it for one drag). The threshold is 8 px on screen. Targets in priority order:
1. the playhead;
2. sentence starts and ends (`timing.json`);
3. word starts and ends;
4. scene cuts and transition ends;
5. popup and caption edges;
6. markers.

While a snap is active, a 1 px dashed `#E8EDF7` line runs through the lanes, with a chip near the pointer naming the target ("Start of sentence 17").

**Moving a cut (rolling edit).**
- Dragging the line between scenes 7 and 8 moves the end of 7 and the start of 8 together. Nothing else moves and the video length does not change.
- Default steps are sentence starts (the builder's own rule: scenes run from their first sentence's start to the next scene's start). Hold Shift for word boundaries ("fine cut"); hold Alt for any frame.
- Limits:
  - Each scene stays at least 2.0 s. This is a UI rule; the builder's floor is 0.2 s.
  - A cut can never cross another cut.
  - Transitions are re-clamped live to 25% of the shorter neighbour.
  - Popups of a shrinking scene are clamped. If a popup can no longer show for 0.6 s, the drag stops and says "Popup needs 0.6 s".
- While dragging, a tooltip shows both lengths, for example "Scene 7: 11.4 s, scene 8: 8.8 s (aim for 8 to 12 s)". Lengths outside the band turn warn-colour; they are never blocked.
- Q / W move the selected scene's start / end cut to the playhead, snapped to the nearest sentence start (Shift for words).

**Split** (S, Ctrl+B or Ctrl+K, or the Blade tool):
- The split lands on the nearest word boundary to the playhead, or the sentence boundary if one is within 0.3 s.
- The new right-hand scene keeps the same picture, marked "Needs its own picture". The motion continues without a jump: the first half ends at the interpolated rectangle and the second half starts there.
- The cut gets no transition (a plain cut), so the split is invisible until a new picture is chosen.

**Merge** (Delete on a scene, Shift+Delete to merge forward): the scene's time goes to its neighbour. Its popups move to the neighbour when they fit; if not, they are removed, the toast reports it, and Undo brings them back.

**Reorder a scene.** Drag the clip by its name bar. A ghost and an insertion line at cuts show where it will land; dropping moves the scene with its narration (section 8, task 1). Alt-drop on another scene swaps pictures only.

**Linked popups.**
- Linking is on by default. A popup belongs to its scene and moves with it.
- Moving a cut re-anchors the popup to its sentence and word.
- With linking off, or with Alt-drag, a popup can move to another scene and is re-linked on drop.

**Popups:** move within the scene; trim edges to words; at least 0.6 s; a warning above 4 s.

**Captions:** edges move only to word boundaries; text edits never touch the voice.

**Music:**
- Dragging the clip sideways changes where the track starts (`music.start_s`); the track loops as the renderer loops it.
- Dragging the volume line up or down changes the volume in 0.5 dB steps (0.1 with Shift). Corner handles set the fades.

**Voice:** it cannot be dragged. Pauses can be shortened by dragging the hatched block's right edge (0.1 s steps) or with the Pause inspector. This is the one ripple edit: everything after the pause moves earlier by the removed time, and the toast says so with Undo.

#### 4.6.6 Markers at sentence boundaries

The marker lane is built from three sources:
- **Sentence ticks:** a 6 px tick in `#4A5878` at every sentence start from `timing.json`. Hovering shows "Sentence 17: Fifty years earlier, a stranger had paid...". These are not stored; they are derived and always current.
- **Section flags:** a label pill at the first sentence of each script section (`The reason`), from `script.json` sections. These become YouTube chapters at export, as today. A flag can be renamed for the chapter list; the override is stored in `markers[]`.
- **Your markers:** M at the playhead adds a 10 px diamond in `#60A5FA` (other colours in the inspector) with a label. They are stored in `timeline.json` `markers[]`. "Use as a YouTube chapter" is an option.

Up and Down jump between cuts. Ctrl+Up and Ctrl+Down jump between sentence starts.

#### 4.6.7 Playhead

- A 2 px `#FFC000` line with a 1 px `rgb(0 0 0 / .55)` halo on both sides. This is Premiere's technique, and it keeps the line visible over bright thumbnails.
- It has a 12 x 14 px amber pentagon head in the ruler, and runs through every lane above the clips but below drag ghosts.
- While dragging, a timecode tooltip appears in IBM Plex Mono 11 px.
- During playback, **Follow playhead** pages the view when the playhead reaches 85% of the width.

#### 4.6.8 Zoom and navigation

- Zoom runs from 2 px/s up to 600 px/s (frames visible).
- Ctrl+wheel zooms around the pointer. Shift+wheel scrolls sideways. The wheel alone scrolls the lanes vertically when they overflow, otherwise sideways.
- Shift+Z or `\` fits the whole video. Shift+F zooms to the selection.
- WebView2 zooms the whole page on Ctrl+wheel (pywebview sets `IsZoomControlEnabled`). The timeline must register a non-passive wheel listener and call `preventDefault()` when `ctrlKey` is down.

---

## 5. The preview engine (animatic and live preview)

One engine, `frontend/src/preview/`, is written in framework-free TypeScript with a thin React wrapper. It draws any time `t` of a **render description**:
- `timeline.json` in the Edit room;
- a virtual timeline computed from `storyboard.json`, `timing.json` and the images in the board. This uses the same `scene_bounds` and `clip_transitions` rules as `render/timeline_builder.py`, ported once and tested against the Python version.

The engine never encodes video. FFmpeg makes every file.

### 5.1 Architecture

```
 timeline.json (or virtual timeline) --> RenderDescription (frames, rects, transitions, popups, cues, audio segments)
                                              |
            +---------------------------------+-------------------------------+
            v                                 v                               v
   Picture pipeline (PixiJS 8, WebGL2)   Text pipeline (Canvas 2D -> texture)  Audio graph (Web Audio)
   - textures from createImageBitmap     - captions: Noto via FontFace       - voice: one buffer per sentence
     (decoded at <= 2x preview size)     - popups: the backend's Pillow PNGs   scheduled at its timeline time
   - Ken Burns as a UV transform           (exact pixels), animated with      - music: the ducked stem (exact)
   - transitions as GLSL filters           the renderer's formulas            - master gain = loudness offset
     from TRANSITION-PARITY.md
   - parallax as a 64 x 36 mesh warp
            \_______________________________|_________________________________/
                                      Clock (AudioContext time while playing; on-demand frames while paused)
```

### 5.2 Clock and frames

- While playing, the master clock is the audio clock: `t = t0 + (audioContext.currentTime - c0) x rate`. Pictures are drawn on `requestAnimationFrame`.
- Every drawn picture is the frame the renderer would output: `n = floor(t x fps + 1e-6)` and `t_frame = n / fps`. Late frames are dropped; audio is never dropped.
- While paused or scrubbing, frames are drawn on demand from the current pointer time, throttled to one per animation frame.

### 5.3 Picture: Ken Burns, transitions, popups, captions

**Ken Burns** mirrors the Motion v2 reference, written out in [MOTION-PARITY.md](MOTION-PARITY.md) (`render/motion.py`, `render/scene_clips.py`), and the frame layout of `render/assembly.py`. Frame by frame:

```text
fps      = timeline.fps                                         # 30; frames are whole frames, first scene at 0
len_k    = frames from scene k's start to the next scene's start (the last one to its end)
cut i|j  = n = round(transition_out.duration_s * fps) frames (0 below min_frames = 2),
           CENTRED on the boundary C: h = n // 2 frames before C and n - h after
clip k   = h_in frames before the scene + len_k + (n_out - h_out) frames after it  # overlaps included
k        = index of output frame n inside clip k (0 = the clip's first frame, before the scene starts)
t        = k / (clip_frames - 1)            (0 when the clip has one frame)
E        = ease(t)                          # the preset's curve, or motion.ease when set:
                                            #   ease_in_out_cubic (default), ease_out_quart, ease_in_out_sine, snap, linear
c(E)     = c0 + (c1 - c0) * E               # centres of start_rect and end_rect move in a straight line
w(E)     = w0 * (w1 / w0)^E,  h(E) = h0 * (h1 / h0)^E          # even zoom rate
rect     = (c.x - w/2, c.y - h/2, w, h), kept inside the cover region (render/geometry.py), plus the
           handheld wobble for the handheld preset
pixel u  = source position x + (u + 0.5) * w / W               # pixel centres, as the reference
```

`ease` and the window maths are written once in `frontend/src/preview/motion.ts`. They are tested against JSON fixtures produced by the Python reference, and agreement is required to 1e-6. The renderer's FFmpeg path (`zoompan`) rounds its window to whole pixels, while the Python path is sub-pixel; `tests/test_motion.py` holds the two within 0.5 px, and the preview follows the sub-pixel reference. A timeline from before Motion v2 still plays: the preview reads the same fields the renderer reads, so both apply the v2 path.

**Transitions.**
- For the frames of a cut (from `C - h` to `C + n - h`), the outgoing scene A (still moving) and the incoming scene B (already moving: its clip started `h_in` frames before its scene) are drawn into two render textures. A GLSL filter blends them with the cut's progress.
- Each transition id of the Transitions v2 library (`config/transitions.yaml`, version 2) has one filter, written from `docs/design/TRANSITION-PARITY.md`: the easing (`ease_in_out_cubic`, `ease_out_quart`, `linear` or `snap`), the progress per frame, and the procedural light leaks and burns with the same noise seeds.
- Legacy `xfade` names are mapped exactly as the renderer maps them (the `legacy:` table: `fade` to `film_dissolve`, `wipeleft` to `luma_wipe_left`, and so on).
- Parallax (`motion.style: parallax`) uses the same displacement on a 64 x 36 mesh with a back and a front layer (PixiJS `MeshPlane`), as MOTION-PARITY.md section 6 describes. Without a depth map it is drawn as the eased Ken Burns move, as the renderer does.
- The last scene ends with a fade to black (white for `dip_white`) inside its own frames.

**Popups.**
- The engine shows the same PNG the renderer overlays. When text, style or position changes, it asks `POST /api/projects/{id}/popups/preview` for the PNG and its x/y, debounced 150 ms; this takes about 50 ms because Pillow is fast. Until the PNG arrives it draws an approximation, labelled `Drawing...`.
- Animation copies `_popup_exprs` and the fade filter in `render/ffmpeg.py`:
  - travel = max(1, 120 x scale) and ease = max(0, 1 - (t - start) / 0.35)^2;
  - `slide_left`: x = x0 + travel x ease;
  - `pop`: y = y0 + (travel / 2) x ease;
  - alpha fades in and out over min(0.3 s, length / 3);
  - visible while start <= t <= end.

**Captions.**
- `render/captions.py` already decides every line break: cue text carries `\N`, and the ASS file uses `WrapStyle: 2`, so libass never re-wraps. The engine splits on `\N` and never wraps either.
- Style follows `captions.style`: Noto family from `assets/fonts` (served at `/api/fonts/...` and loaded with `FontFace`), size `48 / 1080 x frame height`, bold, white fill. The outline is drawn first with `strokeText`, line width `2 x outline x scale`, rounded joins. The text is bottom-centre (`\an2`) with `margin_v = 60 / 1080 x frame height`.
- Arabic, Devanagari, Japanese and Korean use the browser's HarfBuzz shaping with the same fonts.
- The **active word** style colours the word being spoken (`\k`-style switch at the word start from `timing.json`). Single word pop shows one word per cue with a 1.0 to 1.12 scale pop over 0.12 s (`\t`). The ASS writer gets the same presets.
- Text is the one place where the preview can differ by a pixel or two. The monitor says so on hover, and the parity tests check caption boxes, not pixels.

### 5.4 Audio: voice, ducked music, loudness

- **Voice.** One `AudioBuffer` per sentence (`05_voice/sentences/<id>.wav`) is scheduled with `AudioBufferSourceNode.start(when, offset)` at its timeline time.
  - Buffers are decoded on demand for a window from 10 s behind to 60 s ahead of the playhead, cached in an LRU of about 40 MB.
  - Because the voice is a list of sentence clips, pause trims, reorders and re-recorded sentences preview instantly, without rebuilding `voice.wav`.
  - An own recording, which has no sentence files, is split once by the backend using `timing.json`.
- **Music: one ducking function for both sides.**
  - Today `timeline.json` stores `duck_db: -12` and the attack and release times, but `render/ffmpeg.py` ignores `duck_db`. It runs `sidechaincompress` with the global threshold 0.03, ratio 8 and 300/800 ms. An editor control labelled "Dip under the voice: -12 dB" would therefore not match the render.
  - Proposal (contract change): the backend computes a gain envelope once, from the voice's RMS at 10 ms with `duck_db`, attack and release (numpy, under a second for 10 minutes). It writes `07_edit/music_ducked.wav`, the music stem after track offset, looping, volume, fades and ducking.
  - The renderer mixes that stem instead of running `sidechaincompress`. The preview plays the same file and the music lane draws the same envelope.
  - While a new stem is being built after a change, the preview applies the envelope live to the raw track through a `GainNode` curve.
- **Loudness.** The renderer normalises the mix to -16 LUFS (`loudnorm`). The backend measures the mix of voice and stem once (EBU R128) and returns a `preview_gain_db`, so the preview plays at the loudness of the final file.

### 5.5 Scrubbing, skimming, loop, render range

| Feature | Behaviour |
|---|---|
| Scrub | dragging the playhead or ruler draws frames at the pointer; with "Scrub audio" on, it plays 60 ms voice grains with 10 ms fades |
| Skim | with Skim on, hovering the Scenes lane shows the frame under the pointer in the monitor without moving the playhead (CapCut's "preview axis"); leaving the lane restores the playhead frame |
| Loop | I and O set the range (X sets it to the selected clip, Alt+X clears it); Ctrl+L loops playback; with no range, loop plays the selected scene |
| Play around cut | Shift+/ plays from 2 s before to 2 s after the selected cut; this also happens automatically once after a transition change |
| Render range | Ctrl+Shift+E renders the in/out range at the proxy size with the real renderer, then opens **Compare** (a vertical A/B wipe between the live preview and the rendered clip) |

### 5.6 Performance budget and quality

| Measure | Budget on a team laptop (4-core i7, 16 GB, integrated GPU) |
|---|---|
| Playback | 30 fps at 960 x 540 with 2% or fewer dropped frames; main thread at most 8 ms per frame |
| Seek | at most 50 ms to a cached frame, at most 250 ms cold |
| Open a 60-scene, 10-minute project | first frame within 1.5 s |
| Memory (WebView2 renderer process) | at most 700 MB |
| Picture textures | decoded at up to 2x the preview size (at most 1920 wide); 8 to 12 cached (current scene +/- 3 plus the next transition) |
| Lane thumbnails | 128 px tall JPEGs from the backend, never the 2K/4K PNGs |

Quality: **Full** (monitor size x devicePixelRatio, at most 1280 x 720), **Half**, and **Auto**. Auto drops to Half after 3 frames over 33 ms and returns after 5 s of headroom.

### 5.7 Fallbacks and offline

- **No WebGL2** (blocked driver, lost context). The engine switches to Canvas 2D:
  - Ken Burns stays exact (`drawImage` with source rectangles);
  - dissolves and dips stay exact;
  - pushes and wipes are close;
  - other transitions fall back to a dissolve, with a `Simplified preview` chip.
  - A lost WebGL context restores itself and shows "Restarting the preview..." meanwhile.
- **Missing picture.** A navy slate with an icon and "Picture missing (scene 7)", the same as the renderer's error.
- **Mock media** (`CCS_*_PROVIDER=mock`). Placeholder pictures, silent voice with evenly spaced words: everything works offline and the waveform is flat. A `Mock media` chip shows in the top bar.

### 5.8 Parity tests (how "identical" is proven)

Parity is proven at two levels:
- **Unit level.** TypeScript functions (ease, rectangles, transition progress, popup motion, ducking envelope reading, scene bounds) are compared with Python fixtures and must match to 1e-6.
- **Frame level** (`tests/preview_parity/`, Playwright plus FFmpeg, run on demand):
  - Three mock projects: 3 scenes; 12 scenes with every transition used; Shorts. For each, 40 frames are rendered by the engine in headless Chromium and extracted from the FFmpeg 1080p render (scaled to the preview size).
  - Required: SSIM of at least 0.95 on motion-only frames and at least 0.92 on transition frames. Caption boxes are compared by position (IoU of at least 0.9), not by pixels.
  - Audio: voice and picture stay within +/-1 frame over 10 minutes; music RMS per 400 ms window stays within +/-1 dB of the final render.

---

## 6. Keyboard shortcuts

One documented map. It follows the shared core of Premiere, Resolve and CapCut. Where they disagree, both common keys are accepted (split is S, Ctrl+B and Ctrl+K). Shortcuts work while focus is in the workspace, never while typing in a text field. Esc leaves a text field and returns to the timeline.

| Action | Keys |
|---|---|
| Play / pause | Space, K |
| Play backwards / forwards (press again for 2x, 4x) | J / L |
| One frame back / forward | Left / Right |
| One second back / forward | Shift+Left / Shift+Right |
| Previous / next word | Alt+Left / Alt+Right |
| Previous / next cut | Up / Down |
| Previous / next sentence | Ctrl+Up / Ctrl+Down |
| Go to start / end | Home / End |
| Mark in / out | I / O |
| Mark the selected clip | X |
| Clear in and out | Alt+X |
| Loop playback | Ctrl+L |
| Play around the selected cut | Shift+/ |
| Split at the playhead | S, Ctrl+B, Ctrl+K |
| Move the selected scene's start / end cut to the playhead | Q / W |
| Select tool / Blade tool | A / B |
| Merge scene into the previous / next one; delete popup, marker or cue text | Delete or Backspace / Shift+Delete |
| Nudge the selected cut or clip by one frame / one word | , and . / Shift+, and Shift+. |
| Select previous / next clip on the lane | [ / ] |
| Add a marker | M |
| Add a popup at the playhead | T |
| Undo / redo | Ctrl+Z / Ctrl+Shift+Z, Ctrl+Y |
| Copy / paste settings (motion, transition, popup style) | Ctrl+C / Ctrl+V |
| Duplicate a popup or marker | Ctrl+D |
| Select all scenes | Ctrl+A |
| Deselect | Esc |
| Snapping on / off | N |
| Zoom in / out | = and -, Ctrl+= and Ctrl+-, Ctrl+wheel |
| Fit the whole video / zoom to the selection | Shift+Z or \ / Shift+F |
| Storyboard view / Timeline view | Ctrl+1 / Ctrl+2 |
| Open the Render menu / render the in-out range | Ctrl+E / Ctrl+Shift+E |
| Ask Claude | Ctrl+I |
| Full-screen preview | Ctrl+Shift+F (Esc to leave) |
| Keyboard shortcut sheet | ? or Ctrl+/ |
| Storyboard: move the selected card earlier / later | Alt+Left / Alt+Right (in Storyboard view) |
| Storyboard: open the selected card in the inspector | Enter |

Implementation notes:
- Use `tinykeys`, with one keymap module so tooltips and the shortcut sheet read from the same table.
- Space must not scroll; text selection is disabled inside lanes.
- WebView2's browser accelerators (F5, Ctrl+F, Ctrl+P, Ctrl+plus/minus) stay off because pywebview disables them outside debug mode. Never ship the production editor in debug mode.

---

## 7. States

| State | Where | What the person sees | Copy |
|---|---|---|---|
| Empty: no video chosen | `/editing` | today's project picker with the three-step guide | "Pick a video on the left" |
| Empty: no timeline yet | workspace | Storyboard view with a banner; Timeline switch disabled | "The timeline is built at step 7. It starts when the pictures are approved." |
| Empty: no storyboard yet | board | centred illustration plus progress when the AI is planning | "No storyboard yet. The AI is planning the scenes (about a minute)." |
| Building | workspace | skeleton lanes with a progress line in the top bar | "Laying the scenes on the voice... 60%" |
| Generating a picture | card or clip, inspector | sheen over the picture, progress chip, Cancel; the old picture plays until the new one is accepted | "Making a new picture, try 2 of 3" then "New picture ready. Compare" |
| Re-recording a sentence | voice lane | sentence hatched with a spinner; the old audio plays; when ready, the toast reports the length change | "Sentence 17 is 0.3 s longer. Scenes after it moved later. Undo" |
| Locked scene | card or clip | lock badge plus hatch; locked fields read-only with an Unlock button | "Locked: the AI will not change this scene. Unlock to edit." |
| Approved video | workspace | read-only banner with **Edit again** | "Approved by Imran on 10 Oct at 17:45. Editing makes a new version that needs approval again." |
| View-only role | workspace | banner; editing controls disabled with tooltips; playback works | "You can watch and comment. Ask an editor to make changes." |
| Failed picture check | card or clip, inspector, checks | red 3 px top edge, reason chip, **New picture**, **Use anyway** (reason required, logged) | "Readable text in the picture. Pictures must not contain words." |
| Failed gate (blocks export) | checks pill (red), render menu footer | the list of reasons with Go to links; "Approve anyway" with a reason | "1 to fix: music has no licence file." |
| Rendering | Render button (progress ring plus `1080p 42%`), render menu, Renders page | editing continues; the render uses the snapshot taken when it started; later edits mark it out of date | "Out of date: you changed 3 things since this render." |
| Render failed | render menu row, toast | plain reason plus "Show the render log" | "The picture for scene 7 is missing. Replace it, then render again." |
| Saving failed | top bar save state | retries automatically; after 3 failures shows a banner with **Save a copy** | "Not saved: the project folder is not reachable. Retrying..." |
| Changed elsewhere | top bar | a 409 on save shows a dialog | "This video changed in another window. Reload it, or keep yours as a new version." |
| Offline / mock media | top bar chip | `Mock media` or `Offline` chip; cost hints say "free (mock)" | |
| Preview degraded | monitor chip | `Simplified preview` or `Half quality` | "Your graphics driver blocks the full preview. The final render is not affected." |

The mockups show these states:
- `mockup-storyboard.html`: selected, locked, failed check, generating, warning and plan-stage cards.
- `mockup-editor.html`: the Render menu, the selected scene and the pause, with a states sheet below the screen.

---

## 8. The ten most common tasks, step by step

Each task lists the fastest path, then the keyboard path. Every change is one undo step unless it says otherwise.

**1. Reorder a scene (move scene 9 before scene 8)**
1. In the Storyboard view, drag card 9 by its picture. An amber bar shows where it will land between cards 7 and 8.
2. Drop it. The toast says: "Scene 9 now comes before scene 8. Its narration moved with it; the voice was put together again from the recorded sentences (no new cost). Undo".
3. Keyboard: select card 9 and press Alt+Left. In the Timeline view, drag the clip's name bar and drop it on the insertion line at a cut.
4. To swap only the pictures and keep the narration where it is, hold Alt while dropping one scene's picture on the other.

What happens:
- Scene and sentence order change in `storyboard.json` and `timeline.json` together; the voice track becomes a new list of sentence clips; times are re-laid.
- Popups, captions and markers move with their sentences.
- The board shows "Narration order differs from the script", because `script.json` is not changed.

**2. Re-split narration (sentence 18 belongs to scene 7, not scene 8)**
1. Drag the cut between scenes 7 and 8 to the right. It snaps to the next sentence start, and the tooltip shows both lengths.
2. Drop it. Scene 7 now covers sentences 16 to 18. Popups and transitions are re-clamped.
3. Or, in the inspector's Narration section of scene 8, use the sentence menu: "Move to scene 7". Or select scene 8, put the playhead in sentence 19 and press Q.
4. Fine cut: hold Shift to cut at a word instead of a sentence start.

**3. Swap a picture (scene 10)**
1. Select scene 10. In the inspector's Picture section, choose **Replace...** to pick a file or a picture from this video, **New picture** to write a note ("note seen from across the shop, writing not readable"), or drag a picture from Library, Pictures onto the clip.
2. New picture shows its progress on the clip and card. When it is ready, the monitor opens a side-by-side **Compare** (old and new).
3. Choose **Use new** or **Keep old**. The other version stays in Library, Pictures, Other tries.
4. The camera move keeps its framing as fractions of the picture, re-fitted to the new picture's size. A locked motion keeps its exact framing.

**4. Adjust motion with handles (scene 7: push in further, toward Ruth)**
1. Select scene 7. The Motion section opens and the monitor shows the green Start and red End boxes.
2. Click **End**: the monitor jumps to the scene's last frame. Drag the red box's corner inward (115% to 125%) and drag the box toward Ruth.
3. Move the playhead or press Space to see the move. Change **Ease** if the move should start or stop gently.
4. The preset label becomes "Custom (from Slow push in)"; **Reset to preset** brings it back. The arrow keys nudge the active box.
5. Boxes cannot leave the picture. Above 160% zoom the inspector warns: "Very close: the picture may look soft at 1080p".

**5. Change a transition (cut 7 to 8)**
1. Click the transition badge on the cut, or select scene 7 and open "Transition out".
2. In Library, Transitions, hover tiles to see each one play, then click a tile. Or drag a tile onto any cut.
3. Set the length with the slider. The limit tick shows "Longest here: 2.4 s".
4. The monitor plays 2 s around the cut once.
5. "Apply to all cuts" and "Vary all (no repeats)" are in the same section. Vary respects the pack's rules: no repeats, at most one light leak or burn per minute, a dip at section ends.

**6. Edit a popup (scene 7's "Fifty years earlier")**
1. Double-click the popup on the monitor or its clip, then type. The counter shows `3 / 6 words` and turns red above 6.
2. Pick a style and a position (grid, or drag on the monitor and it snaps to the 5 positions) and an animation (Slide, Fade, Pop).
3. Re-time it by dragging the clip edges (they snap to words) or by setting "Appears 0.3 s after sentence 17 starts".
4. The preview shows the exact picture the renderer will use within about 0.2 s.
5. Remove it with Delete; add one with T at the playhead.

**7. Fix a caption word ("Calahan" should be "Callahan")**
1. Double-click the cue on the Captions lane, or in Video, Captions search for "Calahan".
2. Correct the word in the inspector's text box. The word keeps its timing. Press Enter.
3. A prompt offers **Fix everywhere** for other cues with the same spelling.
4. Captions follow the spoken words. If the narrator also said the name wrongly, use Narration, **Re-record** for that sentence. That costs a little; the inspector shows the cost before you confirm.

**8. Change the music**
1. Select the Music clip, or open Library, Music.
2. Press play on a track to hear 10 s. Check the licence badge.
3. Click **Use**, or drag the track onto the Music lane.
4. Set Volume (`-18 dB`), Dip under the voice (`-12 dB`) and the fades in the inspector. Drag the clip sideways to start the track at a better point.
5. The dip line on the lane and the preview update together, from the same ducking envelope as the render. A track without a licence file adds a warning to Checks.

**9. Trim a pause (the 1.6 s pause after sentence 17)**
1. On the Voice lane, click the hatched `1.6 s pause` block. The inspector shows "Pause after sentence 17".
2. Choose **Shorten to 0.6 s**, or drag the block's right edge.
3. Everything after the pause moves 1.0 s earlier. The cut stays at the start of sentence 18. The toast says "Pause shortened by 1.0 s. The video is now 2:03.0. Undo".
4. Bulk option: "Shorten all pauses over 1.0 s to 0.6 s (saves 7.4 s)".
5. The voice files are never edited; the timeline's voice clip list changes (section 10.2).

**10. Render a preset (1080p)**
1. Click **Render** (Ctrl+E). The menu shows each size's status: `Not rendered`, `Out of date` or `Rendered`.
2. Tick 1080p (and 720p for a quick check if wanted). Keep "Burn in captions" and "Show popups" on, then click **Render 1080p**.
3. Keep editing while it renders: the button shows `1080p 42%` and the menu shows phases and time left. Edits made after the start mark the render "Out of date" when it finishes.
4. When it is done: **Play** or **Open folder**.
5. Then **Approve and send to export** at the bottom of the same menu. It is disabled while something blocks, with the reason shown.

---

## 9. Accessibility and plain-English copy rules

### 9.1 Accessibility

**Keyboard.** Every action has a keyboard path: section 6, plus Tab and Shift+Tab across panels in reading order, and arrow keys inside lists and grids.
- The timeline is a composite widget. Tab enters the active lane; Up and Down move between lanes; Left and Right move between clips; Enter opens the inspector; the drag operations have keyboard equivalents (nudges, Q, W, Alt+arrows).
- No shortcut overrides Tab, Shift+Tab or screen-reader keys.

**Focus.** Keyboard focus is a 2 px `#E8EDF7` outline with a 1 px dark halo and 2 px offset. It is distinct from the amber selection ring and visible on amber. Pointer clicks do not show focus rings (`:focus-visible`).

**Names and roles.**
- Each clip is a button with a full name, for example "Scene 7, 1:00.8 to 1:11.2, slow push in, transition out light leak, popup Fifty years earlier, locked".
- Lanes are labelled groups. The monitor canvas has a text alternative updated each second: "Scene 7: Ruth in a hospital bed by the window. Popup: Fifty years earlier. Caption: Fifty years earlier, a stranger had paid for her groceries".
- A **Scene list** view (a table of every scene with times, motion, transition, popup and checks, like Premiere's Sequence Index) gives screen-reader users and spreadsheet thinkers the whole timeline. It also exports to CSV.

**Announcements.** A polite live region speaks results: "Scene 7 now ends at 1:11.6", "Pause shortened by 1.0 s", "1080p render finished".

**Contrast.**
- Text is at least 4.5:1 (body text on panels is 14.9:1; muted text 6.9:1; faint text 5.0:1).
- UI boundaries and lane clips are at least 3:1 against the lane.
- White text on lane colours is at least 6:1.
- Status always pairs a colour with an icon and a word.

**Target size.** Controls are at least 24 x 24 px (WCAG 2.2 target size). Trim handles are smaller (8 px hit areas) and have keyboard and inspector equivalents.

**Motion.** With `prefers-reduced-motion`:
- library tiles do not autoplay (they play on focus or click);
- sheens become a static "Working..." label;
- the preview still plays, because the video is the content.

**Zoom and scaling.** The layout works at 100% to 175% Windows scaling, with the 1280 x 720 compact preset. Text never goes below 10 px, and the ruler is the only 10 px text.

### 9.2 Plain-English copy rules

- Name things the way editors do, and never by their file or field name. Use Scene, Picture, Popup, Caption, Voice, Music, Cut, Transition, Motion, Pause, Section, Marker, Render, Size.
- Never use `transition_out`, `start_rect`, `xfade`, `render_key`, "ripple", "rolling edit" or "sidechain" in the UI. Say "Move the cut", "Shorten the pause" and "Dip under the voice".
- Start buttons with a verb that says the result: **New picture**, **Shorten to 0.6 s**, **Render 1080p**, **Approve and send to export**. Never use plain "OK" or "Submit".
- Say what something costs before it costs it: "Makes a new picture. About $0.03." "Re-records one sentence with the voice tool. About $0.01." Use "free" for things that are free, such as reorders and pause trims.
- Report every change with Undo in the toast and the number that changed: "Pause shortened by 1.0 s. The video is now 2:03.0. Undo".
- In errors, say what happened and what to do, never whose fault it is. For example, "The picture for scene 7 is missing. Replace it, then render again." No codes in the main text; the code goes in "Details".
- Write times as `1:05.6`, lengths as `10.4 s`, sizes as `1080p`, money as `$0.03`. Use one decimal for seconds and `tnum` digits.
- Keep sentences under 20 words in UI text and tooltips to one line plus the shortcut, for example "Split at the playhead (S)".
- Do not exclaim and do not apologise. Ask for confirmation only for things that cost money or cannot be undone. Everything else is done immediately, with Undo.
- Use the same word for the same thing everywhere: "Checks" everywhere, never "gates" in one place and "QA" in another. The picture check is "Picture check".

---

## 10. Data flow

### 10.1 Files and who owns them

| File | Written by | The workspace reads | The workspace writes |
|---|---|---|---|
| `03_script/script.json` | script stage | sentences, sections | never |
| `04_storyboard/storyboard.json` | storyboard stage; Storyboard v2 board (stages 4 to 6, approve) | plan fields | stages 4 to 6: draft plus approve (as today); stage 7 and later: plan mirrors from structural edits and plan-only fields (prompt, shot, cast, notes) through the API |
| `05_voice/timing.json`, `voice.wav`, `sentences/*.wav` | voice stage | clock, words, audio | never directly; a re-record is a voice-stage job for one sentence |
| `06_images/scene_NN*.png`, `images.json` | images stage | pictures, checks | never directly; new picture and upload go through the images stage API |
| `07_edit/timeline.json` | edit stage builder; the Edit room | everything | yes: every cut edit |
| `07_edit/popups/*.png`, `music_ducked.wav`, `captions.ass` | renderer helpers | preview assets | through the API (popup preview, music stem) |
| `07_edit/history/timeline.r<rev>.json` | Edit room autosave | History panel | yes |
| `07_edit/handoff/*` | hand-off writers | none | through the API |

### 10.2 Which edit writes where

| Edit | `storyboard.json` | `timeline.json` | Other effects |
|---|---|---|---|
| Move a cut (re-split narration) | `sentence_ids` of both scenes (plan mirror) | `scenes[].start_s/end_s`, `sentence_ids`, `edited += times`; popups re-anchored; transitions re-clamped | none |
| Split / merge a scene | new or merged scene, fields copied as in `storyboardUtils` | scenes, popups | split scene flagged "needs its own picture" |
| Reorder scenes | scene order and sentence order | scenes re-laid; `voice.segments` in the new sentence order; popups, cues and markers moved with their sentences | none (no voice cost) |
| Replace picture or accept a new one | `image.path`, `image.status = approved`, `locked.image` | `scenes[].image`, rectangles re-fitted unless motion is edited or locked, `edited += image` | `06_images` new file and `images.json` entry; cost logged for new pictures |
| Prompt, shot, cast, references, notes | yes | none | used by the next New picture |
| Motion (preset, boxes, ease) | none (the timeline is the truth after stage 7) | `scenes[].motion`, `edited += motion` | none |
| Transition (type, length) | none | `scenes[].transition_out`, `edited += transition_out` | none |
| Popup text | `popup.text` (plan of record) | `popups[].text`, `edited += text` | popup PNG re-rendered |
| Popup style, position, animation, timing | none | `popups[]` | PNG re-rendered on style or position |
| Caption text, split, merge, timing | none | `captions.cues[]`, `edited` | none |
| Caption style | none | `captions.style` | none |
| Music (track, offset, volume, dips, fades) | none | `music` | `music_ducked.wav` rebuilt |
| Shorten a pause | none | `voice.segments`; every later time shifted by the removed length | none |
| Re-record a sentence | none | that sentence's segment; later times shifted by the length change | voice stage writes `sentences/<id>.v2.wav` and updates `timing.json` |
| Markers | none | `markers[]` | chapters at export |
| Render sizes | none | `presets` | renders, `render_state.json` |

**Structural edits** are cut moves, split, merge and reorder. They go through one endpoint that writes `timeline.json` and the plan mirror in `storyboard.json` atomically: temporary files, then rename, then an audit row. The two files can never disagree about which sentences make a scene.

### 10.3 Commands, undo, autosave, versions

**Commands.** Every UI action, shortcut or Ask Claude proposal becomes a **command**: a label, Immer patches and inverse patches. Drags coalesce into one command when the pointer is released. An Ask Claude proposal is one command with the label "Claude: shorten 6 pauses".

**Undo / redo.**
- The stack is in memory, 200 steps per session.
- Undo after a server job (a new picture) reverts the timeline's pointer to the old picture. The money is spent, and the new picture stays in Other tries.

**Autosave.**
- `PUT /api/projects/{id}/timeline` runs 1.5 s after the last change and when the window loses focus or closes.
- The request carries `If-Match: <revision>`. The server validates with the Pydantic model, writes atomically, increments `revision` and audits `timeline.save` with a change summary ("Scene 7 motion changed; pause after sentence 17 shortened").
- A 409 opens the "changed elsewhere" dialog.

**Versions (History panel).**
- The server keeps a snapshot every 5 minutes of editing or every 50 commands. It also keeps named snapshots: **Built by the app** (the first build), **Before rebuild**, and **Approved 10 Oct 17:45**.
- Restoring a snapshot is itself a command, so it can be undone. This is VidRush's "revert to any previous auto-saved snapshot, including the original AI-generated version".

### 10.4 Regeneration and locks

Two kinds of protection:
- **Changed by you.** Every human change adds the field to the element's `edited` list (scene, popup, cue, music, captions style). Such fields are kept automatically, with no lock needed. The UI shows a small white dot.
- **Locked.** A person can lock a scene (or a storyboard field group, as today) to protect AI choices they like but have not changed. Locked means the AI changes nothing in that element.

**Upstream regeneration** (redo the storyboard, redo pictures) keeps today's rules: storyboard locks are respected and approved pictures are not regenerated unless named.

**Rebuilding the timeline** (the edit stage runs again after a voice re-record, a storyboard change, or "Rebuild from the plan") merges instead of overwriting:
1. Build a fresh timeline from storyboard, timing and images with the existing builder.
2. Match scenes, old to new, by `sentence_ids`: exact match first, then the largest overlap.
3. For each matched scene, copy every field in `edited`, or every field if `locked`. Times marked as edited are re-anchored to their first sentence's start (the offset from the sentence is kept), so a re-recorded sentence shifts them correctly.
4. Popups and cues are matched by scene and text (cues by word range), and edited ones are carried over the same way. Music, caption style, markers and presets carry over when edited.
5. Anything that cannot be carried, for example a scene whose sentences no longer exist, is listed in a **Not kept** report with **Undo rebuild**. A **Before rebuild** snapshot is always taken first.

### 10.5 Approvals and roles

- **Storyboard (stage 4) and pictures (stage 6):** approve as today. The server re-validates, fixes unlocked fields that break rules, and records the approver from the session (M5A).
- **Edit (stage 7):** saving needs no approval. **Approve and send to export** requires:
  - every ticked size rendered from the current revision (its `render_key` matches);
  - no failing blocking check, or an override with a written reason (audit `stage.approve` with `override_reason`).
  Approval moves the project to export (today's behaviour, including "missing sizes render first").
- **Editing after approval:** editors may edit. The first change after approval shows a confirmation. The edit stage goes back to `awaiting_review`, and the export stage shows "The exported files are older than the current edit".
- **Roles (M5A):**
  - viewer: watch, scrub and read the inspector; every edit control is disabled with a tooltip;
  - editor: everything except overrides of blocking checks, if Settings say only admins may override;
  - admin: everything.
  The server enforces the same rules.

### 10.6 API additions

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/projects/{id}/timeline` | `{timeline, revision, sentences, words, sections, pictures}`, everything the workspace needs in one call |
| PUT | `/api/projects/{id}/timeline` | autosave with `If-Match`; 409 on revision conflict; 422 with plain-English reasons on validation errors |
| POST | `/api/projects/{id}/timeline/structure` | `{op: move_cut, split, merge, reorder, ...}`: atomic write of timeline plus storyboard mirror |
| POST | `/api/projects/{id}/timeline/rebuild` | merge rebuild (10.4); returns the Not kept report |
| GET / POST | `/api/projects/{id}/timeline/history` and `/history/{rev}/restore` | snapshots |
| GET | `/api/projects/{id}/media/thumb?path=...&h=128` | cached JPEG thumbnails (Pillow) |
| GET | `/api/projects/{id}/media/peaks/{voice\|music}.json` | min/max peaks per 10 ms (numpy), cached by file hash |
| GET | `/api/projects/{id}/media/voice/segments/{sid}.wav` | sentence audio; splits an own recording once |
| POST | `/api/projects/{id}/popups/preview` | `{text, style, position, anim, aspect, height}` returns a PNG plus `{x, y, w, h}`; same code as `render/popups.py` |
| POST | `/api/projects/{id}/music/stem` | rebuild `music_ducked.wav`; returns its URL, envelope and `preview_gain_db` |
| POST | `/api/projects/{id}/scenes/{index}/picture` | `{mode: new\|upload\|choose, note?, path?}`; images-stage job for one scene; returns the job id |
| POST | `/api/projects/{id}/sentences/{sid}/re-record` | voice-stage job for one sentence |
| POST | `/api/projects/{id}/render` | `{presets, range?: [in, out]}`; renders (existing redo path) plus range renders |
| POST | `/api/projects/{id}/handoff/{kdenlive\|fcpxml\|capcut}` | writes the hand-off; returns paths and a "kept / not kept" list |
| POST | `/api/projects/{id}/assistant` | `{message, selection}` returns `{explanation, commands[]}` (operations from the same command set; never writes files) |
| GET | `/api/fonts/{file}` | the Noto fonts for the preview |
| static | `/transitions/catalogue.json`, `/transitions/<id>.webp` | from Transitions v2 |

### 10.7 Contract changes required (change the contract files first)

Write `docs/M5-CONTRACT.md` and amend the referenced sections.

1. **`timeline.json` (M3-M4 section 3), all additive:**
   - `revision: int`
   - `scenes[].sentence_ids: [str]`, `scenes[].edited: [str]`
   - `scenes[].motion.ease`: an optional override of the preset's curve, using the Motion v2 names (`ease_in_out_cubic`, `ease_out_quart`, `ease_in_out_sine`, `snap`, `linear`). `motion.style` and `motion.strength` already exist (Motion v2)
   - `voice.segments: [{sentence_id, src_start_s, src_end_s, at_s}]` (absent means the whole `voice.wav` at `start_s`)
   - `popups[].id`, `popups[].anchor: {sentence_id, offset_s}`, `popups[].edited`, `popups[].locked`
   - `captions.cues[].id`, `captions.cues[].edited`
   - `captions.style.preset`, `captions.style.active_colour`, `captions.style.layout: phrase | single_word`
   - `markers: [{id, t, label, colour, chapter: bool}]`
   - `music.stem: path`
   - a pointer from the contract to the parity documents the preview mirrors: MOTION-PARITY.md (camera moves) and TRANSITION-PARITY.md (cuts, centred on the boundary as `render/assembly.py` draws them).
2. **`storyboard.json` (M1-M2 section 6), all additive:**
   - `elements: [{id, kind: character | place | object, name, description, image}]`
   - `scenes[].element_ids`, `scenes[].shot: {size, angle, light}`, `scenes[].references: [path]`
   - `scenes[].section_id`, `scenes[].locked.image`, `scenes[].popup.key_sentence_id`
3. **Renderer:**
   - build the voice track from `voice.segments` (`atrim` plus `concat`);
   - mix the precomputed `music_ducked.wav` instead of `sidechaincompress` (or, if rejected, port `sidechaincompress` to the preview and drop the "-12 dB" label);
   - take `motion.ease` from the scene when it is set, otherwise the preset's curve (`render/motion.py`);
   - add caption presets to the ASS writer.
4. **API:** section 10.6.
5. **Config:** add `active-word`, `single-word-pop` and `boxed` styles to `config/captions.yaml`. Add `editor:` defaults to `config/render.yaml` (snap threshold, minimum scene length 2.0 s, pause shorten default 0.6 s, history cadence).

---

## 11. Technology choices and licences

All front-end dependencies are MIT, ISC, BSD or Apache. There is no GPL or LGPL code in the front end and no Remotion.

| Need | Choice (version checked 2026-10-10) | Licence | Why this one | Considered and rejected |
|---|---|---|---|---|
| UI | React 19, TypeScript 5.9, Tailwind 4, Vite 6 (already in use) | MIT | the app's stack | none |
| Editor state | **zustand 5.0.15** store plus **immer 11.1.21** patches | MIT, MIT | No provider boilerplate. Selector subscriptions keep 80 clips from re-rendering on every playhead tick. Immer patches give labelled undo/redo, a compact change log for autosave and one-step AI proposals | Redux Toolkit (heavier, same idea); zundo 2.3.0 (MIT, snapshot undo without labels or patches, kept as a fallback) |
| Server data | TanStack Query 5 (in use) | MIT | load, save, retries, job polling | none |
| Panels | **react-resizable-panels 4.14.3** | MIT | resizable, persisted, keyboard-accessible separators | hand-rolled splitters |
| Drag and drop (board, library to timeline) | **@dnd-kit/core 6.3.1** plus **@dnd-kit/sortable 10.0.0** | MIT | accessible keyboard drag and screen-reader announcements; good for grids and drop targets | native HTML5 drag and drop (today's board: no keyboard, poor feedback) |
| Timeline | **Custom**: DOM clips (virtualised by time window) plus Canvas 2D for ruler ticks and waveforms. Constants and snapping ideas from OpenCut classic (MIT, archived 2026-05-17, read as reference only) | own | The hard parts are ours in any case: voice-locked rolling cuts, linked popups, word bar, pause blocks, ducking line, sentence markers. A small purpose-built timeline (about 2.5 to 3.5 k lines) gets a CapCut-grade look without fighting a generic engine | **@xzdarcy/react-timeline-editor 1.0.0** (MIT): good drag/resize and scale mechanics, but its look and edit model are a generic animation timeline; we would override rendering and edit logic anyway. Kept as plan B if the custom timeline slips. **@openvideo/timeline 1.4.0** (MIT): Fabric 6 canvas, thin docs, parent repo has a company licence clause |
| Preview compositor | **PixiJS 8.22.0** on WebGL2 with custom GLSL filters; Canvas 2D fallback | MIT | GPU transitions written once from the parity maths; mesh warps for 2.5D parallax; render textures; context-loss handling; 30 fps at 960 x 540 on integrated graphics | Canvas 2D only (exact Ken Burns but cannot do warps, light leaks or blurs at 30 fps, so it is the fallback); raw WebGL2 (more code for the same result); Remotion (company licence above 3 people); etro (GPL-3.0); Diffusion Studio Core (MPL-2.0, a WebCodecs encoder we do not need) |
| Text in preview | Canvas 2D with `FontFace` (Noto from `assets/fonts`) | none (browser) | Chromium's HarfBuzz shaping for Arabic, Devanagari, Japanese and Korean, with the same fonts the renderer uses | Pixi `Text` (also canvas-based; fine but less control over outlines) |
| Audio | Web Audio API | none (browser) | sample-accurate scheduling of sentence clips and gain curves | howler.js (not needed) |
| Waveforms | own Canvas drawing from server peaks in the lanes; **wavesurfer.js 8.0.2** only for Library, Music previews | BSD-3-Clause | Lanes need overlays (word bar, pauses, ducking line) and the timeline's zoom; wavesurfer is ideal for standalone track previews | peaks.js (LGPL-3.0) |
| Shortcuts | **tinykeys 4.0.1** | MIT | tiny, scoped, sequences | hotkeys-js (fine, larger) |
| Icons | lucide-react (in use) | ISC | consistent with the app | none |
| Fonts | Inter, IBM Plex Mono (bundled), Noto (in `assets/fonts`) | OFL | offline parity | runtime Google Fonts (fail offline) |
| Tests | Vitest (unit maths), Playwright (parity, dev only) | MIT, Apache-2.0 | | none |

---

## 12. Build plan: M5.1 to M5.4

Effort assumes the current team and agents; PLAN.md section 8 budgets 4 to 6 weeks for a basic editor plus 8 to 12 for CapCut-lite. This plan totals **about 14 weeks**, with the preview engine first because the storyboard animatic, the editor and the parity proof all depend on it.

### M5.1 Preview engine (about 3 weeks)

Scope:
- the engine package with Ken Burns, Transitions v2 filters, popups, captions, voice segments, music stem, clock, scrub, skim, loop, quality levels and fallback;
- backend thumbnails, peaks, sentence segments, popup preview, music stem, fonts route and `preview_gain_db`;
- the parity harness;
- integration as **Live preview** in today's `EditReview` (proxy stays as "Watch the last render").

Acceptance:
- The 12-scene mock project and a 60-scene, 10-minute mock project play at 30 fps at 960 x 540 with 2% or fewer dropped frames on a team laptop. Seek is at most 50 ms cached and at most 250 ms cold; memory is at most 700 MB.
- TS maths matches the Python fixtures to 1e-6 (ease, rectangles, scene bounds, transition progress, popup motion).
- Frame parity on the three test projects: SSIM of at least 0.95 (motion) and at least 0.92 (transitions); caption box IoU of at least 0.9.
- Sync within +/-1 frame over 10 minutes. Music within +/-1 dB of the render, which requires the ducking-stem contract change.
- Fully offline with mock media. With WebGL2 disabled, the Canvas 2D fallback plays with the `Simplified preview` chip.

### M5.2 Edit room: timeline and inspector (about 5 weeks)

Scope:
- the full-bleed workspace route and shell, top bar, panels and tokens;
- the monitor with motion boxes;
- the timeline: toolbar, overview, ruler, lanes, clips, playhead, snapping, rolling cuts, split, merge, reorder, linked popups, captions, music, pause trims;
- the inspector for every selection type;
- the Library tabs Pictures, Motion, Transitions, Text and Music;
- commands, undo, autosave, history and the keyboard map;
- the Render menu with approve, the checks list, the structure, timeline and history APIs, and the merge rebuild.

Acceptance:
- Five teammates complete the ten tasks of section 8 in a moderated test, each within 2 minutes, without help, at 1440 x 900 and at 1280 x 720.
- Every edit round-trips through server validation. Undo and redo work for every command. Autosave survives an app restart. A rebuild after a re-recorded sentence keeps all edits or reports them as Not kept.
- A 60-scene timeline zooms and scrolls at 60 fps.
- The final 1080p render after edits passes the M5.1 parity checks against the preview.
- Five of the ten tasks can be done with the keyboard only.
- Typecheck, build and unit tests pass, with at least 80% coverage of edit rules (snapping, rolling, clamps, pause ripple, segment mapping).

### M5.3 Storyboard v2 (about 3 weeks)

Scope:
- the board (sections, cards, connectors, ribbon), outline, inspector (plan fields) and animatic strip;
- `@dnd-kit` reorder;
- stage behaviours for 4, 6 and 7, including the scratch voice;
- shot directions, Cast & places and references (contract and prompt composer);
- the images stage passes element pictures as references;
- the Storyboard/Timeline switch.

Acceptance:
- A 60-scene storyboard can be reviewed and approved without sideways scrolling at 1440 x 900.
- Reorder, split and merge work by keyboard.
- The animatic plays with estimated timing before the voice exists and real timing after.
- With the mock image provider, requests carry the style sheet plus element references.
- Approving produces a valid `storyboard.json` (server validation, locks kept on regenerate).
- The old board stays reachable behind a setting until acceptance.

### M5.4 Polish and hand-offs (about 3 weeks)

Scope:
- Ask Claude (proposal, change list, one-step apply);
- transition tiles from the renderer's previews;
- caption presets (active word, single word pop, boxed);
- compact layout polish, the Scene list view, the accessibility pass (axe), the performance pass, onboarding (a 6-step tour and the shortcut sheet);
- Render range plus Compare;
- hand-offs:
  - Kdenlive `.kdenlive` (MLT XML, pinned to 26.08.x, Transform keyframes);
  - Resolve FCPXML 1.9 or 1.10 with `adjust-transform` keyframes plus SRT;
  - CapCut draft, best effort: seeded from an app-created project, pinned version, per-version fixture test.

Acceptance:
- Each hand-off opens in its pinned app version with every scene, correct durations and camera moves (Kdenlive, Resolve) and captions (SRT).
- axe-core reports no serious or critical issues.
- An Ask Claude proposal never applies without a click, always undoes in one step, and asks before anything that costs money.
- The ten tasks pass at 1280 x 720.

---

## 13. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Preview and render differ (fonts, transitions, ducking), so people stop trusting the preview | medium | high | One set of maths with fixtures (5.8). Popups use the renderer's PNGs. Captions use explicit line breaks and the same fonts. A ducking stem shared by both sides. "Compare with render" and Render range for proof. Text marked as approximate |
| Integrated GPUs or WebView2 GPU blocklists make playback stutter | medium | medium | Quality levels with Auto. Texture budget and thumbnails. Canvas 2D fallback. Measure on a real team laptop in week 1 of M5.1 |
| Transitions v2 changes or slips | medium | medium | The engine reads the catalogue. Filters are added per transition id. Legacy `xfade` mapping keeps old projects playable. M5.1 can start with dissolve, dip, wipe and push |
| The voice clock edits (segments, pause trims, re-records) shift times in surprising ways | medium | high | One mapping function (sentence time to timeline time) used everywhere and unit tested. Toasts state every shift. Rebuild merge with a Not kept report and snapshots |
| storyboard.json and timeline.json drift apart | medium | high | Structural edits write both atomically. Clear ownership table (10.2). Validation on save |
| Scope creep toward a full NLE | high | high | Non-goals (1.3). Hand-offs for everything else. Each milestone has fixed acceptance tests |
| Memory with 60 to 80 high-resolution pictures | medium | medium | 128 px thumbnails in lanes and cards, LRU texture cache, decoding at preview size |
| WebView2 behaviour (Ctrl+wheel zoom, accelerator keys, Space scrolling) | high | low | Non-passive wheel handler. Keymap rules. Production never in debug mode. Test matrix at 100%, 150% and 175% scaling |
| Offline gaps (Google Fonts, model downloads) | high | low | Bundle Inter and IBM Plex Mono. Noto from `assets/fonts`. Parallax falls back when the depth model is missing |
| CapCut draft format breaks with an update | high | low | Best effort only, pinned version, seeded drafts, fixture tests, clear labelling |
| Unexpected costs from New picture or Re-record | medium | medium | Cost hints before every paid action, budget guard, paid actions confirmed, history of attempts |
| Non-developer team finds the pro editor intimidating | medium | medium | Storyboard view as the friendly entry, plain copy, defaults that need no tweaking, tour, shortcuts optional |
| Two windows (or a synced folder) edit the same project | low | medium | Revision with `If-Match`, 409 dialog, history snapshots |
| A canvas monitor and timeline are hard to use with a screen reader | medium | medium | DOM clips with names, live region, Scene list table, keyboard paths |
| A dependency changes licence | low | medium | Pinned versions plus a licence check in CI (allow-list MIT, ISC, BSD, Apache, OFL) |

---

## 14. Open questions for the owner

1. **Reordering after the voice exists.** Moving a scene moves its narration (no new voice cost). Should the Edit room allow it, or only the storyboard before the voice?
2. **Ducking.** May the renderer use the precomputed "music dips under the voice" track (identical in preview and final) instead of FFmpeg's compressor? The alternative is to remove the "-12 dB" control, because today the renderer ignores it.
3. **Cast & places.** Should pictures of recurring people and places be sent as reference images to the picture tool for consistency? The Gemini image model supports it, and it uses a little more of the request size.
4. **Scratch voice.** Should the animatic play Windows' built-in voice before the real voice exists (free, offline, robotic)?
5. **Time format.** Should the editors see frames (`00:01:05:18`) or tenths of a second (`1:05.6`) by default?
6. **Laptop scaling.** Do the team laptops run at 150% scaling on 1080p screens? If so, the compact layout is the default for most people.
7. **Approve flow.** Should "Approve and send to export" also start the export copy immediately, which is the behaviour when the export stage is set to auto?

---

## Appendix A: the 12-scene example used in the mockups

Channel **Kind Ledger** (`channels/example-channel.yaml`), video **Why she paid for strangers**, long-form 16:9 at 30 fps, narrator "Daniel, warm narrator".
- Total length 2:04.0 (voice 2:03.2 plus the 0.8 s tail).
- Music: "Quiet Morning (piano)" at -18 dB, dipping -12 dB, licence on file.
- Transition pack: Cinematic. Caption style: Bold white with the active word in yellow.

| # | Section | Time | Length | Narration (sentence numbers) | Popup | Motion | Transition out | Shot |
|---|---|---|---|---|---|---|---|---|
| 1 | Hook | 0:00.0 to 0:09.4 | 9.4 s | (1) Every Friday at six, an old woman in a blue coat walked into Miller's Grocery. (2) And she paid for the cart behind her. | "Every Friday. Six o'clock." top left | Slow push in | Film dissolve 0.7 s | Wide, eye level, dusk |
| 2 | Hook | 0:09.4 to 0:19.8 | 10.4 s | (3) She never waited for a thank-you. (4) She tapped her card, smiled at the cashier, and was gone before anyone could ask her name. | none | Pan right | Blur dissolve 0.7 s | Medium, over the shoulder |
| 3 | The routine | 0:19.8 to 0:30.6 | 10.8 s | (5) For eleven years, the cashier, a young man named Leo, kept count. (6) Four hundred and twelve carts. (7) Groceries for people she had never met. | "412 carts in 11 years" top right | Zoom in | Luma wipe left 0.6 s | Close-up, top-down |
| 4 | The routine | 0:30.6 to 0:40.2 | 9.6 s | (8) Some were young mothers counting coins. (9) Some were night-shift workers. (10) One was a boy buying bread with a jar of pennies. | none | Pan left | Dip to black 0.8 s | Detail, eye level |
| 5 | The absence | 0:40.2 to 0:50.0 | 9.8 s | (11) Then one Friday in March, six o'clock came and went. (12) The blue coat never came through the door. | "Then, one Friday, nothing." centre | Hold still | Film dissolve 0.7 s | Wide, cold light |
| 6 | The absence | 0:50.0 to 1:00.8 | 10.8 s | (13) Leo asked around. (14) Her name was Ruth Callahan. (15) She was eighty-one, she lived alone, and she had been taken to St. Mary's hospital. | "Ruth Callahan, 81" bottom left | Zoom out | Zoom through 0.7 s | Medium, low angle, night |
| 7 | The reason | 1:00.8 to 1:11.2 | 10.4 s | (16) When he visited, she finally told him why. (17) Fifty years earlier, a stranger had paid for her groceries on the worst day of her life. | "Fifty years earlier" top left (moved to sentence 17 by Imran) | Slow push in, 100% to 115% | Light leak (warm) 0.8 s | Close-up, eye level, warm window light |
| 8 | The reason | 1:11.2 to 1:21.0 | 9.8 s | (18) Her husband had just lost his job. (19) She had a baby on her hip and four dollars in her purse. (20) Then a man in a grey hat simply said, "I've got this." | "Four dollars in her purse" bottom right | Pan up | Morph blend 0.7 s | Medium, 1975 film look |
| 9 | The reason | 1:21.0 to 1:31.4 | 10.4 s | (21) She never learned his name. (22) So she decided to become him, one Friday at a time. (23) Every week, for eleven years. | "She became the stranger" centre (locked) | Zoom in | Crosswarp 0.6 s | Close-up, hands, lamp light |
| 10 | The town | 1:31.4 to 1:42.0 | 10.6 s | (24) Leo wrote her story on a sheet of paper and taped it next to register three. (25) By Monday, the whole town had read it. (26) Some read it twice. | none | Pan right | Luma wipe right 0.6 s | Detail, note seen from a distance |
| 11 | The town | 1:42.0 to 1:52.4 | 10.4 s | (27) The next Friday, at six o'clock, forty-one strangers lined up at Miller's Grocery. (28) And each one paid for the cart behind them. | "41 strangers. One Friday." top left | Slow push in | Blur dissolve 0.8 s | Wide, dusk queue |
| 12 | The town | 1:52.4 to 2:04.0 | 11.6 s | (29) Ruth came home in April. (30) The line is still there every Friday. (31) And nobody waits for a thank-you. | none | Zoom out | Dip to black 1.0 s (end) | Wide, morning light |

Notes on the example:
- Every scene runs 8 to 12 s and 8 of 12 scenes carry a popup (67%).
- There are no repeated neighbouring motions or transitions, and there are 8 distinct transitions.
- The cuts at sections end in a dip.
- Pauses over 1 s: after sentences 15 (1.2 s) and 17 (1.6 s).
- Mockup states: scene 7 selected; scene 9 locked; scene 10's first picture failed the check ("readable text in the picture"); scene 12 is making a new picture with a note; scene 5 carries a tip ("Hold still for 9.8 s; a slow drift keeps it alive").

## Appendix B: sources

Repository:
- `docs/PLAN.md`, `docs/M1-M2-CONTRACT.md`, `docs/M3-M4-CONTRACT.md`, `docs/M5A-CONTRACT.md`, `docs/TRANSITIONS-V2-CONTRACT.md`
- `docs/research/2026-10-09-research-dump.md` (editor)
- `frontend/src/index.css`, `components/review/StoryboardBoard.tsx`, `components/storyboard/*`, `components/review/EditReview.tsx`, `components/edit/*`, `layout/AppShell.tsx`, `types/{storyboard,timeline,timing}.ts`
- `backend/cashcow_studio/render/{ffmpeg,geometry,timeline_builder,captions}.py`, `models/{storyboard,timeline}.py`, `cli.py`
- Transitions v2 and Motion v2 work in progress in the working tree on 2026-10-10: `render/{assembly,scene_clips,motion}.py`, `render/transitions/`, `config/transitions.yaml` (version 2), and [MOTION-PARITY.md](MOTION-PARITY.md)
- `config/{transitions,captions,render}.yaml`, `channels/example-channel.yaml`

External, checked 2026-10-09 and 2026-10-10:
- CapCut desktop screenshots and help: capcut.com resource articles; fastshortcuts.com/shortcuts/capcut
- Adobe Premiere Help: whats-new, Properties panel, single-word captions, Sequence Index, AI Assistant; Spectrum tokens on unpkg; photutorial.com on the 25.x UI reaction
- Blackmagic DaVinci Resolve Edit page; Dynamic Zoom guides; owc.com on the Cut page
- Descript help: View menu, word bar, Ken Burns, captions
- OpenCut classic (MIT, archived) and omniclip (MIT) source
- Storyflow (storyflow.so help center: panels with prompts, camera directions, connections, pinned stills); DomoAI blog "best AI storyboard generator" (tools and criteria: visual consistency, cinematic customisation, animation, workflow integration)
- Boords docs (frame fields Label, Sound, Action, Camera, Lighting; versions; animatic with audio)
- Adobe Firefly Boards (infinite canvas, presets, send to Premiere)
- LTX Studio (shot types, Elements for consistency, Storyboard Builder)
- VidRush editor docs (preview centre, tool tabs, History of auto-saved snapshots including the original AI version, Rush Agent)
- noahmorris.nl and nexlev.io (the owner's references for SaaS polish)
- npm registry for versions and licences: pixi.js, zustand, zundo, immer, @dnd-kit/core, @dnd-kit/sortable, react-resizable-panels, tinykeys, wavesurfer.js, @xzdarcy/react-timeline-editor
