#!/usr/bin/env bash
set -euo pipefail
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
build_dir="${BULLSEYE_TEST_BUILD_DIR:-/private/tmp/bullseye-companion-native}"
mkdir -p "$build_dir"
cjson_prefix="${CJSON_PREFIX:-/opt/homebrew}"
compiler="${CXX:-/Library/Developer/CommandLineTools/usr/bin/clang++}"
"$compiler" -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined -fno-omit-frame-pointer \
  -isysroot /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk \
  -isystem /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include/c++/v1 \
  -I"$cjson_prefix/include/cjson" -I"$task_dir/.." \
  -I"$task_dir/../../esp32-display/bullseye_display" \
  -I"$task_dir/../../esp32-scooper/bullseye_scooper" \
  "$task_dir/companion_checks.cpp" -L"$cjson_prefix/lib" -lcjson -o "$build_dir/companion-checks"
"$build_dir/companion-checks"
