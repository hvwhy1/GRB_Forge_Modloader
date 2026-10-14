using System.IO;
using System.Text;
using System.Text.Json;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

internal static class ForgeDirectoryRepair {
    internal sealed record Change(long offset,int length,string field,long oldValue,long newValue);
    internal static List<Change> Normalize(Stream stream) {
        var changes=new List<Change>();
        using var r=new BinaryReader(stream,Encoding.UTF8,true);
        using var w=new BinaryWriter(stream,Encoding.UTF8,true);
        void Set(long at,int length,string field,long value) {
            stream.Position=at;long old=length==4?r.ReadInt32():r.ReadInt64();
            if(old==value)return;
            stream.Position=at;if(length==4)w.Write(checked((int)value));else w.Write(value);
            changes.Add(new(at,length,field,old,value));
        }
        stream.Position=13;long header=r.ReadInt64();
        stream.Position=header;int total=r.ReadInt32();
        stream.Position=header+32;int count=r.ReadInt32();long next=r.ReadInt64();
        var sets=new List<(long address,int count,int capacity)>();var seen=new HashSet<long>();
        for(int i=0;i<count;i++) {
            if(next<0||next>stream.Length-48||!seen.Add(next))throw new IOException("Invalid directory chain");
            long address=next;stream.Position=address;int used=r.ReadInt32();
            stream.Position=address+16;next=r.ReadInt64();int first=r.ReadInt32(),last=r.ReadInt32();
            if(used<0||last<first||last-first+1<used)throw new IOException("Invalid file-set capacity");
            if(used>0)sets.Add((address,used,last-first+1));
        }
        if(next!=-1||sets.Count==0||sets.Sum(s=>s.count)!=total)throw new IOException("File-set counts differ");
        int cumulative=0;
        for(int i=0;i<sets.Count;i++) {
            var set=sets[i];
            Set(set.address+24,4,"FirstIndex",cumulative);
            Set(set.address+28,4,"LastIndex",checked(cumulative+set.capacity-1));
            Set(set.address+16,8,"NextDirectory",i+1<sets.Count?sets[i+1].address:-1L);
            cumulative=checked(cumulative+set.count);
        }
        Set(header+32,4,"DirectoryCount",sets.Count);
        Set(header+36,8,"FirstDirectory",sets[0].address);
        w.Flush();return changes;
    }
    internal static int Run(string[] args) {
        if(args.Length!=5)throw new IOException("repair-directory-ranges <toolkit> <source.forge> <output.forge> <proof.json>");
        string source=Path.GetFullPath(args[2]),output=Path.GetFullPath(args[3]),proof=Path.GetFullPath(args[4]);
        if(Trial.Inside(Path.GetDirectoryName(source)!,output)||File.Exists(output))throw new IOException("Output must be a new private copy outside source directory");
        HeadlessToolkit.InitializeHashes();string sourceHash=Trial.Hash(source);
        List<ForgeStream.Entry> before,after;List<Change> changes;
        using(var original=File.OpenRead(source)) {
            before=ForgeStream.Read(original);Directory.CreateDirectory(Path.GetDirectoryName(output)!);
            using var dest=new FileStream(output,FileMode.CreateNew,FileAccess.ReadWrite,FileShare.None);
            ForgeStream.CopyRange(original,dest,0,original.Length);changes=Normalize(dest);dest.Flush(true);after=ForgeStream.Read(dest);
            if(before.Count!=after.Count)throw new IOException("Repair changed entry count");
            for(int i=0;i<before.Count;i++)if(before[i]!=after[i])throw new IOException("Repair changed an entry table or metadata");
            long cursor=0;
            foreach(var c in changes.OrderBy(c=>c.offset)) {
                if(c.offset<cursor)throw new IOException("Overlapping header edits");
                if(ForgeStream.HashRange(original,cursor,c.offset-cursor)!=ForgeStream.HashRange(dest,cursor,c.offset-cursor))throw new IOException("Unrelated bytes changed");
                cursor=c.offset+c.length;
            }
            if(ForgeStream.HashRange(original,cursor,original.Length-cursor)!=ForgeStream.HashRange(dest,cursor,original.Length-cursor))throw new IOException("Payload bytes changed");
        }
        var atk=new ForgeFile{ShowMessages=true};atk.Deserialize(Path.GetDirectoryName(output)!,output,Game.GhostReconBreakpoint,true,true,true);
        if(atk.Directories==null)throw new IOException("Independent Anvil readback failed");
        var entries=atk.Directories.SelectMany(d=>d.Entries).ToArray();
        if(entries.Length!=after.Count)throw new IOException("Anvil entry count differs");
        for(int i=0;i<entries.Length;i++)if(entries[i].ID!=after[i].Id||entries[i].Extension!=after[i].Type||entries[i].Offset!=after[i].Offset||entries[i].LengthOnDisk!=after[i].Length)throw new IOException("Anvil entry identity differs");
        if(Trial.Hash(source)!=sourceHash)throw new IOException("Source changed");
        File.WriteAllText(proof,JsonSerializer.Serialize(new{verified=true,sourceUnchanged=true,independentAnvilIndexVerified=true,allPayloadAndEntryBytesPreserved=true,populatedGlobalIndexesConsecutive=true,sourceForge=source,sourceSha256=sourceHash,output,outputSha256=Trial.Hash(output),changes,gameModified=false,gameplayVerified=false},new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine($"Directory ranges repaired: {changes.Count} header fields; {after.Count} entries unchanged");return 0;
    }
}
