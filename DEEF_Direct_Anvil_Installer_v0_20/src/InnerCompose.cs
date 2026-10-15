using System.IO;
using System.Text.Json;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

internal static class InnerCompose {
    internal sealed class Request {public string name{get;set;}="";public string payload{get;set;}="";public string operation{get;set;}="replace";public bool allowNameAlias{get;set;}public bool allowTypeChange{get;set;}public string npcTriggerPriority{get;set;}="Various Tweaks single trigger";public bool exclusive{get;set;}public int? priority{get;set;}public string mod{get;set;}="";public string member{get;set;}="";}
    static string Logical(string path) {string n=Path.GetFileName(path);int j=n.IndexOf("_-_",StringComparison.Ordinal);return j>0&&n[..j].All(char.IsDigit)?n[(j+3)..]:n;}
    static bool Resource(string p)=>Path.GetFileName(p).Contains("_-_")&&!new[]{".PrefetchingInfo",".Dependency",".FilterTable",".xml",".Prefab"}.Contains(Path.GetExtension(p),StringComparer.OrdinalIgnoreCase);
    static Dictionary<string,string> IdentityIndex(string folder) {
        var result=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
        foreach(string file in Directory.GetFiles(folder,"*",SearchOption.AllDirectories)) {
            string key="metadata:"+Path.GetRelativePath(folder,file);
            if(Resource(file)){var id=HeadlessToolkit.Identity(file);key=$"{id.Id:X16}:{id.Type:X8}:"+Logical(file);}
            if(!result.TryAdd(key,Trial.Hash(file)))throw new IOException("Duplicate readback identity/name");
        }
        return result;
    }
    internal static int Run(string[] args) {
        string? report=args.Length>5?Path.GetFullPath(args[5]):null;
        try {
            if(args.Length!=7)throw new IOException("compose-inner <toolkit> <source.forge> <parent.data> <request.json> <report.json> <output.forge>");
            string source=Path.GetFullPath(args[2]),root=Path.GetDirectoryName(source)!,request=Path.GetFullPath(args[4]),output=Path.GetFullPath(args[6]);
            if(new[]{report!,request,output}.Any(p=>Trial.Inside(root,p)||Trial.Inside(Path.GetFullPath(args[1]),p))||File.Exists(output))throw new IOException("Unsafe private inner composition paths");
            HeadlessToolkit.InitializeHashes();
            using var stream=File.OpenRead(source);var entries=ForgeStream.Read(stream);
            var parents=entries.Where(e=>Logical(e.Name+".data").Equals(Logical(args[3]),StringComparison.OrdinalIgnoreCase)).ToArray();
            if(parents.Length!=1)throw new IOException("Ambiguous parent .data");var parent=parents[0];
            string work=Path.Combine(Path.GetDirectoryName(report!)!,"InnerContents");Directory.CreateDirectory(work);
            string original=Path.Combine(work,parent.Ordinal+"_-_"+parent.Name+".data");
            using(var copy=File.Create(original))ForgeStream.CopyRange(stream,copy,parent.Offset,parent.Length);
            string folder=Path.Combine(work,"Unpacked");var read=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,original,folder);
            if(read.failed)throw new IOException("Parent unpack failed: "+read.message);
            var before=NestedTrial.Index(folder);var files=Directory.GetFiles(folder).Where(Resource).ToArray();
            var byId=files.GroupBy(HeadlessToolkit.Identity).ToDictionary(g=>g.Key,g=>g.ToArray());
            File.WriteAllText(Path.Combine(work,"BASE_IDENTITY_INDEX.json"),JsonSerializer.Serialize(byId.SelectMany(k=>k.Value.Select(f=>new{name=Path.GetFileName(f),id=k.Key.Id.ToString("X16"),type=k.Key.Type}))));
            var seen=new Dictionary<(ulong,uint),string>();var changed=new HashSet<string>(StringComparer.OrdinalIgnoreCase);var rows=new List<object>();
            int ordinal=Directory.GetFiles(folder).Select(p=>int.TryParse(Path.GetFileName(p).Split("_-_",2)[0],out int n)?n:0).Max(),added=0;
            var requests=JsonSerializer.Deserialize<Request[]>(File.ReadAllText(request))!;
            var prepared=new List<Request>();
            bool UsePriority(Request[] variants) {
                if(!variants.Any(v=>v.exclusive)&&variants.Any(v=>!v.priority.HasValue))return false;
                var ranked=variants.Where(v=>v.priority.HasValue).ToArray();
                if(ranked.Length==0)return false;
                int highest=ranked.Max(v=>v.priority!.Value);
                var winners=ranked.Where(v=>v.priority==highest).ToArray();
                if(winners.Select(v=>Trial.Hash(v.payload)).Distinct().Count()!=1)return false;
                prepared.Add(winners[0]);rows.Add(new{state="EXPLICIT_RESOURCE_PRIORITY",winner=new{winners[0].mod,winners[0].member},excluded=variants.Where(v=>v!=winners[0]).Select(v=>new{v.mod,v.member}),fieldMerge=false});return true;
            }

            var rejected=new HashSet<Request>();
            foreach(var group in requests.GroupBy(v=>Logical(v.name),StringComparer.OrdinalIgnoreCase)) {
                var variants=group.ToArray();
                if(!variants.Any(v=>v.operation=="remove")||!variants.Any(v=>v.operation=="replace"))continue;
                var ranked=variants.Where(v=>v.priority.HasValue).ToArray();
                if(ranked.Length==0||(!variants.Any(v=>v.exclusive)&&variants.Any(v=>!v.priority.HasValue)))throw new IOException("Removal conflicts with a replacement; choose an explicit priority: "+group.Key);
                int highest=ranked.Max(v=>v.priority!.Value);var winners=ranked.Where(v=>v.priority==highest).ToArray();
                if(winners.Length!=1)throw new IOException("Removal/replacement priority tie: "+group.Key);
                foreach(var item in variants.Where(v=>v!=winners[0]))rejected.Add(item);
                rows.Add(new{state="EXPLICIT_REMOVAL_OR_REPLACEMENT_PRIORITY",winner=new{winners[0].mod,winners[0].member},excluded=variants.Where(v=>v!=winners[0]).Select(v=>new{v.mod,v.member})});
            }
            requests=requests.Where(v=>!rejected.Contains(v)).ToArray();
            foreach(var group in requests.Where(i=>i.operation=="replace").GroupBy(i=>HeadlessToolkit.Identity(i.payload))) {
                var variants=group.ToArray();
                if(variants.Length==1||variants.Select(i=>Trial.Hash(i.payload)).Distinct().Count()==1){prepared.AddRange(variants);continue;}
                if(variants.Any(v=>v.exclusive)){if(UsePriority(variants))continue;throw new IOException("Competing exact resource choices");}
                if(!byId.TryGetValue(group.Key,out var originalMatches)||originalMatches.Length!=1) {if(!byId.ContainsKey(group.Key)&&UsePriority(variants))continue;throw new IOException("Conflicting new/ambiguous inner resource: "+variants[0].name);}
                byte[] baseline=File.ReadAllBytes(originalMatches[0]);var data=variants.Select(i=>File.ReadAllBytes(i.payload)).ToArray();
                if(data.Any(b=>b.Length!=baseline.Length||!b.AsSpan(0,13).SequenceEqual(baseline.AsSpan(0,13)))) {if(UsePriority(variants))continue;throw new IOException("Conflicting inner schemas: "+variants[0].name);}
                bool Satisfies(byte[] candidate)=>data.All(incoming=>Enumerable.Range(0,baseline.Length).All(i=>incoming[i]==baseline[i]||candidate[i]==incoming[i]));
                int winner=Array.FindIndex(data,Satisfies);
                if(winner>=0) {
                    prepared.Add(variants[winner]);rows.Add(new{state="THREE_WAY_DOMINANT_AUTHORED_PAYLOAD",names=variants.Select(v=>v.name).ToArray(),sourceHashes=variants.Select(v=>Trial.Hash(v.payload)).ToArray(),baselineSha256=Trial.Hash(originalMatches[0])});continue;
                }
                // Known AAC setting consists of fixed, eight-byte asset references.
                // WABO changes its magazine reference; Various Tweaks changes its
                // trigger reference. Validate both complete slots and all other
                // bytes instead of performing an unrestricted byte merge.
                if(variants.Length==2&&baseline.Length==147&&group.Key.Type==3713420518&&variants.All(v=>Logical(v.name)=="DBWeaponSmith_AttachmentPerWeaponSetting_AAC.DBWeaponSmith_AttachmentPerWeaponSetting")) {
                    int wabo=Array.FindIndex(variants,v=>v.allowNameAlias),other=Array.FindIndex(variants,v=>!v.allowNameAlias);
                    if(wabo<0||other<0)throw new IOException("AAC merge provenance incomplete");
                    bool Only(byte[] b,int start)=>Enumerable.Range(0,b.Length).All(i=>b[i]==baseline[i]||(i>=start&&i<start+8));
                    if(!Only(data[wabo],56)||!Only(data[other],101))throw new IOException("AAC changes outside verified magazine/trigger reference slots");
                    void VerifyReference(byte[] blob,int offset,string extension) {
                        ulong reference=BitConverter.ToUInt64(blob,offset);
                        if(!byId.Any(k=>k.Key.Id==reference&&k.Value.Any(f=>f.EndsWith(extension,StringComparison.OrdinalIgnoreCase))))throw new IOException("AAC reference does not resolve to "+extension);
                    }
                    VerifyReference(data[wabo],56,".MagazineAttachmentDBEntry");VerifyReference(data[other],101,".TriggerAttachmentDBEntry");
                    byte[] blended=(byte[])data[wabo].Clone();data[other].AsSpan(101,8).CopyTo(blended.AsSpan(101,8));
                    string dir=Path.Combine(work,"ConflictMerges");Directory.CreateDirectory(dir);string merged=Path.Combine(dir,variants[other].name);File.WriteAllBytes(merged,blended);
                    prepared.Add(new Request{name=variants[other].name,payload=merged});
                    rows.Add(new{state="AAC_MAGAZINE_AND_TRIGGER_REFERENCE_MERGE",names=variants.Select(v=>v.name).ToArray(),magazineOffset=56,triggerOffset=101,bothReferenceTargetClassesVerified=true,baselineSha256=Trial.Hash(originalMatches[0]),mergedSha256=Trial.Hash(merged)});continue;
                }
                if(UsePriority(variants))continue;
                throw new IOException("COMPETING_INNER_PAYLOADS_REQUIRE_FIELD_MERGE_OR_PRIORITY: "+variants[0].name);
            }
            foreach(var item in requests.Where(i=>i.operation!="replace")) {
                // Narrow author-documented removal: disabling forced cover.
                if(item.operation!="remove"||Logical(item.name)!="DBPlayerCover.DBCoverParameters")throw new IOException("Unsupported authored removal");
                string instructions=File.ReadAllText(item.payload);
                if(!instructions.Contains("DBPlayerCover.DBCoverParameters")||!instructions.Contains("Remove this file"))throw new IOException("Cover removal instructions do not match");
                var selected=files.Where(f=>Logical(f).Equals(item.name,StringComparison.OrdinalIgnoreCase)).ToArray();
                if(selected.Length!=1)throw new IOException("Cover removal target absent/ambiguous");
                string selectedFile=selected[0],originalSha256=Trial.Hash(selectedFile);changed.Add(Path.GetFileName(selectedFile));File.Delete(selectedFile);
                rows.Add(new{name=item.name,state="AUTHOR_REQUESTED_REMOVAL",target=Path.GetFileName(selectedFile),originalSha256,instructionsSha256=Trial.Hash(item.payload)});
            }
            foreach(var item in prepared) {
                string payload=Path.GetFullPath(item.payload);if(Trial.Inside(root,payload))throw new IOException("Payload inside source directory");
                var id=HeadlessToolkit.Identity(payload);string sha=Trial.Hash(payload);
                if(seen.TryGetValue(id,out string? prior)) {
                    if(prior!=sha)throw new IOException("COMPETING_INNER_PAYLOADS: "+item.name);
                    rows.Add(new{name=item.name,state="IDENTICAL_SHARED_PAYLOAD"});continue;
                }
                seen[id]=sha;string target;string state;
                if(!byId.ContainsKey(id)&&item.allowTypeChange) {
                    var old=byId.Where(k=>k.Key.Item1==id.Id).SelectMany(k=>k.Value).ToArray();
                    if(old.Length!=1)throw new IOException("Type-change root ID is absent/ambiguous: "+item.name);
                    string previous=old[0];changed.Add(Path.GetFileName(previous));
                    string prefix=Path.GetFileName(previous).Split("_-_",2)[0];
                    // The author's display extension can name the former class.
                    // Anvil emits the actual header class on independent unpack.
                    string extension=HashedData.HashedStrings.TryGetValue(id.Type,out var typeName)?typeName:id.Type.ToString();
                    target=Path.Combine(folder,prefix+"_-_"+Path.GetFileNameWithoutExtension(Logical(item.name))+"."+extension);
                    if(File.Exists(target))throw new IOException("Type-change destination collision");
                    File.Delete(previous);File.Copy(payload,target,false);changed.Add(Path.GetFileName(target));
                    rows.Add(new{name=item.name,state="AUTHORED_ROOT_TYPE_CHANGE",target=Path.GetFileName(target),identity=id.Id.ToString("X16"),type=id.Type,sha256=sha});
                    continue;
                }
                if(byId.TryGetValue(id,out var matches)) {
                    if(matches.Length!=1)throw new IOException("Duplicate existing binary identity: "+item.name);
                    target=matches[0];state="REPLACED";
                    bool numericTypeAlias=uint.TryParse(Path.GetExtension(target).TrimStart('.'),out uint numericType)&&numericType==id.Type&&Path.GetFileNameWithoutExtension(Logical(target)).Equals(Path.GetFileNameWithoutExtension(Logical(item.name)),StringComparison.OrdinalIgnoreCase);
                    if(!item.allowNameAlias&&!numericTypeAlias&&!Logical(target).Equals(Logical(item.name),StringComparison.OrdinalIgnoreCase))throw new IOException("INNER_ID_NAME_MISMATCH: "+item.name+" maps to "+Path.GetFileName(target));
                } else {
                    target=Path.Combine(folder,(++ordinal)+"_-_"+Logical(item.name));state="ADDED";added++;
                }
                File.Copy(payload,target,true);changed.Add(Path.GetFileName(target));
                rows.Add(new{name=item.name,state,target=Path.GetFileName(target),identity=id.Id.ToString("X16"),type=id.Type,sha256=sha});
            }
            var duplicateIds=Directory.GetFiles(folder).Where(Resource).GroupBy(f=>HeadlessToolkit.Identity(f).Id).Where(g=>g.Count()>1).ToArray();
            if(duplicateIds.Length>0)throw new IOException("Anvil serializes one object per root ID; unresolved duplicate IDs: "+string.Join(",",duplicateIds.Take(8).Select(g=>g.Key.ToString("X16"))));
            // This clone uses a special weapon layout unsupported by the author
            // stats reader. Its known UI slot must remain a WeaponUI object,
            // including when WABO reuses the old ID for an AmmoTypeUI object.
            var finalFiles=Directory.GetFiles(folder).Where(Resource).ToArray();
            var clone=finalFiles.Where(f=>Logical(f)=="HDG_UBSTG_Bailiff_Clone.GR_WeaponDBEntry").ToArray();
            if(clone.Length==1) {
                byte[] bytes=File.ReadAllBytes(clone[0]);
                if(bytes.Length>=489&&HeadlessToolkit.Identity(clone[0]).Id==0x1D969D75448UL) {
                    ulong reference=BitConverter.ToUInt64(bytes,481);
                    if(reference==0x1D969D83A59UL||reference==0x1CB5B41F5E0UL) {
                        var targets=finalFiles.Where(f=>HeadlessToolkit.Identity(f).Id==reference).ToArray();
                        if(targets.Length!=1||HeadlessToolkit.Identity(targets[0]).Type!=1924792938U)
                            throw new IOException("Bailiff clone UI reference resolves to the wrong resource class");
                        rows.Add(new{state="BAILIFF_WEAPON_UI_REFERENCE_CLASS_VERIFIED",reference=reference.ToString("X16"),offset=481});
                    }
                }
            }
            var expected=NestedTrial.Index(folder);
            foreach(var kv in before.Where(k=>!changed.Contains(k.Key)))if(!expected.TryGetValue(kv.Key,out var sha)||sha!=kv.Value)throw new IOException("Unselected original resource changed");
            // Removing a selected object shifts subsequent extraction ordinals.
            // Identity, class, logical name, bytes and metadata must still match.
            bool removed=requests.Any(i=>i.operation=="remove");
            var intended=removed?IdentityIndex(folder):expected;
            string packdir=Path.Combine(work,"Packed");Directory.CreateDirectory(packdir);string packed=Path.Combine(packdir,Path.GetFileName(original));
            var write=NestedTrial.RepackData(typeof(DataFile),typeof(Game),Game.GhostReconBreakpoint,original,folder,packed);
            if(write.failed)throw new IOException("Inner repack failed: "+write.message);
            void Verify(string data,string dest) {
                var unpack=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,data,dest);
                if(unpack.failed)throw new IOException("Inner readback failed: "+unpack.message);
                var actual=removed?IdentityIndex(dest):NestedTrial.Index(dest);
                if(actual.Count!=intended.Count||intended.Any(k=>!actual.TryGetValue(k.Key,out var sha)||sha!=k.Value))throw new IOException("Complete inner payload/metadata readback differs");
            }
            Verify(packed,Path.Combine(work,"Readback"));
            var receipt=ForgeStream.ReplaceMany(source,new[]{packed},output);
            using(var rewritten=File.OpenRead(output)) {
                var index=ForgeStream.Read(rewritten);var outer=index.Single(e=>e.Id==parent.Id&&e.Type==parent.Type);
                string from=Path.Combine(work,"FromForge.data");using(var copy=File.Create(from))ForgeStream.CopyRange(rewritten,copy,outer.Offset,outer.Length);
                Verify(from,Path.Combine(work,"FromForgeReadback"));
            }
            File.WriteAllText(report!,JsonSerializer.Serialize(new{verified=true,mode="COMPLETE_INNER_COMPOSITION",sourceUnchanged=true,gameModified=false,independentAnvilIndexVerified=true,sourceForge=source,sourceSha256=Trial.Hash(source),output,outputSha256=Trial.Hash(output),addedEntries=added,replacedEntries=seen.Count-added,allUnselectedOriginalObjectsPreserved=true,completeNestedReadbackVerified=true,forgeReceipt=receipt,rows,gameplayVerified=false},new JsonSerializerOptions{WriteIndented=true}));
            return 0;
        }catch(Exception e){Console.Error.WriteLine(e);if(report!=null)File.WriteAllText(report,JsonSerializer.Serialize(new{verified=false,error=e.ToString(),gameModified=false}));return 1;}
    }
}
