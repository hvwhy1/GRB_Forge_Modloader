using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.Json;
using System.Threading.Tasks;

// v0.14: PRIVATE ONLY: change exactly ONE bit in ONE extracted nested resource's bytes.
// Prove the change survives rebuilding .data and its containing .forge while every
 // other inner/outer resource matches its expected SHA-256 and duplicate counts.
 // This is a transport/serialization test, NOT a valid game-content modification.
// Unpack copied Forge -> select copied .data -> rebuild .data -> replace ONE
// resource within a private extracted Forge -> rebuild new Forge -> extract
// -> assert every outer payload exactly matches the expected modified state
// -> extract the replaced inner .data and compare nested resource hashes.
// No installed-game write, no asset injection, no mod deployment.
internal static class StageReplace
{
    private static readonly StringComparer Cmp = StringComparer.OrdinalIgnoreCase;
    private static readonly JsonSerializerOptions JsonOpt = new() { WriteIndented = true };
    private static string Now() => DateTime.Now.ToString("HH:mm:ss");

    private static Dictionary<string, string> TopIndex(string folder)
    {
        if (!Directory.Exists(folder)) throw new DirectoryNotFoundException(folder);
        if (Directory.EnumerateDirectories(folder, "*", SearchOption.TopDirectoryOnly).Any())
            throw new IOException("Unexpected nested folders in Forge writer directory; refusing incomplete serialization.");
        var found = new Dictionary<string, string>(Cmp);
        foreach (string file in Directory.EnumerateFiles(folder, "*", SearchOption.TopDirectoryOnly))
        {
            string name = Path.GetFileName(file);
            if (!found.TryAdd(name, Trial.Hash(file)))
                throw new IOException("Duplicate extracted Forge path: " + name);
        }
        return found;
    }

    // Only numbered AnvilToolkit-extracted objects qualify. Never edit auxiliary
    // .Dependency/.PrefetchingInfo/.FilterTable files, even in a private trial.
    private static bool IsObjectResource(string key)
    {
        if (key.Contains('/') || key.Contains('\\')) return false;
        int sep = key.IndexOf("_-_", StringComparison.Ordinal);
        if (sep <= 0 || !key[..sep].All(char.IsDigit)) return false;
        string ext = Path.GetExtension(key);
        return !new[] { ".Dependency", ".PrefetchingInfo", ".FilterTable", ".xml", ".Prefab" }
            .Contains(ext, Cmp);
    }

    private static string Logical(string name)
    {
        int sep = name.IndexOf("_-_", StringComparison.Ordinal);
        return sep > 0 && name[..sep].All(char.IsDigit) ? name[(sep + 3)..] : name;
    }

    private static Dictionary<string, string[]> GroupHashes(Dictionary<string, string> map) =>
        map.GroupBy(e => Logical(e.Key), Cmp)
        .ToDictionary(g => g.Key,
            g => g.Select(e => e.Value).OrderBy(x => x, StringComparer.OrdinalIgnoreCase).ToArray(), Cmp);

    private static (string[] missing, string[] extra, string[] changed) Compare(
        Dictionary<string, string> expected, Dictionary<string, string> actual)
    {
        var a = GroupHashes(expected);
        var b = GroupHashes(actual);
        return (
            a.Keys.Except(b.Keys, Cmp).OrderBy(x => x, Cmp).ToArray(),
            b.Keys.Except(a.Keys, Cmp).OrderBy(x => x, Cmp).ToArray(),
            a.Where(e => b.TryGetValue(e.Key, out var hashes)
                && !e.Value.SequenceEqual(hashes, StringComparer.OrdinalIgnoreCase))
                .Select(e => e.Key).OrderBy(x => x, Cmp).ToArray());
    }

    private static (string[] missing, string[] extra, string[] changed) CompareNested(
        Dictionary<string, string> expected, Dictionary<string, string> actual)
    {
        var a = NestedTrial.ByLogicalName(expected);
        var b = NestedTrial.ByLogicalName(actual);
        return (
            a.Keys.Except(b.Keys, Cmp).OrderBy(x => x, Cmp).ToArray(),
            b.Keys.Except(a.Keys, Cmp).OrderBy(x => x, Cmp).ToArray(),
            a.Where(e => b.TryGetValue(e.Key, out var hashes)
                && !e.Value.SequenceEqual(hashes, StringComparer.OrdinalIgnoreCase))
                .Select(e => e.Key).OrderBy(x => x, Cmp).ToArray());
    }

