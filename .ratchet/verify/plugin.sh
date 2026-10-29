#!/usr/bin/env bash
# The Claude adapter and marketplace must stay valid.
set -euo pipefail
claude plugin validate ./plugin
claude plugin validate .
