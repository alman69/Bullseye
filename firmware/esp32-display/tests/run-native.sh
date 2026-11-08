#!/usr/bin/env bash
set -euo pipefail
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
build_dir="${BULLSEYE_TEST_BUILD_DIR:-/private/tmp/bullseye-display-native}"
mkdir -p "$build_dir"
cjson_prefix="${CJSON_PREFIX:-/opt/homebrew}"
compiler="${CXX:-/Library/Developer/CommandLineTools/usr/bin/clang++}"
if [[ ! -f "$cjson_prefix/include/cjson/cJSON.h" ]]; then
  echo 'Native checks require cJSON headers/library; set CJSON_PREFIX. Firmware uses the core-bundled copy.' >&2
  exit 1
fi
"$compiler" -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined -fno-omit-frame-pointer \
  -isysroot /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk \
  -isystem /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include/c++/v1 \
  -I"$cjson_prefix/include/cjson" -I"$task_dir/../bullseye_display" \
  "$task_dir/display_checks.cpp" -L"$cjson_prefix/lib" -lcjson -o "$build_dir/display-checks"
"$build_dir/display-checks"
