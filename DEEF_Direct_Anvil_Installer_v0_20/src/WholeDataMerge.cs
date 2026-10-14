using System.IO;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

// Author-supplied .data can contain only the changed objects. Merge by binary
// identity into the complete live parent rather than dropping omitted members.
internal static class WholeDataMerge {
    static bool Resource(string p)=>Path.GetFileName(p).Contains("_-_") &&
        !new[]{".PrefetchingInfo",".Dependency",".FilterTable",".xml",".Prefab"}
            .Contains(Path.GetExtension(p),StringComparer.OrdinalIgnoreCase);
    internal static (string folder,int replaced,int preserved) Validate(string original,string incoming,string work,bool allowNew=false) {
        Directory.CreateDirectory(work);
        var originalRoot=DataFile.CreateForgeEntry(original,0,Game.GhostReconBreakpoint);
        var incomingRoot=DataFile.CreateForgeEntry(incoming,0,Game.GhostReconBreakpoint);
        if(originalRoot.ID!=incomingRoot.ID||originalRoot.Extension!=incomingRoot.Extension)
            throw new IOException("WHOLE_DATA_ID_TYPE_MISMATCH");
        string baseline=Path.Combine(work,"Baseline"),mod=Path.Combine(work,"Incoming");
        foreach(var pair in new[]{(original,baseline),(incoming,mod)}) {
            var read=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,pair.Item1,pair.Item2);
            if(read.failed)throw new IOException("Cannot unpack whole-container merge: "+read.message);
        }
        var targets=Directory.GetFiles(baseline).Where(Resource).GroupBy(HeadlessToolkit.Identity)
            .ToDictionary(g=>g.Key,g=>g.ToArray());
        var edits=Directory.GetFiles(mod).Where(Resource).ToArray();
        if(edits.Length==0)throw new IOException("Whole-container payload contains no resources");
        var seen=new HashSet<(ulong,uint)>();
        int added=0;
        int ordinal=Directory.GetFiles(baseline).Select(p=>int.TryParse(Path.GetFileName(p).Split("_-_",2)[0],out int n)?n:0).DefaultIfEmpty(0).Max();
        string Name(string p) {string n=Path.GetFileName(p);int i=n.IndexOf("_-_",StringComparison.Ordinal);return i>0 && n[..i].All(char.IsDigit)?n[(i+3)..]:n;}
        foreach(string payload in edits) {
            var identity=HeadlessToolkit.Identity(payload);
            if(!seen.Add(identity))throw new IOException("WHOLE_DATA_DUPLICATE_BINARY_ID");
            if(!targets.TryGetValue(identity,out var matches)) {
                if(!allowNew)throw new IOException("WHOLE_DATA_NEW_OR_AMBIGUOUS_RESOURCE_REQUIRES_PRIVATE_REVIEW: "+Path.GetFileName(payload));
                // Preserve old identities even when a new authored object has
                // the same human name. Distinct ordinals keep both files intact;
                // complete binary readback is still required before promotion.
                string dest=Path.Combine(baseline,(++ordinal)+"_-_"+Name(payload));
                File.Copy(payload,dest,false);added++;continue;
            }
            if(matches.Length!=1)throw new IOException("WHOLE_DATA_AMBIGUOUS_BINARY_ID: "+Path.GetFileName(payload));
            if(!Name(matches[0]).Equals(Name(payload),StringComparison.OrdinalIgnoreCase))
                throw new IOException("WHOLE_DATA_ID_NAME_MISMATCH: "+Name(payload));
            File.Copy(payload,matches[0],true);
        }
        Console.WriteLine($"Whole parent: {added} verified new identities requested.");
        return (baseline,edits.Length,targets.Count-(edits.Length-added));
    }
    internal static string Prepare(string original,string incoming,string work,bool allowNew=false) {
        var merge=Validate(original,incoming,work,allowNew);
        var expected=NestedTrial.Index(merge.folder);
        string outputDir=Path.Combine(work,"Packed");Directory.CreateDirectory(outputDir);
        string packed=Path.Combine(outputDir,Path.GetFileName(original));
        var write=NestedTrial.RepackData(typeof(DataFile),typeof(Game),Game.GhostReconBreakpoint,
            original,merge.folder,packed);
        if(write.failed)throw new IOException("Whole-container merge repack failed: "+write.message);
        string readback=Path.Combine(work,"Readback");
        var read=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,packed,readback);
        if(read.failed)throw new IOException("Whole-container merge readback failed: "+read.message);
        var actual=NestedTrial.Index(readback);
        if(expected.Count!=actual.Count||expected.Any(kv=>!actual.TryGetValue(kv.Key,out var sha)||sha!=kv.Value))
            throw new IOException("Whole-container merge readback changed resources or original metadata");
        Console.WriteLine($"Merged complete parent: {merge.replaced} supplied objects, {merge.preserved} original objects preserved.");
        return packed;
    }
}
