# Forge Assembly

## Build from your mod archives

Run `START_FORGE_ASSEMBLY.cmd`, select your mod archives, then use **Scan & stage** and **Install reviewed batch**. After Ubisoft verification, record a verified baseline first. Scan & stage rebuilds the selected archives; it does not use the saved recovery payload as its source.

**Conflicts & new assets** shows settings for enabled archives currently in the selected mod folder. Opening the page indexes newly added or changed archives in the background; **Refresh mod settings** also rereads the folder. Removed and disabled mods disappear from the settings list. For example, Pathfinder white-hot controls appear only when that archive is present and enabled. Hidden preferences are retained locally so re-enabling a mod restores its settings.

Resource alternatives learned during staging remain under **Discovered mod variants**, even after a later build encounters different conflicts. Save a variant there before scanning again, or choose it in the waiting conflict dialog to continue the current build. Choices remain bound to the original archive hash and are rechecked by staging. Known incompatible files remain held regardless of priority.

Errors now give a short explanation and a next step. Full Python/Anvil details and the affected merge request are saved in `workspace/diagnostics/errors`; use **Diagnostics & recovery > Open operation error reports**. The BCM receiver rename is supported only when the original and incoming object have a unique matching binary ID and type. Packing still requires full resource/metadata readback and Forge index/checksum validation. Private validation of the previously failed complete layer passed; this does not replace gameplay validation of a new mod selection.

The distributable tools ZIP omits personal settings and toolkit paths. Updating an existing folder retains its local preferences; a fresh extraction starts with the source defaults.

The October 14 compatibility tests identified startup crashes in the selected Syringe/Binoculars item-wheel replacements and the No ADS Zoom Updated camera replacements. Their four exact archive/member fingerprints are held out of future builds. Other Various Tweaks resources remain eligible. Standard night vision and thermal are selected; the removed white-hot/no-thermal packages are disabled. The separate DX11 FPV camera controls remain available.

Complete base-parent overrides are inserted before entirely new parents, in separate file sets, so later additions preserve the override slots. The corrected archive build installed six generated Forges and its runtime stage, then survived 92.5 seconds with DX11 FPV/ReShade active and no crash report. The user confirmed gameplay, visible weapons, approximately 200 shotgun damage, F9 first person, normal night vision/thermal, and stability on this archive-generated installation. Evidence: workspace/auto_runs/20261014_164539_ef5829_layered/GAMEPLAY_CONFIRMATION.json. Future mod selections still require their own validation.

## Workspace storage

Scan & stage cleans disposable extraction, readback and intermediate Forge files before and after each build, including failed builds. Verified composition steps also release scratch files immediately. Reports remain available for diagnosis. Cleanup supports Windows paths longer than 260 characters and never follows junctions or symbolic links.

The default workspace budget is **200 GiB**, with **20 GiB free disk space** reserved during staging. The guard checks Python writes and monitors owned packing processes; a short overshoot is possible between checks. It stops staging and cleans scratch when space runs out. Installation checks backup space before replacing game files; cleanup never interrupts an installation rollback. Use **Diagnostics & recovery > Clean generated files** or change the budget there (50–500 GiB).

Cleanup keeps **one staged build**: the latest successfully verified stage, including its resource/runtime dependencies. After a replacement validates, the previous stage's payloads are removed; failed builds leave the current stage available. Diagnostic builds retain reports without saving another stage. Historical reports and confirmation receipts remain available. Saved complete setups and recovery files referenced by receipt history are retained separately. Cleanup retires unreferenced, completed backup sets older than 24 hours while keeping the two newest Forge rollback sets. Duplicate backup files share storage only after SHA-256 verification. Unknown or incomplete backup sets are retained. If retained recovery data fills the budget, staging stops rather than silently deleting it.

Windows folder Properties can count hard-linked Forge files repeatedly. Cleanup reports count each file identity once and also report the disk's free-space change. The budget includes retained backups and stages; it is not permission to retain another 200 GiB of scratch for every run.

The ZIP and Git repository contain tools, not your local rollback backups or saved setup payloads. Keep those recovery files when replacing an existing installation. A completely fresh installer needs your mod archives, AnvilToolkit and a verified game baseline; it cannot recover the old baseline from a modded game by itself.

## Restore the complete working setup

