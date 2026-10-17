using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json;

// v0.12.3 diagnostic: test the INNER .data serializer from a copied Forge entry.
// No live installation, no editing of the original Forge, and NO changed assets.
internal static class NestedTrial
{
    private static readonly StringComparer Cmp = StringComparer.OrdinalIgnoreCase;
    private static readonly JsonSerializerOptions JsonOpt = new() { WriteIndented = true };
    private static string Time() => DateTime.Now.ToString("HH:mm:ss");

    private static object NewUnpackedDataFile(Type dataType, object game, string extractFolder)
    {
        // Unlike DataFile(path,game,batch), DataFile(Game) does NOT launch any
        // asynchronous background extraction. Explicitly configure the private
        // ExtractFolder property, then invoke the synchronous Deserialize(path).
        var data = Activator.CreateInstance(dataType, new[] { game })
            ?? throw new IOException("DataFile(Game) constructor returned null");
        var prop = dataType.GetProperty("ExtractFolder", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
            ?? throw new MissingMemberException(dataType.FullName, "ExtractFolder");
        if (prop.GetSetMethod(nonPublic: true) != null) prop.SetValue(data, extractFolder);
        else
        {
            var backing = dataType.GetField("<ExtractFolder>k__BackingField", BindingFlags.NonPublic | BindingFlags.Instance)
                ?? throw new MissingFieldException(dataType.FullName, "<ExtractFolder>k__BackingField");
            backing.SetValue(data, extractFolder);
        }
        if (!string.Equals((string?)prop.GetValue(data), extractFolder, StringComparison.OrdinalIgnoreCase))
            throw new IOException("DataFile.ExtractFolder was not configured correctly");
        dataType.GetProperty("Batch")!.SetValue(data, true);
        return data;
    }

    internal static (bool failed, string message) UnpackData(Type dataType, object game, string localFile, string extractedFolder)
    {
        var data = NewUnpackedDataFile(dataType, game, extractedFolder);
        HeadlessToolkit.InitializeHashes();
        // In this API Item1 means HasFailed, NOT 'success'.
        using var input = File.OpenRead(localFile);
        var (hasFailed, message) = Trial.TupleResult(Trial.Invoke(data, "Deserialize", new[] { typeof(Stream) }, input));
        return (hasFailed, message);
    }

    internal static Dictionary<string, string> Index(string folder)
    {
        if (!Directory.Exists(folder)) return new Dictionary<string, string>(Cmp);
        var files = Directory.EnumerateFiles(folder, "*", SearchOption.AllDirectories).ToArray();
        var map = new Dictionary<string, string>(Cmp);
        var hashes=new string[files.Length];
        if(files.Length>=128)
            Parallel.For(0,files.Length,new ParallelOptions{MaxDegreeOfParallelism=4},i=>hashes[i]=Trial.Hash(files[i]));
        else
            for(int i=0;i<files.Length;i++)hashes[i]=Trial.Hash(files[i]);
        for(int i=0;i<files.Length;i++)
        {
            var file=files[i];
            var name = Path.GetRelativePath(folder, file).Replace('\\', '/');
            if (!map.TryAdd(name, hashes[i]))
                throw new IOException("Duplicate nested extraction path: " + name);
        }
        return map;
    }

    private static string Logical(string name)
    {
        name = name.Replace('\\', '/');
        var slash = name.LastIndexOf('/');
        var prefix = slash < 0 ? "" : name[..(slash + 1)];
        var leaf = name[(slash + 1)..];
        int sep = leaf.IndexOf("_-_", StringComparison.Ordinal);
        return prefix + (sep > 0 && leaf[..sep].All(char.IsDigit) ? leaf[(sep + 3)..] : leaf);
    }

    internal static Dictionary<string, string[]> ByLogicalName(Dictionary<string, string> entries)
        => entries.GroupBy(kv => Logical(kv.Key), Cmp)
            .ToDictionary(g => g.Key,
                g => g.Select(item => item.Value).OrderBy(h => h, StringComparer.OrdinalIgnoreCase).ToArray(), Cmp);

