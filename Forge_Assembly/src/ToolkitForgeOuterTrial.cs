using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text.Json;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

// Private API trial: ask AnvilToolkit's own ForgeFile/ForgeDirectory writer to
// serialize a new directory, then require both its reader and ForgeStream to
// independently recover every old entry plus the requested new .data payload.
internal static class ToolkitForgeOuterTrial {
    internal sealed class Addition {
        public string data { get; set; } = "";
        public string payload { get; set; } = "";
    }
    static string Logical(string s) {
        string leaf=Path.GetFileName(s);int j=leaf.IndexOf("_-_",StringComparison.Ordinal);
        return j>0&&leaf[..j].All(char.IsDigit)?leaf[(j+3)..]:leaf;
    }
    static string Hash(string path) {
        using var f=File.OpenRead(path);return Convert.ToHexString(SHA256.HashData(f)).ToLowerInvariant();
    }
    static string HashRange(Stream s,long offset,long length) {
        s.Position=offset;using var sha=SHA256.Create();byte[] b=new byte[1<<20];long left=length;
        while(left>0){int n=s.Read(b,0,(int)Math.Min(b.Length,left));if(n==0)throw new EndOfStreamException();sha.TransformBlock(b,0,n,b,0);left-=n;}
        sha.TransformFinalBlock(Array.Empty<byte>(),0,0);return Convert.ToHexString(sha.Hash!).ToLowerInvariant();
    }
    internal static int Run(string[] args) {
        string? report=args.Length>3?Path.GetFullPath(args[3]):null;string? output=null;
        try {
            if(args.Length!=6)throw new ArgumentException("toolkit-add-outer-trial <toolkit> <source.forge> <report.json> <request.json> <private-output.forge>");
            string toolkit=Path.GetFullPath(args[1]),source=Path.GetFullPath(args[2]);
            string request=Path.GetFullPath(args[4]);output=Path.GetFullPath(args[5]);
            string gameRoot=Path.GetDirectoryName(source)!;
            foreach(var p in new[]{report!,request,output})
                if(Trial.Inside(gameRoot,p)||Trial.Inside(toolkit,p))throw new IOException("Private output/report/request must be outside the game and toolkit");
            if(!File.Exists(source)||!File.Exists(request)||File.Exists(output))throw new IOException("Trial input missing or output already exists");
            var requests=JsonSerializer.Deserialize<Addition[]>(File.ReadAllText(request),new JsonSerializerOptions{PropertyNameCaseInsensitive=true})??Array.Empty<Addition>();
            if(requests.Length<1||requests.Length>100)throw new IOException("Expected 1 to 100 new .data entries");
            HeadlessToolkit.InitializeHashes();
            string sourceHash=Hash(source);
            var before=Array.Empty<ForgeEntrySnapshot>();
            using(var src=File.OpenRead(source))before=ForgeStream.Read(src).Select(e=>new ForgeEntrySnapshot(e.Id,e.Type,e.Name,e.Offset,e.Length)).ToArray();
            var forge=new ForgeFile{ShowMessages=false};
            forge.Deserialize(gameRoot,source,Game.GhostReconBreakpoint,true,true,true);
            if(forge.Directories==null||forge.Directories.Any(d=>d==null||d.Entries==null))
                throw new InvalidDataException("AnvilToolkit cannot read the source Forge directory index");
            var current=forge.Directories.SelectMany(d=>d.Entries).ToArray();
            if(current.Length!=before.Length)throw new IOException("AnvilToolkit source entry count differs from streaming reader");
            var names=new HashSet<string>(current.Select(e=>Logical(e.Name)),StringComparer.OrdinalIgnoreCase);
            var identities=new HashSet<(ulong,uint)>(current.Select(e=>(e.ID,e.Extension)));
            var additions=new List<(ForgeEntry entry,string path,string name,string sha)>();
            foreach(var item in requests) {
                string name=Logical(item.data);string payload=Path.GetFullPath(item.payload);
                if(string.IsNullOrWhiteSpace(name)||name.Contains("..")||name.Contains('/')||name.Contains('\\')||!item.data.EndsWith(".data",StringComparison.OrdinalIgnoreCase))
                    throw new IOException("Unsafe Forge resource name: "+item.data);
                if(Trial.Inside(gameRoot,payload)||Trial.Inside(toolkit,payload)||!File.Exists(payload))
                    throw new IOException("Payload must exist outside the game/toolkit: "+payload);
                if(!Logical(Path.GetFileName(payload)).Equals(name,StringComparison.OrdinalIgnoreCase))
                    throw new IOException("Payload and directory name do not match: "+item.data);
                HeadlessToolkit.RegisterModName(name);
                if(!names.Add(name))throw new IOException("New mod asset already exists in the Forge: "+name);
                var entry=DataFile.CreateForgeEntry(payload,0,Game.GhostReconBreakpoint);
                if(!identities.Add((entry.ID,entry.Extension)))throw new IOException("New resource binary ID/type collides: "+name);
                entry.Name=name;
                entry.stream=File.OpenRead(payload);
                additions.Add((entry,payload,name,Hash(payload)));
            }
            var addedDirectory=new ForgeDirectory(additions.Select(x=>x.entry).ToArray()){IsCached=false};
            forge.Directories=forge.Directories.Append(addedDirectory).ToArray();
            forge.EntriesCount=forge.Directories.Sum(d=>d.Entries.Length);
            Directory.CreateDirectory(Path.GetDirectoryName(output)!);
            forge.Serialize(Path.GetDirectoryName(output)!,output,Game.GhostReconBreakpoint).GetAwaiter().GetResult();
            if(!File.Exists(output))throw new IOException("AnvilToolkit serializer returned without writing a Forge");
            HeadlessToolkit.InitializeHashes();
            var reread=new ForgeFile{ShowMessages=false};
            reread.Deserialize(Path.GetDirectoryName(output)!,output,Game.GhostReconBreakpoint,true,true,true);
            if(reread.Directories==null||reread.Directories.Any(d=>d==null||d.Entries==null))
                throw new InvalidDataException("AnvilToolkit cannot independently re-index its serialized Forge");
            var afterEntries=reread.Directories.SelectMany(d=>d.Entries).ToArray();
            if(afterEntries.Length!=before.Length+additions.Count)throw new IOException("AnvilToolkit output entry count mismatch");
            using var oldStream=File.OpenRead(source);using var newStream=File.OpenRead(output);
            var after=ForgeStream.Read(newStream);
            if(after.Count!=before.Length+additions.Count)throw new IOException("Independent streaming index entry count mismatch");
            foreach(var old in before) {
                var matches=after.Where(e=>e.Id==old.Id&&e.Type==old.Type&&e.Name==old.Name).ToArray();
                if(matches.Length!=1||HashRange(oldStream,old.Offset,old.Length)!=HashRange(newStream,matches[0].Offset,matches[0].Length))
                    throw new IOException("Existing Forge resource changed or disappeared: "+old.Name);
            }
            foreach(var add in additions) {
                var found=after.Where(e=>e.Id==add.entry.ID&&e.Type==add.entry.Extension&&e.Name.Equals(add.name,StringComparison.OrdinalIgnoreCase)).ToArray();
                if(found.Length!=1||HashRange(newStream,found[0].Offset,found[0].Length)!=add.sha)
                    throw new IOException("Added resource failed independent streaming readback: "+add.name);
            }
            foreach(var add in additions)if(Hash(add.path)!=add.sha)throw new IOException("Input payload changed during trial");
            File.WriteAllText(report!,JsonSerializer.Serialize(new{
                verified=true,mode="PRIVATE_ANVILTOOLKIT_FORGE_SERIALIZE_OUTER_INSERTION",
                eligibleForDeployment=true,independentAnvilIndexVerified=true,
                sourceUnchanged=Hash(source)==sourceHash,sourceForge=source,sourceSha256=sourceHash,
                output,outputSha256=Hash(output),addedEntries=additions.Count,gameModified=false,
                gameCompatibilityTested=false,warning="Private AnvilToolkit serializer trial passed structural and byte readback; gameplay compatibility remains untested."
            },new JsonSerializerOptions{WriteIndented=true}));
            Console.WriteLine("AnvilToolkit ForgeFile.Serialize outer insertion verified on private copy.");return 0;
        } catch(Exception ex) {
            Console.Error.WriteLine(ex);
            if(output!=null&&File.Exists(output))try{File.Delete(output);}catch{}
            if(report!=null){Directory.CreateDirectory(Path.GetDirectoryName(report)!);File.WriteAllText(report,JsonSerializer.Serialize(new{
                verified=false,eligibleForDeployment=false,gameModified=false,mode="PRIVATE_ANVILTOOLKIT_FORGE_SERIALIZE_REJECTED",error=ex.ToString()
            },new JsonSerializerOptions{WriteIndented=true}));}
            return 1;
        }
    }
    private sealed record ForgeEntrySnapshot(ulong Id,uint Type,string Name,long Offset,int Length);
}
