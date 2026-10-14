using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Runtime.Loader;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

// Experimental headless AnvilToolkit 1.3.7 diagnostic.
// Only paths UNDER a freshly created trial workspace are passed to ATK.
// This harness intentionally has NO deployment-to-game command.
internal static class Trial
{
    private const string ContainerNs = "AnvilToolkit.FileTypes.AnvilNext.Containers.";
    private static readonly StringComparer Comparer = StringComparer.OrdinalIgnoreCase;
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    internal static string Hash(string path)
    {
        using var stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }
    private static string Relative(string root, string path) => Path.GetRelativePath(root, path).Replace('\\', '/');
    internal static bool Inside(string root, string path)
    {
        var fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        return Path.GetFullPath(path).StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase);
    }
    private static IEnumerable<string> FindFiles(string directory)
    {
        if (!Directory.Exists(directory)) return Enumerable.Empty<string>();
        try { return Directory.EnumerateFiles(directory, "*", SearchOption.AllDirectories).ToArray(); }
        catch (Exception ex)
        {
            Console.WriteLine("WARNING: Directory enumeration failed: " + ex.Message);
            return Enumerable.Empty<string>();
        }
    }
    private static bool IsSidecar(string path)
    {
        var name = Path.GetFileName(path);
        return name.EndsWith(".bak", StringComparison.OrdinalIgnoreCase)
            || name.EndsWith(".dependency", StringComparison.OrdinalIgnoreCase)
            || name.EndsWith(".ignored", StringComparison.OrdinalIgnoreCase)
            || name.EndsWith(".xml", StringComparison.OrdinalIgnoreCase);
    }
    // ATK 1.3.7's ForgeFile.Deserialize writes direct children of
    // Work/<ForgeName>.forge. It does NOT necessarily create /Extracted/.
    // A directory named Extracted is used by the game-folder GUI workflow,
    // and some nested .data operations; both layouts are supported here.
    // Never accept arbitrary files outside the expected source-named folder.
    private static string? ExtractedKey(string relative, string sourceName)
    {
        var norm = relative.Replace('\\', '/');
        var prefix = sourceName.Replace('\\', '/') + "/";
        if (!norm.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) return null;
        var resource = norm[prefix.Length..];
        if (resource.StartsWith("Extracted/", StringComparison.OrdinalIgnoreCase))
            resource = resource["Extracted/".Length..];
        if (string.IsNullOrEmpty(resource) ||
            resource.StartsWith("Backups/", StringComparison.OrdinalIgnoreCase)) return null;
        return resource;
    }
    private static string DescribeLayout(string root, string sourceName)
    {
        var container = Path.Combine(root, sourceName);
        if (!Directory.Exists(container)) return "NO_NAMED_CONTAINER_DIRECTORY";
        var extracted = Path.Combine(container, "Extracted");
        if (Directory.Exists(extracted) && FindFiles(extracted).Any()) return "NAMED_CONTAINER_EXTRACTED_SUBFOLDER";
        if (Directory.EnumerateFiles(container, "*", SearchOption.TopDirectoryOnly).Any())
            return "DIRECT_CHILDREN_OF_NAMED_CONTAINER";
        return "NAMED_CONTAINER_EMPTY_OR_OTHER_LAYOUT";
    }
    private static Dictionary<string, string> ResourceIndex(string root, string sourceName)
    {
        var entries = new Dictionary<string, string>(Comparer);
        foreach (var file in FindFiles(root))
        {
            if (IsSidecar(file)) continue;
            var key = ExtractedKey(Relative(root, file), sourceName);
            if (string.IsNullOrEmpty(key)) continue;
            if (entries.ContainsKey(key)) throw new IOException("Duplicate extracted resource path: " + key);
            entries.Add(key, Hash(file));
        }
        return entries;
    }
    private static List<object> NonResourceArtifacts(string root, string sourceName, int max = 80)
    {
        return FindFiles(root)
            .Where(p => ExtractedKey(Relative(root, p), sourceName) == null || IsSidecar(p))
            .Take(max)
            .Select(p => (object)new { path = Relative(root, p), size = new FileInfo(p).Length })
            .ToList();
    }
    internal static Assembly LoadToolkit(string toolkit)
    {
        var dll = Path.Combine(toolkit, "AnvilToolkit.dll");
        var libs = Path.Combine(toolkit, "Libs");
        if (!File.Exists(dll) || !Directory.Exists(libs))
            throw new InvalidOperationException("Select the folder containing AnvilToolkit.dll and Libs from ATK 1.3.7.");
        Environment.SetEnvironmentVariable("PATH", libs + Path.PathSeparator + Environment.GetEnvironmentVariable("PATH"));
        AssemblyLoadContext.Default.Resolving += (_, name) =>
        {
            foreach (var root in new[] { toolkit, libs })
            {
                var candidate = Path.Combine(root, (name.Name ?? "") + ".dll");
                if (!File.Exists(candidate)) continue;
                try { return AssemblyLoadContext.Default.LoadFromAssemblyPath(candidate); } catch { }
            }
            return null;
        };
        AssemblyLoadContext.Default.ResolvingUnmanagedDll += (_, name) =>
        {
            foreach (var candidate in new[] { Path.Combine(libs, name), Path.Combine(libs, name + ".dll"), Path.Combine(toolkit, name + ".dll") })
            {
                if (File.Exists(candidate))
                {
                    try { return NativeLibrary.Load(candidate); } catch { }
                }
            }
            return IntPtr.Zero;
        };
        return AssemblyLoadContext.Default.LoadFromAssemblyPath(dll);
    }
    internal static object Invoke(object owner, string name, Type[] types, params object[] arguments)
    {
        var method = owner.GetType().GetMethod(name, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance, null, types, null);
        if (method == null) throw new MissingMethodException(owner.GetType().FullName, name + "(" + string.Join(",", types.Select(x => x.Name)) + ")");
        try { return method.Invoke(owner, arguments)!; }
        catch (TargetInvocationException e) { throw new InvalidOperationException($"AnvilToolkit.{name}: {e.InnerException}", e.InnerException); }
    }
    internal static (bool success, string message) TupleResult(object result)
    {
        var type = result.GetType();
        var success = (bool)(type.GetField("Item1")?.GetValue(result) ?? false);
        var message = type.GetField("Item2")?.GetValue(result)?.ToString() ?? "(no message)";
        return (success, message);
    }
    // The GUI sets DataStorage.ActiveGame when a game is selected. A headless
    // caller has no GUI to do that: passing Game.GhostReconBreakpoint to
    // ForgeFile.Serialize does not necessarily initialize all global lookups.
    internal static object SetToolkitGameContext(Assembly dll, Type gameType, object game)
    {
        var storage = dll.GetType("AnvilToolkit.Utils.DataStorage", throwOnError: false);
        if (storage == null)
            throw new InvalidOperationException("Cannot find AnvilToolkit.Utils.DataStorage to set the active game; refusing Forge serialization.");
        const BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
        var property = storage.GetProperty("ActiveGame", flags);
        var field = storage.GetField("ActiveGame", flags);
        object? before;
        object? after;
        string kind;
        if (property != null && property.PropertyType == gameType && property.GetSetMethod(true) != null)
        {
            before = property.GetValue(null);
            property.SetValue(null, game);
            after = property.GetValue(null);
            kind = "property";
        }
        else if (field != null && field.FieldType == gameType && !field.IsInitOnly)
        {
            before = field.GetValue(null);
            field.SetValue(null, game);
            after = field.GetValue(null);
            kind = "field";
        }
        else
            throw new InvalidOperationException("DataStorage.ActiveGame is not a writable Game field/property; refusing Forge serialization.");
        if (!Equals(game, after))
            throw new InvalidOperationException("Attempted to set DataStorage.ActiveGame, but its value did not persist.");
        return new { storage = storage.FullName, member = kind, before = before?.ToString(), after = after?.ToString() };
    }

    // Confirmed from AnvilToolkit 1.3.7's decompiled Manager and DataStorage:
    // Manager.Oodle calls OodleDllResolver.TryResolveFrom(DataStorage.OodleLocation,
    // DataStorage.OodleVersions[DataStorage.ActiveGame]). The GUI normally
    // runs from its installation directory, where default "Libs" resolves.
    // The headless bridge intentionally runs from a private TEMP sandbox, so
    // the default relative path must be made absolute without copying DLLs.
    internal static object ConfigureToolkitOodle(Assembly dll, string toolkit, object game, bool verifyResolver)
    {
        const BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
        var storage = dll.GetType("AnvilToolkit.Utils.DataStorage", true)!;
        var locationField = storage.GetField("OodleLocation", flags)
            ?? throw new MissingFieldException("AnvilToolkit.Utils.DataStorage", "OodleLocation");
        if (locationField.FieldType != typeof(string) || locationField.IsInitOnly)
            throw new InvalidOperationException("DataStorage.OodleLocation is not a writable string field.");
        var versionsField = storage.GetField("OodleVersions", flags)
            ?? throw new MissingFieldException("AnvilToolkit.Utils.DataStorage", "OodleVersions");
        if (versionsField.GetValue(null) is not IDictionary versions || !versions.Contains(game))
            throw new InvalidOperationException("OodleVersions has no mapping for GhostReconBreakpoint.");
        int version = Convert.ToInt32(versions[game]);
        // Use the already installed toolkit's own Oodle binary. No native
        // libraries are bundled, copied, downloaded, or modified by the bridge.
        var libs = Path.GetFullPath(Path.Combine(toolkit, "Libs"));
        var nativeName = $"oo2core_{version}_win64.dll";
        var nativePath = Path.Combine(libs, nativeName);
        if (!Directory.Exists(libs) || !File.Exists(nativePath))
            throw new FileNotFoundException($"AnvilToolkit requires Oodle v{version} at {nativePath}", nativePath);
        var before = locationField.GetValue(null)?.ToString();
        locationField.SetValue(null, libs);
        var after = locationField.GetValue(null)?.ToString();
        if (!string.Equals(after, libs, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("Unable to initialize AnvilToolkit's OodleLocation.");
        bool resolved = false;
        if (verifyResolver)
        {
            // Exercise the exact getter used inside Manager.Decompress.
            // A missing or unloadable DLL is reported BEFORE a repack attempt.
            var managerType = dll.GetType("AnvilToolkit.Compressions.Manager", true)!;
            var manager = Activator.CreateInstance(managerType, new object[] { false })
                ?? throw new InvalidOperationException("Cannot instantiate AnvilToolkit.Compressions.Manager.");
            var getter = managerType.GetProperty("Oodle", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
                ?? throw new MissingMemberException(managerType.FullName, "Oodle");
            try
            {
                resolved = getter.GetValue(manager) != null;
            }
            catch (TargetInvocationException ex)
            {
                throw new InvalidOperationException("Oodle resolver failed using toolkit-local library "
                    + nativePath + ": " + ex.InnerException, ex.InnerException);
            }
            if (!resolved)
                throw new InvalidOperationException("AnvilToolkit Manager.Oodle returned null after selecting the correct game.");
        }
        return new {
            before, after, game = game.ToString(), requiredOodleVersion = version,
            library = nativePath, librarySizeBytes = new FileInfo(nativePath).Length,
            librarySha256 = Hash(nativePath), resolverVerified = resolved,
            verificationSkippedInTraceOnly = !verifyResolver,
            noNativeBinariesCopied = true
        };
    }

    // ForgeFile.Serialize catches and prints the exception from its
    // Parallel.ForEach DataFile.CreateForgeEntry calls. By preflighting each
    // ordinary resource sequentially, we capture the actual stack/inner
    // exception and prevent destructive/incomplete serialization.
    internal static object PreflightForgeEntries(Assembly dll, Type gameType, object game,
        string writerDirectory, out int failedTotal)
    {
        var dataType = dll.GetType(ContainerNs + "DataFile", throwOnError: true)!;
        var method = dataType.GetMethod("CreateForgeEntry",
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static,
            binder: null, types: new[] { typeof(string), typeof(uint), gameType }, modifiers: null);
        if (method == null)
            throw new MissingMethodException(dataType.FullName, "CreateForgeEntry(string, uint, Game)");
        var files = Directory.EnumerateFiles(writerDirectory, "*", SearchOption.TopDirectoryOnly)
            .Where(p => new[] { ".data", ".rawasset" }.Contains(Path.GetExtension(p), StringComparer.OrdinalIgnoreCase))
            .OrderBy(p => p, StringComparer.OrdinalIgnoreCase).ToArray();
        var failures = new List<object>();
        failedTotal = 0;
        var succeeded = 0;
        uint timestamp = checked((uint)DateTimeOffset.UtcNow.ToUnixTimeSeconds());
        foreach (var file in files)
        {
            try
            {
                var entry = method.Invoke(null, new object[] { file, timestamp, game });
                if (entry == null) throw new IOException("CreateForgeEntry returned null");
                succeeded++;
            }
            catch (Exception ex)
            {
                failedTotal++;
                var actual = ex is TargetInvocationException invocation ? (invocation.InnerException ?? ex) : ex;
                var text = actual.ToString();
                if (failures.Count < 16)
                    failures.Add(new { file = Path.GetFileName(file), type = actual.GetType().FullName,
                        message = actual.Message, stackTrace = text });
                Console.WriteLine($"PRE-FLIGHT FAIL [{Path.GetFileName(file)}]: {actual.GetType().Name}: {actual.Message}");
                if (failedTotal == 1) Console.WriteLine("First preflight exception stack:\n" + text);
            }
        }
        Console.WriteLine($"Forge resource preflight: {succeeded}/{files.Length} passed, {failedTotal} failed.");
        return new { status = failedTotal == 0 ? "ALL_ENTRIES_READABLE" : "BLOCKED_ENTRY_READ_ERRORS",
            tested = files.Length, succeeded, failed = failedTotal, failures };
    }

    private static int CountResources(string root, string sourceName)
    {
        int count = 0;
        foreach (var file in FindFiles(root))
        {
            if (IsSidecar(file)) continue;
            if (ExtractedKey(Relative(root, file), sourceName) != null) count++;
        }
        return count;
    }
    private static void WaitForOutputs(string work, string sourceName, int maxWaitSeconds, List<string> log)
    {
        // Wait until both filenames AND lengths stop changing. Merely seeing
        // a stable file count can incorrectly accept a partial extraction.
        string? previous = null;
        int stableSeconds = 0;
        for (var elapsed = 0; elapsed <= maxWaitSeconds; elapsed += 2)
        {
            var files = FindFiles(work)
                .Where(p => !IsSidecar(p) && ExtractedKey(Relative(work, p), sourceName) != null)
                .OrderBy(p => p, StringComparer.OrdinalIgnoreCase)
                .ToArray();
            var snapshot = string.Join("\n", files.Select(p =>
                Relative(work, p) + "|" + new FileInfo(p).Length + "|" + File.GetLastWriteTimeUtc(p).Ticks));
            if (files.Length > 0 && string.Equals(previous, snapshot, StringComparison.Ordinal)) stableSeconds += 2;
            else stableSeconds = 0;
            if (elapsed == 0 || elapsed % 10 == 0 || (files.Length > 0 && stableSeconds >= 6))
                Console.WriteLine($"[{DateTime.Now:HH:mm:ss}] Extraction observation: {files.Length} resource files, elapsed={elapsed}s, stable={stableSeconds}s");
            if (files.Length > 0 && stableSeconds >= 6)
            {
                log.Add($"Found {files.Length} resources in {DescribeLayout(work, sourceName)}; stable for 6s.");
                return;
            }
            if (elapsed >= maxWaitSeconds) break;
            Thread.Sleep(2000);
            previous = snapshot;
        }
        log.Add("Observation period ended; extracted file count was " + CountResources(work, sourceName));
    }
    // Shared post-Deserialize barrier for the v0.15 GUI and staging engine.
    // AnvilToolkit 1.3.7 may return while Forge resource files are still being
    // written on background tasks. Never copy/hash them until stable; the
    // original passing v0.11 test waited for the same condition for six seconds.
    internal static int WaitForStableForgeContents(string extractedForgeDir, int timeoutSeconds = 90)
    {
        const int requiredStableSeconds = 6;
        string? previous = null;
        int stableSeconds = 0;
        var stopwatch = Stopwatch.StartNew();
        while (stopwatch.Elapsed < TimeSpan.FromSeconds(timeoutSeconds))
        {
            try
            {
                var files = Directory.Exists(extractedForgeDir)
                    ? Directory.GetFiles(extractedForgeDir, "*", SearchOption.TopDirectoryOnly)
                        .OrderBy(path => Path.GetFileName(path), StringComparer.OrdinalIgnoreCase).ToArray()
                    : Array.Empty<string>();
                var snapshot = string.Join("\n", files.Select(path => {
                    var info = new FileInfo(path);
                    return info.Name + "|" + info.Length + "|" + info.LastWriteTimeUtc.Ticks;
                }));
                var complete = files.Length > 0 && files.All(path => new FileInfo(path).Length > 0);
                if (complete && string.Equals(previous, snapshot, StringComparison.Ordinal))
                    stableSeconds++;
                else stableSeconds = 0;
                if (complete && stableSeconds >= requiredStableSeconds)
                {
                    Console.WriteLine($"Forge extraction barrier PASS: {files.Length} files stable for {stableSeconds}s in {extractedForgeDir}");
                    return files.Length;
                }
                previous = snapshot;
            }
            catch (IOException) { stableSeconds = 0; previous = null; }
            catch (UnauthorizedAccessException) { stableSeconds = 0; previous = null; }
            Thread.Sleep(1000);
        }
        throw new IOException("Forge extraction did not stabilize in " + timeoutSeconds +
            " seconds. Refusing to read or stage potentially partial .data files: " + extractedForgeDir);
    }
    // The previously successful v0.12.3-v0.14 tests tried nested containers in
    // ascending file size, and attempted one incompatible container before
    // reaching the known good SnowTriplanar data. A direct GUI inspection had
    // skipped that order and failed. Reproduce only the first preceding probe
    // (in PRIVATE copies); this may prime toolkit state, but is still treated as
    // a diagnostic, not evidence of a general fix. The requested target is never
    // silently changed to the preparation candidate.
    internal static object PrepareNestedInspection(Type dataType, object game, string forgeDirectory,
        string selectedName, string privateScratch)
    {
        var ordered = Directory.EnumerateFiles(forgeDirectory, "*.data", SearchOption.TopDirectoryOnly)
            .Where(path => new FileInfo(path).Length > 0)
            .OrderBy(path => new FileInfo(path).Length)
            .ThenBy(path => path, StringComparer.OrdinalIgnoreCase).ToArray();
        var selected = Array.FindIndex(ordered,
            path => string.Equals(Path.GetFileName(path), selectedName, StringComparison.OrdinalIgnoreCase));
        if (selected <= 0)
            return new { attempted = false, reason = "Selected target is the first eligible nested resource or is absent" };
        string precursor = ordered[0];
        string sourceName = Path.GetFileName(precursor);
        string prepInput = Path.Combine(privateScratch, "NestedPreparation", "Input");
        Directory.CreateDirectory(prepInput);
        string prepCopy = Path.Combine(prepInput, sourceName);
        CopyOnce(precursor, prepCopy);
        if (!string.Equals(Hash(precursor), Hash(prepCopy), StringComparison.OrdinalIgnoreCase))
            throw new IOException("Private preparation resource copy did not match source hash");
        string prepExtract = Path.Combine(prepInput, "Extracted", sourceName);
        try
        {
            var (failed, message) = NestedTrial.UnpackData(dataType, game, prepCopy, prepExtract);
            Console.WriteLine("Nested deserializer preparation (private): " + sourceName +
                "; HasFailed=" + failed + "; Message=" + message);
            return new { attempted = true, name = sourceName, hasFailed = failed, message };
        }
        catch (Exception exception)
        {
            Console.WriteLine("Nested deserializer preparation failed (selected target unchanged): "
                + sourceName + ": " + exception.Message);
            return new { attempted = true, name = sourceName, hasFailed = true,
                message = exception.GetType().Name + ": " + exception.Message };
        }
    }
    // Confirmed from the user's decompiled AnvilToolkit 1.3.7 ForgeFile.Serialize():
    // it calls new DirectoryInfo(folder).GetFiles("*.*") (NONRECURSIVE), and
    // filters for .data/.rawasset. LoadManifests scans the SAME folder.
    // Therefore `folder` must be Work/<ForgeName>.forge; DO NOT relocate
    // resources to an Extracted child and DO NOT pass the Work parent.
    // Only direct-child Forge layouts are supported in this trial.
    private static Dictionary<string, object> ValidateForgeWriterDirectory(
        string work, string forgeName, Dictionary<string, string> originalResources)
    {
        var forgeDirectory = Path.Combine(work, forgeName);
        if (!Directory.Exists(forgeDirectory))
            throw new IOException("Unpacked Forge writer directory does not exist: " + forgeDirectory);
        // A nested .data extraction can introduce other directories; never
        // pretend those are recognized by ForgeFile.Serialize's nonrecursive scan.
        if (Directory.EnumerateDirectories(forgeDirectory, "*", SearchOption.TopDirectoryOnly).Any())
            throw new IOException("Forge writer has nested directories; this tested writer path only accepts direct-child resources. Re-extract into a fresh sandbox.");
        if (originalResources.Keys.Any(k => k.Contains('/') || k.Contains('\\')))
            throw new IOException("Original Forge extraction contains nested resource paths, which this serializer would not traverse.");
        var actualFiles = Directory.EnumerateFiles(forgeDirectory, "*", SearchOption.TopDirectoryOnly)
            .Select(Path.GetFileName).Where(n => n != null).Select(n => n!).ToArray();
        var expectedFiles = originalResources.Keys.ToArray();
        if (actualFiles.Length != expectedFiles.Length ||
            actualFiles.Except(expectedFiles, Comparer).Any() ||
            expectedFiles.Except(actualFiles, Comparer).Any())
            throw new IOException("Forge writer folder does not contain exactly the original extracted resource files; refusing to serialize.");
        var normal = actualFiles.Count(n => n.EndsWith(".data", StringComparison.OrdinalIgnoreCase)
            || n.EndsWith(".rawasset", StringComparison.OrdinalIgnoreCase));
        if (normal == 0)
            throw new IOException("Forge writer folder contains zero .data/.rawasset resources; refusing metadata-only serialization.");
        var metadata = actualFiles.Count(n => n.EndsWith(".MetaFile", StringComparison.OrdinalIgnoreCase)
            || n.EndsWith(".PrefetchInfo", StringComparison.OrdinalIgnoreCase));
        return new Dictionary<string, object>
        {
            ["status"] = "DIRECT_CHILD_FORGE_SERIALIZER_PATH_CONFIRMED_FROM_DLL",
            ["writerDirectory"] = forgeDirectory,
            ["directRegularResources"] = normal,
            ["directMetadataResources"] = metadata,
            ["directTotalResources"] = actualFiles.Length,
            ["nestedDirectories"] = 0
        };
    }

    // During repack ATK sorts entries and LoadManifests can insert metadata
    // in a different order. The N_-_ prefix is a mutable output ordinal.
    // A logical basename is NOT necessarily unique: one Forge may include
    // multiple distinct assets called, for example, SHD-SPE-HOU-IceParallax.data.
    // Preserve multiplicity by comparing the SHA-256 multiset for each logical
    // filename. This detects missing, altered, duplicated or substituted bytes
    // while allowing an entry to be assigned a new numeric ordinal.
    private static string ForgeLogicalName(string path)
    {
        var name = Path.GetFileName(path);
        int sep = name.IndexOf("_-_", StringComparison.Ordinal);
        return sep > 0 && name[..sep].All(char.IsDigit) ? name[(sep + 3)..] : name;
    }
    private static Dictionary<string, string[]> LogicalResourceIndex(
        Dictionary<string, string> indexed, bool forge)
    {
        return indexed
            .GroupBy(entry => forge ? ForgeLogicalName(entry.Key) : entry.Key, Comparer)
            .ToDictionary(
                group => group.Key,
                group => group.Select(entry => entry.Value)
                    .OrderBy(hash => hash, StringComparer.OrdinalIgnoreCase).ToArray(),
                Comparer);
    }
    private static bool SameHashMultiset(string[] first, string[] second) =>
        first.SequenceEqual(second, StringComparer.OrdinalIgnoreCase);

    private static long ForgeOrdinal(string path)
    {
        var name = Path.GetFileName(path);
        int sep = name.IndexOf("_-_", StringComparison.Ordinal);
        return sep > 0 && long.TryParse(name[..sep], out var ordinal) ? ordinal : long.MaxValue;
    }
    private static int CountRenumberedMatchingResources(
        Dictionary<string, string> expected, Dictionary<string, string> actual)
    {
        // Pair entries ONLY when both their logical names and hashes match.
        // Repeated identical assets are paired in numeric-ordinal order.
        // A changed resource is reported separately as changed, never as
        // a harmless renumbering.
        Dictionary<string, List<string>> GroupByIdentity(Dictionary<string, string> resources)
        {
            var groups = new Dictionary<string, List<string>>(Comparer);
            foreach (var entry in resources)
            {
                var identity = ForgeLogicalName(entry.Key) + "\u001f" + entry.Value;
                if (!groups.TryGetValue(identity, out var names))
                    groups.Add(identity, names = new List<string>());
                names.Add(entry.Key);
            }
            return groups;
        }
        var source = GroupByIdentity(expected);
        var readback = GroupByIdentity(actual);
        int renumbered = 0;
        foreach (var entry in source)
        {
            if (!readback.TryGetValue(entry.Key, out var counterpart)) continue;
            var left = entry.Value.OrderBy(ForgeOrdinal).ThenBy(x => x, Comparer).ToArray();
            var right = counterpart.OrderBy(ForgeOrdinal).ThenBy(x => x, Comparer).ToArray();
            for (int i = 0; i < Math.Min(left.Length, right.Length); i++)
                if (!Comparer.Equals(left[i], right[i])) renumbered++;
        }
        return renumbered;
    }
    internal static void CopyOnce(string source, string destination)
    {
        if (File.Exists(destination)) throw new IOException("Refusing to overwrite an existing trial file: " + destination);
        File.Copy(source, destination);
    }
    internal static void EnsureScratchSpace(string source, string tempRoot)
    {
        // Four times the source size is a minimum heuristic, not a guarantee for decompressed archives.
        var drive = new DriveInfo(Path.GetPathRoot(tempRoot)!);
        var size = new FileInfo(source).Length;
        var minimum = Math.Max(512L * 1024 * 1024, checked(size * 4));
        if (drive.AvailableFreeSpace < minimum)
            throw new IOException($"Insufficient free scratch space: {drive.AvailableFreeSpace / 1048576} MiB free; require at least {minimum / 1048576} MiB. Try a smaller .data or free space.");
    }
    private static string Timestamp() => DateTime.Now.ToString("HH:mm:ss");

    public static int Run(string[] args)
    {
        var traceOnly = args[0].Equals("trace", StringComparison.OrdinalIgnoreCase);
        string sandbox = "", source = "", sourceOriginalHash = "", oldCwd = Environment.CurrentDirectory;
        var outcome = new Dictionary<string, object?>();
        var log = new List<string>();
        var events = new List<string>();
        FileSystemWatcher? watcher = null;
        string? reportCopy = null;
        int exitCode = 1;
        try
        {
            if (args.Length < 3) throw new ArgumentException("Usage: trial|trace <AnvilToolkit-dir> <source.data|source.forge> [maxMiB] [report.json] [waitSeconds] [forgeFlags]");
            var toolkit = Path.GetFullPath(args[1].Trim('"'));
            source = Path.GetFullPath(args[2].Trim('"'));
            if (!File.Exists(source)) throw new FileNotFoundException("Trial source not found", source);
            var ext = Path.GetExtension(source).ToLowerInvariant();
            if (ext != ".data" && ext != ".forge") throw new ArgumentException("Input must be .data or .forge");
            var info = new FileInfo(source);
            var maxMiB = args.Length >= 4 && long.TryParse(args[3], out var m) ? m : 2048;
            if (maxMiB < 1 || info.Length > maxMiB * 1024L * 1024L)
                throw new ArgumentException($"Source size {info.Length / 1048576} MiB exceeds maximum {maxMiB} MiB");
            if (args.Length >= 5 && !string.IsNullOrWhiteSpace(args[4])) reportCopy = Path.GetFullPath(args[4].Trim('"'));
            var waitSeconds = args.Length >= 6 && int.TryParse(args[5], out var seconds) ? Math.Clamp(seconds, 0, 180) : 24;
            // The first two Forge flags can be tested in read-only trace mode only.
            // 'stream' is kept false until its semantics have been confirmed.
            var forgeFlags = args.Length >= 7 ? args[6] : "000";
            if (forgeFlags.Length != 3 || forgeFlags.Any(c => c != '0' && c != '1') || forgeFlags[2] != '0')
                throw new ArgumentException("Forge flag pattern must be 000, 100, 010 or 110 (stream stays off).");
            if (!traceOnly && forgeFlags != "000")
                throw new ArgumentException("Nondefault Forge flags can be tested only in trace mode, not automatic repack mode.");
            var tempRoot = Path.Combine(Path.GetTempPath(), "DEEF_AnvilBridge_Trials");
            if (Inside(Path.GetDirectoryName(source)!, tempRoot))
                throw new IOException("Your TEMP directory is inside the source file's directory; move TEMP outside the game first.");
            if (reportCopy != null)
            {
                if (!string.Equals(Path.GetExtension(reportCopy), ".json", StringComparison.OrdinalIgnoreCase)
                    || string.Equals(reportCopy, source, StringComparison.OrdinalIgnoreCase)
                    || Inside(Path.GetDirectoryName(source)!, reportCopy)
                    || Inside(toolkit, reportCopy))
                    throw new ArgumentException("Diagnostic report must be a .json outside the source and AnvilToolkit directories.");
            }
            EnsureScratchSpace(source, tempRoot);
            sandbox = Path.Combine(tempRoot, DateTime.UtcNow.ToString("yyyyMMdd_HHmmss") + "_" + Guid.NewGuid().ToString("N")[..8]);
            // The binary MUST NOT be placed in the extraction directory: AnvilToolkit
            // creates Work/<sourceName>.forge as a directory. v0.3 copied the Forge
            // file to that exact pathname, causing the observed File/Directory collision.
            var input = Path.Combine(sandbox, "Input");
            var work = Path.Combine(sandbox, "Work");
            var scratch = Path.Combine(sandbox, "Scratch");
            Directory.CreateDirectory(input);
            Directory.CreateDirectory(work);
            Directory.CreateDirectory(scratch);
            if (Inside(sandbox, source)) throw new IOException("Source must not be inside the new trial sandbox.");
            var local = Path.Combine(input, Path.GetFileName(source));
            if (Inside(work, local)) throw new IOException("Input/extraction workspaces must be separate.");
            CopyOnce(source, local);
            sourceOriginalHash = Hash(source);
            if (Hash(local) != sourceOriginalHash) throw new IOException("Copy hash mismatch; trial stopped");
            outcome["source"] = source;
            outcome["workingCopy"] = local;
            outcome["extractionRoot"] = work;
            outcome["inputRoot"] = input;
            outcome["sourceSha256"] = sourceOriginalHash;
            outcome["sourceSizeBytes"] = info.Length;
            outcome["mode"] = traceOnly ? "TRACE_UNPACK_ONLY" : "ROUND_TRIP_ON_COPY";
            outcome["forgeDeserializeFlags"] = forgeFlags;
            outcome["repackAttempted"] = false;
            outcome["verified"] = false;
            outcome["sandbox"] = sandbox;
            outcome["platform"] = RuntimeInformation.OSDescription;
            Console.WriteLine("Sandbox: " + sandbox);
            Console.WriteLine("Working copy (Input): " + local);
            Console.WriteLine("Extraction root (Work): " + work);
            Console.WriteLine("Toolkit folder: " + toolkit);
            Console.WriteLine("Mode: " + outcome["mode"]);
            Console.WriteLine("Input SHA-256: " + sourceOriginalHash);
            Console.WriteLine("Time: " + Timestamp());
            // Redirect relative file writes and temporary caches into the private workspace.
            // ATK internals are undocumented; this is a best-effort boundary, NOT an OS sandbox.
            Environment.SetEnvironmentVariable("TEMP", scratch);
            Environment.SetEnvironmentVariable("TMP", scratch);
            Environment.CurrentDirectory = sandbox;
            outcome["workingDirectoryDuringToolkitCall"] = Environment.CurrentDirectory;
            log.Add("Only the private copied path is passed to ATK. No deploy command exists.");
            var dll = LoadToolkit(toolkit);
            var gameType = dll.GetType("AnvilToolkit.Utils.Game", true)!;
            var game = Enum.Parse(gameType, "GhostReconBreakpoint", true);
            // The GUI normally sets this singleton during game selection.
            // The bridge must do so explicitly before *either* Deserialize or Serialize.
            var gameContext = SetToolkitGameContext(dll, gameType, game);
            outcome["toolkitGameContext"] = gameContext;
            Console.WriteLine("AnvilToolkit assembly loaded, game=GhostReconBreakpoint.");
            Console.WriteLine("Toolkit global game context: " + JsonSerializer.Serialize(gameContext));
            var oodleContext = ConfigureToolkitOodle(dll, toolkit, game, verifyResolver: !traceOnly);
            outcome["toolkitOodleContext"] = oodleContext;
            Console.WriteLine("Toolkit Oodle context: " + JsonSerializer.Serialize(oodleContext));
            watcher = new FileSystemWatcher(sandbox)
            {
                IncludeSubdirectories = true,
                NotifyFilter = NotifyFilters.FileName | NotifyFilters.DirectoryName | NotifyFilters.Size | NotifyFilters.LastWrite
            };
            watcher.Created += (_, e) => { lock (events) { if (events.Count < 500) events.Add("CREATE " + Relative(sandbox, e.FullPath)); } };
            watcher.Renamed += (_, e) => { lock (events) { if (events.Count < 500) events.Add("RENAME " + Relative(sandbox, e.FullPath)); } };
            watcher.Changed += (_, e) => { lock (events) { if (events.Count < 500) events.Add("CHANGE " + Relative(sandbox, e.FullPath)); } };
            watcher.EnableRaisingEvents = true;
            var start = Stopwatch.StartNew();
            var sourceName = Path.GetFileName(local);
            object instance;
            Type type;
            if (ext == ".forge")
            {
                // ForgeFile.Deserialize checks File.Exists on argument #2,
                // so it must receive the full Input path. Argument #1 is a separate
                // Work directory in which it can create <forgeName>.forge/.
                // Do NOT place the input binary in Work/<forgeName>.
                if (!Path.IsPathFullyQualified(local) || !File.Exists(local))
                    throw new IOException("Forge input must be an existing fully qualified private working copy.");
                type = dll.GetType(ContainerNs + "ForgeFile", true)!;
                instance = Activator.CreateInstance(type)!;
                Console.WriteLine($"[{Timestamp()}] Calling ForgeFile.Deserialize(work + separator, FULL working-copy path, GhostReconBreakpoint, {forgeFlags[0] == '1'},{forgeFlags[1] == '1'},false) ...");
                Invoke(instance, "Deserialize", new[] { typeof(string), typeof(string), gameType, typeof(bool), typeof(bool), typeof(bool) },
                    work + Path.DirectorySeparatorChar, local, game, forgeFlags[0] == '1', forgeFlags[1] == '1', false);
            }
            else
            {
                type = dll.GetType(ContainerNs + "DataFile", true)!;
                instance = Activator.CreateInstance(type, new object[] { local, game, false })!;
                Console.WriteLine($"[{Timestamp()}] Calling DataFile.Deserialize(localPath) ...");
                var result = TupleResult(Invoke(instance, "Deserialize", new[] { typeof(string) }, local));
                outcome["deserializeResult"] = new { success = result.success, message = result.message };
                Console.WriteLine("DATA Deserialize result: " + result.success + " / " + result.message);
                if (!result.success) throw new IOException("DataFile.Deserialize reported failure: " + result.message);
            }
            Console.WriteLine($"[{Timestamp()}] Deserialize returned after {start.Elapsed.TotalSeconds:F1}s.");
            outcome["deserializeDurationSeconds"] = Math.Round(start.Elapsed.TotalSeconds, 3);
            WaitForOutputs(work, sourceName, waitSeconds, log);
            var extracted = ResourceIndex(work, sourceName);
            var sandboxArtifacts = FindFiles(sandbox)
                .Where(f => !Inside(work, f) && !Inside(input, f) && !Inside(scratch, f))
                .Select(f => Relative(sandbox, f))
                .Take(80).ToArray();
            outcome["unexpectedSandboxFiles"] = sandboxArtifacts;
            outcome["nonResourceArtifacts"] = NonResourceArtifacts(work, sourceName);
            outcome["unpackedFileCount"] = extracted.Count;
            outcome["extractionLayout"] = DescribeLayout(work, sourceName);
            outcome["extractedContainerDirectory"] = Path.Combine(work, sourceName);
            outcome["extractedSample"] = extracted.Keys.Order(StringComparer.OrdinalIgnoreCase).Take(35).ToArray();
            outcome["possibleExtractionDirectories"] = Directory.EnumerateDirectories(work, "*", SearchOption.AllDirectories)
                .Select(p => Relative(work, p)).Take(90).ToArray();
            Console.WriteLine($"[{Timestamp()}] Found {extracted.Count} actual extracted resources inside Work.");
            if (extracted.Count == 0)
            {
                log.Add("No resources under the named extracted Forge/DATA directory. Serialization is DISABLED.");
                throw new IOException("AnvilToolkit returned, but no resources were found under Work/<sourceName>. Review possibleExtractionDirectories, unexpectedSandboxFiles and nonResourceArtifacts in the JSON report. Refusing to repack.");
            }
            if (ext == ".forge" && !Directory.Exists(Path.Combine(work, sourceName)))
                throw new IOException("Unpacked Forge directory is absent, refusing to repack.");
            if (traceOnly)
            {
                outcome["verified"] = false;
                outcome["unpackObserved"] = true;
                log.Add("Unpack observed. Trace-only mode deliberately did not repack.");
                Console.WriteLine("UNPACK OBSERVED. No repack attempted (trace-only mode).");
                exitCode = 0;
            }
            else
            {
                // Capture before the writer runs. Each payload is later independently read back.
                Console.WriteLine($"[{Timestamp()}] Repacking COPIED container only...");
                outcome["repackAttempted"] = true;
                var beforeWriteUtc = File.GetLastWriteTimeUtc(local);
                var beforeWriteSize = new FileInfo(local).Length;
                var beforeWriteHash = Hash(local);
                if (ext == ".forge")
                {
                    // Proven against AnvilToolkit 1.3.7's decompiled code:
                    // Serialize(folder, fileName, game) enumerates only .data
                    // and .rawasset directly under `folder`. Its metadata
                    // loader also searches `folder` itself.
                    var layout = ValidateForgeWriterDirectory(work, sourceName, extracted);
                    outcome["forgeWriterLayout"] = layout;
                    var writerDirectory = Path.Combine(work, sourceName);
                    var writerInput = ResourceIndex(work, sourceName);
                    var writerMissing = extracted.Keys.Except(writerInput.Keys, Comparer).ToArray();
                    var writerChanged = extracted.Where(p => !writerInput.TryGetValue(p.Key, out var hash) || hash != p.Value)
                        .Select(p => p.Key).ToArray();
                    if (writerMissing.Length != 0 || writerChanged.Length != 0 || writerInput.Count != extracted.Count)
                        throw new IOException("Forge writer input does not preserve all extracted file hashes. Aborting repack.");
                    Console.WriteLine($"[{Timestamp()}] Writer scans named Forge directory: {writerDirectory}");
                    Console.WriteLine($"[{Timestamp()}] Writer sees {layout["directRegularResources"]} direct .data/.rawasset resources and {layout["directMetadataResources"]} metadata resources.");
                    var preflight = PreflightForgeEntries(dll, gameType, game, writerDirectory, out var preflightFailures);
                    outcome["forgeEntryPreflight"] = preflight;
                    if (preflightFailures != 0)
                        throw new IOException($"{preflightFailures} .data/.rawasset entries failed AnvilToolkit's CreateForgeEntry preflight; Forge serialization BLOCKED. Inspect forgeEntryPreflight.failures in the JSON report.");
                    var task = Invoke(instance, "Serialize", new[] { typeof(string), typeof(string), gameType },
                        writerDirectory, local, game) as Task;
                    if (task == null) throw new IOException("ForgeFile.Serialize did not return a Task");
                    task.GetAwaiter().GetResult();
                }
                else
                {
                    Invoke(instance, "Serialize", new[] { typeof(string), typeof(bool), typeof(bool) }, local, false, false);
                }
                var afterWriteHash = Hash(local);
                outcome["postRepackSha256"] = afterWriteHash;
                var writeObserved = File.GetLastWriteTimeUtc(local) != beforeWriteUtc
                    || new FileInfo(local).Length != beforeWriteSize || afterWriteHash != beforeWriteHash;
                outcome["repackBinaryWriteObserved"] = writeObserved;
                if (!writeObserved) throw new IOException("Serialize returned, but no change to the working binary file's timestamp, size or hash was observed. Cannot prove repacking took place; refusing to call this a round-trip PASS.");
                outcome["repackDurationSeconds"] = Math.Round(start.Elapsed.TotalSeconds - (double)outcome["deserializeDurationSeconds"]!, 3);
                Console.WriteLine($"[{Timestamp()}] Repack returned. Verifying by fresh extraction...");
                // The read-back input and output must be separated as well.
                // Putting verifyCopy inside verifyWork would repeat the v0.3 collision.
                var verifyInput = Path.Combine(sandbox, "ReadbackInput");
                var verifyWork = Path.Combine(sandbox, "ReadbackWork");
                Directory.CreateDirectory(verifyInput);
                Directory.CreateDirectory(verifyWork);
                var verifyCopy = Path.Combine(verifyInput, sourceName);
                CopyOnce(local, verifyCopy);
                outcome["readbackInput"] = verifyCopy;
                outcome["readbackExtractionRoot"] = verifyWork;
                object fresh;
                if (ext == ".forge")
                {
                    if (!Path.IsPathFullyQualified(verifyCopy) || !File.Exists(verifyCopy))
                        throw new IOException("Re-extraction source is not an existing private Forge copy.");
                    fresh = Activator.CreateInstance(type)!;
                    Invoke(fresh, "Deserialize", new[] { typeof(string), typeof(string), gameType, typeof(bool), typeof(bool), typeof(bool) },
                        verifyWork + Path.DirectorySeparatorChar, verifyCopy, game, false, false, false);
                }
                else
                {
                    fresh = Activator.CreateInstance(type, new object[] { verifyCopy, game, false })!;
                    var second = TupleResult(Invoke(fresh, "Deserialize", new[] { typeof(string) }, verifyCopy));
                    outcome["verificationDeserializeResult"] = new { success = second.success, message = second.message };
                    if (!second.success) throw new IOException("Read-back DataFile.Deserialize failed: " + second.message);
                }
                WaitForOutputs(verifyWork, sourceName, waitSeconds, log);
                var readback = ResourceIndex(verifyWork, sourceName);
                outcome["readbackExtractionLayout"] = DescribeLayout(verifyWork, sourceName);
                // Compare a multiset of (logical name, SHA-256) signatures.
                // A resource basename may recur more than once, and repacking
                // may move metadata and change every numeric ordinal.
                // Resource data remains subject to EXACT SHA-256 matching.
                var expectedLogical = LogicalResourceIndex(extracted, ext == ".forge");
                var actualLogical = LogicalResourceIndex(readback, ext == ".forge");
                outcome["verificationIdentityMode"] = ext == ".forge"
                    ? "FORGE_LOGICAL_NAME_AND_SHA256_MULTISET_PRESERVING_DUPLICATE_COUNT"
                    : "EXACT_RELATIVE_PATH_AND_SHA256_MULTISET";
                var missingAll = expectedLogical.Keys.Except(actualLogical.Keys, Comparer).ToArray();
                var extraAll = actualLogical.Keys.Except(expectedLogical.Keys, Comparer).ToArray();
                var changedAll = expectedLogical.Where(p =>
                        actualLogical.TryGetValue(p.Key, out var hashes)
                        && !SameHashMultiset(p.Value, hashes))
                    .Select(p => p.Key).ToArray();
                var missing = missingAll.Take(50).ToArray();
                var extra = extraAll.Take(50).ToArray();
                var changed = changedAll.Take(50).ToArray();
                outcome["duplicateLogicalNameGroupsBefore"] = expectedLogical.Count(p => p.Value.Length > 1);
                outcome["duplicateLogicalNameGroupsAfter"] = actualLogical.Count(p => p.Value.Length > 1);
                outcome["duplicateLogicalNameExamples"] = expectedLogical
                    .Where(p => p.Value.Length > 1).Take(20)
                    .Select(p => new { logicalName = p.Key, count = p.Value.Length }).ToArray();
                if (ext == ".forge")
                    outcome["renumberedResourceCount"] = CountRenumberedMatchingResources(extracted, readback);
                outcome["readbackResourceCount"] = readback.Count;
                outcome["missingResources"] = missing;
                outcome["extraResources"] = extra;
                outcome["changedResources"] = changed;
                outcome["missingLogicalNameCount"] = missingAll.Length;
                outcome["extraLogicalNameCount"] = extraAll.Length;
                outcome["changedLogicalNameOrMultiplicityCount"] = changedAll.Length;
                if (ext == ".forge" && changedAll.Any(x => x.EndsWith(".PrefetchInfo", StringComparison.OrdinalIgnoreCase)))
                {
                    outcome["prefetchMetadataAudit"] = ForgePrefetchAudit.AnalyzeFolders(
                        dll, gameType, game, Path.Combine(work, sourceName), Path.Combine(verifyWork, sourceName));
                }
                bool pass = missingAll.Length == 0 && extraAll.Length == 0 && changedAll.Length == 0
                    && extracted.Count == readback.Count
                    && expectedLogical.Count == actualLogical.Count;
                outcome["verified"] = pass;
                outcome["repackPassed"] = pass;
                log.Add(pass ? "PASS: every extracted payload survived repack and fresh read-back" : "FAIL: extracted content differs after round trip; do not deploy");
                Console.WriteLine(pass ? "ROUND-TRIP PASS (private copy only)" : "ROUND-TRIP FAIL (private copy only)");
                exitCode = pass ? 0 : 5;
            }
        }
        catch (Exception ex)
        {
            outcome["verified"] = false;
            outcome["error"] = ex.ToString();
            log.Add("ERROR: " + ex);
            Console.Error.WriteLine(ex);
            exitCode = 1;
        }
        finally
        {
            watcher?.Dispose();
            Environment.CurrentDirectory = oldCwd;
            if (!string.IsNullOrEmpty(sandbox) && Directory.Exists(sandbox))
            {
                try
                {
                    outcome["safety"] = "PRIVATE_COPY_ONLY; no live deployment implemented; third-party DLL internals remain unverified";
                    if (!string.IsNullOrEmpty(source) && File.Exists(source) && !string.IsNullOrEmpty(sourceOriginalHash))
                    {
                        var unchanged = Hash(source) == sourceOriginalHash;
                        outcome["originalSourceUnchanged"] = unchanged;
                        if (!unchanged) { outcome["verified"] = false; exitCode = 8; log.Add("CRITICAL: original source SHA-256 changed!"); }
                    }
                    lock (events) outcome["sandboxFileEvents"] = events.ToArray();
                    outcome["log"] = log;
                    var report = Path.Combine(sandbox, "DEEF_ANVIL_TRIAL_RESULT.json");
                    File.WriteAllText(report, JsonSerializer.Serialize(outcome, JsonOptions));
                    if (reportCopy != null)
                    {
                        Directory.CreateDirectory(Path.GetDirectoryName(reportCopy)!);
                        if (File.Exists(reportCopy)) File.Delete(reportCopy);
                        File.Copy(report, reportCopy);
                    }
                    Console.WriteLine("Report: " + report);
                    if (reportCopy != null) Console.WriteLine("Convenience report: " + reportCopy);
                }
                catch (Exception e) { Console.Error.WriteLine("Could not write final diagnostic report: " + e); exitCode = 9; }
            }
        }
        return exitCode;
    }
}