    // DataFile.Deserialize uses uint32.GetHashedString() when assigning an
    // extracted resource's extension. Depending on which name tables have been
    // initialized, the SAME type ID can be rendered as a number (2063174246)
    // or a friendly class name (CompiledMaterialTemplateContainer). Thus a
    // label alone is NOT a reliable byte identity in the final nested readback.
    // Remove *only* the trailing type-label extension and entry ordinal. Keep
    // the relative path and the actual resource stem as part of the identity.
    private static string NestedStemWithoutTypeLabel(string name)
    {
        name = name.Replace('\\', '/');
        int lastSlash = name.LastIndexOf('/');
        string parent = lastSlash < 0 ? "" : name[..(lastSlash + 1)];
        string leaf = name[(lastSlash + 1)..];
        int separator = leaf.IndexOf("_-_", StringComparison.Ordinal);
        if (separator > 0 && leaf[..separator].All(char.IsDigit))
            leaf = leaf[(separator + 3)..];
        int dot = leaf.LastIndexOf('.');
        if (dot > 0) leaf = leaf[..dot];
        return parent + leaf;
    }

    private static Dictionary<string, string[]> NestedStemHashMultiset(Dictionary<string, string> entries)
        => entries.GroupBy(kv => NestedStemWithoutTypeLabel(kv.Key), Cmp)
            .ToDictionary(g => g.Key,
                g => g.Select(kv => kv.Value)
                    .OrderBy(h => h, StringComparer.OrdinalIgnoreCase).ToArray(), Cmp);

    private static bool SameNestedStemsAndHashes(
        Dictionary<string, string> expected, Dictionary<string, string> actual)
    {
        if (expected.Count != actual.Count) return false;
        var a = NestedStemHashMultiset(expected);
        var b = NestedStemHashMultiset(actual);
        return a.Count == b.Count && a.All(kv => b.TryGetValue(kv.Key, out var h)
            && kv.Value.SequenceEqual(h, StringComparer.OrdinalIgnoreCase));
    }

    private static bool SameNestedHashMultiset(
        Dictionary<string, string> expected, Dictionary<string, string> actual)
        => expected.Count == actual.Count && expected.Values
            .OrderBy(h => h, StringComparer.OrdinalIgnoreCase)
            .SequenceEqual(actual.Values.OrderBy(h => h, StringComparer.OrdinalIgnoreCase),
                StringComparer.OrdinalIgnoreCase);

    private static void RequireNoDifferences((string[] missing, string[] extra, string[] changed) diff,
        int oldCount, int newCount, string stage)
    {
        if (oldCount != newCount || diff.missing.Length != 0 || diff.extra.Length != 0 || diff.changed.Length != 0)
            throw new IOException($"{stage}: FAILED verification (counts {oldCount}/{newCount}; " +
                $"missing {diff.missing.Length}, extra {diff.extra.Length}, changed {diff.changed.Length}).");
    }

    private static void CheckInside(string sandbox, string file)
    {
        if (!Trial.Inside(sandbox, file)) throw new IOException("Refusing to write outside private sandbox: " + file);
    }

    private static string CheckSource(string source, string toolkit, string reportCopy, string selector)
    {
        if (!File.Exists(source) || !source.EndsWith(".forge", StringComparison.OrdinalIgnoreCase))
            throw new IOException("Choose an existing .forge file for the combined test.");
        if (!File.Exists(Path.Combine(toolkit, "AnvilToolkit.dll")) ||
            !Directory.Exists(Path.Combine(toolkit, "Libs")))
            throw new IOException("Toolkit folder must contain AnvilToolkit.dll and Libs.");
        if (new FileInfo(source).Length > 128L * 1024 * 1024)
            throw new IOException("This combined test is limited to 128 MiB Forge archives.");
        if (Trial.Inside(Path.GetDirectoryName(source)!, reportCopy) || Trial.Inside(toolkit, reportCopy))
            throw new IOException("The JSON report must not be written inside the game/toolkit folders.");
        if (!reportCopy.EndsWith(".json", StringComparison.OrdinalIgnoreCase))
            throw new IOException("Report destination must end in .json.");
        if (selector.Equals("AUTO", StringComparison.OrdinalIgnoreCase)) selector = "";
        if (selector.Contains('/') || selector.Contains('\\') || selector == "." || selector == ".."
            || (selector.Length != 0 && !selector.EndsWith(".data", StringComparison.OrdinalIgnoreCase)))
            throw new ArgumentException("Nested selector must be AUTO or one exact .data filename, not a path.");
        return selector;
    }

