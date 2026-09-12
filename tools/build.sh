# 一键打包（macOS）：读取 config.py 版本号，产出 dist/peTTrello-v{版本}.app
# 用法：bash tools/build.sh
set -euo pipefail

cd "$(dirname "$0")/.." || exit 1

version=$(sed -n 's/^[[:space:]]*APP_VERSION = "\([^"]*\)"/\1/p' app/config.py | head -n 1)
if [ -z "$version" ]; then
    echo "未能从 app/config.py 读取 APP_VERSION" >&2
    exit 1
fi
name="peTTrello-v$version"
echo "打包: $name"

# 锁定项目 venv 的 PyInstaller：PATH 上的 pyinstaller 可能属于另一个
# Python 环境（没装 PySide6），打出的包缺全部 Qt 库但构建"成功"
PYI=".venv/bin/pyinstaller"
if [ ! -x "$PYI" ]; then
    echo "未找到 $PYI，请先在项目根创建 .venv 并安装 requirements.txt" >&2
    exit 1
fi

"$PYI" --windowed \
    --name "$name" \
    --icon "assets/app.icns" \
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

# ── 打包后清理：删除未使用的 Qt 组件 ──────────────────────
# --exclude-module 只挡 Python 绑定（*.abi3.so），PyInstaller 的
# PySide6 hook 仍会把对应 Qt C++ framework 二进制全量收集进来。
# 应用只用 QtCore/QtGui/QtWidgets（已 grep 证实无 Network/Pdf/
# Quick/Qml/Svg/DBus/OpenGL 引用），删掉死重可减 ~24MB。
# 若清理后启动异常，把对应项从下面列表移除即可。
app_contents="$app_path/Contents"
qt_lib="$app_contents/Frameworks/PySide6/Qt/lib"
# 注意：QtDBus 不能删——QtGui 的二进制对它有硬链接，删了启动即崩
for fw in QtPdf QtQuick QtQml QtQmlModels QtQmlMeta QtQmlWorkerScript \
          QtNetwork QtOpenGL QtSvg QtVirtualKeyboard QtVirtualKeyboardQml; do
    rm -rf "$qt_lib/$fw.framework" "$app_contents/Frameworks/$fw"
done

qt_plugins="$app_contents/Frameworks/PySide6/Qt/plugins"
rm -f "$qt_plugins/iconengines/libqsvgicon.dylib" \
      "$qt_plugins/platforminputcontexts/libqtvirtualkeyboardplugin.dylib" \
      "$qt_plugins/imageformats/libqpdf.dylib" \
      "$qt_plugins/imageformats/libqsvg.dylib" \
      "$qt_plugins/imageformats/libqtiff.dylib" \
      "$qt_plugins/imageformats/libqmacheif.dylib" \
      "$qt_plugins/imageformats/libqmacjp2.dylib" \
      "$qt_plugins/imageformats/libqwbmp.dylib" \
      "$qt_plugins/imageformats/libqtga.dylib"

# Qt 自带翻译只留中英兜底标准对话框文案（应用自带中英 i18n）
find "$app_contents" -type d -name translations -path "*PySide6*" -print0 |
    while IFS= read -r -d '' tdir; do
        find "$tdir" -name "*.qm" \
            ! -name "qtbase_zh_CN.qm" ! -name "qtbase_en.qm" -delete
    done

# 发布只需要 .app；PyInstaller 同时产出的 onedir 目录与之内容
# 完全重复（各 ~86MB），直接删除，dist 磁盘占用减半
rm -rf "dist/$name"

# 桌宠应用不占 Dock（对齐 Windows 版不显示在任务栏的行为）
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" \
    "$app_path/Contents/Info.plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Set :LSUIElement true" \
        "$app_path/Contents/Info.plist"

# 清理删除了 bundle 内文件，原 ad-hoc 签名已失效，重签保证可启动
codesign --force --deep --sign - "$app_path" 2>/dev/null

echo "完成: $app_path"
du -sh "$app_path"
