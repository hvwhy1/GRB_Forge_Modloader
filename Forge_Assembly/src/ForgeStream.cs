using System.IO;
using System.Text;
using System.Security.Cryptography;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

// Version-27 replacement writer. Existing payloads and metadata stay in place;
// changed payloads are appended and only their offset, size and hash are updated.
// No format conversion, deduplication, or prefetch canonicalization occurs.
internal static class ForgeStream
{
    internal sealed record Entry(int Ordinal, ulong Id, uint Type, string Name,
        long Offset, int Length, long Table, long Info);
    static void Range(Stream s, long offset, long length)
    {
        if (offset < 0 || length < 0 || offset > s.Length || length > s.Length-offset)
            throw new InvalidDataException("Forge range outside file");
    }
    internal static List<Entry> Read(Stream stream)
    {
        using var r = new BinaryReader(stream, Encoding.UTF8, true);
        stream.Position=0;
        if (!r.ReadBytes(9).SequenceEqual(Encoding.ASCII.GetBytes("scimitar\0")) || r.ReadUInt32()!=27)
            throw new InvalidDataException("Only Breakpoint version-27 Forges are supported");
        long header=r.ReadInt64(); Range(stream,header,44); stream.Position=header;
        int total=r.ReadInt32(); r.ReadInt32(); stream.Position+=12;
        r.ReadInt64(); r.ReadInt32(); int directories=r.ReadInt32(); long next=r.ReadInt64();
        if(total<1 || total>10000000 || directories<1 || directories>100000)
            throw new InvalidDataException("Invalid Forge entry counts");
        var entries=new List<Entry>(); var visited=new HashSet<long>();
        for(int d=0;d<directories;d++) {
            Range(stream,next,48); if(!visited.Add(next))throw new InvalidDataException("Cyclic directories");
            stream.Position=next; int count=r.ReadInt32(); r.ReadInt32(); long table=r.ReadInt64();
            next=r.ReadInt64(); r.ReadInt32(); r.ReadInt32(); long info=r.ReadInt64(); r.ReadInt64();
            if(count<0 || count>total-entries.Count)throw new InvalidDataException("Invalid directory count");
            Range(stream,table,checked(count*20L)); Range(stream,info,checked(count*192L));
            ForgeIndexTables.Append(stream,count,table,info,entries);
        }
        if(entries.Count!=total || next!=-1)throw new InvalidDataException("Incomplete directory chain");
        return entries;
    }
    internal static void CopyRange(Stream input, Stream output, long offset, long length)
    {
        Range(input,offset,length);input.Position=offset;
        byte[] buffer=new byte[1024*1024];
        while(length>0){int n=input.Read(buffer,0,(int)Math.Min(length,buffer.Length));
            if(n==0)throw new EndOfStreamException();output.Write(buffer,0,n);length-=n;}
    }
    internal static string HashRange(Stream input,long offset,long length)
    {
        Range(input,offset,length);input.Position=offset;
        using var hash=IncrementalHash.CreateHash(HashAlgorithmName.SHA256);byte[] buffer=new byte[1024*1024];
        while(length>0){int n=input.Read(buffer,0,(int)Math.Min(length,buffer.Length));if(n==0)throw new EndOfStreamException();hash.AppendData(buffer,0,n);length-=n;}
        return Convert.ToHexString(hash.GetHashAndReset());
    }
    internal static object Replace(string source,string payload,string output)
        => ReplaceMany(source,new[]{payload},output);
    internal static object ReplaceMany(string source,string[] payloads,string output)
    {
        if(File.Exists(output))throw new IOException("Output already exists");
        using var original=new FileStream(source,FileMode.Open,FileAccess.Read,FileShare.Read);
        var entries=Read(original);
        var changes=new Dictionary<int,(Entry Target,ForgeEntry Metadata,string Path,string Hash)>();
        foreach(string payload in payloads) {
            if(new FileInfo(payload).Length<=0 || new FileInfo(payload).Length>int.MaxValue)
                throw new InvalidDataException("Replacement .data must be nonempty and smaller than 2 GiB (Forge entry size limit)");
            if(Path.GetExtension(payload).Equals(".data",StringComparison.OrdinalIgnoreCase))PackedDataAudit.VerifyFile(payload);
            var metadata=DataFile.CreateForgeEntry(payload,0,Game.GhostReconBreakpoint);
            var matches=entries.Where(e=>e.Id==metadata.ID && e.Type==metadata.Extension).ToArray();
            if(matches.Length!=1)throw new InvalidDataException($"Expected unique ID/type match; found {matches.Length}");
            var target=matches[0];
            if(new FileInfo(payload).Length!=metadata.LengthOnDisk || metadata.LengthOnDisk<=0)
                throw new InvalidDataException("Invalid replacement length");
            if(!changes.TryAdd(target.Ordinal,(target,metadata,payload,Trial.Hash(payload).ToUpperInvariant())))
                throw new InvalidDataException("Conflicting container replacements");
        }
        if(changes.Count==0)throw new InvalidDataException("No replacements");
        string sourceHash=HashRange(original,0,original.Length);
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(output))!);
        using(var dest=new FileStream(output,FileMode.CreateNew,FileAccess.ReadWrite,FileShare.None)) {
            Console.WriteLine($"Streaming {original.Length:N0} source bytes; {changes.Count} replacement containers.");
            CopyRange(original,dest,0,original.Length);
            using var writer=new BinaryWriter(dest,Encoding.UTF8,true);
            foreach(var change in changes.Values) {
                using var replacement=new FileStream(change.Path,FileMode.Open,FileAccess.Read,FileShare.Read);
                if(HashRange(replacement,0,replacement.Length)!=change.Hash)throw new IOException("Replacement changed");
                long appended=checked((dest.Length+32767)/32768*32768);dest.Position=appended;
                CopyRange(replacement,dest,0,replacement.Length);
                dest.SetLength(checked((dest.Length+32767)/32768*32768));
                dest.Position=change.Target.Table;writer.Write(appended);
                dest.Position=change.Target.Table+16;writer.Write(change.Metadata.LengthOnDisk);
                dest.Position=change.Target.Info;writer.Write(change.Metadata.LengthOnDisk);writer.Write(change.Metadata.UMACHash);
            }
            writer.Flush();dest.Flush(true);
            Console.WriteLine("Verifying every entry and all untouched archive bytes...");
            var after=Read(dest);
            if(after.Count!=entries.Count)throw new InvalidDataException("Entry count changed");
            foreach(var e in entries) {
                var a=after[e.Ordinal];
                if(e.Id!=a.Id || e.Type!=a.Type || e.Name!=a.Name)throw new InvalidDataException("Entry identity changed");
                if(changes.TryGetValue(e.Ordinal,out var change)) {
                    if(HashRange(dest,a.Offset,a.Length)!=change.Hash)throw new InvalidDataException("Edited payload readback mismatch");
                } else if(e.Offset!=a.Offset||e.Length!=a.Length)throw new InvalidDataException("Unselected payload location changed");
            }
            // This full original-range proof includes EVERY untouched payload.
            // It is stronger than individual payload hashes and avoids thousands
            // of random seeks across multi-GB Forges. Changed appended payloads
            // were hashed above; untouched entry offsets/sizes must also match.
            var fields=changes.Values.SelectMany(c=>new[]{(c.Target.Table,8L),(c.Target.Table+16,4L),(c.Target.Info,12L)}).OrderBy(x=>x.Item1);
            long cursor=0;
            foreach(var (offset,length) in fields){if(offset<cursor)throw new InvalidDataException("Overlapping fields");
                if(HashRange(original,cursor,offset-cursor)!=HashRange(dest,cursor,offset-cursor))throw new InvalidDataException("Unrelated Forge bytes changed");cursor=offset+length;}
            if(HashRange(original,cursor,original.Length-cursor)!=HashRange(dest,cursor,original.Length-cursor))throw new InvalidDataException("Unrelated Forge bytes changed");
        }
        if(sourceHash!=HashRange(original,0,original.Length) || changes.Values.Any(c=>!Trial.Hash(c.Path).Equals(c.Hash,StringComparison.OrdinalIgnoreCase)))throw new IOException("Input changed");
        // Cross-check with Anvil's independent table reader, not just our parser.
        var atk=new ForgeFile { ShowMessages=false };
        atk.Deserialize(Path.GetDirectoryName(output)!,output,Game.GhostReconBreakpoint,true,true,true);
        var atkEntries=atk.Directories.SelectMany(d=>d.Entries).ToArray();
        using(var check=File.OpenRead(output)) {
            var ours=Read(check);
            if(atkEntries.Length!=ours.Count)throw new InvalidDataException("Anvil entry count mismatch");
            for(int i=0;i<ours.Count;i++) {
                var a=atkEntries[i];var b=ours[i];
                if(a.ID!=b.Id || a.Extension!=b.Type || a.Offset!=b.Offset || a.LengthOnDisk!=b.Length)
                    throw new InvalidDataException("Anvil independent index mismatch");
            }
        }
        return new {verified=true,originalSourceUnchanged=true,sourceSha256=sourceHash,
            stagedForge=Path.GetFullPath(output),stagedForgeSha256=Trial.Hash(output),entryCount=entries.Count,
            replacedIds=changes.Values.Select(c=>c.Target.Id.ToString("X16")).ToArray(),untouchedBytesVerified=true,prefetchBytesPreserved=true,
            independentAnvilIndexVerified=true,packedBlockChecksumsVerified=true,gameCompatibilityTested=false};
    }
}