    public static int Run(string[] args)
    {
        string source = "", sandbox = "", originalSourceHash = "";
        string oldCwd = Environment.CurrentDirectory;
        string? oldTemp = Environment.GetEnvironmentVariable("TEMP");
        string? oldTmp = Environment.GetEnvironmentVariable("TMP");
        string? reportCopy = null;
        var report = new Dictionary<string, object?>
        {
            ["mode"] = "STAGED_NESTED_RESOURCE_REPLACEMENT_PRIVATE_COPY",
            ["verified"] = false,
            ["gameModified"] = false,
            ["stage"] = "INPUT_VALIDATION",
            ["deployable"] = false,
            ["syntheticMutationOnly"] = false
        };
        int code = 1;
        try
        {
            if (args.Length < 8) throw new ArgumentException(
                "Usage: stage <toolkit-folder> <source.forge> <report.json> <exact_data_filename> <exact_object_name> <replacement_file> <staged_output_dir>");
            string toolkit = Path.GetFullPath(args[1].Trim('"'));
            source = Path.GetFullPath(args[2].Trim('"'));
            reportCopy = Path.GetFullPath(args[3].Trim('"'));
            string selector = CheckSource(source, toolkit, reportCopy, (args[4] ?? "").Trim().Trim('"'));
            if (string.IsNullOrWhiteSpace(selector) || selector.Equals("AUTO", StringComparison.OrdinalIgnoreCase))
                throw new IOException("Staging requires an explicit .data target; AUTO is disabled for real payloads.");
            string objectSelector = (args[5] ?? "").Trim().Trim('"');
            if (string.IsNullOrWhiteSpace(objectSelector) || objectSelector == "AUTO" ||
                objectSelector.Contains('/') || objectSelector.Contains('\\') || objectSelector == "." || objectSelector == "..")
                throw new IOException("Provide one exact extracted nested object filename, not a path or AUTO.");
            string payloadFile = Path.GetFullPath(args[6].Trim('"'));
            if (!File.Exists(payloadFile) || new FileInfo(payloadFile).Length < 1 ||
                new FileInfo(payloadFile).Length > int.MaxValue)
                throw new IOException("Payload must be a nonempty local file under 2 GiB (signed 32-bit Forge entry limit).");
            string outputDirectory = Path.GetFullPath(args[7].Trim('"'));
            if (Trial.Inside(Path.GetDirectoryName(source)!, outputDirectory) ||
                Trial.Inside(toolkit, outputDirectory) ||
                Trial.Inside(Path.GetDirectoryName(source)!, payloadFile) ||
                Trial.Inside(toolkit, payloadFile) ||
                Trial.Inside(Path.GetDirectoryName(source)!, reportCopy))
                throw new IOException("Payload, report and staging output must be outside the game and toolkit directories.");
            string payloadHash = Trial.Hash(payloadFile);
            report["replacementSourceSha256"] = payloadHash;
            report["replacementSourceBytes"] = new FileInfo(payloadFile).Length;
            report["requestedNestedObject"] = objectSelector;
            report["stagedOutputDirectory"] = outputDirectory;
            string tempRoot = Path.Combine(Path.GetTempPath(), "DEEF_AnvilBridge_Trials");
            if (Trial.Inside(Path.GetDirectoryName(source)!, tempRoot))
                throw new IOException("Scratch directory is nested inside the game folder.");
            Trial.EnsureScratchSpace(source, tempRoot);
            sandbox = Path.Combine(tempRoot, DateTime.UtcNow.ToString("yyyyMMdd_HHmmss") +
                "_mutation_" + Guid.NewGuid().ToString("N")[..8]);
            string inputRoot = Path.Combine(sandbox, "Input");
            string workRoot = Path.Combine(sandbox, "Work");
            string scratch = Path.Combine(sandbox, "Scratch");
            Directory.CreateDirectory(inputRoot);
            Directory.CreateDirectory(workRoot);
            Directory.CreateDirectory(scratch);
            originalSourceHash = Trial.Hash(source);
            string forgeName = Path.GetFileName(source);
            string forgeInput = Path.Combine(inputRoot, forgeName);
            Trial.CopyOnce(source, forgeInput);
            if (Trial.Hash(forgeInput) != originalSourceHash)
                throw new IOException("Initial Forge working copy differs from game source.");
            report["sandbox"] = sandbox;
            report["source"] = source;
            report["sourceSha256"] = originalSourceHash;
            report["requestedNestedFile"] = selector;
            Environment.CurrentDirectory = sandbox;
            Environment.SetEnvironmentVariable("TEMP", scratch);
            Environment.SetEnvironmentVariable("TMP", scratch);
            Console.WriteLine("Sandbox: " + sandbox);
            Console.WriteLine("Mode: STAGED_NESTED_RESOURCE_REPLACEMENT_PRIVATE_COPY");
            Console.WriteLine("Original Forge SHA-256: " + originalSourceHash);
            var dll = Trial.LoadToolkit(toolkit);
            var storage = dll.GetType("AnvilToolkit.Utils.DataStorage", true)!;
            var activeGame = storage.GetField("ActiveGame", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
                ?? throw new MissingFieldException(storage.FullName, "ActiveGame");
            Type gameType = activeGame.FieldType;
            if (!gameType.IsEnum || gameType.Assembly != dll)
                throw new IOException("Unexpected Toolkit Game enum definition.");
            object game = Enum.Parse(gameType, "GhostReconBreakpoint", ignoreCase: false);
            report["toolkitGameContext"] = Trial.SetToolkitGameContext(dll, gameType, game);
            report["oodleContext"] = Trial.ConfigureToolkitOodle(dll, toolkit, game, verifyResolver: true);
            var forgeType = dll.GetType("AnvilToolkit.FileTypes.AnvilNext.Containers.ForgeFile", true)!;
            var dataType = dll.GetType("AnvilToolkit.FileTypes.AnvilNext.Containers.DataFile", true)!;
            Type[] forgeReadTypes = { typeof(string), typeof(string), gameType, typeof(bool), typeof(bool), typeof(bool) };
            Type[] forgeWriteTypes = { typeof(string), typeof(string), gameType };

            report["stage"] = "UNPACK_ORIGINAL_FORGE";
            Console.WriteLine($"[{Now()}] Extracting private Forge copy...");
            var unpacker = Activator.CreateInstance(forgeType)!;
            Trial.Invoke(unpacker, "Deserialize", forgeReadTypes,
                workRoot + Path.DirectorySeparatorChar, forgeInput, game, false, false, false);
            string forgeFolder = Path.Combine(workRoot, forgeName);
            report["originalForgeStableResourceCount"] = Trial.WaitForStableForgeContents(forgeFolder);
            var originalForge = TopIndex(forgeFolder);
            report["originalForgeResourceCount"] = originalForge.Count;
            Console.WriteLine($"[{Now()}] Extracted {originalForge.Count} Forge resources.");
            if (originalForge.Count == 0) throw new IOException("Forge extraction produced zero resources.");
            var candidates = Directory.EnumerateFiles(forgeFolder, "*", SearchOption.TopDirectoryOnly)
                .Where(f => f.EndsWith(".data", StringComparison.OrdinalIgnoreCase) && new FileInfo(f).Length > 0)
                .Where(f => Cmp.Equals(Path.GetFileName(f), selector) || Cmp.Equals(Logical(Path.GetFileName(f)), selector))
                .OrderBy(f => new FileInfo(f).Length).ThenBy(f => f, Cmp).ToArray();
            report["dataCandidatesFound"] = candidates.Length;
            if (candidates.Length != 1) throw new IOException("Explicit .data selector must match exactly one resource, found " + candidates.Length);

            report["stage"] = "SELECT_AND_UNPACK_NESTED_DATA";
            var candidateDiagnostics = new List<object>();
            string? selectedForgeResource = null, copiedData = null, innerFolder = null;
            Dictionary<string, string>? innerOriginal = null;
            for (int i = 0; i < candidates.Length; i++)
            {
                string candidate = candidates[i];
                string nestedInputRoot = Path.Combine(sandbox, "NestedCandidates", (i + 1).ToString("D2"), "Input");
                Directory.CreateDirectory(nestedInputRoot);
                string name = Path.GetFileName(candidate);
                string copy = Path.Combine(nestedInputRoot, name);
                Trial.CopyOnce(candidate, copy);
                string extracted = Path.Combine(nestedInputRoot, "Extracted", name);
                try
                {
                    report["nestedPreparation"] = Trial.PrepareNestedInspection(
                        dataType, game, forgeFolder, name, sandbox);
                    var (failed, message) = NestedTrial.UnpackData(dataType, game, copy, extracted);
                    if (failed) throw new IOException("DataFile.Deserialize failed: " + message);
                    var objects = NestedTrial.Index(extracted);
                    if (objects.Count == 0) throw new IOException("DataFile contained zero extracted resources.");
                    // Choose an actual extracted object, not prefetching/metadata or a tiny header.
                    bool eligible = objects.Keys.Count(key => IsObjectResource(key) && Cmp.Equals(key, objectSelector)) == 1;
                    if (!eligible) throw new IOException("Mapped nested object does not exist uniquely in this .data file.");
                    selectedForgeResource = candidate;
                    copiedData = copy;
                    innerFolder = extracted;
                    innerOriginal = objects;
                    candidateDiagnostics.Add(new { name, outcome = "SELECTED", internalResourceCount = objects.Count });
                    break;
                }
                catch (Exception ex)
                {
                    candidateDiagnostics.Add(new { name, outcome = "SKIPPED", message = ex.Message });
                    Console.WriteLine($"[{Now()}] Candidate skipped: {name}: {ex.Message}");
                    if (!string.IsNullOrEmpty(selector)) throw;
                }
            }
            report["candidateDiagnostics"] = candidateDiagnostics;
            if (selectedForgeResource == null || copiedData == null || innerFolder == null || innerOriginal == null)
                throw new IOException("No candidate .data container could be unpacked. Review candidateDiagnostics.");
            string selectedName = Path.GetFileName(selectedForgeResource);
            string originalNestedSha = Trial.Hash(copiedData);
            report["selectedForgeResource"] = selectedName;
            report["originalNestedBinarySha256"] = originalNestedSha;
            report["originalNestedResourceCount"] = innerOriginal.Count;
            Console.WriteLine($"[{Now()}] Selected {selectedName}, {innerOriginal.Count} internal resources.");

            // STAGING: exact user-supplied object bytes, not a synthetic bit flip.
            // The container/file mapping must be explicit; no heuristic selection.
            report["stage"] = "REPLACE_EXACT_NESTED_OBJECT_IN_PRIVATE_COPY";
            string[] eligibleObjectKeys = innerOriginal.Keys
                .Where(IsObjectResource).Where(key => Cmp.Equals(key, objectSelector)).ToArray();
            if (eligibleObjectKeys.Length != 1)
                throw new IOException("Object target must identify exactly one extracted resource.");
            string mutationKey = eligibleObjectKeys[0];
            string mutationPath = Path.Combine(innerFolder, mutationKey.Replace('/', Path.DirectorySeparatorChar));
            CheckInside(sandbox, mutationPath);
            string originalObjectHash = Trial.Hash(mutationPath);
            if (Cmp.Equals(originalObjectHash, payloadHash))
                throw new IOException("Replacement is identical to original object; nothing would change.");
            byte[] replacementBytes = File.ReadAllBytes(payloadFile);
            File.WriteAllBytes(mutationPath, replacementBytes);
            string mutatedObjectHash = Trial.Hash(mutationPath);
            if (!Cmp.Equals(mutatedObjectHash, payloadHash))
                throw new IOException("Replacement file hash changed during copying.");
            var expectedInner = NestedTrial.Index(innerFolder);
            if (expectedInner.Count != innerOriginal.Count ||
                expectedInner.Keys.Except(innerOriginal.Keys, Cmp).Any())
                throw new IOException("Inner resource filenames changed unexpectedly.");
            string[] changedInner = innerOriginal.Keys
                .Where(key => !Cmp.Equals(innerOriginal[key], expectedInner[key])).ToArray();
            if (changedInner.Length != 1 || !Cmp.Equals(changedInner[0], mutationKey) ||
                !Cmp.Equals(expectedInner[mutationKey], mutatedObjectHash))
                throw new IOException("Expected exactly one changed inner resource hash.");
            report["replacement"] = new {
                resource = mutationKey,
                originalObjectSha256 = originalObjectHash,
                expectedReplacementSha256 = payloadHash,
                changedResourceCount = changedInner.Length,
                fileLengthBytes = replacementBytes.Length,
                mode = "EXPLICIT_REAL_RESOURCE_PAYLOAD; STAGING_ONLY"
            };
            Console.WriteLine($"[{Now()}] Staged exact user replacement: {mutationKey}, " +
                $"SHA-256 {payloadHash}; exactly one nested object changed.");

            report["stage"] = "REBUILD_NESTED_DATA";
            string nestedRepackedDir = Path.Combine(sandbox, "NestedRepacked");
            Directory.CreateDirectory(nestedRepackedDir);
            string nestedRepacked = Path.Combine(nestedRepackedDir, selectedName);
            var nestedResult = NestedTrial.RepackData(dataType, gameType, game,
                copiedData, innerFolder, nestedRepacked);
            report["nestedSerializeResult"] = new { hasFailed = nestedResult.failed, message = nestedResult.message };
            if (nestedResult.failed || !File.Exists(nestedRepacked) || new FileInfo(nestedRepacked).Length <= 0)
                throw new IOException("Nested serializer failed or returned an empty file: " + nestedResult.message);
            string repackedNestedSha = Trial.Hash(nestedRepacked);
            report["repackedNestedBinarySha256"] = repackedNestedSha;
            Console.WriteLine($"[{Now()}] Rebuilt nested .data (SHA-256 {repackedNestedSha}).");

            report["stage"] = "VERIFY_STANDALONE_REBUILT_DATA";
            string innerCheckRoot = Path.Combine(sandbox, "NestedIndependentReadback", "Input");
            Directory.CreateDirectory(innerCheckRoot);
            string innerCheckFile = Path.Combine(innerCheckRoot, selectedName);
            Trial.CopyOnce(nestedRepacked, innerCheckFile);
            string independentExtract = Path.Combine(innerCheckRoot, "Extracted", selectedName);
            var standaloneResult = NestedTrial.UnpackData(dataType, game, innerCheckFile, independentExtract);
            if (standaloneResult.failed) throw new IOException("Rebuilt .data failed standalone readback: " + standaloneResult.message);
            var standaloneEntries = NestedTrial.Index(independentExtract);
            var standaloneComparison = CompareNested(expectedInner, standaloneEntries);
            report["standaloneNestedReadbackCount"] = standaloneEntries.Count;
            RequireNoDifferences(standaloneComparison, expectedInner.Count, standaloneEntries.Count,
                "Independent nested .data readback");
            report["standaloneNestedVerified"] = true;
            Console.WriteLine($"[{Now()}] Standalone nested .data verification PASS.");

            report["stage"] = "PREPARE_PRIVATE_FORGE_REPLACEMENT";
            // This modifies ONLY one file inside Work/ in the fresh TEMP sandbox,
            // never the original source, the copied Input/ Forge, or any toolkit file.
            CheckInside(sandbox, selectedForgeResource);
            if (!originalForge.TryGetValue(selectedName, out string? oldForgeHash)
                || !Cmp.Equals(oldForgeHash, originalNestedSha))
                throw new IOException("Selected Forge resource was altered before controlled replacement.");
            File.Copy(nestedRepacked, selectedForgeResource, overwrite: true);
            if (!Cmp.Equals(Trial.Hash(selectedForgeResource), repackedNestedSha))
                throw new IOException("Injected private extracted .data hash does not match rebuilt payload.");
            var expectedForge = TopIndex(forgeFolder);
            if (expectedForge.Count != originalForge.Count ||
                expectedForge.Keys.Except(originalForge.Keys, Cmp).Any())
                throw new IOException("Forge resource count or filenames changed before writing.");
            var changedForgeInput = originalForge.Keys
                .Where(name => !Cmp.Equals(originalForge[name], expectedForge[name])).ToArray();
            // A byte-identical nested rebuild is fine. Otherwise ONLY the selected
            // extracted resource may change before the outer serializer runs.
            if (changedForgeInput.Length > 1 ||
                changedForgeInput.Any(name => !Cmp.Equals(name, selectedName)))
                throw new IOException("Unexpected Forge resources changed before repacking.");
            report["controlledChangeCount"] = changedForgeInput.Length;
            report["controlledChangedEntry"] = selectedName;
            Console.WriteLine($"[{Now()}] Controlled private Forge replacement prepared; changes {changedForgeInput.Length} resource.");

            report["stage"] = "PREFLIGHT_AND_REPACK_FORGE";
            object preflight = Trial.PreflightForgeEntries(dll, gameType, game, forgeFolder, out int failures);
            report["forgeEntryPreflight"] = preflight;
            if (failures > 0) throw new IOException($"Forge entry preflight failed on {failures} resources.");
            string rebuiltRoot = Path.Combine(sandbox, "RebuiltForge");
            Directory.CreateDirectory(rebuiltRoot);
            string rebuiltForge = Path.Combine(rebuiltRoot, forgeName);
            if (File.Exists(rebuiltForge)) throw new IOException("Refusing to overwrite Forge output.");
            var forgeWriter = Activator.CreateInstance(forgeType)!;
            if (Trial.Invoke(forgeWriter, "Serialize", forgeWriteTypes, forgeFolder, rebuiltForge, game) is not Task forgeTask)
                throw new IOException("Forge serializer did not return Task.");
            forgeTask.GetAwaiter().GetResult();
            if (!File.Exists(rebuiltForge) || new FileInfo(rebuiltForge).Length < 1024)
                throw new IOException("Forge Serialize returned without writing a substantial new Forge.");
            report["rebuiltForgeSha256"] = Trial.Hash(rebuiltForge);
            report["rebuiltForgeSizeBytes"] = new FileInfo(rebuiltForge).Length;
            Console.WriteLine($"[{Now()}] Rebuilt Forge written to PRIVATE new output.");

            report["stage"] = "EXTRACT_AND_VERIFY_COMBINED_FORGE";
            string verifyInput = Path.Combine(sandbox, "CombinedReadbackInput");
            string verifyWork = Path.Combine(sandbox, "CombinedReadbackWork");
            Directory.CreateDirectory(verifyInput);
            Directory.CreateDirectory(verifyWork);
            string verifyForge = Path.Combine(verifyInput, forgeName);
            Trial.CopyOnce(rebuiltForge, verifyForge);
            var freshForge = Activator.CreateInstance(forgeType)!;
            Trial.Invoke(freshForge, "Deserialize", forgeReadTypes,
                verifyWork + Path.DirectorySeparatorChar, verifyForge, game, false, false, false);
            var forgeReadbackFolder = Path.Combine(verifyWork, forgeName);
            report["readbackForgeStableResourceCount"] = Trial.WaitForStableForgeContents(forgeReadbackFolder);
            var finalForgeResources = TopIndex(forgeReadbackFolder);
            report["readbackForgeResourceCount"] = finalForgeResources.Count;
            var forgeDiff = Compare(expectedForge, finalForgeResources);
            report["forgeMissingLogicalNames"] = forgeDiff.missing;
            report["forgeExtraLogicalNames"] = forgeDiff.extra;
            report["forgeChangedLogicalNames"] = forgeDiff.changed;
            RequireNoDifferences(forgeDiff, expectedForge.Count, finalForgeResources.Count,
                "Two-level Forge readback");
            report["outerForgeVerified"] = true;
            Console.WriteLine($"[{Now()}] Outer Forge verification PASS ({finalForgeResources.Count} resources).");

            report["stage"] = "VERIFY_REINSERTED_DATA_INTERNAL_CONTENT";
            // Ordinals can be renumbered; select exactly one resource by
            // BOTH logical basename AND expected rebuilt binary hash.
            string logicalSelectedName = Logical(selectedName);
            var potential = finalForgeResources
                .Where(p => Cmp.Equals(Logical(p.Key), logicalSelectedName)
                    && Cmp.Equals(p.Value, repackedNestedSha)).ToArray();
            var expectedMultiplicity = expectedForge.Count(p => Cmp.Equals(Logical(p.Key), logicalSelectedName)
                && Cmp.Equals(p.Value, repackedNestedSha));
            if (potential.Length != expectedMultiplicity || potential.Length < 1)
                throw new IOException("Rebuilt nested payload absent or duplicated in rebuilt Forge.");
            // If there are genuinely identical duplicate payloads, any matching
            // copy is equivalent for content verification, but multiplicity is
            // still verified above.
            string finalNestedResource = Path.Combine(forgeReadbackFolder, potential[0].Key);
            // Independently rehash the .data bytes recovered from the final Forge;
            // do not treat a mere resource count or basename as proof of identity.
            string finalNestedBinarySha = Trial.Hash(finalNestedResource);
            bool finalNestedBinaryExact = Cmp.Equals(finalNestedBinarySha, repackedNestedSha);
            report["finalNestedBinarySha256"] = finalNestedBinarySha;
            report["finalNestedBinaryMatchesStandaloneRepack"] = finalNestedBinaryExact;
            if (!finalNestedBinaryExact)
                throw new IOException("Reinserted nested .data bytes differ from the standalone verified rebuild.");
            string finalNestedInputRoot = Path.Combine(sandbox, "FinalNestedReadback", "Input");
            Directory.CreateDirectory(finalNestedInputRoot);
            string finalNestedPath = Path.Combine(finalNestedInputRoot, selectedName);
            Trial.CopyOnce(finalNestedResource, finalNestedPath);
            string finalNestedExtraction = Path.Combine(finalNestedInputRoot, "Extracted", selectedName);
            var finalNestedOutcome = NestedTrial.UnpackData(dataType, game, finalNestedPath, finalNestedExtraction);
            if (finalNestedOutcome.failed)
                throw new IOException("Two-level nested readback failed: " + finalNestedOutcome.message);
            var finalNestedEntries = NestedTrial.Index(finalNestedExtraction);
            report["finalNestedReadbackResourceCount"] = finalNestedEntries.Count;
            var nestedDiff = CompareNested(expectedInner, finalNestedEntries);
            bool strictLabelsMatch = expectedInner.Count == finalNestedEntries.Count &&
                nestedDiff.missing.Length == 0 && nestedDiff.extra.Length == 0 && nestedDiff.changed.Length == 0;
            // Preserve *raw* name discrepancies for auditability even when they
            // turn out to be display aliases for the same underlying file type.
            report["nestedRawMissingLabels"] = nestedDiff.missing;
            report["nestedRawExtraLabels"] = nestedDiff.extra;
            report["nestedRawChangedLabels"] = nestedDiff.changed;
            report["nestedStrictLabelsMatch"] = strictLabelsMatch;
            bool contentHashesMatch = SameNestedHashMultiset(expectedInner, finalNestedEntries);
            bool stemsAndContentMatch = SameNestedStemsAndHashes(expectedInner, finalNestedEntries);
            report["nestedPayloadHashMultisetMatched"] = contentHashesMatch;
            report["nestedStemsAndPayloadHashesMatched"] = stemsAndContentMatch;
            if (!contentHashesMatch || !stemsAndContentMatch)
                throw new IOException("Two-level nested readback changed payload bytes or resource stems; " +
                    "a type-label alias cannot explain the mismatch.");
            // Additional assertion: the specifically edited internal payload must
            // survive BOTH serializers. Name type suffixes may be resolved differently.
            string selectedStem = NestedStemWithoutTypeLabel(mutationKey);
            int expectedEditedCount = expectedInner.Count(kv =>
                Cmp.Equals(NestedStemWithoutTypeLabel(kv.Key), selectedStem) &&
                Cmp.Equals(kv.Value, mutatedObjectHash));
            int finalEditedCount = finalNestedEntries.Count(kv =>
                Cmp.Equals(NestedStemWithoutTypeLabel(kv.Key), selectedStem) &&
                Cmp.Equals(kv.Value, mutatedObjectHash));
            bool exactMutationSurvived = expectedEditedCount > 0 &&
                expectedEditedCount == finalEditedCount;
            report["finalEditedPayloadCountExpected"] = expectedEditedCount;
            report["finalEditedPayloadCountObserved"] = finalEditedCount;
            report["finalEditedPayloadVerified"] = exactMutationSurvived;
            if (!exactMutationSurvived)
                throw new IOException("The intentionally changed inner payload hash did not survive both repacks.");
            if (!strictLabelsMatch)
            {
                // This fallback is allowed ONLY because the final .data is
                // BYTE-IDENTICAL to the independently verified standalone .data.
                // It cannot hide a modified, substituted, or omitted data file.
                if (!finalNestedBinaryExact || !report.ContainsKey("standaloneNestedVerified"))
                    throw new IOException("Cannot accept nested type-label aliases without independently verified bytes.");
                report["nestedVerificationMode"] = "EXACT_DATA_BINARY_AND_RESOURCE_STEM_SHA256_MULTISET_TYPE_LABEL_ALIAS";
                report["nestedTypeNameAliasesAccepted"] = true;
                Console.WriteLine($"[{Now()}] Nested type-name alias accepted: binary identical and " +
                    $"all {finalNestedEntries.Count} resource stems and payload hashes match.");
            }
            else
            {
                report["nestedVerificationMode"] = "STRICT_LOGICAL_NAME_AND_SHA256_MULTISET";
                report["nestedTypeNameAliasesAccepted"] = false;
            }
            report["nestedMissingLogicalNames"] = Array.Empty<string>();
            report["nestedExtraLogicalNames"] = Array.Empty<string>();
            report["nestedChangedLogicalNames"] = Array.Empty<string>();
            report["finalNestedVerified"] = true;
            report["stage"] = "EXPORT_VERIFIED_STAGED_FORGE";
            if (!Cmp.Equals(Trial.Hash(payloadFile), payloadHash))
                throw new IOException("External replacement source changed during staging.");
            // Re-check the source archive before exporting any verified output.
            if (!Cmp.Equals(Trial.Hash(source), originalSourceHash))
                throw new IOException("Original game Forge changed during trial; refusing staging export.");
            Directory.CreateDirectory(outputDirectory);
            string stagedForge = Path.Combine(outputDirectory, forgeName);
            if (File.Exists(stagedForge))
                throw new IOException("Refusing to overwrite existing staged Forge: " + stagedForge);
            File.Copy(rebuiltForge, stagedForge);
            string stagedHash = Trial.Hash(stagedForge);
            if (!Cmp.Equals(stagedHash, Trial.Hash(rebuiltForge)))
                throw new IOException("Staged Forge hash differs from verified private Forge.");
            report["stagedForge"] = stagedForge;
            report["stagedForgeSha256"] = stagedHash;
            report["stage"] = "PASS";
            report["verified"] = true;
            code = 0;
            Console.WriteLine("STAGED REAL REPLACEMENT PASS (verified private copy; NOT game validated or installed)");
        }
        catch (Exception ex)
        {
            report["stageFailed"] = report.TryGetValue("stage", out var stage) ? stage : "UNKNOWN";
            report["error"] = ex.ToString();
            report["verified"] = false;
            Console.Error.WriteLine(ex);
        }
        finally
        {
            Environment.CurrentDirectory = oldCwd;
            Environment.SetEnvironmentVariable("TEMP", oldTemp);
            Environment.SetEnvironmentVariable("TMP", oldTmp);
            if (originalSourceHash.Length > 0 && File.Exists(source))
            {
                try
                {
                    bool intact = Cmp.Equals(Trial.Hash(source), originalSourceHash);
                    report["originalSourceUnchanged"] = intact;
                    if (!intact) { report["verified"] = false; code = 8; }
                }
                catch (Exception ex)
                {
                    report["sourceHashCheckError"] = ex.Message;
                    report["verified"] = false;
                    code = 9;
                }
            }
            report["safety"] = "PRIVATE TEMP REPACK; VERIFIED OUTPUT EXPORTED OUTSIDE GAME; NO GAME DEPLOYMENT";
            try
            {
                string json = JsonSerializer.Serialize(report, JsonOpt);
                if (!string.IsNullOrEmpty(sandbox) && Directory.Exists(sandbox))
                    File.WriteAllText(Path.Combine(sandbox, "STAGE_RESULT.json"), json);
                if (reportCopy != null)
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(reportCopy)!);
                    File.WriteAllText(reportCopy, json);
                }
                Console.WriteLine("Report: " + (reportCopy ?? "(none)"));
            }
            catch (Exception ex) { Console.Error.WriteLine("Unable to save JSON report: " + ex); code = 9; }
        }
        return code;
    }
}
