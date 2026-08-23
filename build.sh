#!/bin/bash
# vcam-ios 本地构建脚本（macOS 或 Linux）
# 用法: ./build.sh
set -e

export THEOS="${THEOS:-$HOME/theos}"
export THEOS_DEVICE_IP=""
export THEOS_DEVICE_PORT=""

echo "==> 检查 Theos..."
if [ ! -d "$THEOS" ]; then
    echo "Theos 未安装，正在安装到 $THEOS ..."
    git clone --recursive https://github.com/theos/theos.git "$THEOS"
    curl -fsSL https://raw.githubusercontent.com/theos/ldid/master/install.sh | sudo sh
fi

echo "==> 检查 iOS SDK (iPhoneOS16.5.sdk)..."
SDK_DIR="$THEOS/sdks/iPhoneOS16.5.sdk"
if [ ! -d "$SDK_DIR" ]; then
    echo "下载 iPhoneOS16.5.sdk ..."
    mkdir -p "$THEOS/sdks"
    git clone --depth 1 --filter=blob:none --sparse https://github.com/theos/sdks.git /tmp/theos-sdks
    cd /tmp/theos-sdks
    git sparse-checkout set iPhoneOS16.5.sdk
    cp -R iPhoneOS16.5.sdk "$SDK_DIR"
    cd -
fi

echo "==> 开始编译..."
make clean package FINALPACKAGE=1

echo ""
echo "==> 构建完成！产物："
ls -la packages/*.deb
