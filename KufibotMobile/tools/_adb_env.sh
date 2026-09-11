#!/usr/bin/env bash
# Shared adb lookup for KufibotMobile/tools scripts. Source this, don't run it.

find_adb() {
    if command -v adb >/dev/null 2>&1; then
        command -v adb
        return 0
    fi
    for candidate in \
        "${ANDROID_HOME:-}/platform-tools/adb" \
        "${ANDROID_SDK_ROOT:-}/platform-tools/adb" \
        "$HOME/Android/Sdk/platform-tools/adb"
    do
        if [[ -n "${candidate}" && -x "${candidate}" ]]; then
            echo "${candidate}"
            return 0
        fi
    done
    return 1
}

ADB="$(find_adb)" || {
    echo "adb bulunamadı. Android SDK platform-tools kurulu olmalı (ör. ~/Android/Sdk/platform-tools)" >&2
    echo "veya ANDROID_HOME ortam değişkenini ayarlayın." >&2
    exit 1
}
