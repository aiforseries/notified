# Builds dist\RegWatch.exe (single file, no Python needed on the target PC; uses Windows' built-in WebView2).
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name RegWatch `
  --icon regwatch/assets/regwatch.ico `
  --add-data "regwatch/tag_rules.json;regwatch" `
  --add-data "regwatch/assets;regwatch/assets" `
  --add-data "regwatch/web;regwatch/web" `
  --collect-submodules regwatch `
  --collect-data curl_cffi `
  --collect-data fpdf `
  --collect-all webview `
  --hidden-import pystray._win32 `
  run_regwatch.py
