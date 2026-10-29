#!/usr/bin/env bash
# ratchet's own tests: real repos, real runners, the real CLI. Writes JUnit via ratchet's unittest helper.
set -euo pipefail
python3 plugin/bin/ratchet junit-unittest tests
