using System.IO;
using System.Text;
using AnvilToolkit.Utils;
using AnvilToolkit.Compressions;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
internal static class HeadlessToolkit {
    internal static (ulong Id, uint Type) Identity(string file) {
        using var reader = new AnvilToolkit.BiendianBinaryReader(File.OpenRead(file));
        DataFile.ReadFileHeader(reader, Game.GhostReconBreakpoint);
        return (reader.ReadClassID(Game.GhostReconBreakpoint), reader.ReadUInt32());
    }
    internal static bool SameIdentity(string a, string b) {
        try { return Identity(a) == Identity(b); }
        catch (EndOfStreamException) { return false; }
    }
    internal static void InitializeHashes() {
        // Missing names must use the binary ID fallback, never launch ATK's
        // interactive download dialog or asynchronously mutate a global table.
        GameFileList.List ??= new Dictionary<ulong, GameFileListEntry>();
        if (HashedData.HashedStrings is { Count: > 1000 }) return;
        // Populate before container work; ATK's normal initializer returns before its background load completes.
        using var stream=typeof(ForgeFile).Assembly.GetManifestResourceStream("AnvilToolkit.Resources.hashes.hl")!;
        using var reader=new BinaryReader(stream);
        if(reader.ReadUInt32()!=1414745160 || reader.ReadInt32()!=1)throw new Exception("Unknown hash dictionary format");
        byte codec=reader.ReadByte();reader.ReadBytes(3);
        int unpacked=reader.ReadInt32(),packed=reader.ReadInt32();
        var data=reader.ReadBytes(packed);
        if(codec==1) {
            if(!OodleDllResolver.TryResolveFrom(DataStorage.OodleLocation,7))throw new Exception("Oodle codec missing");
            data=new Oodle().Decompress(data,packed,unpacked,7);
        } else if(codec!=0)throw new Exception("Unknown hash codec");
        var hashes=new Dictionary<uint,string>();
        using var lines=new StringReader(Encoding.UTF8.GetString(data));
        for(string? line;(line=lines.ReadLine())!=null;)
            if(line.Length>0)foreach(var spelling in new[]{line,line.ToLowerInvariant(),line.ToUpperInvariant()})hashes.TryAdd(spelling.ComputeCRC32(),spelling);
        if(hashes.Count<1000)throw new Exception("Incomplete Anvil hash dictionary");
        HashedData.HashedStrings=hashes;
        Console.WriteLine($"Initialized {hashes.Count} name hashes synchronously.");
    }

    internal static void RegisterModName(string name) {
        if(string.IsNullOrWhiteSpace(name))throw new IOException("Cannot register an empty Anvil name hash");
        var id=name.ComputeCRC32();
        if(HashedData.HashedStrings.TryGetValue(id,out var known)) {
            if(!string.Equals(known,name,StringComparison.OrdinalIgnoreCase))
                throw new IOException($"Mod name CRC collision: {name} conflicts with known Anvil name {known}");
            return;
        }
        HashedData.HashedStrings[id]=name;
    }

}
