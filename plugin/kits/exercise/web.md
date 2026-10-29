# web

Browser apps and sites: React, Next, Vite, Vue, Svelte, Astro, Angular, plain HTML.

## Run
Start: `{{port_env}}=$PORT {{run}}`, with PORT from `{{R}} port`. URL: {{url}}. Ready when the log shows: {{ready_text}}.

## Exercise
1. Start the app in the background, logging to `{{scratch}}/app.log`, and wait until it's ready.
2. **In T3 Code** (`preview_*` tools available):
   - `preview_open` with the URL, `open: false` and `reuseExistingTab: false`. Keep the `tabId` and pass it to every later call.
   - Walk each criterion like a user: `preview_act` (click, type, select, press) and `preview_wait_for`.
   - `preview_snapshot` with `save: true` and `includeImage: true`, then look at the image.
   - Capture at desktop size, and with `preview_resize` at `iphone-14-pro` when layout matters. Use `preview_set_appearance` dark and light when styling changed.
   - For multi-step flows, `preview_recording_start` / `preview_recording_stop`.
   - Close your tab with `t3_preview_close`.
   - Browser profile for logged-in flows: {{browser_profile}}. Never use a personal profile.
3. **Elsewhere:** use Playwright (`npx playwright screenshot <url> <file>`, or a script under `{{scratch}}`). If it isn't available, a UI criterion without e2e coverage is FAIL (unverifiable).
4. Stop the app.

## Evidence
- A screenshot per UI criterion, showing the state the criterion describes.
- Snapshot diagnostics with no console errors and no failed requests. Any of those is a FAIL.
- A recording for flows of more than two steps.

## Good criteria
- "On /settings, toggling Dark mode turns the header background dark and persists after reload."
- "Submitting the form with an empty email shows 'Email is required' and sends no request."
- Name the page, the action, and the visible result. Add a mobile-width check for layout changes.