    private static object? GetOriginalMetaData(Type dataType, Type gameType, object game, string input)
    {
        var method = dataType.GetMethod("GetMetaData", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static,
            null, new[] { typeof(BinaryReader), gameType }, null)
            ?? throw new MissingMethodException(dataType.FullName, "GetMetaData(BinaryReader, Game)");
        using var inputFile = File.Open(input, FileMode.Open, FileAccess.Read, FileShare.Read);
        using var reader = new BinaryReader(inputFile);
        try { return method.Invoke(null, new object?[] { reader, game }); }
        catch (TargetInvocationException ex) { throw new IOException("DataFile.GetMetaData failed: " + ex.InnerException, ex.InnerException); }
    }

    internal static (bool failed, string message) RepackData(Type dataType, Type gameType,
        object game, string sourceCopiedData, string innerFolder, string destination)
    {
        var metadata = GetOriginalMetaData(dataType, gameType, game, sourceCopiedData);
        var serializer = dataType.GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
            .Where(m => m.Name == "Serialize")
            .SingleOrDefault(m => {
                var p = m.GetParameters();
                return p.Length == 4 && p[0].ParameterType == typeof(Stream) &&
                    p[1].ParameterType == typeof(string) && p[2].ParameterType.Name == "MetaData" &&
                    p[3].ParameterType == typeof(bool);
            }) ?? throw new MissingMethodException(dataType.FullName, "Serialize(Stream,string,MetaData,bool)");
        var instance = Activator.CreateInstance(dataType, new[] { game })
            ?? throw new IOException("Unable to construct DataFile(Game) for serialization");
        // Destination must not be the original copied DataFile. Writer returns
        // (HasFailed,Message); it is synchronous for this particular overload.
        if (File.Exists(destination)) throw new IOException("Refusing to overwrite an existing nested trial output");
        using var output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        object? result;
        try { result = serializer.Invoke(instance, new object?[] { output, innerFolder, metadata, false }); }
        catch (TargetInvocationException ex) { throw new IOException("AnvilToolkit.DataFile.Serialize failed: " + ex.InnerException, ex.InnerException); }
        if (result == null) throw new IOException("DataFile.Serialize returned null");
        var (failed, message) = Trial.TupleResult(result);
        // AnvilToolkit disposes BiendianBinaryWriter, which may also close
        // the caller stream. Do not Flush() after invoking the serializer.
        return (failed, message);
    }

