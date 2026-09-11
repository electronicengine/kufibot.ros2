#!/usr/bin/env bash
# Telefonun Android 11+ "Kablosuz hata ayıklama" (Wireless debugging) özelliği
# ile adb bağlantısı kurar. Telefon ve bilgisayar aynı Wi-Fi ağında olmalı.
#
# Telefonda: Ayarlar > Geliştirici seçenekleri > Kablosuz hata ayıklama > açık.
#   İlk kurulum (bir kere, cihaz başına): "Eşleşme kodu ile cihaz eşleştir"
#   dokunun; ekranda bir IP:port ve 6 haneli kod gösterilir.
#     tools/wireless_debug_connect.sh pair <ip:port> <kod>
#   Sonraki bağlantılar: ana "Kablosuz hata ayıklama" ekranındaki
#   "IP adresi ve Port" (eşleştirme ekranındakinden farklı, genelde değişir):
#     tools/wireless_debug_connect.sh connect <ip:port>
set -eo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_adb_env.sh
source "${SCRIPT_DIR}/_adb_env.sh"

usage() {
    cat >&2 <<'EOF'
Usage: tools/wireless_debug_connect.sh pair <ip:port> <eşleşme-kodu>
       tools/wireless_debug_connect.sh connect <ip:port>
       tools/wireless_debug_connect.sh status
       tools/wireless_debug_connect.sh disconnect [ip:port]

  pair       İlk kurulum: telefondaki "Eşleşme kodu ile cihaz eşleştir"
             ekranında gösterilen ip:port ve 6 haneli kodu girin.
  connect    Zaten eşleşmiş bir cihaza, ana kablosuz hata ayıklama ekranındaki
             güncel ip:port ile bağlanır (her açılışta port değişebilir).
  status     Bağlı/eşleşmiş cihazları listeler (adb devices).
  disconnect Belirtilen (veya tüm) kablosuz bağlantıları keser.
EOF
}

ADDR_RE='^[0-9]{1,3}(\.[0-9]{1,3}){3}:[0-9]{1,5}$'

require_addr() {
    if [[ ! "${1:-}" =~ ${ADDR_RE} ]]; then
        echo "Geçersiz adres: '${1:-}'. Beklenen biçim 192.168.1.23:37251" >&2
        exit 1
    fi
}

CMD="${1:-}"
case "${CMD}" in
    pair)
        ADDR="${2:-}"
        CODE="${3:-}"
        require_addr "${ADDR}"
        if [[ -z "${CODE}" ]]; then
            echo "Eşleşme kodu gerekli: tools/wireless_debug_connect.sh pair <ip:port> <kod>" >&2
            exit 1
        fi
        "${ADB}" pair "${ADDR}" "${CODE}"
        ;;
    connect)
        ADDR="${2:-}"
        require_addr "${ADDR}"
        "${ADB}" connect "${ADDR}"
        "${ADB}" devices
        ;;
    status)
        "${ADB}" devices -l
        ;;
    disconnect)
        ADDR="${2:-}"
        if [[ -n "${ADDR}" ]]; then
            require_addr "${ADDR}"
            "${ADB}" disconnect "${ADDR}"
        else
            "${ADB}" disconnect
        fi
        ;;
    -h|--help|"")
        usage
        exit 0
        ;;
    *)
        echo "Bilinmeyen komut: ${CMD}" >&2
        usage
        exit 1
        ;;
esac
