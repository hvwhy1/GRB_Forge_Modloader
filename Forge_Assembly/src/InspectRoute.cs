using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.Json;

// On-disk read-only inventory, extracted only into a temporary private copy.
internal static class InspectRoute
{
    private static readonly StringComparer Cmp = StringComparer.OrdinalIgnoreCase;
    public static int Run(string[] args)
    {
        string source = "", sandbox = "", sourceHash = "";
        string? reportFile = null;
        var report = new Dictionary<string, object?>
        {
            ["mode"] = "READ_ONLY_ROUTE_INVENTORY",
            ["verified"] = false,
            ["gameModified"] = false
        };
        string cwd = Environment.CurrentDirectory;
        string? oldTmp = Environment.GetEnvironmentVariable("TEMP");
        string? oldTmp2 = Environment.GetEnvironmentVariable("TMP");
        int exitCode = 1;
        try
        {
            if (args.Length < 4) throw new ArgumentException(
                "Usage: inspect <toolkit-folder> <source.forge> <report.json> [data filename or AUTO]");
            string toolkit = Path.GetFullPath(args[1].Trim('"'));
            source = Path.GetFullPath(args[2].Trim('"'));
            reportFile = Path.GetFullPath(args[3].Trim('"'));
            string selector = args.Length > 4 ? args[4].Trim().Trim('"') : "AUTO";
            if (!File.Exists(source) || !source.EndsWith(".forge", StringComparison.OrdinalIgnoreCase) ||
                new FileInfo(source).Length > 128L * 1024 * 1024)
                throw new IOException("Select existing Forge (preview limit: 128 MiB).");
            if (!File.Exists(Path.Combine(toolkit, "AnvilToolkit.dll")) ||
                !Directory.Exists(Path.Combine(toolkit, "Libs")))
                throw new IOException("AnvilToolkit.dll and Libs are required.");
            if (Trial.Inside(Path.GetDirectoryName(source)!, reportFile) || Trial.Inside(toolkit, reportFile))
                throw new IOException("Report must be outside game/toolkit.");
            if (selector.Contains('/') || selector.Contains('\\') ||
                (!selector.Equals("AUTO", StringComparison.OrdinalIgnoreCase) && !selector.EndsWith(".data", StringComparison.OrdinalIgnoreCase)))
                throw new IOException("Selector must be AUTO or a single .data filename.");
            string tmp = Path.Combine(Path.GetTempPath(), "DEEF_AnvilBridge_Trials");
            Trial.EnsureScratchSpace(source, tmp);
            if (Trial.Inside(Path.GetDirectoryName(source)!, tmp)) throw new IOException("Scratch cannot be inside game.");
            sandbox = Path.Combine(tmp, DateTime.UtcNow.ToString("yyyyMMdd_HHmmss") + "_inspect_" + Guid.NewGuid().ToString("N")[..8]);
            string inp = Path.Combine(sandbox, "Input"), work = Path.Combine(sandbox, "Work"), scratch = Path.Combine(sandbox, "Scratch");
            Directory.CreateDirectory(inp); Directory.CreateDirectory(work); Directory.CreateDirectory(scratch);
            sourceHash = Trial.Hash(source);
            string local = Path.Combine(inp, Path.GetFileName(source));
            Trial.CopyOnce(source, local);
            if (!Cmp.Equals(sourceHash, Trial.Hash(local))) throw new IOException("Working copy checksum mismatch.");
            Environment.CurrentDirectory = sandbox;
            Environment.SetEnvironmentVariable("TEMP", scratch);
            Environment.SetEnvironmentVariable("TMP", scratch);
            var dll = Trial.LoadToolkit(toolkit);
            Type storage = dll.GetType("AnvilToolkit.Utils.DataStorage", true)!;
            var activeGame = storage.GetField("ActiveGame", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic)!;
            Type gameType = activeGame.FieldType;
            object game = Enum.Parse(gameType, "GhostReconBreakpoint");
            Trial.SetToolkitGameContext(dll, gameType, game);
            Trial.ConfigureToolkitOodle(dll, toolkit, game, verifyResolver: true);
            Type forgeType = dll.GetType("AnvilToolkit.FileTypes.AnvilNext.Containers.ForgeFile", true)!;
            object forge = Activator.CreateInstance(forgeType)!;
            Trial.Invoke(forge, "Deserialize", new[] { typeof(string), typeof(string), gameType, typeof(bool), typeof(bool), typeof(bool) },
                work + Path.DirectorySeparatorChar, local, game, false, false, false);
            string forgeDir = Path.Combine(work, Path.GetFileName(source));
            // The ForgeFile.Deserialize method can return before its writer threads
            // finish; previous versions raced a partially extracted .data file.
            int stableCount = Trial.WaitForStableForgeContents(forgeDir);
            report["extractedForgeStableResourceCount"] = stableCount;
            report["extractionStabilityBarrierPassed"] = true;
            var all = Directory.GetFiles(forgeDir, "*", SearchOption.TopDirectoryOnly).Select(Path.GetFileName)
                .Where(s => !string.IsNullOrWhiteSpace(s)).Select(s => s!).OrderBy(s => s, Cmp).ToArray();
            var data = all.Where(n => n.EndsWith(".data", StringComparison.OrdinalIgnoreCase)).ToArray();
            report["sourceForge"] = source;
            report["forgeEntries"] = all;
            report["nestedDataEntries"] = data;
            report["forgeCount"] = all.Length;
            if (!selector.Equals("AUTO", StringComparison.OrdinalIgnoreCase))
            {
                string Logical(string name) { int i = name.IndexOf("_-_", StringComparison.Ordinal);
                    return i > 0 && name[..i].All(char.IsDigit) ? name[(i + 3)..] : name; }
                var matches = data.Where(n => Cmp.Equals(n, selector) || Cmp.Equals(Logical(n), selector)).ToArray();
                if (matches.Length != 1) throw new IOException(".data selector matched " + matches.Length + " resources (expected exactly one).");
                string nested = matches[0];
                string nestedInput = Path.Combine(forgeDir, nested);
                string outRoot = Path.Combine(sandbox, "Nested", "Input");
                Directory.CreateDirectory(outRoot);
                string copied = Path.Combine(outRoot, nested);
                string stableSourceSha256 = Trial.Hash(nestedInput);
                Trial.CopyOnce(nestedInput, copied);
                if (!Cmp.Equals(stableSourceSha256, Trial.Hash(copied)))
                    throw new IOException("Nested data was not copied faithfully after the Forge stability barrier.");
                report["selectedNestedDataSourceSha256"] = stableSourceSha256;
                string extracted = Path.Combine(outRoot, "Extracted", nested);
                Type dataType = dll.GetType("AnvilToolkit.FileTypes.AnvilNext.Containers.DataFile", true)!;
                report["nestedPreparation"] = Trial.PrepareNestedInspection(
                    dataType, game, forgeDir, nested, sandbox);
                // Record the selected container before invoking third-party code,
                // so a failed inspection can be identified without a stack-trace screenshot.
                report["selectedNestedData"] = nested;
                report["selectedNestedDataSizeBytes"] = new FileInfo(copied).Length;
                report["selectedNestedDataSha256"] = Trial.Hash(copied);
                bool failed;
                string message;
                try
                {
                    (failed, message) = NestedTrial.UnpackData(dataType, game, copied, extracted);
                }
                catch (Exception nestedException)
                {
                    report["failureKind"] = "NESTED_DATA_DESERIALIZE_FAILED";
                    report["nestedDeserializeException"] = nestedException.ToString();
                    throw new IOException("AnvilToolkit threw during extraction of '" + nested +
                        "'. No replacement was staged. Try a different .data resource. " +
                        "For the previously verified WorldMap sample use " +
                        "5_-_SHD-SPE-HOU-SnowTriplanar.data. Cause: " + nestedException.Message, nestedException);
                }
                report["nestedDeserializeResult"] = new { hasFailed = failed, message };
                if (failed)
                {
                    report["failureKind"] = "NESTED_DATA_DESERIALIZE_FAILED";
                    throw new IOException("AnvilToolkit could not inspect selected .data resource '" + nested +
                        "' after the extraction stability barrier. DataFile.Deserialize returned: " + message +
                        ". No replacement was staged. This may be a toolkit limitation for this resource; " +
                        "attach LAST_INVENTORY.json and the Output log for diagnosis.");
                }
                var inner = NestedTrial.Index(extracted);
                report["nestedObjects"] = inner.Keys.OrderBy(x => x, Cmp).ToArray();
                report["nestedObjectHashes"] = inner;
            }
            report["verified"] = true;
            exitCode = 0;
            Console.WriteLine("INVENTORY PASS: " + all.Length + " Forge entries, " + data.Length + " nested .data containers.");
        }
        catch (Exception ex) { report["error"] = ex.ToString(); Console.Error.WriteLine(ex); }
        finally
        {
            Environment.CurrentDirectory = cwd;
            Environment.SetEnvironmentVariable("TEMP", oldTmp);
            Environment.SetEnvironmentVariable("TMP", oldTmp2);
            if (sourceHash.Length > 0 && File.Exists(source))
            {
                try
                {
                    bool intact = Cmp.Equals(sourceHash, Trial.Hash(source));
                    report["originalSourceUnchanged"] = intact;
                    if (!intact) { report["verified"] = false; exitCode = 1; }
                }
                catch (Exception e) { report["sourceIntegrityError"] = e.Message; exitCode = 1; }
            }
            report["safety"] = "PRIVATE READ-ONLY INVENTORY; NO GAME DEPLOYMENT";
            try
            {
                if (reportFile != null) { Directory.CreateDirectory(Path.GetDirectoryName(reportFile)!);
                    File.WriteAllText(reportFile, JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }));
                    Console.WriteLine("Report: " + reportFile); }
            }
            catch (Exception e) { Console.Error.WriteLine("Could not save inventory: " + e); exitCode = 1; }
        }
        return exitCode;
    }
}
