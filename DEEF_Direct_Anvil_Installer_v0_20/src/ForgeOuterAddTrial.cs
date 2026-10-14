using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

// Experimental private-copy Forge v27 directory extension. No installation/export path.
// The game compatibility of newly synthesized 192-byte Forge entry metadata remains untested.
internal static class ForgeOuterAddTrial
{
    internal sealed class Addition {
        public string data { get; set; } = "";
        public string payload { get; set; } = "";
    }
    static long Align(long n) => checked((n + 32767L) / 32768L * 32768L);
    static string Logical(string s) {
        string t=Path.GetFileName(s);int j=t.IndexOf("_-_",StringComparison.Ordinal);
        return j>0 && t[..j].All(char.IsDigit) ? t[(j+3)..] : t;
    }
    static void CheckFileName(string s) {
        if(string.IsNullOrWhiteSpace(s) || Path.GetFileName(s)!=s || s.Contains("..") || !s.EndsWith(".data",StringComparison.OrdinalIgnoreCase))
            throw new IOException("Invalid .data entry name");
    }
    static (long header, long last) Header(Stream source) {
        using var r=new BinaryReader(source,Encoding.UTF8,true);
        source.Position=9;if(r.ReadUInt32()!=27)throw new IOException("Only v27 supported");
        long header=r.ReadInt64();
        source.Position=header+32;int dircount=r.ReadInt32();long next=r.ReadInt64();
        if(dircount<1||dircount>10000)throw new IOException("Invalid directory count");
        var seen=new HashSet<long>();long last=-1;
        for(int i=0;i<dircount;i++) {
            if(next<0 || next>source.Length-48 || !seen.Add(next))throw new IOException("Invalid directory chain");
            last=next;source.Position=next+16;next=r.ReadInt64();
        }
        if(next!=-1)throw new IOException("Unexpected directory chain tail");
        return (header,last);
    }
    static void WriteAt(BinaryWriter w,long position,int value){w.BaseStream.Position=position;w.Write(value);}
    static void WriteAt(BinaryWriter w,long position,long value){w.BaseStream.Position=position;w.Write(value);}
    internal static int Run(string[] args) {
        string? report=args.Length>3?Path.GetFullPath(args[3]):null;
        string? privateOutput = null;
        string phase = "INPUT_VALIDATION";
        try {
            if(args.Length!=6)throw new IOException("stream-add-outer-trial <toolkit> <source.forge> <report.json> <request.json> <private-output.forge>");
            string toolkit=Path.GetFullPath(args[1]),source=Path.GetFullPath(args[2]);
            string request=Path.GetFullPath(args[4]), output=Path.GetFullPath(args[5]);
            string game=Path.GetDirectoryName(source)!;
            foreach(var p in new[]{report!,request,output})
                if(Trial.Inside(game,p)||Trial.Inside(toolkit,p))throw new IOException("Private output/report/request must be outside game and toolkit");
            if(!File.Exists(source)||!File.Exists(request)||File.Exists(output))throw new IOException("Inputs missing or private output already exists");
            privateOutput=output;
            var additions=JsonSerializer.Deserialize<Addition[]>(File.ReadAllText(request),new JsonSerializerOptions{PropertyNameCaseInsensitive=true})??Array.Empty<Addition>();
            if(additions.Length==0||additions.Length>200)throw new IOException("Expected 1 to 200 new entries for a trial");
            HeadlessToolkit.InitializeHashes();
            using var original=File.Open(source,FileMode.Open,FileAccess.Read,FileShare.Read);
            var before=ForgeStream.Read(original);
            var (header,last)=Header(original);
            original.Position=header+32;
            using var directoryCountReader=new BinaryReader(original,Encoding.UTF8,true);
            int originalDirectoryCount=directoryCountReader.ReadInt32();
            var names=new HashSet<string>(before.Select(e=>Logical(e.Name.EndsWith(".data",StringComparison.OrdinalIgnoreCase)
                ?e.Name:e.Name+".data")),StringComparer.OrdinalIgnoreCase);
            var ids=new HashSet<(ulong,uint)>(before.Select(e=>(e.Id,e.Type)));
            var payloads=new List<(string name,string path,string sha,ForgeEntry entry)>();
            foreach(var a in additions) {
                CheckFileName(a.data);
                HeadlessToolkit.RegisterModName(Logical(a.data)[..^5]);
                string path=Path.GetFullPath(a.payload);if(Trial.Inside(game,path)||Trial.Inside(toolkit,path))throw new IOException("Payload must be external to game/toolkit");
                var file=new FileInfo(path);
                if(!file.Exists||file.Length<=0||file.Length>int.MaxValue)throw new IOException("Invalid payload size");
                if(!Logical(a.data).Equals(Logical(file.Name),StringComparison.OrdinalIgnoreCase))throw new IOException("Name mismatch: "+a.data);
                var meta=DataFile.CreateForgeEntry(path,0,Game.GhostReconBreakpoint);
                PackedDataAudit.VerifyFile(path);
                // Different root classes can legitimately share a display name
                // (e.g. weapon Skeleton and EntityBuilder containers). Their
                // binary ID/type pairs, not extracted filenames, distinguish them.
                if(meta.LengthOnDisk!=file.Length || !ids.Add((meta.ID,meta.Extension)))
                    throw new IOException("Existing/colliding binary identity or name: "+a.data);
                payloads.Add((Logical(a.data),path,Trial.Hash(path),meta));
            }
            // Create every metadata field from the actual payload using ATK.
            // Never inherit another entry's parent, metafile key, flags or date.
            string sourceHash=Trial.Hash(source);
            Directory.CreateDirectory(Path.GetDirectoryName(output)!);
            List<ForgeStream.Entry> after;
            using(var dest=new FileStream(output,FileMode.CreateNew,FileAccess.ReadWrite,FileShare.None)) {
                ForgeStream.CopyRange(original,dest,0,original.Length);
                var offsets=new List<long>();
                // Match AnvilToolkit's ForgeDirectory.WriteToFile27 layout. GRB
                // assigns each file set a 5,000-index range and reserves the
                // full offset/metadata tables even when the set has one entry.
                int setIndex=originalDirectoryCount;
                int capacity=Math.Max(5000,payloads.Count);
                long newdir=Align(dest.Length),table=checked(newdir+48L);
                long info=checked(table+capacity*20L);
                long metadataEnd=checked(info+capacity*192L);
                long setEnd=checked(metadataEnd+capacity*176L+1L);
                dest.SetLength(Align(setEnd));
                // Match ATK's physical file-set order too: directory header,
                // reserved tables, lost-and-found region, then aligned payloads.
                // The index reader alone also accepts payloads before their set,
                // which is insufficient evidence that the game accepts that layout.
                foreach(var a in payloads) {
                    long offset=Align(dest.Length);dest.Position=offset;
                    using var file=File.OpenRead(a.path);ForgeStream.CopyRange(file,dest,0,file.Length);
                    offsets.Add(offset);
                }
                dest.SetLength(Align(dest.Length));
                using var writer=new BinaryWriter(dest,Encoding.UTF8,true);
                // Standard GRB v27 file-set header. The last two index fields
                // describe the slot range ATK derives from SetIndex and capacity.
                dest.Position=newdir;writer.Write(payloads.Count);writer.Write(setIndex==0?2:0);
                writer.Write(table);writer.Write(-1L);
                // GRB's populated global indexes must fit its total-entry array.
                // Generic ATK SetIndex*5000 creates out-of-range indexes on small patches.
                writer.Write(before.Count);
                writer.Write(checked(before.Count+capacity-1));
                writer.Write(info);writer.Write(metadataEnd);
                for(int i=0;i<payloads.Count;i++) {
                    var a=payloads[i];
                    dest.Position=table+i*20L;writer.Write(offsets[i]);writer.Write(a.entry.ID);writer.Write(a.entry.LengthOnDisk);
                    byte[] record=MetadataRecord(a.entry,a.name[..^5],i);
                    dest.Position=info+i*192L;writer.Write(record);
                }
                WriteAt(writer,last+16,newdir);
                int newCount=checked(before.Count+payloads.Count);
                WriteAt(writer,header,newCount);
                original.Position=header+20;
                using var r=new BinaryReader(original,Encoding.UTF8,true);
                int hint0=r.ReadInt32(),hint1=r.ReadInt32(),hint2=r.ReadInt32();
                int dirs=r.ReadInt32();
                if(dirs!=originalDirectoryCount)throw new IOException("Source directory count changed during trial");
                Console.WriteLine($"Forge v27 count hints: ({hint0}, {hint1}, {hint2}), original entries: {before.Count}; old directories: {dirs}");
                // GRB v27 normally has two N-based index hints. Preserve unknown
                // hints rather than blindly overwriting a possible capacity field.
                bool hint0changed=hint0==before.Count-1;
                bool hint1changed=hint1==before.Count;
                bool hint2changed=hint2==before.Count;
                if(hint0changed)WriteAt(writer,header+20,newCount-1);
                if(hint1changed)WriteAt(writer,header+24,newCount);
                if(hint2changed)WriteAt(writer,header+28,newCount);
                WriteAt(writer,header+32,checked(dirs+1));
                writer.Flush();dest.Flush(true);
                var rangeEdits=ForgeDirectoryRepair.Normalize(dest);dest.Flush(true);
                phase="LOCAL_FORGE_READBACK";
                after=ForgeStream.Read(dest);
                if(after.Count!=before.Count+payloads.Count)throw new IOException("Added directory not indexed");
                foreach(var old in before){var found=after[old.Ordinal];
                    if(old.Id!=found.Id||old.Type!=found.Type||old.Name!=found.Name||old.Offset!=found.Offset||old.Length!=found.Length||
                      ForgeStream.HashRange(original,old.Offset,old.Length)!=ForgeStream.HashRange(dest,found.Offset,found.Length))
                        throw new IOException("Existing resource modified: "+old.Name);
                }
                foreach(var a in payloads) {
                    var found=after.Where(e=>e.Id==a.entry.ID&&e.Type==a.entry.Extension).ToArray();
                    if(found.Length!=1||!found[0].Name.Equals(a.name[..^5],StringComparison.OrdinalIgnoreCase)||
                        !ForgeStream.HashRange(dest,found[0].Offset,found[0].Length).Equals(a.sha,StringComparison.OrdinalIgnoreCase))
                        throw new IOException("New entry readback failed: "+a.name);
                    if(found[0].Offset<Align(setEnd))throw new IOException("New payload precedes its file-set metadata");
                }
                // Verify old source bytes preserved except documented header and directory-link changes.
                var modified=new List<(long offset,long len)>{(header,4L),(header+32,4L),(last+16,8L)};
                if(hint0changed)modified.Add((header+20,4L));
                if(hint1changed)modified.Add((header+24,4L));
                if(hint2changed)modified.Add((header+28,4L));
                modified.AddRange(rangeEdits.Where(c=>c.offset<original.Length).Select(c=>(c.offset,(long)c.length)));
                var edited=modified.Distinct().OrderBy(x=>x.offset);
                long cursor=0;
                foreach(var (offset,len) in edited){if(offset<cursor)throw new IOException("Header overlap");
                    if(ForgeStream.HashRange(original,cursor,offset-cursor)!=ForgeStream.HashRange(dest,cursor,offset-cursor))throw new IOException("Unrelated source bytes changed");cursor=offset+len;}
                if(ForgeStream.HashRange(original,cursor,original.Length-cursor)!=ForgeStream.HashRange(dest,cursor,original.Length-cursor))throw new IOException("Unrelated source bytes changed");
            }
            if(Trial.Hash(source)!=sourceHash||payloads.Any(p=>Trial.Hash(p.path)!=p.sha))throw new IOException("Inputs changed during trial");
            // Close the private writer before asking AnvilToolkit to reopen the
            // output. Keeping FileShare.None here caused ATK's readback to fail
            // with a Windows sharing violation, which ATK swallowed and exposed
            // only as a null Directories property.
            phase="INDEPENDENT_ANVIL_INDEX_READBACK";
            var atk=new ForgeFile{ShowMessages=true};
            try {
                atk.Deserialize(Path.GetDirectoryName(output)!,output,Game.GhostReconBreakpoint,true,true,true);
            } catch(Exception ex) {
                throw new InvalidDataException("AnvilToolkit refused the synthesized Forge; insertion metadata is NOT validated.",ex);
            }
            if(atk.Directories==null)
                throw new InvalidDataException("AnvilToolkit returned NULL Directories for the synthesized Forge after reopening it. " +
                    "The private entry cannot be installed.");
            if(atk.Directories.Any(x=>x==null || x.Entries==null))
                throw new InvalidDataException("AnvilToolkit returned a missing directory or entry list for the synthesized Forge.");
            var atkEntries=atk.Directories.SelectMany(x=>x.Entries).ToArray();
            if(atkEntries.Length!=after.Count)throw new IOException("Independent Anvil index rejected added directory");
            for(int i=0;i<after.Count;i++)if(atkEntries[i].ID!=after[i].Id||atkEntries[i].Extension!=after[i].Type||
                atkEntries[i].Offset!=after[i].Offset||atkEntries[i].LengthOnDisk!=after[i].Length)
                throw new IOException("Independent Anvil index mismatch");
            File.WriteAllText(report!,JsonSerializer.Serialize(new{
                verified=true,mode="PRIVATE_V27_OUTER_DIRECTORY_INSERTION",sourceUnchanged=true,gameModified=false,
                addedEntries=payloads.Count,
                genericMetadataTemplateCount=0,metadataSource="ANVIL_CREATE_FORGE_ENTRY_V20",
                sourceForge=source,output,sourceSha256=sourceHash,outputSha256=Trial.Hash(output),
                packedBlockChecksumsVerified=true,payloadRegionAfterDirectoryMetadataVerified=true,populatedGlobalIndexesConsecutive=true,layoutRevision=3,
                independentAnvilIndexVerified=true,eligibleForDeployment=true,gameCompatibilityTested=false,
                warning="Structurally verified for opt-in, backed-up installation only. Game compatibility is NOT tested; keep recovery backups."
            },new JsonSerializerOptions{WriteIndented=true}));
            Console.WriteLine("PRIVATE INSERTION STRUCTURALLY VERIFIED; opt-in installation only, gameplay untested");return 0;
        }catch(Exception e){
            Console.Error.WriteLine($"OUTER ADDITION TRIAL FAILED [{phase}]: {e}");
            bool removed = false;
            if(privateOutput!=null && File.Exists(privateOutput)) {
                try { File.Delete(privateOutput); removed=true; }
                catch(Exception cleanup) { Console.Error.WriteLine("INVALID private output cleanup failed: "+cleanup.Message); }
            }
            if(report!=null){Directory.CreateDirectory(Path.GetDirectoryName(report)!);
                File.WriteAllText(report,JsonSerializer.Serialize(new{
                    verified=false, eligibleForDeployment=false, gameModified=false,
                    mode="PRIVATE_OUTER_ADDITION_REJECTED", failurePhase=phase,
                    invalidPrivateOutputDeleted=removed,
                    userMessage=phase=="INDEPENDENT_ANVIL_INDEX_READBACK"
                        ? "AnvilToolkit could not index this synthesized outer .data entry. It was excluded; existing verified replacements remain usable."
                        : "The private outer-entry trial failed before independent validation. No game file was modified.",
                    warning="Do not install experimental output. AnvilToolkit independent readback failed or the trial could not finish.",
                    error=e.ToString()
                },new JsonSerializerOptions{WriteIndented=true}));}
            return 1;
        }
    }
    internal static byte[] MetadataRecord(ForgeEntry entry,string name,int slot) {
        byte[] encoded=Encoding.UTF8.GetBytes(name);
        if(encoded.Length>127)throw new IOException("Forge name too long");
        using var ms=new MemoryStream();using var w=new BinaryWriter(ms,Encoding.UTF8,true);
        w.Write(entry.LengthOnDisk);w.Write(entry.UMACHash);w.Write(entry.EngineVersion);
        w.Write(entry.Extension);w.Write(entry.RevisionNumberData);w.Write(entry.RevisionNumberAttributes);
        // Stock GRB records use count as the final forward-link sentinel.
        w.Write(slot+1);w.Write(slot-1);w.Write(entry.Parent);w.Write(entry.TimeStamp);
        byte[] padded=new byte[128];Buffer.BlockCopy(encoded,0,padded,0,encoded.Length);w.Write(padded);
        w.Write(entry.SCCStatusData);w.Write(entry.MetaFileKey);w.Write(entry.SCCStatusAttributes);w.Write(entry.IsHidden);
        w.Flush();byte[] bytes=ms.ToArray();
        if(bytes.Length!=192)throw new IOException("Invalid v27 metadata record length");
        return bytes;
    }
}
