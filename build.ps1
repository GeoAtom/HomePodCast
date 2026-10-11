# Build dist\HomePodCast.exe, or with -Store dist\HomePodCast.msix for the Microsoft
# Store. Needs Python 3.12 and: pip install -r requirements.txt. The Store package
# also needs the Windows SDK (MakeAppx and MakePri).
param([switch]$Store)

# The exe carries the version from app.py in its file properties
$version = (Select-String -Path app.py -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value
$parts = ($version.Split(".") + "0", "0", "0")[0..3]
$numbers = $parts -join ", "
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

# A package is installed unpacked, so the Store build is a folder, not one file
$bundle = if ($Store) { "--onedir" } else { "--onefile" }
$dist = if ($Store) { "dist\store" } else { "dist" }
pyinstaller --noconfirm --clean $bundle --windowed --name HomePodCast --icon icon.ico `
    --version-file $info --distpath $dist `
    --collect-data soundcard --collect-data customtkinter --collect-submodules pyatv app.py
if (-not $Store -or $LASTEXITCODE) { exit $LASTEXITCODE }

$layout = "dist\store\HomePodCast"
Copy-Item store\Assets $layout -Recurse -Force
# The Store wants four numbers, the last one zero
(Get-Content store\AppxManifest.xml -Raw).Replace('Version="0.0.0.0"', "Version=`"$($parts[0..2] -join '.').0`"") |
    Set-Content -Encoding utf8 $layout\AppxManifest.xml

$sdk = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\makeappx.exe" |
    Sort-Object { [version]$_.Directory.Parent.Name } | Select-Object -Last 1 -ExpandProperty DirectoryName
# The index that lets Windows pick the logo for its scale and size
$config = Join-Path $env:TEMP "HomePodCast-priconfig.xml"
& $sdk\makepri.exe createconfig /cf $config /dq en-US /o | Out-Null
# By default the logos for other scales go into indexes of their own, which only a
# bundle of several packages would load
[xml]$pri = Get-Content $config
$pri.resources.RemoveChild($pri.resources.packaging) | Out-Null
$pri.Save($config)
& $sdk\makepri.exe new /pr store /cf $config /mn $layout\AppxManifest.xml /of $layout\resources.pri /o | Out-Null
if ($LASTEXITCODE) { exit $LASTEXITCODE }
# Unsigned: the Store signs the package when it is submitted
& $sdk\makeappx.exe pack /d $layout /p dist\HomePodCast.msix /o
exit $LASTEXITCODE
