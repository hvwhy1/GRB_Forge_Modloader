using System.IO;
using System.Text.Json;
using System.Security.Cryptography;
using System.Reflection;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

// Private transcode control: preserve both decoded sections, including ATK metadata.
internal static class DataCompressionTrial {
    internal static int Run(string[] args) {
        if(args.Length!=5)throw new IOException("uncompressed-data-trial <toolkit> <source.data> <output.data> <proof.json>");
        string source=Path.GetFullPath(args[2]),output=Path.GetFullPath(args[3]),report=Path.GetFullPath(args[4]);
        if(Path.GetExtension(source)!=".data"||Path.GetExtension(output)!=".data"||File.Exists(output)||Trial.Inside(Path.GetDirectoryName(source)!,output)||Trial.Inside(Path.GetFullPath(args[1]),output))throw new IOException("Unsafe private transcode paths");
        if(new FileInfo(source).Length>256*1024*1024)throw new IOException("Diagnostic data exceeds memory limit");
        string before=Trial.Hash(source);HeadlessToolkit.InitializeHashes();
        byte[][] Read(string path) {
            using var stream=File.OpenRead(path);using var reader=new BinaryReader(stream);
            var first=new CompressedFileData(reader,Game.GhostReconBreakpoint);
            var second=new CompressedFileData(reader,Game.GhostReconBreakpoint);
            if(stream.Position!=stream.Length)throw new IOException("Trailing data outside packed sections");
            return new[]{first.Data,second.Data};
        }
        var original=Read(source);
        var settingsType=typeof(DataFile).Assembly.GetType("AnvilToolkit.Properties.Settings",true)!;
        var settings=settingsType.GetProperty("Default",BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static)!.GetValue(null)!;
        var enabled=settingsType.GetProperty("EnableCompression",BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance)!;
        bool previous=(bool)enabled.GetValue(settings)!;
        Directory.CreateDirectory(Path.GetDirectoryName(output)!);
        try {
            // This setting is process-local; never call Save or modify ATK preferences.
            enabled.SetValue(settings,false);
            using var stream=new FileStream(output,FileMode.CreateNew,FileAccess.Write);using var writer=new BinaryWriter(stream);
            foreach(byte[] bytes in original) {
                var encoded=new CompressedFileData(bytes,Game.GhostReconBreakpoint,-1,0,true,false);
                encoded.Write(writer,Game.GhostReconBreakpoint,-1,true,false);
            }
        } finally {enabled.SetValue(settings,previous);}
        var after=Read(output);
        if(!original.Zip(after).All(p=>p.First.SequenceEqual(p.Second))||before!=Trial.Hash(source))throw new IOException("Decoded content or source changed");
        var oldEntry=DataFile.CreateForgeEntry(source,0,Game.GhostReconBreakpoint);var newEntry=DataFile.CreateForgeEntry(output,0,Game.GhostReconBreakpoint);
        if(oldEntry.ID!=newEntry.ID||oldEntry.Extension!=newEntry.Extension)throw new IOException("Root identity changed");
        using var cacheBytes=File.OpenRead(output);using var memory=new MemoryStream();cacheBytes.CopyTo(memory);
        var cached=new DataFile(memory.ToArray(),Game.GhostReconBreakpoint);
        if(cached.Locked||cached.FilesByID.Count==0)throw new IOException("Independent Anvil object readback failed");
        string Hash(byte[] bytes)=>Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        File.WriteAllText(report,JsonSerializer.Serialize(new{verified=true,privateOnly=true,sourceUnchanged=true,source,sourceSha256=before,output,outputSha256=Trial.Hash(output),decodedSectionsByteExact=true,decodedSectionSha256=original.Select(Hash),objectCount=cached.FilesByID.Count,id=newEntry.ID.ToString("X16"),type=newEntry.Extension,gameModified=false,gameCompatibilityTested=false},new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine($"Uncompressed control preserves both decoded sections and {cached.FilesByID.Count} objects");return 0;
    }
}
