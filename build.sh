#!/bin/sh
set -eu

if [ -z "${THEOS:-}" ] || [ ! -f "$THEOS/makefiles/common.mk" ]; then
    echo "Theos não encontrado. Instale-o conforme https://theos.dev/docs/installation-macos e exporte THEOS." >&2
    exit 1
fi

make clean package FINALPACKAGE=1
