using System.IO;
using System.Text.Json;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

// Merge whole authored parents by verified object identity. Unknown object
// layouts remain indivisible: competing changes need an explicit priority.
internal static class ContainerConflictMerge {
    static bool Resource(string p)=>Path.GetFileName(p).Contains("_-_") &&
        !new[]{".PrefetchingInfo",".Dependency",".FilterTable",".xml",".Prefab"}.Contains(Path.GetExtension(p),StringComparer.OrdinalIgnoreCase);
    static string Name(string p) {string n=Path.GetFileName(p);int i=n.IndexOf("_-_",StringComparison.Ordinal);return i>0&&n[..i].All(char.IsDigit)?n[(i+3)..]:n;}
    internal static string Prepare(string? original,AssetCompose.Request[] variants,string work) {
        Directory.CreateDirectory(work);
        var roots=variants.Select(v=>DataFile.CreateForgeEntry(v.payload,0,Game.GhostReconBreakpoint)).ToArray();
        if(roots.Any(r=>r.ID!=roots[0].ID||r.Extension!=roots[0].Extension))throw new IOException("CONTAINER_MERGE_ROOT_ID_TYPE_MISMATCH");
        var before=variants.Select(v=>v.payload).Distinct().ToDictionary(p=>p,p=>Trial.Hash(p));
        if(original!=null){var root=DataFile.CreateForgeEntry(original,0,Game.GhostReconBreakpoint);if(root.ID!=roots[0].ID||root.Extension!=roots[0].Extension)throw new IOException("Container baseline root identity differs");}
        string? originalBefore=original!=null?Trial.Hash(original):null;
        string seed=original??variants[0].payload;
        seed=ContainerNormalization.Prepare(seed,Path.Combine(work,"SeedNormalization"));
        string baseline=Path.Combine(work,"Baseline");
        var read=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,seed,baseline);
        if(read.failed)throw new IOException("Cannot unpack container merge baseline: "+read.message);
        var originals=Directory.GetFiles(baseline).Where(Resource).GroupBy(HeadlessToolkit.Identity).ToDictionary(g=>g.Key,g=>g.ToArray());
        if(originals.Values.Any(v=>v.Length!=1))throw new IOException("Ambiguous baseline object identity");
        var incoming=new Dictionary<(ulong Id,uint Type),List<(string path,AssetCompose.Request request)>>();
        for(int n=0;n<variants.Length;n++) {
            string normalized=ContainerNormalization.Prepare(variants[n].payload,Path.Combine(work,n.ToString(),"Normalization"));
            string folder=Path.Combine(work,n.ToString(),"Incoming");
            read=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,normalized,folder);
            if(read.failed)throw new IOException("Cannot unpack container conflict candidate: "+read.message);
            var seen=new HashSet<(ulong,uint)>();
            foreach(string path in Directory.GetFiles(folder).Where(Resource)) {
                var id=HeadlessToolkit.Identity(path);
                if(!seen.Add(id))throw new IOException("Duplicate candidate object identity");
                if(!incoming.TryGetValue(id,out var rows))incoming[id]=rows=new();
                rows.Add((path,variants[n]));
            }
        }
        int ordinal=Directory.GetFiles(baseline).Select(p=>int.TryParse(Path.GetFileName(p).Split("_-_",2)[0],out int n)?n:0).DefaultIfEmpty(0).Max();
        int added=0;var decisions=new List<object>();
        foreach(var pair in incoming) {
            originals.TryGetValue(pair.Key,out var old);
            string? baselineHash=original!=null&&old!=null?Trial.Hash(old[0]):null;
            var changed=pair.Value.Where(v=>Trial.Hash(v.path)!=baselineHash).ToArray();
            if(changed.Length==0)continue;
            var hashes=changed.Select(v=>Trial.Hash(v.path)).Distinct().ToArray();
            var winner=changed[0];string state="IDENTICAL_OR_SINGLE_OBJECT_CHANGE";
            if(hashes.Length>1) {
                var ranked=changed.Where(v=>v.request.priority.HasValue).ToArray();
                if(ranked.Length==0||changed.Any(v=>!v.request.priority.HasValue))throw new IOException("CONTAINER_OBJECT_CONFLICT_NEEDS_PRIORITY: "+Name(winner.path));
                int highest=ranked.Max(v=>v.request.priority!.Value);
                var best=ranked.Where(v=>v.request.priority==highest).ToArray();
                if(best.Select(v=>Trial.Hash(v.path)).Distinct().Count()!=1)throw new IOException("CONTAINER_OBJECT_PRIORITY_TIE: "+Name(winner.path));
                winner=best[0];state="EXPLICIT_OBJECT_PRIORITY";
            }
            string destination;
            if(old!=null) {
                destination=old[0];
                if(!Name(destination).Equals(Name(winner.path),StringComparison.OrdinalIgnoreCase))throw new IOException("Container object ID/name mismatch");
            }else {destination=Path.Combine(baseline,(++ordinal)+"_-_"+Name(winner.path));added++;}
            File.Copy(winner.path,destination,true);
            decisions.Add(new{identity=$"{pair.Key.Id:X16}:{pair.Key.Type:X8}",name=Name(winner.path),state,winner=new{winner.request.mod,winner.request.member},candidateCount=pair.Value.Count});
        }
        var expected=NestedTrial.Index(baseline);
        string output=Path.Combine(work,"Packed",Path.GetFileName(seed));Directory.CreateDirectory(Path.GetDirectoryName(output)!);
        var write=NestedTrial.RepackData(typeof(DataFile),typeof(Game),Game.GhostReconBreakpoint,seed,baseline,output);
        if(write.failed)throw new IOException("Container union repack failed: "+write.message);
        PackedDataAudit.VerifyFile(output);
        var outputRoot=DataFile.CreateForgeEntry(output,0,Game.GhostReconBreakpoint);
        if(outputRoot.ID!=roots[0].ID||outputRoot.Extension!=roots[0].Extension)throw new IOException("Merged container root identity changed");
        string readback=Path.Combine(work,"Readback");
        read=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,output,readback);
        if(read.failed)throw new IOException("Container union readback failed: "+read.message);
        var actual=NestedTrial.Index(readback);
        if(expected.Count!=actual.Count||expected.Any(e=>!actual.TryGetValue(e.Key,out var hash)||hash!=e.Value))throw new IOException("Container union changed objects or metadata during readback");
        if((original!=null&&Trial.Hash(original)!=originalBefore)||before.Any(e=>Trial.Hash(e.Key)!=e.Value))throw new IOException("Container source changed");
        File.WriteAllText(Path.Combine(work,"MERGE_PROOF.json"),JsonSerializer.Serialize(new{verified=true,objectUnionVerified=true,allUnselectedOriginalObjectsPreserved=true,metadataReadbackVerified=true,sourceHashes=before,outputSha256=Trial.Hash(output),newObjects=added,decisions,gameplayVerified=false},new JsonSerializerOptions{WriteIndented=true}));
        return output;
    }
}
