# Build dist\HomePodCast.exe. Needs Python 3.12 and: pip install -r requirements.txt

# The exe carries the version from app.py in its file properties
$version = (Select-String -Path app.py -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value
$numbers = (($version.Split(".") + "0", "0", "0")[0..3]) -join ", "
$info = Join-Path $env:TEMP "HomePodCast-version.txt"
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($numbers), prodvers=($numbers)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'HomePod Cast'),
      StringStruct('ProductName', 'HomePod Cast'),
      StringStruct('FileVersion', '$version'),
      StringStruct('ProductVersion', '$version'),
      StringStruct('OriginalFilename', 'HomePodCast.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Encoding ascii $info

pyinstaller --noconfirm --clean --onefile --windowed --name HomePodCast --icon icon.ico `
    --version-file $info `
    --collect-data soundcard --collect-data customtkinter --collect-submodules pyatv app.py
