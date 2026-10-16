using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;

// Report differences in AnvilToolkit's regenerated PrefetchInfo metadata.
// This utility does not change any files or relax payload verification.
internal static class ForgePrefetchAudit
{
    private const long MaxDetailedBytes = 32L * 1024 * 1024;
    private static string Digest(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

    private static string HexContext(byte[] bytes, long center, int radius = 16)
    {
        if (bytes.Length == 0) return "";
        long start = Math.Max(0, Math.Min(center - radius, bytes.LongLength - 1));
        long length = Math.Min(2L * radius + 1, bytes.LongLength - start);
        return Convert.ToHexString(bytes.AsSpan((int)start, (int)length)).ToLowerInvariant();
    }

    internal static Dictionary<string, object?> Analyze(
        Assembly dll, Type gameType, object game, string oldPrefetch, string newPrefetch)
    {
        var result = new Dictionary<string, object?>
        {
            ["schema"] = "DEEF.prefetch_metadata_audit.1",
            ["mode"] = "READ_ONLY_COMPARISON_OF_PRIVATE_EXTRACTED_FILES",
            ["eligibleForDeployment"] = false,
            ["sourceModified"] = false,
            ["originalExtractedPrefetch"] = oldPrefetch,
            ["rebuiltExtractedPrefetch"] = newPrefetch,
            ["status"] = "UNDETERMINED"
        };
        try
        {
            if (!File.Exists(oldPrefetch) || !File.Exists(newPrefetch))
                throw new FileNotFoundException("Original or rebuilt extracted prefetch metadata is missing.");
            if (!oldPrefetch.EndsWith(".PrefetchInfo", StringComparison.OrdinalIgnoreCase)
                || !newPrefetch.EndsWith(".PrefetchInfo", StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("Only extracted .PrefetchInfo metadata files may be audited.");
            var oldSize = new FileInfo(oldPrefetch).Length;
            var newSize = new FileInfo(newPrefetch).Length;
            result["originalBytes"] = oldSize;
            result["rebuiltBytes"] = newSize;
            result["originalSha256"] = Trial.Hash(oldPrefetch);
            result["rebuiltSha256"] = Trial.Hash(newPrefetch);
            var identical = oldSize == newSize && Equals(result["originalSha256"], result["rebuiltSha256"]);
            result["byteIdentical"] = identical;
            if (oldSize > MaxDetailedBytes || newSize > MaxDetailedBytes)
            {
                result["status"] = identical ? "IDENTICAL" : "DIFFERENT_TOO_LARGE_FOR_IN_MEMORY_DETAIL";
                result["note"] = "Audits above 32 MiB remain hash-only; no metadata validation performed.";
                return result;
            }
            var before = File.ReadAllBytes(oldPrefetch);
            var after = File.ReadAllBytes(newPrefetch);
            int shared = Math.Min(before.Length, after.Length);
            int prefix = 0;
            while (prefix < shared && before[prefix] == after[prefix]) prefix++;
            int suffix = 0;
            while (suffix < shared - prefix && before[before.Length - 1 - suffix] == after[after.Length - 1 - suffix]) suffix++;
            result["commonPrefixBytes"] = prefix;
            result["commonSuffixBytes"] = suffix;
            result["firstDifferenceOffset"] = identical ? null : prefix;
            if (!identical)
            {
                result["originalAtFirstDifferenceHex"] = HexContext(before, prefix);
                result["rebuiltAtFirstDifferenceHex"] = HexContext(after, prefix);
                if (oldSize == newSize)
                {
                    long mismatches = 0;
                    for (int i = 0; i < before.Length; i++) if (before[i] != after[i]) mismatches++;
                    result["differentBytePositions"] = mismatches;
                }
            }
            // Diagnostic only: call PrefetchingFileInfos on the extracted *copies*
            // to see whether the toolkit independently transforms metadata on reserialization.
            // This DOES NOT prove the transformed metadata is semantically equivalent.
            try
            {
                var prefetchType = dll.GetTypes().FirstOrDefault(t => t.Name == "PrefetchingFileInfos")
                    ?? throw new MissingMemberException("PrefetchingFileInfos type not found");
                var ctor = prefetchType.GetConstructor(new[] { typeof(string), gameType })
                    ?? throw new MissingMethodException("PrefetchingFileInfos(string, Game)");
                var serialize = prefetchType.GetMethods(BindingFlags.Public | BindingFlags.Instance)
                    .FirstOrDefault(m => m.Name == "Serialize" && m.ReturnType == typeof(byte[])
                        && m.GetParameters().Length == 1 && m.GetParameters()[0].ParameterType == gameType)
                    ?? throw new MissingMethodException("PrefetchingFileInfos.Serialize(Game) => byte[]");
                byte[] Convert(string path)
                {
                    object obj = ctor.Invoke(new object[] { path, game });
                    return (byte[])(serialize.Invoke(obj, new[] { game })
                        ?? throw new IOException("Prefetch serializer returned null"));
                }
                var fromOriginal = Convert(oldPrefetch);
                var fromRebuilt = Convert(newPrefetch);
                result["toolkitReserialization"] = new
                {
                    attempted = true,
                    originalParsedReserializedBytes = fromOriginal.LongLength,
                    originalParsedReserializedSha256 = Digest(fromOriginal),
                    rebuiltParsedReserializedBytes = fromRebuilt.LongLength,
                    rebuiltParsedReserializedSha256 = Digest(fromRebuilt),
                    originalParsesAndReserializesToRebuiltBytes = fromOriginal.SequenceEqual(after),
                    rebuiltPrefetchIsStableUnderReserialization = fromRebuilt.SequenceEqual(after),
                    originalPrefetchIsStableUnderReserialization = fromOriginal.SequenceEqual(before)
                };
            }
            catch (Exception ex)
            {
                var underlying = ex is TargetInvocationException tie && tie.InnerException != null ? tie.InnerException : ex;
                result["toolkitReserialization"] = new { attempted = true, failed = true,
                    error = underlying.GetType().Name + ": " + underlying.Message };
            }
            result["status"] = identical ? "IDENTICAL" : "PREFETCH_BYTES_DIFFER_UNVERIFIED";
            result["note"] = "Prefetch metadata byte differences remain a hard verification failure. "
                + "A repeatable transformation is not proof of in-game compatibility.";
        }
        catch (Exception ex)
        {
            result["status"] = "AUDIT_FAILED";
            result["error"] = ex.GetType().Name + ": " + ex.Message;
        }
        return result;
    }

    internal static Dictionary<string, object?> AnalyzeFolders(
        Assembly dll, Type gameType, object game, string oldFolder, string newFolder)
    {
        var left = Directory.GetFiles(oldFolder, "*.PrefetchInfo", SearchOption.TopDirectoryOnly);
        var right = Directory.GetFiles(newFolder, "*.PrefetchInfo", SearchOption.TopDirectoryOnly);
        if (left.Length != 1 || right.Length != 1)
            return new Dictionary<string, object?> {
                ["status"] = "PREFETCH_COUNT_NOT_ONE", ["originalMetadataFiles"] = left.Length,
                ["rebuiltMetadataFiles"] = right.Length, ["eligibleForDeployment"] = false };
        return Analyze(dll, gameType, game, left[0], right[0]);
    }

    internal static int Run(string[] args)
    {
        try
        {
            if (args.Length != 5) throw new ArgumentException("prefetch-audit <ATK folder> <extracted original .PrefetchInfo> <extracted rebuilt .PrefetchInfo> <output json>");
            string toolkit = Path.GetFullPath(args[1]);
            string oldFile = Path.GetFullPath(args[2]);
            string newFile = Path.GetFullPath(args[3]);
            string output = Path.GetFullPath(args[4]);
            if (!output.EndsWith(".json", StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("Audit output must be .json");
            if (string.Equals(output, oldFile, StringComparison.OrdinalIgnoreCase)
                || string.Equals(output, newFile, StringComparison.OrdinalIgnoreCase)
                || Trial.Inside(toolkit, output))
                throw new ArgumentException("Audit output may not overwrite inputs or toolkit files.");
            var dll = Trial.LoadToolkit(toolkit);
            var gameType = dll.GetType("AnvilToolkit.Utils.Game", true)!;
            var game = Enum.Parse(gameType, "GhostReconBreakpoint", true);
            Trial.SetToolkitGameContext(dll, gameType, game);
            var result = Analyze(dll, gameType, game, oldFile, newFile);
            Directory.CreateDirectory(Path.GetDirectoryName(output)!);
            File.WriteAllText(output, JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
            Console.WriteLine("Prefetch metadata audit: " + result["status"]);
            Console.WriteLine("Result: " + output);
            Console.WriteLine("Read-only. No game or extracted metadata files modified.");
            return (string?)result["status"] == "AUDIT_FAILED" ? 1 : 0;
        }
        catch (Exception ex) { Console.Error.WriteLine("Prefetch audit failed: " + ex); return 1; }
    }
}
