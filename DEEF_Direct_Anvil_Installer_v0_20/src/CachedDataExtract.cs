using System.IO;
using System.Text.Json;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

internal static class CachedDataExtract {
    internal static int Run(string[] args) {
        if(args.Length!=6)throw new IOException("cached-extract <toolkit> <source.forge> <parent.data> <names.json> <output-folder>");
        string source=Path.GetFullPath(args[2]),output=Path.GetFullPath(args[5]);
        if(Trial.Inside(Path.GetDirectoryName(source)!,output)||Trial.Inside(Path.GetFullPath(args[1]),output))throw new IOException("Output must be private and outside source/toolkit");
        var names=JsonSerializer.Deserialize<string[]>(File.ReadAllText(args[4]))!;
        if(names.Any(n=>n!=Path.GetFileName(n)||n.Contains("..")))throw new IOException("Invalid selected resource name");
        HeadlessToolkit.InitializeHashes();string before=Trial.Hash(source);
        using var stream=File.OpenRead(source);var entries=ForgeStream.Read(stream);
        var parents=entries.Where(e=>(e.Name+".data").Equals(args[3],StringComparison.OrdinalIgnoreCase)).ToArray();
        if(parents.Length!=1||parents[0].Length>256*1024*1024)throw new IOException("Invalid/oversized parent");
        using var bytes=new MemoryStream();ForgeStream.CopyRange(stream,bytes,parents[0].Offset,parents[0].Length);
        var data=new DataFile(bytes.ToArray(),Game.GhostReconBreakpoint);if(data.Locked||data.FilesByID.Count==0)throw new IOException("Cached parent failed");
        Directory.CreateDirectory(output);var rows=new List<object>();
        foreach(string name in names) {
            var matches=data.FilesByID.Values.Where(f=>f.Name.Equals(name,StringComparison.OrdinalIgnoreCase)).ToArray();
            if(matches.Length!=1)throw new IOException("Selected name absent or ambiguous: "+name);
            var item=matches[0];string extension=HashedData.HashedStrings.TryGetValue(item.Type,out var type)?type:item.Type.ToString();
            string label=args[0]=="cached-extract-short" ? item.ID.ToString("X16")+(extension=="GR_WeaponDBEntry"?".GR_WeaponDBEntry":".bin") : "1_-_"+name+"."+extension;
            string file=Path.Combine(output,label);if(File.Exists(file))throw new IOException("Selected output already exists");
            File.WriteAllBytes(file,item.Data);rows.Add(new{name,id=item.ID.ToString("X16"),type=item.Type,file,sha256=Trial.Hash(file)});
        }
        if(before!=Trial.Hash(source))throw new IOException("Source changed during read-only extraction");
        var available=data.FilesByID.Values.Select(f=>new{name=f.Name,id=f.ID.ToString("X16"),type=f.Type,extension=HashedData.HashedStrings.TryGetValue(f.Type,out var ext)?ext:f.Type.ToString()}).OrderBy(f=>f.name).ToArray();
        File.WriteAllText(Path.Combine(output,"READ_ONLY_EXTRACTION.json"),JsonSerializer.Serialize(new{source,sourceSha256=before,sourceUnchanged=true,rows,available},new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine($"Cached extraction: {rows.Count} selected objects from {data.FilesByID.Count}");return 0;
    }
}
