# 一键打包（macOS）：读取 config.py 版本号，产出 dist/桌宠看板v{版本}.app
# 用法：bash tools/build.sh
set -euo pipefail

cd "$(dirname "$0")/.." || exit 1

version=$(sed -n 's/^[[:space:]]*APP_VERSION = "\([^"]*\)"/\1/p' app/config.py | head -n 1)
if [ -z "$version" ]; then
    echo "未能从 app/config.py 读取 APP_VERSION" >&2
    exit 1
fi
name="桌宠看板v$version"
echo "打包: $name"

pyinstaller --windowed \
    --name "$name" \
    --exclude-module PySide6.QtWebEngineCore \
    --exclude-module PySide6.QtWebEngineWidgets \
    --exclude-module PySide6.QtQml \
    --exclude-module PySide6.QtQuick \
    --exclude-module PySide6.QtMultimedia \
    --exclude-module PySide6.Qt3DCore \
    --exclude-module PySide6.QtNetwork \
    --clean --noconfirm \
    main.py

app_path="dist/$name.app"
if [ ! -d "$app_path" ]; then
    echo "打包失败：未生成 $app_path" >&2
    exit 1
fi

# 桌宠应用不占 Dock（对齐 Windows 版不显示在任务栏的行为）
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" \
    "$app_path/Contents/Info.plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Set :LSUIElement true" \
        "$app_path/Contents/Info.plist"

echo "完成: $app_path"
