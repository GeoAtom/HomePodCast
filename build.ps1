# Build dist\HomePodCast.exe. Needs Python 3.12 and: pip install -r requirements.txt
pyinstaller --noconfirm --clean --onefile --windowed --name HomePodCast --icon icon.ico `
    --collect-data soundcard --collect-data customtkinter --collect-submodules pyatv app.py
