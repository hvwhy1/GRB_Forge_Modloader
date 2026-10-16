using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.Json;
using System.Threading.Tasks;

// Replaces ONE existing complete .data entry with a mod-supplied .data blob.
// All Anvil operations use private copies. Caller explicitly chooses whether
// to install an independently verified staged Forge later.
internal static class StageDataReplace
{
    static readonly StringComparer Cmp = StringComparer.OrdinalIgnoreCase;
    static string Logical(string filename)
    {
        int p = filename.IndexOf("_-_", StringComparison.Ordinal);
        return p > 0 && filename[..p].All(char.IsDigit) ? filename[(p+3)..] : filename;
    }
    static Dictionary<string, string> Index(string dir)
    {
        if (Directory.GetDirectories(dir).Length > 0) throw new IOException("Unexpected nested Forge extraction folders.");
        return Directory.GetFiles(dir).ToDictionary(
            file => Path.GetFileName(file) ?? throw new IOException("Missing extracted filename"),
            file => Trial.Hash(file), Cmp);
    }
    static Dictionary<string,string[]> Groups(Dictionary<string,string> files) => files
        .GroupBy(p => Logical(p.Key), Cmp)
        .ToDictionary(g => g.Key, g => g.Select(p => p.Value).OrderBy(x=>x,Cmp).ToArray(), Cmp);
    static void SameResources(Dictionary<string,string> expected, Dictionary<string,string> actual)
    {
        if (expected.Count != actual.Count) throw new IOException("Forge resource count changed.");
        var oldGroups = Groups(expected);
        var newGroups = Groups(actual);
        if (oldGroups.Count != newGroups.Count || oldGroups.Any(p => !newGroups.TryGetValue(p.Key, out var hashes)
            || !p.Value.SequenceEqual(hashes, Cmp)))
            throw new IOException("Forge readback payload name/hash/multiplicity mismatch.");
    }
    public static int Run(string[] args)
    {
        string source="", originalHash="", oldCwd=Environment.CurrentDirectory;
        string? reportFile=null, oldTemp=Environment.GetEnvironmentVariable("TEMP"), oldTmp=Environment.GetEnvironmentVariable("TMP");
        var report = new Dictionary<string,object?> { ["mode"]="EXPLICIT_FULL_DATA_ENTRY_REPLACEMENT",
            ["verified"]=false, ["gameModified"]=false, ["stage"]="INPUT_VALIDATION" };
        int exit=1;
        try
        {
            if (args.Length<7) throw new ArgumentException(
                "Usage: stage-data <toolkit> <source.forge> <report.json> <exact_data_file> <payload.data> <staged_output_dir>");
            string toolkit=Path.GetFullPath(args[1]), forge=Path.GetFullPath(args[2]);source=forge;
            reportFile=Path.GetFullPath(args[3]);string selector=args[4], payload=Path.GetFullPath(args[5]);
            string outputRoot=Path.GetFullPath(args[6]);
            if (!File.Exists(forge) || Path.GetExtension(forge).ToLowerInvariant()!=".forge" ||
                new FileInfo(forge).Length>128L*1024*1024)
                throw new IOException("Source Forge must exist and be no larger than 128 MiB (current safety limit).");
            if (!Directory.Exists(toolkit) || !File.Exists(Path.Combine(toolkit,"AnvilToolkit.dll")) ||
                !Directory.Exists(Path.Combine(toolkit,"Libs"))) throw new IOException("Invalid AnvilToolkit folder.");
            if (!File.Exists(payload) || Path.GetExtension(payload).ToLowerInvariant()!=".data" ||
                new FileInfo(payload).Length==0 || new FileInfo(payload).Length>64L*1024*1024)
                throw new IOException("Payload must be an existing .data file under 2 GiB (signed 32-bit Forge entry limit).");
            if (selector.Contains('/') || selector.Contains('\\') || selector.Contains("..") ||
                !selector.EndsWith(".data",StringComparison.OrdinalIgnoreCase))
                throw new IOException("Data target must be one exact .data resource name, not a path.");
            if (Trial.Inside(Path.GetDirectoryName(forge)!,payload) || Trial.Inside(toolkit,payload) ||
                Trial.Inside(Path.GetDirectoryName(forge)!,reportFile) || Trial.Inside(toolkit,reportFile) ||
                Trial.Inside(Path.GetDirectoryName(forge)!,outputRoot) || Trial.Inside(toolkit,outputRoot))
                throw new IOException("Payload/output/report must be outside source Forge and toolkit folders.");
            string payloadHash=Trial.Hash(payload);
            originalHash=Trial.Hash(source);
            string scratchRoot=Path.Combine(Path.GetTempPath(),"DEEF_AnvilBridge_Trials");
            Trial.EnsureScratchSpace(forge,scratchRoot);
            if (Trial.Inside(Path.GetDirectoryName(forge)!,scratchRoot)) throw new IOException("Unsafe temp root.");
            string scratch=Path.Combine(scratchRoot,DateTime.UtcNow.ToString("yyyyMMdd_HHmmss")+"_data_"+Guid.NewGuid().ToString("N")[..8]);
            Directory.CreateDirectory(scratch);
            string forgeName=Path.GetFileName(source), inputRoot=Path.Combine(scratch,"Input"),workRoot=Path.Combine(scratch,"Work");
            Directory.CreateDirectory(inputRoot);Directory.CreateDirectory(workRoot);
            string inputForge=Path.Combine(inputRoot,forgeName);
            Trial.CopyOnce(source,inputForge);
            if (!Cmp.Equals(Trial.Hash(inputForge),originalHash)) throw new IOException("Forge copy hash mismatch.");
            Environment.CurrentDirectory=scratch;
            Environment.SetEnvironmentVariable("TEMP",scratch);
            Environment.SetEnvironmentVariable("TMP",scratch);
            report["sandbox"]=scratch;report["sourceSha256"]=originalHash;
            report["payloadSha256"]=payloadHash;report["source"]=source;
            var dll=Trial.LoadToolkit(toolkit);
            var storage=dll.GetType("AnvilToolkit.Utils.DataStorage",true)!;
            var context=storage.GetField("ActiveGame",BindingFlags.Static|BindingFlags.Public|BindingFlags.NonPublic)!;
            Type gameType=context.FieldType;
            object game=Enum.Parse(gameType,"GhostReconBreakpoint");
            Trial.SetToolkitGameContext(dll,gameType,game);
            Trial.ConfigureToolkitOodle(dll,toolkit,game,true);
            var forgeType=dll.GetType("AnvilToolkit.FileTypes.AnvilNext.Containers.ForgeFile",true)!;
            Type[] readTypes={typeof(string),typeof(string),gameType,typeof(bool),typeof(bool),typeof(bool)};
            Type[] writeTypes={typeof(string),typeof(string),gameType};
            report["stage"]="UNPACK_FORGE";
            Trial.Invoke(Activator.CreateInstance(forgeType)!,"Deserialize",readTypes,
                workRoot+Path.DirectorySeparatorChar,inputForge,game,false,false,false);
            string forgeDir=Path.Combine(workRoot,forgeName);
            Trial.WaitForStableForgeContents(forgeDir);
            var original=Index(forgeDir);
            string[] matches=original.Keys.Where(k=>Cmp.Equals(k,selector)||Cmp.Equals(Logical(k),Logical(selector))).ToArray();
            if (matches.Length!=1) throw new IOException("Target matches "+matches.Length+" entries, expected exactly one.");
            string selected=matches[0], replacePath=Path.Combine(forgeDir,selected);
            if (Cmp.Equals(Trial.Hash(replacePath),payloadHash)) throw new IOException("Replacement is byte-identical to original.");
            File.Copy(payload,replacePath,true);
            if (!Cmp.Equals(Trial.Hash(replacePath),payloadHash)) throw new IOException("Payload copy checksum mismatch.");
            var expected=Index(forgeDir);
            string[] changed=original.Keys.Where(k=>!Cmp.Equals(original[k],expected[k])).ToArray();
            if (changed.Length!=1||!Cmp.Equals(changed[0],selected)) throw new IOException("Expected exactly one resource changed.");
            report["selectedData"]=selected;report["originalForgeResourceCount"]=original.Count;
            report["stage"]="PREFLIGHT_AND_SERIALIZE";
            report["forgeEntryPreflight"]=Trial.PreflightForgeEntries(dll,gameType,game,forgeDir,out int fail);
            if (fail!=0) throw new IOException("Forge entry preflight failure count: "+fail);
            string rebuilt=Path.Combine(scratch,"Rebuilt",forgeName);
            Directory.CreateDirectory(Path.GetDirectoryName(rebuilt)!);
            if (Trial.Invoke(Activator.CreateInstance(forgeType)!,"Serialize",writeTypes,forgeDir,rebuilt,game) is not Task task)
                throw new IOException("Forge serializer did not return a Task.");
            task.GetAwaiter().GetResult();
            if (!File.Exists(rebuilt)||new FileInfo(rebuilt).Length<1024)throw new IOException("Empty Forge output.");
            report["stage"]="READBACK_AND_VERIFY";
            string readInput=Path.Combine(scratch,"ReadInput");string readWork=Path.Combine(scratch,"ReadWork");
            Directory.CreateDirectory(readInput);Directory.CreateDirectory(readWork);
            string readForge=Path.Combine(readInput,forgeName);Trial.CopyOnce(rebuilt,readForge);
            Trial.Invoke(Activator.CreateInstance(forgeType)!,"Deserialize",readTypes,
                readWork+Path.DirectorySeparatorChar,readForge,game,false,false,false);
            string readDir=Path.Combine(readWork,forgeName);
            Trial.WaitForStableForgeContents(readDir);
            var actual=Index(readDir);
            SameResources(expected,actual);
            string[] readMatches=actual.Keys.Where(k=>Cmp.Equals(Logical(k),Logical(selected))).ToArray();
            if (readMatches.Length!=1 || !Cmp.Equals(actual[readMatches[0]],payloadHash))
                throw new IOException("Final extracted .data is not byte identical to selected mod payload.");
            report["readbackForgeResourceCount"]=actual.Count;
            report["finalDataBinaryMatchesModPayload"]=true;
            if (!Cmp.Equals(Trial.Hash(source),originalHash))throw new IOException("Original source changed during serialization.");
            report["stage"]="EXPORT_VERIFIED_FORGE";
            Directory.CreateDirectory(outputRoot);
            string staged=Path.Combine(outputRoot,forgeName);
            if (File.Exists(staged))throw new IOException("Refusing to overwrite a previous staged output.");
            Trial.CopyOnce(rebuilt,staged);
            string stagedSha=Trial.Hash(staged);
            if (!Cmp.Equals(stagedSha,Trial.Hash(rebuilt)))throw new IOException("Export hash mismatch.");
            report["stagedForge"]=staged;report["stagedForgeSha256"]=stagedSha;
            report["stage"]="PASS";report["verified"]=true;
            Console.WriteLine("WHOLE .DATA REPLACEMENT VERIFIED: "+selected+" (private output only)");exit=0;
        }
        catch(Exception ex)
        {
            report["error"]=ex.ToString();report["stageFailed"]=report["stage"];
            Console.Error.WriteLine(ex);
        }
        finally
        {
            Environment.CurrentDirectory=oldCwd;
            Environment.SetEnvironmentVariable("TEMP",oldTemp);Environment.SetEnvironmentVariable("TMP",oldTmp);
            if (File.Exists(source)&&originalHash.Length>0)
            {
                bool intact=Cmp.Equals(Trial.Hash(source),originalHash);
                report["originalSourceUnchanged"]=intact;
                if (!intact) {report["verified"]=false;exit=1;}
            }
            report["safety"]="PRIVATE COPY STAGING ONLY; NO LIVE GAME DEPLOYMENT";
            if (reportFile!=null)
            {
                try {Directory.CreateDirectory(Path.GetDirectoryName(reportFile)!);
                    File.WriteAllText(reportFile,JsonSerializer.Serialize(report,new JsonSerializerOptions{WriteIndented=true}));
                    Console.WriteLine("Report: "+reportFile);}
                catch(Exception ex){Console.Error.WriteLine(ex);exit=1;}
            }
        }
        return exit;
    }
}
