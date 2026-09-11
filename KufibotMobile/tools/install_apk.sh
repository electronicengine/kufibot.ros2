#!/usr/bin/env bash
# Build çıktısı APK'yı USB veya kablosuz hata ayıklama ile bağlı bir Android
# telefona kurar. adb'nin telefonu görebilmesi için önce cihazın bağlı olması
# (USB) veya tools/wireless_debug_connect.sh ile bağlanılmış olması gerekir.
set -eo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MOBILE_DIR="$(dirname -- "${SCRIPT_DIR}")"
# shellcheck source=_adb_env.sh
source "${SCRIPT_DIR}/_adb_env.sh"

usage() {
    cat >&2 <<EOF
Usage: tools/install_apk.sh [apk-yolu]

  apk-yolu   Kurulacak APK dosyası. Verilmezse önce release, yoksa debug
             build çıktısı otomatik aranır:
               android/app/build/outputs/apk/release/app-release.apk
               android/app/build/outputs/apk/debug/app-debug.apk
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

APK_PATH="${1:-}"
if [[ -z "${APK_PATH}" ]]; then
    RELEASE_APK="${MOBILE_DIR}/android/app/build/outputs/apk/release/app-release.apk"
    DEBUG_APK="${MOBILE_DIR}/android/app/build/outputs/apk/debug/app-debug.apk"
    if [[ -f "${RELEASE_APK}" ]]; then
        APK_PATH="${RELEASE_APK}"
    elif [[ -f "${DEBUG_APK}" ]]; then
        APK_PATH="${DEBUG_APK}"
    else
        echo "Hazır bir APK bulunamadı. Önce derleyin (ör. android/ içinde ./gradlew assembleRelease)" >&2
        echo "veya APK yolunu argüman olarak verin." >&2
        exit 1
    fi
fi

if [[ ! -f "${APK_PATH}" ]]; then
    echo "APK bulunamadı: ${APK_PATH}" >&2
    exit 1
fi

DEVICES="$("${ADB}" devices | awk 'NR>1 && $2=="device" {print $1}')"
if [[ -z "${DEVICES}" ]]; then
    echo "Bağlı/yetkili bir cihaz bulunamadı. 'adb devices' çıktısını kontrol edin:" >&2
    "${ADB}" devices >&2
    echo "USB hata ayıklamasını etkinleştirin veya tools/wireless_debug_connect.sh ile bağlanın." >&2
    exit 1
fi

DEVICE_COUNT="$(wc -l <<<"${DEVICES}")"
if [[ "${DEVICE_COUNT}" -gt 1 ]]; then
    echo "Birden fazla cihaz bağlı, hedef seçin (-s ile "${ADB}" install kullanın):" >&2
    "${ADB}" devices >&2
    exit 1
fi

echo "Kuruluyor: ${APK_PATH} -> ${DEVICES}"
"${ADB}" install -r "${APK_PATH}"
echo "Kurulum tamamlandı."
