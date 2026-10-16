using System.IO;
using System.Text.Json;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

internal static class StreamCommands
{
    static string Logical(string s) {
        int p=s.IndexOf("_-_",StringComparison.Ordinal);
        return p>0 && s[..p].All(char.IsDigit)?s[(p+3)..]:s;
    }
    static bool Resource(string p)=>Path.GetFileName(p).Contains("_-_") && !new[]{".PrefetchingInfo",".Dependency",".FilterTable",".xml",".Prefab"}.Contains(Path.GetExtension(p),StringComparer.OrdinalIgnoreCase);
    static string Label(ForgeStream.Entry e)=>$"{e.Ordinal}_-_{e.Name}.data";
    internal static int Run(string[] args)
    {
        string? report=null;
        try {
            bool inspect=args[0]=="stream-inspect",whole=args[0]=="stream-stage-data";
            if(args.Length!=(inspect?5:whole?7:8))throw new ArgumentException("Invalid stream command arguments");
            string toolkit=Path.GetFullPath(args[1]),source=Path.GetFullPath(args[2]);report=Path.GetFullPath(args[3]);
            if(Trial.Inside(Path.GetDirectoryName(source)!,report)||Trial.Inside(toolkit,report))throw new IOException("Report must be outside source/toolkit folders");
            string scratch=Path.Combine(Path.GetDirectoryName(report)!,"stream-"+Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(scratch);
            using var input=new FileStream(source,FileMode.Open,FileAccess.Read,FileShare.Read);
            var entries=ForgeStream.Read(input);
            var result=new Dictionary<string,object?> { ["verified"]=false,["gameModified"]=false,["sourceForge"]=source,
                ["nestedDataEntries"]=entries.Where(e=>e.Id!=16 && e.Id!=145).Select(Label).ToArray(),
                ["eligibleForDeployment"]=false,["nameEvidence"]="VERSION_27_ENTRY_INDEX",
                ["writerCapability"]="VERIFIED_STREAM_REPLACEMENT_V27" };
            if(inspect && args[4].Equals("AUTO",StringComparison.OrdinalIgnoreCase)) {
                var atk=new ForgeFile { ShowMessages=false };
                atk.Deserialize(Path.GetDirectoryName(source)!,source,Game.GhostReconBreakpoint,true,true,true);
                if(atk.Directories is null)throw new InvalidDataException("Anvil returned no Forge directories");
                var independent=atk.Directories.SelectMany(d=>d.Entries).ToArray();
                if(independent.Length!=entries.Count)throw new InvalidDataException("Anvil index count mismatch");
                for(int i=0;i<entries.Count;i++) {
                    var a=independent[i];var b=entries[i];
                    if(a.ID!=b.Id || a.Extension!=b.Type || a.Offset!=b.Offset || a.LengthOnDisk!=b.Length)
                        throw new InvalidDataException("Anvil index identity/location mismatch");
                }
                result["independentAnvilIndexVerified"]=true;
            }
            if(!inspect || !args[4].Equals("AUTO",StringComparison.OrdinalIgnoreCase)) {
                HeadlessToolkit.InitializeHashes();
                var matches=entries.Where(e=>Logical(Label(e)).Equals(Logical(args[4]),StringComparison.OrdinalIgnoreCase)).ToArray();
                if(matches.Length!=1)throw new IOException($"Container name must resolve uniquely; got {matches.Length}");
                var entry=matches[0];
                string copied=Path.Combine(scratch,Label(entry));
                using(var dest=new FileStream(copied,FileMode.CreateNew,FileAccess.Write))ForgeStream.CopyRange(input,dest,entry.Offset,entry.Length);
                string folder=Path.Combine(scratch,"Extracted",Path.GetFileName(copied));
                Dictionary<string,string>? expected=null;string payload="";string packed="";
                if(!whole) {
                    var unpack=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,copied,folder);
                    if(unpack.failed)throw new IOException(unpack.message);
                    var files=Directory.GetFiles(folder).Where(Resource).ToArray();
                    result["nestedObjects"]=files.Select(Path.GetFileName).ToArray();
                    if(!inspect) {
                        payload=Path.GetFullPath(args[6]);var identity=HeadlessToolkit.Identity(payload);
                        var objects=files.Where(p=>HeadlessToolkit.Identity(p)==identity).ToArray();
                        if(objects.Length!=1)throw new IOException($"Resource ID/type must resolve uniquely; got {objects.Length}");
                        File.Copy(payload,objects[0],true);expected=NestedTrial.Index(folder);
                        string packRoot=Path.Combine(scratch,"Packed");Directory.CreateDirectory(packRoot);
                        packed=Path.Combine(packRoot,Path.GetFileName(copied));
                        var write=NestedTrial.RepackData(typeof(DataFile),typeof(Game),Game.GhostReconBreakpoint,copied,folder,packed);
                        if(write.failed)throw new IOException(write.message);
                        string verify=Path.Combine(scratch,"Readback",Path.GetFileName(copied));
                        var read=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,packed,verify);
                        if(read.failed)throw new IOException(read.message);
                        var actual=NestedTrial.Index(verify);
                        if(expected.Count!=actual.Count || expected.Any(k=>!actual.TryGetValue(k.Key,out var h)||h!=k.Value))throw new IOException("Nested bytes or metadata changed during repack");
                        result["finalEditedPayloadVerified"]=true;
                        result["resolvedObject"]=Path.GetFileName(objects[0]);
                    }
                } else {
                    payload=Path.GetFullPath(args[5]);
                    packed=WholeDataMerge.Prepare(copied,payload,Path.Combine(scratch,"WholeMerge"));
                }
                if(!inspect) {
                    var dataEntry=DataFile.CreateForgeEntry(packed,0,Game.GhostReconBreakpoint);
                    if(dataEntry.ID!=entry.Id || dataEntry.Extension!=entry.Type)throw new IOException("Replacement container ID/type differs from selected target");
                    string outputRoot=Path.GetFullPath(args[whole?6:7]);
                    if(Trial.Inside(Path.GetDirectoryName(source)!,outputRoot)||Trial.Inside(toolkit,outputRoot))throw new IOException("Output must be outside source/toolkit folders");
                    Directory.CreateDirectory(outputRoot);
                    var receipt=ForgeStream.Replace(source,packed,Path.Combine(outputRoot,Path.GetFileName(source)));
                    foreach(var kv in JsonSerializer.Deserialize<Dictionary<string,JsonElement>>(JsonSerializer.Serialize(receipt))!)result[kv.Key]=kv.Value;
                    if(whole){result["finalDataMergedFromModPayload"]=true;result["allOriginalNestedMembersPreserved"]=true;}
                    result["eligibleForDeployment"]=true;
                }
            }
            result["verified"]=true;
            File.WriteAllText(report,JsonSerializer.Serialize(result,new JsonSerializerOptions{WriteIndented=true}));
            Console.WriteLine("Stream command verified: "+report);return 0;
        } catch(Exception e) {
            Console.Error.WriteLine(e);
            if(report!=null){Directory.CreateDirectory(Path.GetDirectoryName(report)!);File.WriteAllText(report,JsonSerializer.Serialize(new {verified=false,error=e.ToString()}));}
            return 1;
        }
    }
}
