# Forge Assembly

Source for the Forge Assembly installer. Scan-and-stage rebuilds selected mod archives, installs prepatch Forge foundations first, composes assets and gameplay definitions, then installs matching DX11 FPV/ReShade configuration.

The archive-generated build was confirmed working in gameplay, including weapon assets, 200 base damage for player shotguns, F9 first person, and standard NV/thermal. Known incompatible ADS-camera and item-wheel files are excluded by exact archive/member fingerprints.

## Run locally

Use Windows, Python 3.14 with Tkinter, the .NET 9 SDK, and 7-Zip. Obtain mod archives and the complete original AnvilToolkit 1.3.7 folder separately; keep its DLL and Libs together.

Open `DEEF_Direct_Anvil_Installer_v0_20/START_FORGE_ASSEMBLY.cmd`. Select the game and toolkit folders. Alternatively, copy `data/local_toolkit.example.json` to `data/local_toolkit.json` inside the installer folder and set `toolkitDirectory`, or set `ANVIL_TOOLKIT_DIR`.

After Ubisoft verifies the game, record the clean baseline. Use **Scan & stage**, review the results, then **Install reviewed batch** with the game closed. New mod selections require their own gameplay validation.

## Development

From `DEEF_Direct_Anvil_Installer_v0_20`, run:

```powershell
py -3 -m unittest discover -s tests
dotnet build src/AnvilBridgeProbe.csproj -c Release -o bin -p:AnvilToolkitDirectory="C:\path\to\complete\AnvilToolkit"
```

Game files, mod archives, third-party binaries, generated builds, local settings, caches, saved stages, and backups remain local and are excluded from this repository. The installer README's saved-setup recovery requires local payloads; normal archive rebuilding is independent of those payloads.

The app is now Forge Assembly. Conflict choices pause and resume private composition; priorities/completeness are on Conflicts & new assets, and reports are under Diagnostics. Keep existing workspace and backup paths when updating. Internal legacy folder and receipt names are retained for compatibility.
