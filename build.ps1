# Builds dist\RegWatch.exe (single file, no Python needed on the target PC).
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name RegWatch `
  --icon regwatch/assets/regwatch.ico `
  --add-data "regwatch/tag_rules.json;regwatch" `
  --add-data "regwatch/assets;regwatch/assets" `
  --collect-submodules regwatch `
  --collect-data curl_cffi `
  --collect-data customtkinter `
  --collect-data fpdf `
  --hidden-import pystray._win32 `
  run_regwatch.py
