# Builds dist\Notified.exe (single file, no Python needed on the target PC; uses Windows' built-in WebView2).
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name Notified `
  --icon notified/assets/notified.ico `
  --add-data "notified/tag_rules.json;notified" `
  --add-data "notified/assets;notified/assets" `
  --add-data "notified/web;notified/web" `
  --collect-submodules notified `
  --collect-data curl_cffi `
  --collect-data fpdf `
  --collect-all webview `
  --hidden-import pystray._win32 `
  run_notified.py
