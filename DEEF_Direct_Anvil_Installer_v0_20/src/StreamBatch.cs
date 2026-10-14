using System.IO;
using System.Text.Json;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

internal static class StreamBatch
{
    internal sealed record Edit(string data,string kind,string payload,string? @object = null);
    static string Logical(string s)=>System.Text.RegularExpressions.Regex.Replace(s,@"^\d+_-_","");
    static string Stem(string s) {var label=Logical(Path.GetFileName(s));int dot=label.LastIndexOf('.');return dot<0?label:label[..dot];}
    internal static int Run(string[] args)
    {
        string report=Path.GetFullPath(args[3]);
        try {
            string source=Path.GetFullPath(args[2]),request=Path.GetFullPath(args[4]),output=Path.GetFullPath(args[5]);
            foreach(string path in new[]{report,request,output})
                if(Trial.Inside(Path.GetDirectoryName(source)!,path)||Trial.Inside(Path.GetFullPath(args[1]),path))throw new IOException("Batch output/request must be outside game/toolkit");
            var edits=JsonSerializer.Deserialize<Edit[]>(File.ReadAllText(request))??throw new IOException("No edits");
            if(edits.Length==0)throw new IOException("No edits");
            HeadlessToolkit.InitializeHashes();
            string work=Path.Combine(Path.GetDirectoryName(report)!,"batch-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(work);
            using var input=new FileStream(source,FileMode.Open,FileAccess.Read,FileShare.Read);
            var entries=ForgeStream.Read(input);var replacements=new List<string>();
            var groups=edits.GroupBy(e=>Logical(e.data),StringComparer.OrdinalIgnoreCase).ToArray();
            int done=0;
            foreach(var group in groups) {
                Console.WriteLine($"Preparing container {++done}/{groups.Length}: {group.Key} ({group.Count()} edits)");
                var candidates=entries.Where(e=>Logical(e.Name+".data").Equals(group.Key,StringComparison.OrdinalIgnoreCase)).ToArray();
                if(candidates.Length!=1)throw new IOException("Container name is absent or ambiguous: "+group.Key);
                var entry=candidates[0];
                if(group.Any(e=>e.kind=="data")) {
                    if(group.Count()!=1)throw new IOException("Whole-container/nested conflict");
                    string payload=Path.GetFullPath(group.Single().payload);
                    if(new FileInfo(payload).Length<=0 || new FileInfo(payload).Length>int.MaxValue)
                        throw new IOException("Whole-container replacement exceeds Forge signed-32-bit size limit");
                    var identity=DataFile.CreateForgeEntry(payload,0,Game.GhostReconBreakpoint);
                    if(identity.ID!=entry.Id||identity.Extension!=entry.Type)throw new IOException("Whole-container ID/type mismatch");
                    string wholeDir=Path.Combine(work,entry.Ordinal.ToString());Directory.CreateDirectory(wholeDir);
                    string originalData=Path.Combine(wholeDir,$"{entry.Ordinal}_-_{entry.Name}.data");
                    using(var copy=File.Create(originalData))ForgeStream.CopyRange(input,copy,entry.Offset,entry.Length);
                    string merged=WholeDataMerge.Prepare(originalData,payload,Path.Combine(wholeDir,"Merge"));
                    replacements.Add(merged);continue;
                }
                if(group.Any(e=>e.kind!="inner"))throw new IOException("Unknown edit kind");
                string dir=Path.Combine(work,entry.Ordinal.ToString());Directory.CreateDirectory(dir);
                string copied=Path.Combine(dir,$"{entry.Ordinal}_-_{entry.Name}.data");
                using(var dest=new FileStream(copied,FileMode.CreateNew,FileAccess.Write))ForgeStream.CopyRange(input,dest,entry.Offset,entry.Length);
                string extracted=Path.Combine(dir,"Extracted",Path.GetFileName(copied));
                var read=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,copied,extracted);
                if(read.failed)throw new IOException(read.message);
                var index=new Dictionary<(ulong,uint),List<string>>();
                foreach(string file in Directory.GetFiles(extracted)) {
                    if(!Path.GetFileName(file).Contains("_-_"))continue;
                    if(new[]{".PrefetchingInfo",".Dependency",".FilterTable",".xml",".Prefab"}.Contains(Path.GetExtension(file),StringComparer.OrdinalIgnoreCase))continue;
                    var id=HeadlessToolkit.Identity(file);if(!index.TryGetValue(id,out var list))index[id]=list=new();list.Add(file);
                }
                var changed=new HashSet<(ulong,uint)>();
                foreach(var edit in group) {
                    var id=HeadlessToolkit.Identity(edit.payload);
                    if(!changed.Add(id))throw new IOException("Two edits target the same binary resource ID/type");
                    if(!index.TryGetValue(id,out var hits)||hits.Count!=1)throw new IOException("Resource ID/type is absent or ambiguous: "+edit.payload);
                    // A resource-ID match is necessary but should also agree with the
                    // previously characterized visible resource name where available.
                    if(!string.IsNullOrWhiteSpace(edit.@object) &&
                        !Stem(hits[0]).Equals(Stem(edit.@object),StringComparison.OrdinalIgnoreCase))
                        throw new IOException("Resource name differs from characterized route: "+edit.@object+" -> "+Path.GetFileName(hits[0]));
                    File.Copy(edit.payload,hits[0],true);
                }
                var expected=NestedTrial.Index(extracted);string packedDir=Path.Combine(dir,"Packed");Directory.CreateDirectory(packedDir);
                string packed=Path.Combine(packedDir,Path.GetFileName(copied));
                var write=NestedTrial.RepackData(typeof(DataFile),typeof(Game),Game.GhostReconBreakpoint,copied,extracted,packed);
                if(write.failed)throw new IOException(write.message);
                string verified=Path.Combine(dir,"Readback",Path.GetFileName(copied));
                read=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,packed,verified);
                if(read.failed)throw new IOException(read.message);
                var actual=NestedTrial.Index(verified);
                if(expected.Count!=actual.Count||expected.Any(e=>!actual.TryGetValue(e.Key,out var hash)||hash!=e.Value))throw new IOException("Batch nested round-trip mismatch");
                replacements.Add(packed);
            }
            var receipt=ForgeStream.ReplaceMany(source,replacements.ToArray(),output);
            var result=JsonSerializer.Deserialize<Dictionary<string,object>>(JsonSerializer.Serialize(receipt))!;
            result["allRequestedEditsVerified"]=true;result["requestedEdits"]=edits.Length;result["containerCount"]=groups.Length;
            result["writerMode"]="STREAM_BATCH_V27";result["gameCompatibilityTested"]=false;
            File.WriteAllText(report,JsonSerializer.Serialize(result,new JsonSerializerOptions{WriteIndented=true}));return 0;
        }catch(Exception e){Console.Error.WriteLine(e);Directory.CreateDirectory(Path.GetDirectoryName(report)!);File.WriteAllText(report,JsonSerializer.Serialize(new{verified=false,error=e.ToString()}));return 1;}
    }
}
