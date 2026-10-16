using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

// Read-only identity gate. A filename match must ALSO match the actual binary
// ID/type; a header collision never authorizes writing a differently named item.
internal static class StreamPreflight
{
    static string Logical(string s) {
        var label=Path.GetFileName(s);int p=label.IndexOf("_-_",StringComparison.Ordinal);
        return p>0 && label[..p].All(char.IsDigit) ? label[(p+3)..] : label;
    }
    static string Stem(string s) {
        var label=Logical(s);int dot=label.LastIndexOf('.');return dot<0?label:label[..dot];
    }
    internal static int Run(string[] args) {
        string? report=args.Length>3?Path.GetFullPath(args[3]):null;
        string? scratch=null;
        try {
            if(args.Length!=5)throw new ArgumentException("Usage: stream-preflight <toolkit> <source.forge> <report.json> <request.json>");
            string toolkit=Path.GetFullPath(args[1]),source=Path.GetFullPath(args[2]),request=Path.GetFullPath(args[4]);
            if(Trial.Inside(Path.GetDirectoryName(source)!,report!) || Trial.Inside(toolkit,report!) ||
               Trial.Inside(Path.GetDirectoryName(source)!,request) || Trial.Inside(toolkit,request))
                throw new IOException("Preflight report/request must be outside game/toolkit");
            var edits=JsonSerializer.Deserialize<StreamBatch.Edit[]>(File.ReadAllText(request))??throw new IOException("Missing edits");
            if(edits.Length==0)throw new IOException("Empty preflight request");
            scratch=Path.Combine(Path.GetDirectoryName(report!)!,"preflight-"+Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(scratch);
            HeadlessToolkit.InitializeHashes();
            using var stream=new FileStream(source,FileMode.Open,FileAccess.Read,FileShare.Read);
            var entries=ForgeStream.Read(stream);
            var accepted=new HashSet<int>();
            var rejected=new Dictionary<int,string>();
            var groups=edits.Select((e,i)=>(e,i)).GroupBy(x=>Logical(x.e.data),StringComparer.OrdinalIgnoreCase).ToArray();
            int container=0;
            foreach(var group in groups) {
                Console.WriteLine($"Preflight container {++container}/{groups.Length}: {group.Key} ({group.Count()} edits)");
                var candidates=entries.Where(e=>Logical(e.Name+".data").Equals(group.Key,StringComparison.OrdinalIgnoreCase)).ToArray();
                if(candidates.Length!=1)throw new IOException("Container missing or ambiguous: "+group.Key);
                var forgeEntry=candidates[0];
                if(group.Any(x=>x.e.kind=="data")) {
                    if(group.Count()!=1 || group.Any(x=>x.e.kind!="data"))throw new IOException("Whole/nested edit conflict");
                    var target=group.Single();
                    try {
                        var identity=DataFile.CreateForgeEntry(target.e.payload,0,Game.GhostReconBreakpoint);
                        if(identity.ID!=forgeEntry.Id || identity.Extension!=forgeEntry.Type)
                            rejected[target.i]="WHOLE_DATA_ID_TYPE_MISMATCH";
                        else {
                            string mergeDir=Path.Combine(scratch,"whole-"+forgeEntry.Ordinal);Directory.CreateDirectory(mergeDir);
                            string originalData=Path.Combine(mergeDir,$"{forgeEntry.Ordinal}_-_{forgeEntry.Name}.data");
                            using(var copy=File.Create(originalData))ForgeStream.CopyRange(stream,copy,forgeEntry.Offset,forgeEntry.Length);
                            WholeDataMerge.Validate(originalData,target.e.payload,Path.Combine(mergeDir,"Merge"));
                            accepted.Add(target.i);
                        }
                    } catch(Exception e) when(e is IOException || e is InvalidDataException || e is EndOfStreamException) {
                        rejected[target.i]="INVALID_WHOLE_DATA_PAYLOAD: "+e.Message;
                    }
                    continue;
                }
                if(group.Any(x=>x.e.kind!="inner"))throw new IOException("Unknown edit kind");
                string dir=Path.Combine(scratch,forgeEntry.Ordinal.ToString());Directory.CreateDirectory(dir);
                string copied=Path.Combine(dir,$"{forgeEntry.Ordinal}_-_{forgeEntry.Name}.data");
                using(var dest=new FileStream(copied,FileMode.CreateNew,FileAccess.Write))
                    ForgeStream.CopyRange(stream,dest,forgeEntry.Offset,forgeEntry.Length);
                string extracted=Path.Combine(dir,"Extracted",Path.GetFileName(copied));
                var unpack=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,copied,extracted);
                if(unpack.failed)throw new IOException("Cannot inspect nested container: "+unpack.message);
                var files=Directory.GetFiles(extracted).Where(f=>Path.GetFileName(f).Contains("_-_") &&
                    !new[]{".PrefetchingInfo",".Dependency",".FilterTable",".xml",".Prefab"}.Contains(Path.GetExtension(f),StringComparer.OrdinalIgnoreCase)).ToArray();
                var names=files.GroupBy(Stem,StringComparer.OrdinalIgnoreCase)
                    .ToDictionary(g=>g.Key,g=>g.ToArray(),StringComparer.OrdinalIgnoreCase);
                var ids=new Dictionary<string,(ulong Id,uint Type)>(StringComparer.OrdinalIgnoreCase);
                foreach(var file in files)ids[file]=HeadlessToolkit.Identity(file);
                var targets=new Dictionary<(ulong,uint),List<int>>();
                foreach(var item in group) {
                    var edit=item.e;
                    if(string.IsNullOrWhiteSpace(edit.@object)) {rejected[item.i]="MISSING_CHARACTERIZED_OBJECT_NAME";continue;}
                    if(!names.TryGetValue(Stem(edit.@object),out var byName)||byName.Length!=1) {
                        rejected[item.i]="RESOURCE_NAME_ABSENT_OR_AMBIGUOUS: "+Logical(edit.@object);continue;
                    }
                    try {
                        var payloadId=HeadlessToolkit.Identity(edit.payload);
                        if(payloadId!=ids[byName[0]]) {
                            string actual=files.FirstOrDefault(f=>ids[f]==payloadId) is string hit ? Path.GetFileName(hit) : "NOT_PRESENT";
                            rejected[item.i]="PAYLOAD_ID_NAME_MISMATCH: expected="+Logical(edit.@object)+"; binary_identity_matches="+actual;
                            continue;
                        }
                        if(files.Count(f=>ids[f]==payloadId)!=1) {
                            rejected[item.i]="DUPLICATE_BINARY_ID_TYPE_IN_GAME_CONTAINER";continue;
                        }
                        accepted.Add(item.i);
                        if(!targets.TryGetValue(payloadId,out var list))targets[payloadId]=list=new List<int>();
                        list.Add(item.i);
                    } catch(Exception e) when(e is IOException || e is InvalidDataException || e is EndOfStreamException) {
                        rejected[item.i]="INVALID_PAYLOAD_HEADER: "+e.Message;
                    }
                }
                // Any duplicate target binary identity is a conflict, not a last-wins update.
                foreach(var hit in targets.Where(kv=>kv.Value.Count>1))
                    foreach(int i in hit.Value){accepted.Remove(i);rejected[i]="TWO_MODS_TARGET_SAME_BINARY_ID_TYPE";}
                Directory.Delete(dir,true);
            }
            if(accepted.Count+rejected.Count!=edits.Length)throw new IOException("Preflight did not classify every route");
            var result=new {
                verified=true,sourceForge=source,sourceBytes=stream.Length,requestedEdits=edits.Length,
                acceptedIndices=accepted.OrderBy(i=>i).ToArray(),
                excluded=rejected.OrderBy(x=>x.Key).Select(x=>new {index=x.Key,reason=x.Value,
                    @object=edits[x.Key].@object,data=edits[x.Key].data}).ToArray(),
                eligibleForDeployment=false,mode="READ_ONLY_BINARY_IDENTITY_PREFLIGHT"
            };
            File.WriteAllText(report!,JsonSerializer.Serialize(result,new JsonSerializerOptions{WriteIndented=true}));
            Console.WriteLine($"IDENTITY PREFLIGHT: {accepted.Count}/{edits.Length} accepted; {rejected.Count} quarantined. No Forge output.");
            return 0;
        } catch(Exception e) {
            Console.Error.WriteLine(e);
            if(report!=null){Directory.CreateDirectory(Path.GetDirectoryName(report)!);
                File.WriteAllText(report,JsonSerializer.Serialize(new {verified=false,error=e.ToString()}));}
            return 1;
        } finally {
            if(scratch!=null && Directory.Exists(scratch))
                try{Directory.Delete(scratch,true);}catch(Exception e){Console.Error.WriteLine("Preflight cleanup warning: "+e.Message);}
        }
    }
}
