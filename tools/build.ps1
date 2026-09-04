# 一键打包：读取 config.py 版本号，生成 桌宠看板v{版本}.exe
# 用法：powershell -ExecutionPolicy Bypass -File tools\build.ps1
$ErrorActionPreference = "Stop"

$version = Select-String -Path "app\config.py" -Pattern 'APP_VERSION = "([^"]+)"' |
    ForEach-Object { $_.Matches[0].Groups[1].Value }
if (-not $version) { throw "未能从 app/config.py 读取 APP_VERSION" }
$name = "桌宠看板v$version"
Write-Host "打包: $name"

pyinstaller --onefile --windowed `
    --name $name `
    --exclude-module PySide6.QtWebEngineCore `
    --exclude-module PySide6.QtWebEngineWidgets `
    --exclude-module PySide6.QtQml `
    --exclude-module PySide6.QtQuick `
    --exclude-module PySide6.QtMultimedia `
    --exclude-module PySide6.Qt3DCore `
    --exclude-module PySide6.QtNetwork `
    --clean --noconfirm `
    main.py

Write-Host "完成: dist\$name.exe"
