using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

// Batch-add absent objects to ONE confirmed .data parent on a private Forge copy.
// A whole parent is repacked once, and the rewritten Forge is checked twice.
internal static class StreamInnerAddBatch {
    internal sealed class Addition {
        public string name { get; set; } = "";
        public string payload { get; set; } = "";
    }
    static string Logical(string name) {
        var leaf=Path.GetFileName(name);int j=leaf.IndexOf("_-_",StringComparison.Ordinal);
        return j>0 && leaf[..j].All(char.IsDigit) ? leaf[(j+3)..] : leaf;
    }
    static bool ObjectFile(string f) {
        string ext=Path.GetExtension(f);
        return Path.GetFileName(f).Contains("_-_") &&
            !new[]{".PrefetchingInfo",".Dependency",".FilterTable",".xml",".Prefab"}
             .Contains(ext,StringComparer.OrdinalIgnoreCase);
    }
    static void SafeName(string name,bool data=false) {
        if(string.IsNullOrWhiteSpace(name)||name!=Path.GetFileName(name)||name.Contains("..")||
           name.Contains(':')||name.Contains('/')||name.Contains('\\')||
           (data && !name.EndsWith(".data",StringComparison.OrdinalIgnoreCase)))
            throw new IOException("Invalid resource name: "+name);
    }
    internal static int Run(string[] args) {
        string? report=null,output=null,scratch=null;bool outputOwned=false;
        try {
            if(args.Length!=7)throw new ArgumentException(
                "stream-add-inner-batch <toolkit> <source.forge> <existing.data> <request.json> <report.json> <output.forge>");
            string toolkit=Path.GetFullPath(args[1]);string source=Path.GetFullPath(args[2]);
            string parentName=args[3];string request=Path.GetFullPath(args[4]);
            report=Path.GetFullPath(args[5]);output=Path.GetFullPath(args[6]);
            SafeName(parentName,true);
            string root=Path.GetDirectoryName(source)!;
            if(!source.EndsWith(".forge",StringComparison.OrdinalIgnoreCase)||!File.Exists(source)||
                !File.Exists(request)||File.Exists(output)||
                new[]{request,report,output}.Any(p=>Trial.Inside(root,p)||Trial.Inside(toolkit,p)))
                throw new IOException("Unsafe output location or missing input; game/toolkit directories are read-only");
            outputOwned=true;
            var additions=JsonSerializer.Deserialize<Addition[]>(File.ReadAllText(request),
                new JsonSerializerOptions { PropertyNameCaseInsensitive=true })??Array.Empty<Addition>();
            if(additions.Length<1||additions.Length>1000)throw new IOException("Batch requires 1..1000 objects");
            var names=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var payloads=new List<(string name,string path,string sha,(ulong,uint) identity)>();
            HeadlessToolkit.InitializeHashes();
            foreach(var item in additions) {
                SafeName(item.name);
                string path=Path.GetFullPath(item.payload);
                if(Trial.Inside(root,path)||Trial.Inside(toolkit,path)||!File.Exists(path))
                    throw new IOException("Payload missing or unsafe: "+path);
                var file=new FileInfo(path);
                if(file.Length<1||file.Length>int.MaxValue)throw new IOException("Payload size invalid: "+item.name);
                if(!Logical(item.name).Equals(Logical(file.Name),StringComparison.OrdinalIgnoreCase)||
                    !names.Add(Logical(item.name)))throw new IOException("Duplicate/incorrect resource name: "+item.name);
                string extension=Path.GetExtension(item.name).TrimStart('.');
                if(extension.Length>0)HeadlessToolkit.RegisterModName(extension);
                payloads.Add((item.name,path,Trial.Hash(path),HeadlessToolkit.Identity(path)));
            }
            if(payloads.Select(p=>p.identity).Distinct().Count()!=payloads.Count)
                throw new IOException("Multiple additions have the same binary ID/type");
            string originalHash=Trial.Hash(source);
            using var src=File.OpenRead(source);
            var entries=ForgeStream.Read(src);
            var parents=entries.Where(e=>Logical(e.Name+".data").Equals(Logical(parentName),StringComparison.OrdinalIgnoreCase)).ToArray();
            if(parents.Length!=1)throw new IOException("Parent .data absent or ambiguous");
            var parent=parents[0];
            scratch=Path.Combine(Path.GetDirectoryName(report)!,"inner_batch_"+Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(scratch);
            string originalData=Path.Combine(scratch,$"{parent.Ordinal}_-_{parent.Name}.data");
            using(var dst=File.Create(originalData))ForgeStream.CopyRange(src,dst,parent.Offset,parent.Length);
            string unpack=Path.Combine(scratch,"Unpacked",Path.GetFileName(originalData));
            var action=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,originalData,unpack);
            if(action.failed)throw new IOException("Could not unpack parent: "+action.message);
            var oldFiles=Directory.GetFiles(unpack).Where(ObjectFile).ToArray();
            var oldIds=oldFiles.Select(HeadlessToolkit.Identity).ToHashSet();
            var oldNames=oldFiles.Select(f=>Logical(Path.GetFileName(f))).ToHashSet(StringComparer.OrdinalIgnoreCase);
            if(payloads.Any(p=>oldIds.Contains(p.identity)||oldNames.Contains(Logical(p.name))))
                throw new IOException("Existing binary ID or logical object name collision: not an addition");
            int highest=-1;
            foreach(var f in Directory.GetFiles(unpack)) {
                string token=Path.GetFileName(f).Split("_-_",2)[0];
                if(int.TryParse(token,out int n))highest=Math.Max(highest,n);
            }
            var addedNames=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
            foreach(var item in payloads) {
                string named=checked(++highest).ToString()+"_-_"+Logical(item.name);
                File.Copy(item.path,Path.Combine(unpack,named),false);
                addedNames[named]=item.sha;
            }
            var intended=NestedTrial.Index(unpack);
            string packdir=Path.Combine(scratch,"Packed");Directory.CreateDirectory(packdir);
            string packed=Path.Combine(packdir,Path.GetFileName(originalData));
            var repack=NestedTrial.RepackData(typeof(DataFile),typeof(Game),Game.GhostReconBreakpoint,
                originalData,unpack,packed);
            if(repack.failed)throw new IOException("Parent .data repack failed: "+repack.message);
            void VerifyExtract(string data,string dest) {
                var result=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,data,dest);
                if(result.failed)throw new IOException("Inner readback failed: "+result.message);
                var read=NestedTrial.Index(dest);
                var missing=intended.Where(kv=>!read.ContainsKey(kv.Key)).Select(kv=>kv.Key).Take(8).ToArray();
                var changed=intended.Where(kv=>read.TryGetValue(kv.Key,out string? sha)&&sha!=kv.Value)
                    .Select(kv=>kv.Key).Take(8).ToArray();
                var unexpected=read.Keys.Where(k=>!intended.ContainsKey(k)).Take(8).ToArray();
                if(read.Count!=intended.Count||missing.Length>0||changed.Length>0)
                    throw new IOException($"Inner readback differs: expected {intended.Count}, got {read.Count}; " +
                        $"missing=[{string.Join(", ",missing)}]; changed=[{string.Join(", ",changed)}]; " +
                        $"unexpected=[{string.Join(", ",unexpected)}]");
                foreach(var kv in addedNames)
                    if(!read.TryGetValue(kv.Key,out string? sha)||!kv.Value.Equals(sha,StringComparison.OrdinalIgnoreCase))
                        throw new IOException("New payload failed round trip: "+kv.Key);
            }
            VerifyExtract(packed,Path.Combine(scratch,"ReadbackData",Path.GetFileName(originalData)));
            var replace=ForgeStream.ReplaceMany(source,new[]{packed},output);
            using(var rewritten=File.OpenRead(output)) {
                var updated=ForgeStream.Read(rewritten);
                if(updated.Count!=entries.Count||updated[parent.Ordinal].Id!=parent.Id||updated[parent.Ordinal].Type!=parent.Type)
                    throw new IOException("Outer Forge structure changed unexpectedly");
                string copy=Path.Combine(scratch,"FromOuter",Path.GetFileName(originalData));
                Directory.CreateDirectory(Path.GetDirectoryName(copy)!);
                using(var dst=File.Create(copy))ForgeStream.CopyRange(rewritten,dst,updated[parent.Ordinal].Offset,updated[parent.Ordinal].Length);
                VerifyExtract(copy,Path.Combine(scratch,"FullReadback",Path.GetFileName(originalData)));
            }
            if(Trial.Hash(source)!=originalHash||payloads.Any(x=>Trial.Hash(x.path)!=x.sha))
                throw new IOException("Input modified during staging");
            File.WriteAllText(report,JsonSerializer.Serialize(new {
                verified=true,mode="PRIVATE_BATCH_INNER_INSERTION",gameModified=false,
                gameCompatibilityTested=false,eligibleForDeployment=true,
                independentAnvilIndexVerified=true,sourceUnchanged=true,
                sourceForge=source,sourceSha256=originalHash,output,
                outputSha256=Trial.Hash(output),addedEntries=payloads.Count,
                parent=parentName,added=addedNames.Keys.ToArray(),outerStreamReceipt=replace,
                warning="Structural + binary verification passed, but gameplay compatibility is untested. Never install without backups."
            },new JsonSerializerOptions{WriteIndented=true}));
            Console.WriteLine("BATCH INNER INSERTION VERIFIED ON PRIVATE COPY: "+payloads.Count);
            return 0;
        }catch(Exception ex) {
            Console.Error.WriteLine(ex);
            if(outputOwned&&output!=null)try {File.Delete(output);}catch(Exception){}
            if(report!=null) {
                Directory.CreateDirectory(Path.GetDirectoryName(report)!);
                File.WriteAllText(report,JsonSerializer.Serialize(new {
                    verified=false,eligibleForDeployment=false,gameModified=false,
                    mode="PRIVATE_BATCH_INNER_INSERTION_FAILED",error=ex.ToString()
                },new JsonSerializerOptions{WriteIndented=true}));
            }
            return 1;
        } finally {
            if(scratch!=null && Directory.Exists(scratch)) {
                try { Directory.Delete(scratch,true); }
                catch(Exception ex) { Console.Error.WriteLine("Private scratch cleanup failed: "+ex.Message); }
            }
        }
    }
}
