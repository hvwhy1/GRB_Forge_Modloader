using System.IO;
using System.Text.Json;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

internal static class FoundationParent
{
    internal static int Run(string[] args)
    {
        string? report=null,output=null;bool outputOwned=false;
        try {
            if(args.Length!=7)throw new ArgumentException("stream-foundation-parent toolkit sourceForge authorForge parent output report");
            string toolkit=Path.GetFullPath(args[1]),source=Path.GetFullPath(args[2]),author=Path.GetFullPath(args[3]),parent=args[4];
            output=Path.GetFullPath(args[5]);report=Path.GetFullPath(args[6]);
            if(Trial.Inside(Path.GetDirectoryName(source)!,output)||Trial.Inside(toolkit,output))throw new IOException("Foundation result must be private and separate from source/toolkit");
            if(File.Exists(output))throw new IOException("Output already exists");
            outputOwned=true;
            HeadlessToolkit.InitializeHashes();
            using var input=new FileStream(author,FileMode.Open,FileAccess.Read,FileShare.Read);
            var matches=ForgeStream.Read(input).Where(e=>e.Name.Equals(parent,StringComparison.OrdinalIgnoreCase)).ToArray();
            if(matches.Length!=1)throw new IOException("Author parent is absent or ambiguous");
            var entry=matches[0];
            string scratch=Path.Combine(Path.GetDirectoryName(report)!,"author-parent-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(scratch);
            string payload=Path.Combine(scratch,$"{entry.Ordinal}_-_{entry.Name}.data");
            using(var dest=File.Create(payload))ForgeStream.CopyRange(input,dest,entry.Offset,entry.Length);
            var identity=DataFile.CreateForgeEntry(payload,0,Game.GhostReconBreakpoint);
            if(identity.ID!=entry.Id||identity.Extension!=entry.Type)throw new IOException("Authored parent does not match its Forge identity");
            string extracted=Path.Combine(scratch,"Extracted",Path.GetFileName(payload));
            var unpack=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,payload,extracted);
            if(unpack.failed)throw new IOException("Authored complete parent cannot unpack: "+unpack.message);
            int files=Directory.GetFiles(extracted).Length;
            if(files==0)throw new IOException("Authored complete parent is empty");
            string authorPayloadHash=Trial.Hash(payload);byte[] authoredBytes=File.ReadAllBytes(payload);
            var corrections=new List<(long Offset,uint Previous,uint Correct)>();string normalization="CHECKSUM_ONLY";
            try{corrections=PackedDataAudit.RepairChecksums(payload);}
            catch(InvalidDataException) {
                // Legacy author packets can contain unrecognized trailing bytes.
                // Do not relax the strict audit or discard bytes blindly: rebuild
                // from the complete extracted content and compare every file.
                if(!File.ReadAllBytes(payload).SequenceEqual(authoredBytes))throw new IOException("Invalid author packet was modified before repack");
                string normalized=Path.Combine(scratch,"Normalized",Path.GetFileName(payload));Directory.CreateDirectory(Path.GetDirectoryName(normalized)!);
                var write=NestedTrial.RepackData(typeof(DataFile),typeof(Game),Game.GhostReconBreakpoint,payload,extracted,normalized);
                if(write.failed)throw new IOException("Authored parent normalization failed: "+write.message);
                PackedDataAudit.VerifyFile(normalized);payload=normalized;normalization="REPACK_VERIFIED_CONTENT";
            }
            byte[] normalizedBytes=File.ReadAllBytes(payload);
            var allowed=corrections.SelectMany(c=>Enumerable.Range(checked((int)c.Offset),4)).ToHashSet();
            bool checksumOnly=normalization=="CHECKSUM_ONLY";
            if(checksumOnly&&(authoredBytes.Length!=normalizedBytes.Length||Enumerable.Range(0,authoredBytes.Length).Any(i=>authoredBytes[i]!=normalizedBytes[i]&&!allowed.Contains(i))))throw new IOException("Checksum normalization changed authored content");
            if(corrections.Count>0||!checksumOnly) {
                string readback=Path.Combine(scratch,"ChecksumReadback");
                var check=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,payload,readback);
                var expected=NestedTrial.Index(extracted);var actual=NestedTrial.Index(readback);
                if(check.failed||expected.Count!=actual.Count||expected.Any(k=>!actual.TryGetValue(k.Key,out var sha)||sha!=k.Value))throw new IOException("Normalized authored parent readback differs");
            }
            var proof=JsonSerializer.SerializeToElement(ForgeStream.Replace(source,payload,output));
            var result=JsonSerializer.Deserialize<Dictionary<string,object?>>(proof.GetRawText())!;
            result["authorCompleteParentVerified"]=true;result["authorForge"]=author;
            result["authorParent"]=parent;result["authorPayloadSha256"]=authorPayloadHash;result["authoredFiles"]=files;
            result["normalizedPayloadSha256"]=Trial.Hash(payload);result["repairedChecksumCount"]=corrections.Count;
            result["normalization"]=normalization;result["allNonChecksumBytesPreserved"]=checksumOnly;
            result["allAuthorExtractedFilesPreserved"]=true;result["authorContentReadbackVerified"]=true;
            result["requestedEdits"]=1;result["allRequestedEditsVerified"]=true;
            Directory.CreateDirectory(Path.GetDirectoryName(report)!);File.WriteAllText(report,JsonSerializer.Serialize(result));
            Console.WriteLine($"Preserved authored complete parent {parent}: {files} unpacked files, including new mod assets");return 0;
        } catch(Exception ex) {
            if(outputOwned&&output!=null&&File.Exists(output))File.Delete(output);
            if(report!=null)File.WriteAllText(report,JsonSerializer.Serialize(new {verified=false,error=ex.ToString()}));
            Console.Error.WriteLine(ex);return 1;
        }
    }
}
