// Operation-local lookup: preserve duplicates and local-parent precedence.
// Ambiguous candidates still go through the existing byte-hash validation.
internal sealed class ForgeIdentityIndex
{
    readonly Dictionary<string, ILookup<(ulong, uint), ForgeStream.Entry>> local;
    readonly ILookup<(ulong, uint), (string forge, ForgeStream.Entry entry)> all;
    internal ForgeIdentityIndex(Dictionary<string, List<ForgeStream.Entry>> inventories)
    {
        local = inventories.ToDictionary(k => k.Key, k => k.Value.ToLookup(e => (e.Id, e.Type)));
        all = inventories.SelectMany(k => k.Value.Select(e => (forge: k.Key, entry: e)))
            .ToLookup(v => (v.entry.Id, v.entry.Type));
    }
    internal ForgeStream.Entry[] Local(string source, ulong id, uint type)
        => local[source][(id, type)].ToArray();
    internal (string forge, ForgeStream.Entry entry)[] Find(string source, ulong id, uint type)
    {
        var matches = Local(source, id, type);
        return matches.Length > 0 ? matches.Select(e => (source, e)).ToArray()
            : all[(id, type)].Where(v => v.forge != source).ToArray();
    }
}
