# godot

Godot 4 games and apps (`project.godot`). Godot often exits 0 even after script errors, so checks must scan its output.

## Run
Project: `{{dir}}`. Binary: `{{godot}}`. Main scene: {{main_scene}}.

## Exercise
1. **Project loads cleanly:**
   `{{R}} exec-check --fail-on "SCRIPT ERROR|Parse Error|^ERROR:" -- {{godot}} --headless --path {{dir}} --quit`.
2. **Behaviour:** run the unit and integration tests the criteria rely on.
   - GUT: `{{godot}} --headless --path {{dir}} -s addons/gut/gut_cmdln.gd -gexit`.
   - gdUnit4: `addons/gdUnit4/runtest.sh`.
   - Prefer scene tests that simulate input (gdUnit4's `scene_runner`) over reading code.
3. **Visuals and feel.** Headless mode doesn't render, so capture frames with Movie Maker:
   `{{godot}} --path {{dir}} --write-movie {{scratch}}/frames/f.png --fixed-fps 30 --quit-after 240 <scene.tscn>`
   (wrap it in `xvfb-run -a` on Linux with no display).
   - Look at representative frames (for example every 30th).
   - Compare against reference frames or the spec if the repo keeps them.
   - When the repo has an input-replay harness (recorded inputs played back through `Input.parse_input_event`), run the replay that covers the criterion and capture during it.
4. **Performance criteria:** run the benchmark scene or the game with `--print-fps` for a fixed duration and record the numbers. Fixed-fps movie mode doesn't measure real speed.
5. **Subjective criteria** (looks like the reference, feels responsive): score them against a written rubric. If the run uses a judge panel, every panelist scores independently.

## Evidence
- Clean load output.
- Test output.
- Selected frames per visual criterion, registered with notes on what each shows.
- FPS numbers for performance criteria.

## Good criteria
- "Pressing `ui_accept` while on the floor gives the player an upward velocity within one physics frame (gdUnit4 scene_runner test)."
- "`main.tscn` loads headless with no SCRIPT ERROR lines."
- "In the replay `jump_gap.json`, the player lands on platform B. Frame 150 shows the score at 1."
- Make "feel" measurable: frames, timings, distances, or a rubric with a reference.
