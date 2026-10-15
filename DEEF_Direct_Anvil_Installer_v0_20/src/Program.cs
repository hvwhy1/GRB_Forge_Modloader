using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Runtime.Loader;
using System.Security.Cryptography;
using System.Threading;
using System.Text.Json;
using System.Text.Json.Serialization;

// AnvilToolkit API discovery and private-copy Forge trials.
internal static class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };

    // AnvilToolkit DataFile.Deserialize can touch WPF-backed type/name services.
    // A console entry point defaults to MTA unless explicitly marked. Keep
    // toolkit nested operations on the STA main thread (no Task.Run wrapper).
    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            if (args.Length >= 2 && new[]{"whole-data-merge-trial","repair-directory-ranges","uncompressed-data-trial","cached-extract","cached-extract-short","compare-data-parents","schema-export","compose-inner","compose-assets","stream-foundation-parent","stream-format-tests","stream-inspect","stream-stage","stream-stage-data","stream-batch","stream-preflight","stream-add-trial","stream-add-outer-trial","stream-add-inner-batch","stream-pack-new-data","toolkit-add-outer-trial"}.Contains(args[0]))
            {
                var library = Trial.LoadToolkit(Path.GetFullPath(args[1]));
                var gameType = library.GetType("AnvilToolkit.Utils.Game", true)!;
                var game = Enum.Parse(gameType, "GhostReconBreakpoint");
                Trial.SetToolkitGameContext(library, gameType, game);
                Trial.ConfigureToolkitOodle(library, Path.GetFullPath(args[1]), game, true);
                return args[0]=="whole-data-merge-trial" ? WholeDataMerge.RunTrial(args) :
                    args[0]=="repair-directory-ranges" ? ForgeDirectoryRepair.Run(args) :
                    args[0]=="uncompressed-data-trial" ? DataCompressionTrial.Run(args) :
                    (args[0]=="cached-extract" || args[0]=="cached-extract-short") ? CachedDataExtract.Run(args) :
                    args[0]=="compare-data-parents" ? DataParentCompare.Run(args) :
                    args[0]=="schema-export" ? SchemaExport.Run(args) :
                    args[0]=="compose-inner" ? InnerCompose.Run(args) :
                    args[0]=="compose-assets" ? AssetCompose.Run(args) :
                    args[0]=="stream-foundation-parent" ? FoundationParent.Run(args) :
                    args[0]=="stream-format-tests" ? V20FormatTests.Run() :
                    args[0]=="toolkit-add-outer-trial" ? ToolkitForgeOuterTrial.Run(args) :
                    args[0]=="stream-add-inner-batch" ? StreamInnerAddBatch.Run(args) :
                    args[0]=="stream-add-outer-trial" ? ForgeOuterAddTrial.Run(args) :
                    args[0]=="stream-pack-new-data" ? StreamPackNewData.Run(args,library,gameType,game) :
                    args[0]=="stream-add-trial" ? StreamAddTrial.Run(args) :
                    args[0]=="stream-batch" ? StreamBatch.Run(args) :
                    args[0]=="stream-preflight" ? StreamPreflight.Run(args) : StreamCommands.Run(args);
            }
            if (args.Length == 6 && (args[0] == "stream-replace" || args[0] == "stream-replace-many"))
            {
                var library = Trial.LoadToolkit(Path.GetFullPath(args[1]));
                var gameType = library.GetType("AnvilToolkit.Utils.Game", true)!;
                var game = Enum.Parse(gameType, "GhostReconBreakpoint");
                Trial.SetToolkitGameContext(library, gameType, game);
                Trial.ConfigureToolkitOodle(library, Path.GetFullPath(args[1]), game, true);
                var payloads = args[0] == "stream-replace-many"
                    ? (JsonSerializer.Deserialize<string[]>(File.ReadAllText(args[3])) ?? throw new IOException("Missing replacement list")).Select(Path.GetFullPath).ToArray()
                    : new[] { Path.GetFullPath(args[3]) };
                var receipt = ForgeStream.ReplaceMany(Path.GetFullPath(args[2]),payloads,Path.GetFullPath(args[4]));
                File.WriteAllText(args[5], JsonSerializer.Serialize(receipt, JsonOptions));
                Console.WriteLine("Streaming replacement verified: " + args[5]);
                return 0;
            }
            if (args.Length == 4 && args[0] == "debug-data")
            {
                var library = Trial.LoadToolkit(Path.GetFullPath(args[1]));
                var gameType = library.GetType("AnvilToolkit.Utils.Game", true)!;
                var game = Enum.Parse(gameType, "GhostReconBreakpoint");
                Trial.SetToolkitGameContext(library, gameType, game);
                Trial.ConfigureToolkitOodle(library, Path.GetFullPath(args[1]), game, true);
                AppDomain.CurrentDomain.FirstChanceException += (_, e) => Console.Error.WriteLine("FIRST CHANCE: " + e.Exception);
                var debugResult = NestedTrial.UnpackData(library.GetType("AnvilToolkit.FileTypes.AnvilNext.Containers.DataFile", true)!, game, Path.GetFullPath(args[2]), Path.GetFullPath(args[3]));
                Console.WriteLine(debugResult);
                return debugResult.failed ? 1 : 0;
            }
            if (args.Length >= 3 && (args[0] == "trial" || args[0] == "trace")) return Trial.Run(args);
            if (args.Length >= 4 && args[0] == "nested") return NestedTrial.Run(args);
            if (args.Length >= 4 && args[0] == "combined") return CombinedTrial.Run(args);
            if (args.Length >= 4 && args[0] == "mutation") return MutationTrial.Run(args);
            if (args.Length >= 8 && args[0] == "stage") return StageReplace.Run(args);
            if (args.Length >= 8 && args[0] == "large-stage") return LargeForgeStageTrial.Run(args);
            if (args.Length >= 7 && args[0] == "stage-data") return StageDataReplace.Run(args);
            if (args.Length >= 4 && args[0] == "inspect") return InspectRoute.Run(args);
            if (args.Length >= 4 && args[0] == "large-index") return LargeForgeIndex.Run(args);
            if (args.Length == 5 && args[0] == "prefetch-audit") return ForgePrefetchAudit.Run(args);
            if (args.Length < 2 || args[0] != "probe")
            {
                Usage();
                return 2;
            }
            var toolkit = Path.GetFullPath(args[1].Trim('"'));
            string dll = Directory.Exists(toolkit) ? Path.Combine(toolkit, "AnvilToolkit.dll") : toolkit;
            if (!File.Exists(dll)) throw new FileNotFoundException("AnvilToolkit.dll not found", dll);
            var root = Path.GetDirectoryName(dll)!;
            var output = args.Length > 2 ? Path.GetFullPath(args[2].Trim('"')) : Path.Combine(Environment.CurrentDirectory, "ANVIL_API_PROBE.json");
            if (string.Equals(dll, output, StringComparison.OrdinalIgnoreCase)) throw new ArgumentException("Output cannot overwrite DLL");
            var libraryDir = Path.Combine(root, "Libs");

            // Use the user's original, unmodified AnvilToolkit DLLs in place.
            AssemblyLoadContext.Default.Resolving += (_, name) =>
            {
                string file = name.Name + ".dll";
                foreach (string folder in new[] {root, libraryDir})
                {
                    string p = Path.Combine(folder, file);
                    if (File.Exists(p))
                    {
                        try { return AssemblyLoadContext.Default.LoadFromAssemblyPath(p); }
                        catch { return null; }
                    }
                }
                return null;
            };

            var warnings = new List<string>();
            var assembly = AssemblyLoadContext.Default.LoadFromAssemblyPath(dll);
            Type[] types;
            try { types = assembly.GetTypes(); }
            catch (ReflectionTypeLoadException e)
            {
                types = e.Types.Where(t => t != null).Cast<Type>().ToArray();
                warnings.Add("Some types could not load. Check DLL dependencies and desktop runtime.");
                warnings.AddRange(e.LoaderExceptions.Where(x => x != null).Select(x => x!.GetType().Name + ": " + x.Message).Take(25));
            }

            bool InterestingType(Type t)
            {
                var n = t.FullName ?? t.Name;
                return n.Contains("ForgeFile", StringComparison.OrdinalIgnoreCase)
                    || n.Contains("ForgeDirectory", StringComparison.OrdinalIgnoreCase)
                    || n.Contains("DataFile", StringComparison.OrdinalIgnoreCase)
                    || n.Contains("ForgeEntry", StringComparison.OrdinalIgnoreCase)
                    || n.Contains("GameExplorer", StringComparison.OrdinalIgnoreCase)
                    || n.Contains("FileAction", StringComparison.OrdinalIgnoreCase)
                    || n.Contains("FileManager", StringComparison.OrdinalIgnoreCase);
            }
            bool InterestingMethod(MethodInfo m)
            {
                var n = m.Name;
                return new[] { "unpack", "repack", "serialize", "deserialize", "extract", "createforgeentry", "save", "load", "read", "write", "decompress", "compress" }
                    .Any(s => n.Contains(s, StringComparison.OrdinalIgnoreCase));
            }

            object MethodReport(MethodBase m)
            {
                string Signature()
                {
                    try { return m.ToString() ?? m.Name; }
                    catch { return m.Name + " (signature unavailable)"; }
                }
                var pars = new List<object>();
                try
                {
                    foreach (var p in m.GetParameters())
                        pars.Add(new { name = p.Name, type = p.ParameterType.FullName, isOptional = p.IsOptional, isByRef = p.ParameterType.IsByRef });
                }
                catch (Exception e) { pars.Add(new { error = e.Message }); }
                return new
                {
                    name = m.Name,
                    signature = Signature(),
                    isPublic = m.IsPublic,
                    isStatic = m.IsStatic,
                    parameters = pars,
                    returnType = m is MethodInfo mi ? mi.ReturnType.FullName : null
                };
            }

            var candidates = new List<object>();
            var details = new List<object>();
            const BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance | BindingFlags.DeclaredOnly;
            foreach (var t in types.OrderBy(t => t.FullName))
            {
                try
                {
                    var methods = t.GetMethods(flags);
                    foreach (var m in methods.Where(InterestingMethod))
                        candidates.Add(new { type = t.FullName, method = MethodReport(m) });
                    if (!InterestingType(t)) continue;
                    details.Add(new
                    {
                        name = t.FullName,
                        isPublic = t.IsPublic,
                        isAbstract = t.IsAbstract,
                        isSealed = t.IsSealed,
                        baseType = t.BaseType?.FullName,
                        constructors = t.GetConstructors(flags).Select(MethodReport).ToArray(),
                        methods = methods.Select(MethodReport).ToArray(),
                        properties = t.GetProperties(flags).Select(p => new { name = p.Name, type = Safe(() => p.PropertyType.FullName), canRead = p.CanRead, canWrite = p.CanWrite }).ToArray(),
                        fields = t.GetFields(flags).Select(f => new { name = f.Name, type = Safe(() => f.FieldType.FullName), isStatic = f.IsStatic }).ToArray()
                    });
                }
                catch (Exception e) { warnings.Add("Could not inspect " + t.FullName + ": " + e.Message); }
            }
            var result = new
            {
                schema = "DEEF.AnvilBridgeProbe.1",
                timestampUtc = DateTimeOffset.UtcNow,
                safety = "READ_ONLY_INSPECTION; NO SERIALIZERS INVOKED",
                platform = RuntimeInformation.OSDescription,
                framework = RuntimeInformation.FrameworkDescription,
                toolkitDirectory = root,
                toolkitSha256 = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(dll))).ToLowerInvariant(),
                toolkitVersion = assembly.GetName().Version?.ToString(),
                totalTypesLoaded = types.Length,
                candidateMethods = candidates,
                coreTypes = details,
                warnings
            };
            Directory.CreateDirectory(Path.GetDirectoryName(output)!);
            File.WriteAllText(output, JsonSerializer.Serialize(result, JsonOptions));
            Console.WriteLine($"Probe completed. Loaded {types.Length} types; found {candidates.Count} relevant methods and {details.Count} core types.");
            Console.WriteLine("Report: " + output);
            Console.WriteLine("No game or Forge files were modified.");
            return details.Count == 0 ? 3 : 0;
        }
        catch (Exception e)
        {
            Console.Error.WriteLine("Probe failed: " + e);
            return 1;
        }
    }

    private static string? Safe(Func<string?> func)
    {
        try { return func(); } catch { return "(unavailable)"; }
    }
    private static void Usage()
    {
        Console.WriteLine("DEEF AnvilBridge API Probe 0.2 (READ ONLY)");
        Console.WriteLine("Usage: DEEF.AnvilBridgeProbe.exe probe <AnvilToolkit-folder-or-DLL> [output-json]");
        Console.WriteLine("       DEEF.AnvilBridgeProbe.exe trace|trial <AnvilToolkit-folder> <source.forge|source.data> [maxMiB] [report.json] [waitSeconds]");
    }
}

