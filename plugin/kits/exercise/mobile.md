# mobile

iOS/Android apps: React Native, Expo, Flutter, native Swift or Kotlin.

## Run
Start or build: `{{run}}`. Platform: {{platform}}.

## Exercise
1. **In T3 Code** (`device_*` tools available):
   - `device_list`, then `device_open` a simulator or emulator.
   - Install or launch the build.
   - Drive it with the `agent-device` CLI that `device_open` returns.
   - Take a `device_screenshot` for each criterion.
2. **Elsewhere:** use the repo's e2e tool if there is one (Maestro flows, Detox, Flutter `integration_test`). If there's none, a UI criterion without e2e coverage is FAIL (unverifiable).
3. Check the device or Metro logs for crashes and red boxes.

## Evidence
- A screenshot per criterion, saved and registered.
- e2e output where it exists.
- No crash or error lines in the logs.

## Good criteria
- "On the Cart screen, tapping '−' on a quantity of 1 removes the item and shows 'Your cart is empty'."
