#!/bin/sh
# Builds and runs the scooper logic checks on the laptop (needs a C++17 compiler).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
out="${TMPDIR:-/tmp}/bullseye_scooper_checks"
"${CXX:-c++}" -std=c++17 -Wall -Wextra -Werror -I"$here/../bullseye_scooper" \
  "$here/scooper_checks.cpp" -o "$out"
"$out"
