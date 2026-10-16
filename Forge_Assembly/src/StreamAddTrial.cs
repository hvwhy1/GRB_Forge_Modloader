using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Security.Cryptography;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

// v0.19.5 -- diagnostic only. Inserts ONE previously absent INNER object
// into a known existing .data container, then streams the changed parent into
// a copied Forge. No output is eligible for installation.
internal static class StreamAddTrial {
    static readonly StringComparer Ord = StringComparer.OrdinalIgnoreCase;
    static string Logical(string name) {
        var label=Path.GetFileName(name);int p=label.IndexOf("_-_",StringComparison.Ordinal);
        return p>0 && label[..p].All(char.IsDigit) ? label[(p+3)..] : label;
    }
    static bool DataObject(string f) {
        string ext=Path.GetExtension(f);
        return !new[]{".PrefetchingInfo",".Dependency",".FilterTable",".xml",".Prefab"}
            .Contains(ext,StringComparer.OrdinalIgnoreCase) && Path.GetFileName(f).Contains("_-_");
    }
    internal static int Run(string[] args) {
        string? report=null;
        try {
            if(args.Length!=8)throw new ArgumentException(
                "stream-add-trial <toolkit> <original.forge> <existing.data> <new.object> <payload> <report.json> <output.forge>");
            string toolkit=Path.GetFullPath(args[1]);string source=Path.GetFullPath(args[2]);
            string dataName=args[3],objectName=args[4];string payload=Path.GetFullPath(args[5]);
            report=Path.GetFullPath(args[6]);string output=Path.GetFullPath(args[7]);
            string gameRoot=Path.GetDirectoryName(source)!;
            if(!Path.GetFileName(source).EndsWith(".forge",StringComparison.OrdinalIgnoreCase) ||
               !Path.GetFileName(dataName).Equals(dataName,StringComparison.Ordinal) ||
               !Path.GetFileName(objectName).Equals(objectName,StringComparison.Ordinal) ||
               objectName.Contains("..") || dataName.Contains("..") || !dataName.EndsWith(".data",StringComparison.OrdinalIgnoreCase))
                throw new IOException("Invalid Forge/data/object selector");
            if(Trial.Inside(gameRoot,report)||Trial.Inside(gameRoot,output)||
               Trial.Inside(toolkit,report)||Trial.Inside(toolkit,output)||
               Trial.Inside(gameRoot,payload)||Trial.Inside(toolkit,payload))
                throw new IOException("Trial report, payload and output must be outside game/toolkit directories");
            if(!File.Exists(source) || !File.Exists(payload) || File.Exists(output))
                throw new IOException("Missing input or output already exists");
            if(new FileInfo(payload).Length==0||new FileInfo(payload).Length>int.MaxValue)
                throw new IOException("New inner object must be nonempty and <= signed 32-bit entry limit");
            HeadlessToolkit.InitializeHashes();
            var payloadIdentity=HeadlessToolkit.Identity(payload);
            // A byte-level identity is crucial, but the intended display name also
            // must agree with the supplied mod member before adding any entry.
            if(!Logical(Path.GetFileName(payload)).Equals(Logical(objectName),StringComparison.OrdinalIgnoreCase))
                throw new IOException("Payload filename and declared new object name differ");
            string sourceHash=Trial.Hash(source);string payloadHash=Trial.Hash(payload);
            using var src=File.Open(source,FileMode.Open,FileAccess.Read,FileShare.Read);
            var entries=ForgeStream.Read(src);
            var parents=entries.Where(e=>Logical(e.Name+".data").Equals(Logical(dataName),StringComparison.OrdinalIgnoreCase)).ToArray();
            if(parents.Length!=1)throw new IOException("Existing .data parent is missing or ambiguous");
            var parent=parents[0];
            var trialDir=Path.Combine(Path.GetDirectoryName(report)!,"private_add_"+Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(trialDir);
            string copy=Path.Combine(trialDir,$"{parent.Ordinal}_-_{parent.Name}.data");
            using(var dst=File.Create(copy))ForgeStream.CopyRange(src,dst,parent.Offset,parent.Length);
            string unpack=Path.Combine(trialDir,"Unpacked",Path.GetFileName(copy));
            var read=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,copy,unpack);
            if(read.failed)throw new IOException("Original parent could not be unpacked: "+read.message);
            var oldIndex=NestedTrial.Index(unpack);
            var oldFiles=Directory.GetFiles(unpack).Where(DataObject).ToArray();
            var identities=oldFiles.ToDictionary(f=>f,HeadlessToolkit.Identity,Ord);
            if(identities.Values.Contains(payloadIdentity))
                throw new IOException("The binary ID/type is ALREADY PRESENT: replacement or conflict, not an insertion");
            if(oldFiles.Any(f=>Logical(f).Equals(Logical(objectName),StringComparison.OrdinalIgnoreCase)))
                throw new IOException("The logical object name is ALREADY PRESENT: collision, not an insertion");
            // Choose a fresh ordinal; serializer may re-number entries. The
            // post-serialization comparison is based on a complete readback.
            int largest=-1;
            foreach(var f in Directory.GetFiles(unpack)) {
                string token=Path.GetFileName(f).Split("_-_",2)[0];
                if(int.TryParse(token,out int n))largest=Math.Max(largest,n);
            }
            string inserted=Path.Combine(unpack,checked(largest+1).ToString()+"_-_"+Logical(objectName));
            File.Copy(payload,inserted,false);
            var intended=NestedTrial.Index(unpack);
            string packedDir=Path.Combine(trialDir,"Packed");Directory.CreateDirectory(packedDir);
            string packed=Path.Combine(packedDir,Path.GetFileName(copy));
            var repack=NestedTrial.RepackData(typeof(DataFile),typeof(Game),Game.GhostReconBreakpoint,copy,unpack,packed);
            if(repack.failed)throw new IOException("New inner entry repack failed: "+repack.message);
            string nestedReadback=Path.Combine(trialDir,"NestedReadback",Path.GetFileName(copy));
            read=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,packed,nestedReadback);
            if(read.failed)throw new IOException("New inner entry readback failed: "+read.message);
            var found=NestedTrial.Index(nestedReadback);
            if(found.Count!=intended.Count || intended.Any(kv=>!found.TryGetValue(kv.Key,out string? sha)||sha!=kv.Value))
                throw new IOException("Inner .data readback differs from expected newly inserted resource set");
            if(!found.TryGetValue(Path.GetFileName(inserted),out string? addedHash)||
               !addedHash.Equals(payloadHash,StringComparison.OrdinalIgnoreCase))
                throw new IOException("Added inner object failed readback");
            if(!File.Exists(packed)||File.Exists(output))throw new IOException("Invalid stream output state");
            var streamReceipt=ForgeStream.ReplaceMany(source,new[]{packed},output);
            // Verify the OUTER Forge preserved unrelated payloads. Verify the
            // exact inserted object through a second extraction of the rewritten
            // parent from the stream-repacked copy as well.
            using var staged=File.OpenRead(output);
            var after=ForgeStream.Read(staged);
            if(after.Count!=entries.Count)throw new IOException("Existing outer entry count changed");
            var newParent=after[parent.Ordinal];
            if(newParent.Id!=parent.Id||newParent.Type!=parent.Type)throw new IOException("Parent identity changed in outer Forge");
            string copyAgain=Path.Combine(trialDir,"ReadbackOuter",Path.GetFileName(copy));
            Directory.CreateDirectory(Path.GetDirectoryName(copyAgain)!);
            using(var dst=File.Create(copyAgain))ForgeStream.CopyRange(staged,dst,newParent.Offset,newParent.Length);
            string fullExtract=Path.Combine(trialDir,"FullReadback",Path.GetFileName(copy));
            read=NestedTrial.UnpackData(typeof(DataFile),Game.GhostReconBreakpoint,copyAgain,fullExtract);
            if(read.failed)throw new IOException("Outer Forge embedded nested readback failed: "+read.message);
            var final=NestedTrial.Index(fullExtract);
            if(final.Count!=intended.Count || intended.Any(kv=>!final.TryGetValue(kv.Key,out string? sha)||sha!=kv.Value))
                throw new IOException("Outer Forge nested readback differs from expected inserted resource set");
            if(Trial.Hash(source)!=sourceHash||Trial.Hash(payload)!=payloadHash)
                throw new IOException("Inputs changed during trial");
            var receipt=new {
                verified=true, mode="PRIVATE_NEW_INNER_RESOURCE_INSERTION", gameModified=false,
                eligibleForDeployment=false,gameCompatibilityTested=false,
                warning="Private validation only. Does not authorize game installation or infer dependencies.",
                sourceForge=source,sourceSha256=sourceHash,container=dataName,objectName,
                addedIdentityId=payloadIdentity.Item1.ToString("X16"),addedIdentityType=payloadIdentity.Item2,
                inputInnerObjectCount=oldFiles.Length,finalInnerObjectCount=Directory.GetFiles(fullExtract).Count(DataObject),
                addedSha256=payloadHash,sourceUnchanged=true,output=output,outputSha256=Trial.Hash(output),
                outerStreamReceipt=streamReceipt
            };
            File.WriteAllText(report,JsonSerializer.Serialize(receipt,new JsonSerializerOptions{WriteIndented=true}));
            Console.WriteLine("PRIVATE NEW INNER RESOURCE INSERTION VERIFIED; NO GAME DEPLOYMENT");
            return 0;
        } catch(Exception e) {
            Console.Error.WriteLine(e);
            if(report!=null) {Directory.CreateDirectory(Path.GetDirectoryName(report)!);
                File.WriteAllText(report,JsonSerializer.Serialize(new {verified=false,mode="PRIVATE_NEW_INNER_RESOURCE_INSERTION",gameModified=false,eligibleForDeployment=false,error=e.ToString()},new JsonSerializerOptions{WriteIndented=true}));}
            return 1;
        }
    }
}