1. Finish Ubisoft verification or installation of Ghost Recon Breakpoint.
2. Close the game.
3. Run START_FORGE_ASSEMBLY.cmd in this folder.
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

The First person & ReShade page provides separate configuration installers. Numerical camera/FOV values and the toggle key are editable. Conflicts & new assets offers Author values or 100/125/150/175/200 for player shotgun damage, and the existing gameplay conflict choices.

Scan & stage rebuilds from enabled archives, using ordered complete Forge foundations, authored assets and verified compatibility merges. Newly added archives go through the same scan and composition process. The saved complete setup is a separate recovery shortcut. Keep AnvilToolkit in its original complete folder or a complete clone for rebuilding; its executable cannot be used alone. Private composition diagnostics and older trial batches are not the saved-setup restore source.

## Validation

141 automated regression tests pass. The real-size fresh-install replay started with the six original Forge files matching the recorded Ubisoft-verified baseline in a private folder. The installer restored all six modded Forges and every runtime file; all 448 files matched the saved hashes. A second install changed zero Forges and zero runtime files. The live game was untouched during that replay.

Evidence: `workspace/diagnostics/FRESH_INSTALL_REPLAY_20261014_024346/RESULT.json` and `workspace/saved_setup/DX11_COMPLETE_20261014_024812/FRESH_INSTALL_VALIDATION.json`. This proves file reproduction; the private copy was not launched as a separate game installation. The live source build was confirmed working by the user.

The Essentials TEAMMATE parent normalization now rebuilds legacy packets with invalid checksums/trailing bytes only in a private workspace. All 3,804 extracted authored files matched the independent readback in the observed failure case. Strict packed-container verification remains enabled.

Archive rebuild fixes on October 14: new authored .data packets with bad checksums are normalized privately, with complete extracted-file and metadata readback. The normal archive pipeline generates a runtime stage from the selected Native FPV package, numeric/key settings, official ReShade runtime, author preset/effects and selected sound package. Reviewed batch installation deploys Forges first, then these runtime files; a runtime failure rolls the Forge transaction back. ShaderToggler addon files remain a separate explicit choice because the confirmed FPV profile omits that addon.

Single-mod diagnostic stages are private reports and cannot be installed. Use **Enabled mod selection / Scan & stage mods** to rebuild the full selected setup with your current settings. The archive list and Forge count appear in the stage summary. Clean originals are retained across partial installations through verified, linked backup receipts.

Gameplay merge controls now offer 100/125/150/175/200 player shotgun damage, vanilla ammo reserves (including the vanilla max-ammo perk), and the author's vanilla-like suppressor preset independently of WABO difficulty. The Game settings sensitivity profile preserves vanilla weapon ADS sensitivity and input filters and lets Breakpoint control optic FOV. Shared vehicle/player automatic triggers and normal finite-count syringes are restored from verified vanilla objects. Coupled, incompatible wheel swaps cannot be partially applied. Vanilla concealment restores the original bush/perception/visual-detection definitions; this does not establish that every plant blocks line of sight, which still requires gameplay validation. Runtime packages are staged even when already installed, so fresh installs and reinstalls receive the same sound payload. FPV Home moves the ReShade overlay to Shift+Home. These changes apply through a newly generated stage; no live game test is claimed for this revision.

Removing or disabling a mod and rebuilding also stages verified original Forges left over from the previous recorded install when no selected mod uses those destinations. Install and rollback retain their existing backups and hash checks. Precombined PrePatch-UE variants are excluded unless the standalone Unlock Everything archive is explicitly enabled. Removing files from the archive folder alone does not change an already installed game; run Scan & stage and install the new reviewed batch. The saved complete setup is a fixed recovery snapshot and can include Unlock Everything regardless of the current archive selection. The installer does not replace character saves or recover historical progress.

Runtime cleanup follows explicit configuration/installation receipts inside the workspace. Files omitted from the current runtime plan are restored to their recorded original bytes, or removed if that installation originally created them. Missing/changed backups and unexpected edits block cleanup. Untracked files are not guessed at or deleted. Runtime stages reject save/profile destinations. Scan-and-stage tests cover deleted archives, changed archive payloads, multiple runtime versions, rollback, and untouched save files.

## Conflict priorities and table composition

