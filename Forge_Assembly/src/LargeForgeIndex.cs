using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.Json;

// Large-Forge index uses ATK's version-27 entry-table reader (CreateCache + stream).
// It NEVER calls the archive serializer, extracts payloads, or copies an archive.
// Names from entry metadata are provisional until a full private-copy readback verifies them.
internal static class LargeForgeIndex
{
    static readonly BindingFlags F = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
    static object? Get(object o, string name)
    {
        try {
            var t = o.GetType();
            return t.GetProperty(name,F)?.GetValue(o) ?? t.GetField(name,F)?.GetValue(o);
        } catch { return null; }
    }
    static string? EntryName(object entry)
    {
        foreach (var field in new[] {"Name", "FileName", "ResourceName", "DisplayName"})
            if (Get(entry,field) is string s && s.Length > 0) return s;
        return null;
    }
    public static int Run(string[] args)
    {
        string? reportPath = null;
        var report = new Dictionary<string,object?> {
            ["schema"]="DEEF.large_forge_index.1", ["verified"]=false,
            ["mode"]="READ_ONLY_ATK_ENTRY_TABLE", ["gameModified"]=false,
            ["eligibleForDeployment"]=false, ["nameEvidence"]="PROVISIONAL_ATK_METADATA_ONLY"
        };
        string previous = Environment.CurrentDirectory;
        int code=1;
        try
        {
            if(args.Length != 4) throw new ArgumentException("large-index <toolkitDir> <source.forge> <report.json>");
            var toolkit = Path.GetFullPath(args[1].Trim('"'));
            var source = Path.GetFullPath(args[2].Trim('"'));
            reportPath = Path.GetFullPath(args[3].Trim('"'));
            if (!source.EndsWith(".forge",StringComparison.OrdinalIgnoreCase) || !File.Exists(source))
                throw new IOException("Source must be an existing .forge file");
            if (!reportPath.EndsWith(".json",StringComparison.OrdinalIgnoreCase) ||
                Trial.Inside(Path.GetDirectoryName(source)!,reportPath) || Trial.Inside(toolkit,reportPath))
                throw new IOException("Report must be a JSON outside game/toolkit directory");
            if (!File.Exists(Path.Combine(toolkit,"AnvilToolkit.dll"))) throw new FileNotFoundException("AnvilToolkit.dll missing");
            var size = new FileInfo(source);
            long beforeLength=size.Length;
            long beforeTicks=size.LastWriteTimeUtc.Ticks;
            report["source"] = source;
            report["sourceBytes"] = beforeLength;
            if (beforeLength > 64L*1024*1024*1024)
                throw new IOException("Read-only indexing capped at 64 GiB for this preview");
            // Avoid triggering relative-path writes from third-party settings.
            string scratch=Path.Combine(Path.GetTempPath(),"DEEF_LargeIndex_"+Guid.NewGuid().ToString("N"));
            if (Trial.Inside(Path.GetDirectoryName(source)!,scratch) || Trial.Inside(toolkit,scratch))
                throw new IOException("TEMP scratch directory cannot be inside game or toolkit");
            Directory.CreateDirectory(scratch);
            Environment.CurrentDirectory=scratch;
            var dll=Trial.LoadToolkit(toolkit);
            var storage=dll.GetType("AnvilToolkit.Utils.DataStorage",true)!;
            var gameField=storage.GetField("ActiveGame",BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static)!;
            var gameType=gameField.FieldType;
            var game=Enum.Parse(gameType,"GhostReconBreakpoint");
            Trial.SetToolkitGameContext(dll,gameType,game);
            // Metadata-only, no payload compression or decompression is requested.
            var ft=dll.GetType("AnvilToolkit.FileTypes.AnvilNext.Containers.ForgeFile",true)!;
            var instance=Activator.CreateInstance(ft)!;
            ft.GetField("ShowMessages",F)?.SetValue(instance,false);
            // CreateCache=true prevents backup/extraction folder. stream=true uses
            // ForgeDirectory(entriesOnly:true) which reads the entry tables only.
            Trial.Invoke(instance,"Deserialize",
                new[]{typeof(string),typeof(string),gameType,typeof(bool),typeof(bool),typeof(bool)},
                scratch + Path.DirectorySeparatorChar,source,game,true,true,true);
            var dirs=Get(instance,"Directories") as IEnumerable;
            if (dirs == null) throw new IOException("ATK did not expose Forge directories");
            var entries = new List<Dictionary<string,object?>>();
            int ordinal=0;
            int unidentified=0;
            foreach (object dir in dirs)
            {
                var directoryEntries=Get(dir,"Entries") as IEnumerable;
                if (directoryEntries==null) throw new IOException("Entry-table reader returned no Entries array");
                foreach (object entry in directoryEntries)
                {
                    var name=EntryName(entry);
                    var id=Get(entry,"ID")?.ToString() ?? "";
                    var ext=Get(entry,"Extension")?.ToString() ?? "";
                    if (string.IsNullOrWhiteSpace(name)) unidentified++;
                    entries.Add(new Dictionary<string,object?> {
                        ["ordinal"]=ordinal++, ["name"]=name, ["id"]=id, ["extensionCode"]=ext,
                    });
                }
            }
            if(entries.Count==0) throw new IOException("ATK entry-table reader returned zero entries");
            // A recognized filename is necessary but not sufficient for installing a mod.
            // Only use valid standalone metadata names; do NOT guess anonymous ID types.
            var candidates=new List<string>();
            for(int i=0;i<entries.Count;i++)
            {
                string? n=(string?)entries[i]["name"];
                if(string.IsNullOrWhiteSpace(n) || n.Contains('/') || n.Contains('\\') || n.Contains("..") || n.Length>180) continue;
                // Reader names generally omit the .data extension. For mapping,
                // return both the name and .data form, retaining the true stable ID.
                if(n.EndsWith(".data",StringComparison.OrdinalIgnoreCase)) candidates.Add(i+"_-_"+n);
                else if(!n.Contains('.') && !n.Contains("MetaFile",StringComparison.OrdinalIgnoreCase) &&
                        !n.Contains("Prefetch",StringComparison.OrdinalIgnoreCase)) candidates.Add(i+"_-_"+n+".data");
            }
            if(candidates.Count == 0)
                throw new IOException("ATK read the Forge entry table, but exposed no usable named .data resources. Do not infer targets from the entry ordinals; attach the JSON report for reflection/API diagnosis.");
            var after=new FileInfo(source);
            if(after.Length!=beforeLength || after.LastWriteTimeUtc.Ticks!=beforeTicks)
                throw new IOException("Source size/timestamp changed during read-only indexing");
            report["entryCount"]=entries.Count;
            report["unidentifiedNames"]=unidentified;
            report["nestedDataEntries"]=candidates;
            report["entries"]=entries;
            report["sourceMetadataUnchanged"]=true;
            report["verified"]=true; // parsed metadata only, NEVER verified mod destinations
            report["eligibleForDeployment"]=false;
            report["note"]="Metadata-only; name/type inference must be confirmed during full binary round-trip. Forge entry ordinals are NOT stable IDs.";
            Console.WriteLine($"READ-ONLY LARGE FORGE INDEX: {entries.Count} entries; {candidates.Count} candidate .data names; no archive extraction.");
            code=0;
        }
        catch (Exception ex) {report["error"]=ex.ToString();Console.Error.WriteLine(ex);}
        finally
        {
            Environment.CurrentDirectory=previous;
            if(reportPath != null) try {
                Directory.CreateDirectory(Path.GetDirectoryName(reportPath)!);
                File.WriteAllText(reportPath,JsonSerializer.Serialize(report,new JsonSerializerOptions{WriteIndented=true}));
                Console.WriteLine("Index report: "+reportPath);
            } catch(Exception ex) {Console.Error.WriteLine(ex);code=1;}
        }
        return code;
    }
}
