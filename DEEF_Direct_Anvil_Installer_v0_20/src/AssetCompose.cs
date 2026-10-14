using System.Text.Json;
using System.IO;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

// Compose authored containers using binary identity, never unstable ordinals.
// Copies a complete existing base parent before creating a patch override.
internal static class AssetCompose {
    internal sealed class Request {public string name{get;set;}="";public string payload{get;set;}="";}
    internal static int Run(string[] args) {
        string? report=args.Length>5?Path.GetFullPath(args[5]):null;
        try {
            if(args.Length!=6)throw new IOException("compose-assets <toolkit> <view-root> <target.forge> <request.json> <report.json>");
            string root=Path.GetFullPath(args[2]),source=Path.GetFullPath(args[3]),req=Path.GetFullPath(args[4]);
            if(Path.GetDirectoryName(source)!=root||Trial.Inside(root,report!)||Trial.Inside(root,req))throw new IOException("Unsafe private composition paths");
            HeadlessToolkit.InitializeHashes();
            var requests=JsonSerializer.Deserialize<Request[]>(File.ReadAllText(req))!;
            string work=Path.Combine(Path.GetDirectoryName(report!)!,"Composition");Directory.CreateDirectory(work);
            var inventories=new Dictionary<string,List<ForgeStream.Entry>>();
            foreach(string forge in Directory.GetFiles(root,"*.forge")) {using var f=File.OpenRead(forge);inventories[forge]=ForgeStream.Read(f);}
            var rows=new List<object>();var newPayloads=new List<object>();var patchOverrides=new List<object>();var newAuthored=new List<object>();var replacementPayloads=new List<string>();
            var seen=new Dictionary<(ulong,uint),(string path,string sha)>();
            foreach(var item in requests) {
                string payload=Path.GetFullPath(item.payload);if(Trial.Inside(root,payload))throw new IOException("Payload inside input view");
                var meta=DataFile.CreateForgeEntry(payload,0,Game.GhostReconBreakpoint);var key=(meta.ID,meta.Extension);string sha=Trial.Hash(payload);
                if(seen.TryGetValue(key,out var prior)) {
                    if(prior.sha!=sha)throw new IOException("COMPETING_AUTHORED_CONTAINERS: "+item.name+" and "+Path.GetFileName(prior.path));
                    rows.Add(new{name=item.name,state="IDENTICAL_SHARED_PAYLOAD",identity=meta.ID.ToString("X16")});continue;
                }
                seen[key]=(payload,sha);
                var local=inventories[source].Where(e=>e.Id==meta.ID&&e.Type==meta.Extension).ToArray();
                if(local.Length>1)throw new IOException("Duplicate target container ID");
                var candidates=local.Select(e=>(forge:source,entry:e)).ToArray();
                if(candidates.Length==0)candidates=inventories.Where(k=>k.Key!=source).SelectMany(k=>k.Value.Where(e=>e.Id==meta.ID&&e.Type==meta.Extension).Select(e=>(forge:k.Key,entry:e))).ToArray();
                if(candidates.Length>1) {
                    string Digest((string forge,ForgeStream.Entry entry) c) {using var f=File.OpenRead(c.forge);return ForgeStream.HashRange(f,c.entry.Offset,c.entry.Length);}
                    if(candidates.Select(Digest).Distinct().Count()!=1)throw new IOException("BASE_PARENT_SAME_ID_DIFFERENT_PAYLOAD: "+item.name);
                    candidates=new[]{candidates[0]};
                }
                string selected=payload;string state="NEW_AUTHORED_CONTAINER";string? baseForge=null;
                if(candidates.Length==0)selected=ContainerNormalization.Prepare(payload,Path.Combine(work,seen.Count.ToString(),"NewPacketNormalization"));
                if(candidates.Length==1) {
                    var c=candidates[0];baseForge=c.forge;
                    string clean=Path.GetFileName(item.name);int split=clean.IndexOf("_-_",StringComparison.Ordinal);if(split>0&&clean[..split].All(char.IsDigit))clean=clean[(split+3)..];
                    if(!clean.Equals(c.entry.Name+".data",StringComparison.OrdinalIgnoreCase))throw new IOException("CONTAINER_ID_NAME_MISMATCH: "+item.name+" vs "+c.entry.Name);
                    string dir=Path.Combine(work,seen.Count.ToString());Directory.CreateDirectory(dir);string original=Path.Combine(dir,c.entry.Ordinal+"_-_"+c.entry.Name+".data");
                    using(var input=File.OpenRead(c.forge))using(var copy=File.Create(original))ForgeStream.CopyRange(input,copy,c.entry.Offset,c.entry.Length);
                    selected=WholeDataMerge.Prepare(original,payload,Path.Combine(dir,"Merged"),true);
                    state=local.Length==1?"MERGED_EXISTING_PARENT":"MERGED_COMPLETE_BASE_PARENT_PATCH_OVERRIDE";
                } else {
                    string unpack=Path.Combine(work,seen.Count.ToString(),"AuthorReadback");
                    var read=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,payload,unpack);
                    if(read.failed)throw new IOException("New authored container unpack failed: "+read.message);
                    if(Directory.GetFiles(unpack).Length==0)throw new IOException("Empty new authored container");
                }
                rows.Add(new{name=item.name,state,identity=meta.ID.ToString("X16"),type=meta.Extension,payload=selected,baseForge});
                if(local.Length==1)replacementPayloads.Add(selected);
                else {
                    var addition=new{data=Path.GetFileName(selected),payload=selected};newPayloads.Add(addition);
                    if(candidates.Length==1)patchOverrides.Add(addition);else newAuthored.Add(addition);
                }
            }
            string current=source;
            if(replacementPayloads.Count>0) {
                current=Path.Combine(Path.GetDirectoryName(report!)!,"Replacements",Path.GetFileName(source));Directory.CreateDirectory(Path.GetDirectoryName(current)!);
                ForgeStream.ReplaceMany(source,replacementPayloads.ToArray(),current);
            }
            string? output=null;string? outerProof=null;
            var outerProofs=new List<string>();
            if(newPayloads.Count>0) {
                // Insert complete overrides of base-game parents before entirely
                // new parents. A separate file set keeps later additions from
                // shifting the override slots in the patch directory.
                foreach(var group in new[]{patchOverrides,newAuthored}.Where(g=>g.Count>0)) {
                    string outer=Path.Combine(Path.GetDirectoryName(report!)!,"Outer",outerProofs.Count.ToString());Directory.CreateDirectory(outer);
                    string request=Path.Combine(outer,"REQUEST.json");outerProof=Path.Combine(outer,"PROOF.json");output=Path.Combine(outer,Path.GetFileName(source));
                    File.WriteAllText(request,JsonSerializer.Serialize(group));
                    if(ForgeOuterAddTrial.Run(new[]{"stream-add-outer-trial",args[1],current,outerProof,request,output})!=0)throw new IOException("New parent Forge insertion failed");
                    outerProofs.Add(outerProof);current=output;
                }
            } else output=current;
            output ??= current;
            using(var file=File.OpenRead(output)) {
                var after=ForgeStream.Read(file);
                foreach(var key in seen.Keys)if(after.Count(e=>e.Id==key.Item1&&e.Type==key.Item2)!=1)throw new IOException("Final asset identity coverage missing");
            }
            bool layoutVerified=outerProofs.All(p=>JsonDocument.Parse(File.ReadAllText(p)).RootElement.GetProperty("payloadRegionAfterDirectoryMetadataVerified").GetBoolean());
            File.WriteAllText(report!,JsonSerializer.Serialize(new{verified=true,gameModified=false,sourceUnchanged=true,sourceForge=source,sourceSha256=Trial.Hash(source),output,outputSha256=Trial.Hash(output),independentAnvilIndexVerified=true,packedBlockChecksumsVerified=true,outerFileSetLayoutVerified=layoutVerified,outerProof,outerProofs,baseOverridesBeforeNewParents=true,rows,newContainers=newPayloads.Count,replacedContainers=replacementPayloads.Count,gameplayVerified=false},new JsonSerializerOptions{WriteIndented=true}));
            return 0;
        }catch(Exception e){Console.Error.WriteLine(e);if(report!=null)File.WriteAllText(report,JsonSerializer.Serialize(new{verified=false,error=e.ToString(),gameModified=false}));return 1;}
    }
}