Open **Conflicts & new assets > Conflict priorities**. Gameplay conflict settings, including Radio/Nightmare caller preferences, are on that same page. The three tabs provide an optional ordered mod list, separate weapon field donors, and exact resource/variant choices discovered by a scan. Higher entries win unresolved object conflicts. Ordering starts disabled: unknown conflicts do not receive an alphabetical winner. Save the choices and run a new Scan & stage. Resource choices are bound to the selected archive hash; changed or missing choices require review.

The general composition process preserves the complete original container, combines distinct objects by binary ID and type, credits identical payloads once, and ignores unchanged copies when another candidate changes that object. Entirely new identities are included and read back, rather than treated as missing targets. Incompatible changes to one object require a priority; variants in the same archive require an exact member choice. Selecting an exact container variant excludes its alternatives rather than mixing their optional assets. All resulting objects and container metadata are checked after repacking, followed by independent Forge readback.

Weapon fields can choose different donors for damage, ballistics, handling, recoil/spread, fire rate, sound/detection range and ADS sensitivity. The established WABO/Damage Balance rule remains the default. The validated AAC magazine/trigger reference merge and authored dominant-payload checks remain active. Vanilla sensitivity and the shotgun/LMG controls run afterward. Unsupported binary layouts are not combined by guessing byte offsets: their objects require a whole-object winner or remain blocked. This is object-level table composition plus schema-aware field merging, not a promise that arbitrary fields in every GRB class can be interpreted.

Each run writes CONFLICT_PRIORITIES.json, CONFLICT_POLICY_REPORT.json, composition proofs and any PRIORITY_EXCLUSIONS.json. Mutually exclusive losers are reported as user exclusions rather than silently credited as installed. A changed selection or build setting invalidates a previously staged batch; unrelated changes during a build stop that build; exact choices requested by a waiting build are accepted at that checkpoint. These rules preserve existing prepatch order, compatibility holds, clean-baseline restoration and runtime cleanup. Build and readback verification do not replace gameplay testing.

Generalized-conflict validation: 131 regression tests, the C# format checks, and a private archive rebuild of six Forges (14 complete composition steps plus 35 resource edits) passed. Real-container fixtures verified object union, exact variant selection, tied/unranked conflict rejection, and grouping three same-named inputs into the correct two-member binary conflict. The declared DBContainerEntry folder alias also passed a separate Smith & Wesson + WABO + Damage Balance merge with complete database readback. Known camera/addon compatibility exclusions remain. These were private file tests; no live game install or gameplay test was performed for this revision.

Fresh-checkout defaults now enable complete-content composition and the tested gameplay profile even when workspace/settings.json is missing or contains only paths. Existing explicit choices are preserved. A supported WABO Compatibility Tool 1.1.2 ZIP in the selected archive folder is discovered by its verified hash; it is used only as a reader. Missing tools stop weapon composition early with a clear message instead of silently falling back to legacy mapping. The scan log states which process is active. Ubisoft verification does not clear installer caches; complete containers are composed before the smaller remaining characterization pass, and reusable composition outputs remain bound to source/payload hashes. Fresh/partial settings and missing-reader behavior are covered by three additional tests (134 total).

## Forge Assembly layout and interactive staging

Run START_FORGE_ASSEMBLY.cmd. Conflict priorities, mod completeness, and gameplay conflict settings (including Radio/Nightmare caller selection) are grouped under Conflicts & new assets. Open reports is under Diagnostics & recovery. Mod library manages archive selection; First person & ReShade retains its configuration/install controls.

Resolvable composition conflicts pause the current private build and open the exact-variant chooser automatically. Save choice and continue retries the failed composition step against the same verified source, preserving earlier completed layers. During that pause only current resource choices can change. Cancel staging releases the worker without an error popup. Corruption, ambiguous physical identities and unrelated configuration changes still stop rather than approving unsafe files.

Mod selection now follows archive presence and saved user choices, without applying an author's mod-list defaults. Historical route evidence, author credits and verified compatibility rules remain. Existing workspace paths and receipt identifiers retain their legacy names for backup compatibility; update the program files in place and keep workspace/backups in place. New tools packages are Forge_Assembly.zip. The UI layout, asynchronous Save/Continue and Cancel paths, and resumed build/install/rollback are covered by 141 tests. A real private MK48 variant conflict was selected and retried successfully with independent Forge readback; the live game was untouched.
