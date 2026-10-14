using System.IO;
using System.Text.Json;
using System.Security.Cryptography;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

// Read-only cached comparison avoids opening 120,000 individual files.
internal static class DataParentCompare {
    internal static int Run(string[] args) {
        if(args.Length!=6)throw new IOException("compare-data-parents <toolkit> <left.forge> <right.forge> <parent> <report.json>");
        HeadlessToolkit.InitializeHashes();
        Dictionary<ulong,DataFile.FileData> Read(string path) {
            using var stream=File.OpenRead(path);var entries=ForgeStream.Read(stream);
            var parents=entries.Where(e=>(e.Name+".data").Equals(args[4],StringComparison.OrdinalIgnoreCase)).ToArray();
            if(parents.Length!=1)throw new IOException("Parent does not resolve uniquely");
            var entry=parents[0];if(entry.Length>256*1024*1024)throw new IOException("Parent exceeds diagnostic memory limit");
            using var copied=new MemoryStream();ForgeStream.CopyRange(stream,copied,entry.Offset,entry.Length);
            var data=new DataFile(copied.ToArray(),Game.GhostReconBreakpoint);
            if(data.Locked||data.FilesByID.Count==0)throw new IOException("Cached parent read failed");
            return data.FilesByID;
        }
        var left=Read(args[2]);var right=Read(args[3]);
        string Hash(byte[] bytes)=>Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        var changed=right.Where(p=>!left.TryGetValue(p.Key,out var old)||!old.Data.SequenceEqual(p.Value.Data)).Select(p=>new{
            id=p.Key.ToString("X16"),name=p.Value.Name,type=p.Value.Type,sha256=Hash(p.Value.Data),bytes=p.Value.Data.Length,
            oldType=left.TryGetValue(p.Key,out var old)?old.Type:(uint?)null,oldSha256=old==null?null:Hash(old.Data),oldBytes=old?.Data.Length}).ToArray();
        var missing=left.Where(p=>!right.ContainsKey(p.Key)).Select(p=>new{id=p.Key.ToString("X16"),name=p.Value.Name,type=p.Value.Type}).ToArray();
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(args[5]))!);
        File.WriteAllText(args[5],JsonSerializer.Serialize(new{readOnly=true,left=Path.GetFullPath(args[2]),right=Path.GetFullPath(args[3]),leftCount=left.Count,rightCount=right.Count,changed,missing},new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine($"Cached comparison: {left.Count} vs {right.Count} objects; {changed.Length} changed; {missing.Length} missing");return 0;
    }
}