    public static int Run(string[] args)
    {
        string sandbox = "", source = "", originalHash = "", oldCwd = Environment.CurrentDirectory;
        string? reportCopy = null;
        var oldTemp = Environment.GetEnvironmentVariable("TEMP");
        var oldTmp = Environment.GetEnvironmentVariable("TMP");
        var report = new Dictionary<string, object?>();
        int status = 1;
        try
        {
            if (args.Length < 4)
                throw new ArgumentException("Usage: nested <AnvilToolkit folder> <source.forge> <report.json> [exact .data filename]");
            string toolkit = Path.GetFullPath(args[1].Trim('"'));
            source = Path.GetFullPath(args[2].Trim('"'));
            reportCopy = Path.GetFullPath(args[3].Trim('"'));
            string requested = args.Length >= 5 ? (args[4] ?? "").Trim().Trim('"').Trim() : "";
            // An empty selector or the explicit keyword AUTO means choose an
            // embedded .data automatically; never interpret it as a path.
            if (requested.Equals("auto", StringComparison.OrdinalIgnoreCase)) requested = "";
            if (!source.EndsWith(".forge", StringComparison.OrdinalIgnoreCase) || !File.Exists(source))
                throw new IOException("Select an existing .forge file for the nested trial");
            if (requested.Contains('\\') || requested.Contains('/') || requested == "." || requested == "..")
                throw new ArgumentException("The optional nested selector must be an exact .data filename, not a path. " +
                    "For automatic selection, leave the prompt empty (or type AUTO). Received: " + requested);
            if (requested.Length > 0 && !requested.EndsWith(".data", StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("The optional selector must end in .data; for automatic selection press Enter.");
            if (!reportCopy.EndsWith(".json", StringComparison.OrdinalIgnoreCase) ||
                Trial.Inside(Path.GetDirectoryName(source)!, reportCopy) || Trial.Inside(toolkit, reportCopy))
                throw new IOException("Report destination must be outside the game and toolkit folders");
            if (new FileInfo(source).Length > 128L * 1024 * 1024)
                throw new IOException("v0.12 nested test is limited to a 128 MiB Forge. Use the recommended WorldMap sample.");
            var tempRoot = Path.Combine(Path.GetTempPath(), "DEEF_AnvilBridge_Trials");
            if (Trial.Inside(Path.GetDirectoryName(source)!, tempRoot))
                throw new IOException("TEMP may not be nested within the game folder");
            Trial.EnsureScratchSpace(source, tempRoot);
            sandbox = Path.Combine(tempRoot, DateTime.UtcNow.ToString("yyyyMMdd_HHmmss") + "_nested_" + Guid.NewGuid().ToString("N")[..8]);
            string input = Path.Combine(sandbox, "Input");
            string work = Path.Combine(sandbox, "Work");
            string scratch = Path.Combine(sandbox, "Scratch");
            Directory.CreateDirectory(input); Directory.CreateDirectory(work); Directory.CreateDirectory(scratch);
            originalHash = Trial.Hash(source);
            string local = Path.Combine(input, Path.GetFileName(source));
            Trial.CopyOnce(source, local);
            if (Trial.Hash(local) != originalHash) throw new IOException("Forge working copy hash mismatch");
            report["mode"] = "NESTED_DATA_UNCHANGED_ROUND_TRIP_ON_PRIVATE_COPY";
            report["source"] = source;
            report["sourceSha256"] = originalHash;
            report["sandbox"] = sandbox;
            report["verified"] = false;
            report["gameModified"] = false;
            report["targetNestedFile"] = requested;
            Environment.SetEnvironmentVariable("TEMP", scratch);
            Environment.SetEnvironmentVariable("TMP", scratch);
            Environment.CurrentDirectory = sandbox;
            Console.WriteLine("Sandbox: " + sandbox);
            Console.WriteLine("Mode: NESTED_DATA_UNCHANGED_ROUND_TRIP_ON_PRIVATE_COPY");
            Console.WriteLine("Toolkit: " + toolkit);
            Console.WriteLine("Source Forge SHA-256: " + originalHash);

            var dll = Trial.LoadToolkit(toolkit);
            // Determine the enum from the same DataStorage.ActiveGame field that
            // the passing v0.11 Forge trial initializes. In ATK 1.3.7 the enum is
            // AnvilToolkit.Utils.Game, NOT AnvilToolkit.Game.
            var storageType = dll.GetType("AnvilToolkit.Utils.DataStorage", throwOnError: true)!;
            var activeGame = storageType.GetField("ActiveGame",
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
                ?? throw new MissingFieldException(storageType.FullName, "ActiveGame");
            var gameType = activeGame.FieldType;
            if (!gameType.IsEnum || !ReferenceEquals(gameType.Assembly, dll))
                throw new IOException("DataStorage.ActiveGame must be an enum defined by AnvilToolkit.dll");
            object game = Enum.Parse(gameType, "GhostReconBreakpoint", ignoreCase: false);
            report["gameEnumType"] = gameType.FullName;
            Console.WriteLine("AnvilToolkit game enum resolved: " + gameType.FullName);
            report["gameContext"] = Trial.SetToolkitGameContext(dll, gameType, game);
            report["oodleContext"] = Trial.ConfigureToolkitOodle(dll, toolkit, game, true);
            var forgeType = dll.GetType("AnvilToolkit.FileTypes.AnvilNext.Containers.ForgeFile", true)!;
            var forge = Activator.CreateInstance(forgeType)!;
            Trial.Invoke(forge, "Deserialize", new[] { typeof(string), typeof(string), gameType, typeof(bool), typeof(bool), typeof(bool) },
                work + Path.DirectorySeparatorChar, local, game, false, false, false);
            string forgeFolder = Path.Combine(work, Path.GetFileName(source));
            if (!Directory.Exists(forgeFolder)) throw new IOException("Forge extraction directory missing");
            var allData = Directory.EnumerateFiles(forgeFolder, "*", SearchOption.TopDirectoryOnly)
                .Where(p => p.EndsWith(".data", StringComparison.OrdinalIgnoreCase) && new FileInfo(p).Length > 0)
                .OrderBy(p => new FileInfo(p).Length).ThenBy(p => p, Cmp).ToArray();
            report["forgeDataResourcesFound"] = allData.Length;
            Console.WriteLine($"[{Time()}] Forge unpacked; {allData.Length} candidate nested .data files found");
            if (allData.Length == 0) throw new IOException("No nested .data resources to test");
            if (!string.IsNullOrEmpty(requested))
            {
                allData = allData.Where(p => Cmp.Equals(Path.GetFileName(p), requested)).ToArray();
                if (allData.Length != 1) throw new IOException("Requested .data resource not found: " + requested);
            }
            var dataType = dll.GetType("AnvilToolkit.FileTypes.AnvilNext.Containers.DataFile", true)!;
            string? selected = null, localData = null, inner = null;
            Dictionary<string,string>? originalEntries = null;
            var candidates = new List<object>();
            int attempts = 0;
            foreach (string candidate in allData)
            {
                // Scan every candidate on this bounded 128 MiB Forge, rather than
                // failing after 12 arbitrary files that may contain no nested objects.
                if (++attempts > (string.IsNullOrEmpty(requested) ? allData.Length : 1)) break;
                var name = Path.GetFileName(candidate);
                if (new FileInfo(candidate).Length > 128L * 1024 * 1024) continue;
                var trialInput = Path.Combine(sandbox, "NestedCandidates", attempts.ToString("D2"), "Input");
                Directory.CreateDirectory(trialInput);
                var copy = Path.Combine(trialInput, name);
                Trial.CopyOnce(candidate, copy);
                var extracted = Path.Combine(trialInput, "Extracted", name);
                try
                {
                    var (failed, message) = UnpackData(dataType, game, copy, extracted);
                    if (failed) throw new IOException("DataFile.Deserialize: " + message);
                    var entries = Index(extracted);
                    if (entries.Count == 0) throw new IOException("Nested .data extracted zero files");
                    selected = candidate;
                    localData = copy;
                    inner = extracted;
                    originalEntries = entries;
                    candidates.Add(new { name, outcome = "SELECTED", embeddedFiles = entries.Count });
                    break;
                }
                catch (Exception ex)
                {
                    candidates.Add(new { name, outcome = "SKIPPED", reason = ex.Message });
                    Console.WriteLine("Candidate skipped: " + name + " - " + ex.Message);
                    if (!string.IsNullOrEmpty(requested)) throw;
                }
            }
            report["candidateDiagnostics"] = candidates;
            if (selected == null || localData == null || inner == null || originalEntries == null)
                throw new IOException("No nested .data among the attempted candidates extracted any resources; inspect candidateDiagnostics");
            string selectedName = Path.GetFileName(selected);
            report["selectedForgeDataEntry"] = selectedName;
            report["nestedOriginalDataSha256"] = Trial.Hash(localData);
            report["originalNestedResourceCount"] = originalEntries.Count;
            report["originalNestedResourceSample"] = originalEntries.Keys.OrderBy(x => x, Cmp).Take(24).ToArray();
            Console.WriteLine($"[{Time()}] Selected {selectedName}: {originalEntries.Count} embedded resources");
            Console.WriteLine($"[{Time()}] Repacking selected inner .data to a NEW private output...");
            var repackedRoot = Path.Combine(sandbox, "NestedRepacked");
            Directory.CreateDirectory(repackedRoot);
            var repacked = Path.Combine(repackedRoot, selectedName);
            var (writeFailed, writeMessage) = RepackData(dataType, gameType, game, localData, inner, repacked);
            report["nestedSerializeResult"] = new { hasFailed = writeFailed, message = writeMessage };
            report["nestedRepackAttempted"] = true;
            if (writeFailed) throw new IOException("Nested DataFile.Serialize failed: " + writeMessage);
            if (!File.Exists(repacked) || new FileInfo(repacked).Length == 0)
                throw new IOException("Nested DataFile.Serialize produced no nonempty output");
            report["nestedRepackedDataSha256"] = Trial.Hash(repacked);
            report["nestedRepackedDataSizeBytes"] = new FileInfo(repacked).Length;
            // Re-extract from a fresh working copy; the parent extracted data is
            // never overwritten. All paths remain under this TEMP sandbox.
            var readbackInput = Path.Combine(sandbox, "NestedReadbackInput");
            Directory.CreateDirectory(readbackInput);
            var readbackCopy = Path.Combine(readbackInput, selectedName);
            Trial.CopyOnce(repacked, readbackCopy);
            var readbackFolder = Path.Combine(readbackInput, "Extracted", selectedName);
            var (readFailed, readMessage) = UnpackData(dataType, game, readbackCopy, readbackFolder);
            report["nestedReadbackDeserialize"] = new { hasFailed = readFailed, message = readMessage };
            if (readFailed) throw new IOException("Nested readback extraction failed: " + readMessage);
            var actualEntries = Index(readbackFolder);
            report["readbackNestedResourceCount"] = actualEntries.Count;
            var expected = ByLogicalName(originalEntries);
            var actual = ByLogicalName(actualEntries);
            var missing = expected.Keys.Except(actual.Keys, Cmp).OrderBy(x => x, Cmp).ToArray();
            var extra = actual.Keys.Except(expected.Keys, Cmp).OrderBy(x => x, Cmp).ToArray();
            var changed = expected.Where(p => actual.TryGetValue(p.Key, out var hs)
                    && !p.Value.SequenceEqual(hs, StringComparer.OrdinalIgnoreCase))
                .Select(p => p.Key).OrderBy(x => x, Cmp).ToArray();
            bool pass = originalEntries.Count > 0 && originalEntries.Count == actualEntries.Count
                && missing.Length == 0 && extra.Length == 0 && changed.Length == 0;
            report["verificationMode"] = "NESTED_LOGICAL_FILENAME_AND_SHA256_MULTISET";
            report["duplicateNestedNamesBefore"] = expected.Count(p => p.Value.Length > 1);
            report["duplicateNestedNamesAfter"] = actual.Count(p => p.Value.Length > 1);
            report["missingNestedNames"] = missing;
            report["extraNestedNames"] = extra;
            report["changedNestedNamesOrMultiplicities"] = changed;
            report["verified"] = pass;
            status = pass ? 0 : 5;
            Console.WriteLine(pass
                ? $"NESTED DATA ROUND-TRIP PASS ({actualEntries.Count} resource hashes matched; private copy only)"
                : $"NESTED DATA ROUND-TRIP FAIL: {missing.Length} missing, {extra.Length} extra, {changed.Length} altered (private copy only)");
        }
        catch (Exception ex)
        {
            report["error"] = ex.ToString();
            report["verified"] = false;
            Console.Error.WriteLine(ex);
            status = 1;
        }
        finally
        {
            Environment.CurrentDirectory = oldCwd;
            Environment.SetEnvironmentVariable("TEMP", oldTemp);
            Environment.SetEnvironmentVariable("TMP", oldTmp);
            if (!string.IsNullOrEmpty(originalHash) && File.Exists(source))
            {
                try
                {
                    bool intact = Cmp.Equals(Trial.Hash(source), originalHash);
                    report["originalSourceUnchanged"] = intact;
                    if (!intact) { report["verified"] = false; status = 8; }
                }
                catch (Exception e) { report["sourceHashCheckError"] = e.Message; report["verified"] = false; status = 9; }
            }
            report["safety"] = "PRIVATE TEMP COPIES ONLY; no live deployment or modified asset injection implemented";
            try
            {
                if (!string.IsNullOrEmpty(sandbox) && Directory.Exists(sandbox))
                    File.WriteAllText(Path.Combine(sandbox, "NESTED_DATA_TRIAL_RESULT.json"), JsonSerializer.Serialize(report, JsonOpt));
                if (reportCopy != null)
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(reportCopy)!);
                    File.WriteAllText(reportCopy, JsonSerializer.Serialize(report, JsonOpt));
                }
                Console.WriteLine("Report: " + (reportCopy ?? "(none)"));
            }
            catch (Exception ex) { Console.Error.WriteLine("Could not save diagnostic report: " + ex); status = 9; }
        }
        return status;
    }
}
