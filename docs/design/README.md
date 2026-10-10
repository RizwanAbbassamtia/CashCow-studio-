# Design: Storyboard v2 and the Edit room (M5)

Status: proposed for approval, 2026-10-10. This answers the owner's brief: "the editing tool will be close to Adobe or CapCut, a professional look". The references were Storyflow, the DomoAI round-up of storyboard tools, and noahmorris.nl (NexLev, VidRush).

## The three files

| File | What it is | How to look at it |
|---|---|---|
| [EDITOR-DESIGN.md](EDITOR-DESIGN.md) | The full specification for both screens. It covers: layout and pixel sizes, every panel and field, the timeline lanes, editing rules and the live preview engine, plus shortcuts, states, the ten most common tasks, accessibility, data flow, technology, the build plan and risks. | Read on GitHub or any Markdown viewer |
| [mockup-storyboard.html](mockup-storyboard.html) | High-fidelity mockup of **Storyboard v2**. It shows the "Why she paid for strangers" video at step 6 (pictures ready), with scene 7 selected and the animatic at the bottom. | Open in Edge or Chrome, ideally at 1440 x 900 (it adapts from 1100 to 1920 px wide). Scroll down for the legend and the card states |
| [mockup-editor.html](mockup-editor.html) | High-fidelity mockup of the **Edit room** (the M5 timeline editor). The playhead is at 1:05.6, scene 7 is selected, and the Render menu is open. | Same as above. Below the screen, use "Show region numbers" and "Render menu open". The States section and the scene clip anatomy are also there |

The mockups are single HTML files with no images. The pictures are drawn placeholders, and the only external resources are the Inter and IBM Plex Mono fonts from Google Fonts. Hover the transition tiles, cards and buttons to see their hover states.

## The five key decisions, in plain English

1. **One workspace, two views, with the look of CapCut and Premiere.**
   - The Storyboard view is a Storyflow-style board of scene cards, joined by their transitions, for planning and checking the story.
   - The Timeline view is a professional editor for precise work. It has the four panels editors know: library, live preview, settings for what you selected, and a full-width timeline.
   - The colours are a calm dark graphite. The brand yellow appears only on what is selected or playing, and on the main button, so it always points at something.

2. **The narration is the clock.**
   - Scenes are cut where sentences start, and moving a cut never changes what is said.
   - Only three things change the timing: shortening a pause, moving a scene with its narration, and re-recording a sentence. The first two are free because the voice is put back together from the recorded sentences. All three can be undone.
   - This is how the editor stays simple and can never break the voice sync.

3. **A live preview that matches the final video.**
   - You see every change at once, with no waiting for a render.
   - The app draws the video from the timeline with the same maths the final render uses for camera moves and transitions. Popups use the exact same pictures, and captions use the same fonts and line breaks.
   - FFmpeg still makes the final file. "Compare with render" lets anyone check the two match.

4. **Pictures that stay consistent, fixed where you see them.**
   - "Cast & places" are recurring people and places, such as Ruth, Leo and Miller's Grocery, each with a reference picture.
   - Every scene also has shot directions, such as close-up, eye level and warm window light.
   - Both are sent to the picture tool for every scene, so the video looks like one story.
   - A failed picture check, or a new picture being made, shows on the card itself with the fix next to it.

5. **Your edits are safe, and the build is staged.**
   - Every change can be undone. When the AI redoes something, it keeps what you changed.
   - History keeps the original AI version.
   - Nothing that costs money (a new picture or a re-recorded sentence) happens without showing the cost first.
   - It is built in four steps of about 14 weeks in total:
     - M5.1: live preview engine;
     - M5.2: timeline editor;
     - M5.3: Storyboard v2;
     - M5.4: polish, Ask Claude, and one-click hand-off to Kdenlive, DaVinci Resolve and CapCut.
   - It uses only free parts under MIT or BSD licences.

## Questions for the owner

These are listed in [EDITOR-DESIGN.md section 14](EDITOR-DESIGN.md#14-open-questions-for-the-owner):
- Should scenes be movable after the voice exists?
- May the music "dip under the voice" be computed by the app, so preview and render are identical?
- Should Cast & places reference pictures be sent to the picture tool?
- Is a free scratch voice wanted for the early animatic?
- Should time be shown as frames or tenths of a second?
- Do the laptops run at 150% screen scaling?
- Should approval also start the export copy?

## Related documents

- `docs/PLAN.md` (sections 2, 3 and 8): why the app has its own timeline, with FFmpeg rendering and hand-offs.
- `docs/M1-M2-CONTRACT.md` section 6 and `docs/M3-M4-CONTRACT.md` section 3: `storyboard.json` and `timeline.json`. EDITOR-DESIGN.md section 10.7 lists the additions this design needs.
- `docs/TRANSITIONS-V2-CONTRACT.md`: the cinematic transition library and the camera moves that the preview engine mirrors.
- The two parity documents from that work:
  - [MOTION-PARITY.md](MOTION-PARITY.md) (camera moves and 2.5D parallax), already in this folder;
  - `TRANSITION-PARITY.md` (the cuts, which are centred on the narration's cut point), which comes with the same wave.
- `docs/M5A-CONTRACT.md`: routes, roles and the audit log used by approvals.
