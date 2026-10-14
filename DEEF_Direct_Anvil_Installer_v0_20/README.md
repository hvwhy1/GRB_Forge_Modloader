# DEEF Breakpoint Mod Studio v20

## Build from your mod archives

Run `START_DEEF.cmd`, select your mod archives, then use **Scan & stage** and **Install reviewed batch**. After Ubisoft verification, record a verified baseline first. Scan & stage rebuilds the selected archives; it does not use the saved recovery payload as its source.

The October 14 compatibility tests identified startup crashes in the selected Syringe/Binoculars item-wheel replacements and the No ADS Zoom Updated camera replacements. Their four exact archive/member fingerprints are held out of future builds. Other Various Tweaks resources remain eligible. Standard night vision and thermal are selected; the removed white-hot/no-thermal packages are disabled. The separate DX11 FPV camera controls remain available.

Complete base-parent overrides are inserted before entirely new parents, in separate file sets, so later additions preserve the override slots. The corrected archive build installed six generated Forges and its runtime stage, then survived 92.5 seconds with DX11 FPV/ReShade active and no crash report. The user confirmed gameplay, visible weapons, approximately 200 shotgun damage, F9 first person, normal night vision/thermal, and stability on this archive-generated installation. Evidence: workspace/auto_runs/20261014_164539_ef5829_layered/GAMEPLAY_CONFIRMATION.json. Future mod selections still require their own validation.

## Restore the complete working setup

1. Finish Ubisoft verification or installation of Ghost Recon Breakpoint.
2. Close the game.
3. Run START_DEEF.cmd in this folder.
4. For recovery, click **Install saved complete setup**.

Scan & stage builds from your enabled mod archives; it does not stage this premade recovery copy.

This restores the saved working state directly. It installs the complete Forge files, including their prepatch foundations, repaired file-set indexes, weapon models/textures, merged damage definitions, and the selected gameplay changes. It then installs the matching DX11 first-person loader, ReShade preset/effects, camera configuration and sound package. AnvilToolkit and GRB Manager are not needed for this saved-setup restore. Python is used by the GUI.

The saved setup is in `workspace/saved_setup/DX11_COMPLETE_20261014_024812`. Its manifest and Payloads folder contain all 448 required mod files. Keep this folder with the installer. The small tools ZIP does not include these large game payloads. The restore supports the recorded game executable/version and installation path.

Every changed Forge and runtime file is backed up before replacement. The installer checks hashes, refuses conflicting unknown graphics loaders, requires the game to be closed, and restores backups if a later component fails. Unchanged files are skipped. No typed confirmation phrases are required.

## Saved gameplay settings

- DX11 only: Native d3d11.dll plus standalone ReShade dxgi.dll, with no dinput8.dll renderer selector or duplicate graphics chain.
- First person: F9 toggle after loading a character, manual activation, FOV/ADS FOV 100, body following enabled, Vanilla interactions.
- Camera: forward clearance 0.18 m and near clip 0.04 m. Native locomotion DOF and autofocus suppression enabled. Headgear appearance at 4 cm still needs a specific visual check.
- Player shotguns: 200 base damage in 13 player variants. NPC/test/boss variants and the Bailiff sidearm retain their values. The user confirmed this patch works in gameplay.
- Repaired SPEAR/4-AC and KS1/516 assets and damage remain included. The stable build retains the original LMG accuracy profile.

## Change or rebuild mods

The First person & ReShade page provides separate configuration installers. Numerical camera/FOV values and the toggle key are editable. Merge options offers Author values or 200 for player shotgun damage, and the existing gameplay conflict choices.

Scan & stage rebuilds from enabled archives, using ordered complete Forge foundations, authored assets and verified compatibility merges. Newly added archives go through the same scan and composition process. The saved complete setup is a separate recovery shortcut. Keep AnvilToolkit in its original complete folder or a complete clone for rebuilding; its executable cannot be used alone. Private composition diagnostics and older trial batches are not the saved-setup restore source.

## Validation

100 automated regression tests pass. The real-size fresh-install replay started with the six original Forge files matching the recorded Ubisoft-verified baseline in a private folder. The installer restored all six modded Forges and every runtime file; all 448 files matched the saved hashes. A second install changed zero Forges and zero runtime files. The live game was untouched during that replay.

Evidence: `workspace/diagnostics/FRESH_INSTALL_REPLAY_20261014_024346/RESULT.json` and `workspace/saved_setup/DX11_COMPLETE_20261014_024812/FRESH_INSTALL_VALIDATION.json`. This proves file reproduction; the private copy was not launched as a separate game installation. The live source build was confirmed working by the user.

The Essentials TEAMMATE parent normalization now rebuilds legacy packets with invalid checksums/trailing bytes only in a private workspace. All 3,804 extracted authored files matched the independent readback in the observed failure case. Strict packed-container verification remains enabled.

Archive rebuild fixes on October 14: new authored .data packets with bad checksums are normalized privately, with complete extracted-file and metadata readback. The normal archive pipeline generates a runtime stage from the selected Native FPV package, numeric/key settings, official ReShade runtime, author preset/effects and selected sound package. Reviewed batch installation deploys Forges first, then these runtime files; a runtime failure rolls the Forge transaction back. ShaderToggler addon files remain a separate explicit choice because the confirmed FPV profile omits that addon.

Single-mod diagnostic stages are private reports and cannot be installed. Use **Enabled mod selection / Scan & stage mods** to rebuild the full selected setup with your current settings. The archive list and Forge count appear in the stage summary. Clean originals are retained across partial installations through verified, linked backup receipts.
