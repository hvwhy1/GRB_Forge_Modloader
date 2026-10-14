using System.IO;
using System.Text.Json;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

// Validate legacy/new authored packets without losing any decoded asset.
internal static class ContainerNormalization {
    internal static string Prepare(string source,string work) {
        try{PackedDataAudit.VerifyFile(source);return source;}catch(InvalidDataException){}
        Directory.CreateDirectory(work);
        string originalHash=Trial.Hash(source),copy=Path.Combine(work,"Input",Path.GetFileName(source));
        Directory.CreateDirectory(Path.GetDirectoryName(copy)!);File.Copy(source,copy,false);
        string before=Path.Combine(work,"Before");
        var read=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,source,before);
        if(read.failed)throw new IOException("Authored packet could not unpack: "+read.message);
        var expected=NestedTrial.Index(before);
        if(expected.Count==0)throw new IOException("Authored packet has no extracted content");
        string mode="CHECKSUM_ONLY";int corrections=0;string output=copy;
        try{corrections=PackedDataAudit.RepairChecksums(copy).Count;}
        catch(InvalidDataException) {
            if(Trial.Hash(copy)!=originalHash)throw new IOException("Malformed packet was modified before normalization");
            mode="REPACK_VERIFIED_CONTENT";output=Path.Combine(work,"Normalized",Path.GetFileName(source));Directory.CreateDirectory(Path.GetDirectoryName(output)!);
            var write=NestedTrial.RepackData(typeof(DataFile),typeof(Game),Game.GhostReconBreakpoint,source,before,output);
            if(write.failed)throw new IOException("Authored packet repack failed: "+write.message);
        }
        PackedDataAudit.VerifyFile(output);
        var original=DataFile.CreateForgeEntry(source,0,Game.GhostReconBreakpoint);var final=DataFile.CreateForgeEntry(output,0,Game.GhostReconBreakpoint);
        if(original.ID!=final.ID||original.Extension!=final.Extension)throw new IOException("Packet normalization changed root identity");
        string after=Path.Combine(work,"After");var check=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,output,after);
        var actual=NestedTrial.Index(after);
        if(check.failed||actual.Count!=expected.Count||expected.Any(k=>!actual.TryGetValue(k.Key,out var sha)||sha!=k.Value))throw new IOException("Packet normalization changed authored content or metadata");
        if(Trial.Hash(source)!=originalHash)throw new IOException("Authored packet source changed");
        File.WriteAllText(Path.Combine(work,"NORMALIZATION.json"),JsonSerializer.Serialize(new{verified=true,sourceUnchanged=true,sourceSha256=originalHash,output,outputSha256=Trial.Hash(output),mode,correctedChecksums=corrections,allExtractedFilesPreserved=true,rootIdentityPreserved=true,extractedFiles=expected.Count}));
        return output;
    }
}
