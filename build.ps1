# Builds dist\RegWatch.exe (single file, no Python needed on the target PC).
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name RegWatch `
  --add-data "regwatch/tag_rules.json;regwatch" `
  --collect-submodules regwatch.sources `
  --collect-data curl_cffi `
  run_regwatch.py
