# 一键打包（Windows）：实际打包逻辑在 tools/build_app.py（Python 侧处理
# UTF-8 中文包名，避免 PowerShell 5.1 读取脚本的编码乱码）
# 用法：powershell -ExecutionPolicy Bypass -File tools\build.ps1
$ErrorActionPreference = "Stop"
python "tools\build_app.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
