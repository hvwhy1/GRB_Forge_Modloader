using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using AnvilToolkit.FileTypes.AnvilNext.Containers;
using AnvilToolkit.Utils;

// Pack an unpacked mod .data directory into a new .data container on a private
// path. The result is diagnostic until it is independently inserted/read back
// through a Forge trial; this command never edits an installed Forge.
internal static class StreamPackNewData {
    private static readonly StringComparer Cmp=StringComparer.OrdinalIgnoreCase;
    private static string Hash(string path) {
        using var s=File.OpenRead(path);return Convert.ToHexString(SHA256.HashData(s));
    }
    private static Dictionary<string,string> Index(string folder)=>NestedTrial.Index(folder);
    private static string Logical(string path) {
        string leaf=Path.GetFileName(path);int split=leaf.IndexOf("_-_",StringComparison.Ordinal);
        if(split>0&&leaf[..split].All(char.IsDigit))leaf=leaf[(split+3)..];
        return leaf;
    }
    private static string ObjectKey(string path)=>Path.GetFileNameWithoutExtension(Logical(path));

    internal static int Run(string[] args,System.Reflection.Assembly library,Type gameType,object game) {
        string? report=null,output=null,scratch=null,packInput=null;string source="";string sourceFingerprint="";
        try {
            if(args.Length!=5)throw new IOException("stream-pack-new-data <toolkit> <unpacked-data-folder> <private-output.data> <report.json>");
            source=Path.GetFullPath(args[2]);output=Path.GetFullPath(args[3]);report=Path.GetFullPath(args[4]);
            string toolkit=Path.GetFullPath(args[1]);
            if(!Directory.Exists(source)||File.Exists(output)||!output.EndsWith(".data",StringComparison.OrdinalIgnoreCase))
                throw new IOException("Input directory must exist and output must be a new .data file");
            foreach(string path in new[]{output,report})
                if(Trial.Inside(toolkit,path)||Trial.Inside(source,path))
                    throw new IOException("Output and report must be outside the toolkit and input tree");
            var inputs=Directory.GetFiles(source,"*",SearchOption.TopDirectoryOnly);
            if(inputs.Length==0||inputs.Length>20000)throw new IOException("Expected 1 to 20,000 top-level files in the unpacked resource");
            var ignored=DataStorage.IgnoredExtensions.Select(x=>x.ToString()).ToHashSet(Cmp);
            var settingsType=library.GetType("AnvilToolkit.Properties.Settings",true)!;
            var defaultSettings=settingsType.GetProperty("Default",System.Reflection.BindingFlags.Static|
                System.Reflection.BindingFlags.Public|System.Reflection.BindingFlags.NonPublic)!.GetValue(null)!;
            var compileXml=(bool)settingsType.GetProperty("CompileXMLWhenRepacking",
                System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.Public|
                System.Reflection.BindingFlags.NonPublic)!.GetValue(defaultSettings)!;
            var expected=new Dictionary<string,string>(Cmp);
            var excludedArtifactExtensions=new HashSet<string>(new[]{".bak",".backup",".old",".tmp",".orig"},Cmp);
            foreach(string file in inputs) {
                string name=Path.GetFileName(file);
                if(ignored.Contains(Path.GetExtension(name))||excludedArtifactExtensions.Contains(Path.GetExtension(name)))continue;
                if(!expected.TryAdd(name,Hash(file)))throw new IOException("Case-insensitive duplicate input filename: "+name);
            }
            if(compileXml) {
                foreach(string xml in expected.Keys.Where(n=>Path.GetExtension(n).Equals(".xml",StringComparison.OrdinalIgnoreCase)).ToArray()) {
                    string stem=Path.GetFileNameWithoutExtension(xml);
                    var other=expected.Keys.FirstOrDefault(n=>!n.Equals(xml,StringComparison.OrdinalIgnoreCase)&&
                        Path.GetFileNameWithoutExtension(n).Equals(stem,StringComparison.OrdinalIgnoreCase));
                    if(other!=null)expected.Remove(other);
                }
            }
            if(expected.Count==0)throw new IOException("No files remain after AnvilToolkit ignored-extension filtering");
            string parentName=ObjectKey(output);
            var roots=expected.Keys.Where(name=>ObjectKey(name).Equals(parentName,StringComparison.OrdinalIgnoreCase)
                && !new[]{".PrefetchingInfo",".Dependency",".FilterTable",".Prefab"}.Contains(Path.GetExtension(name),Cmp)).ToArray();
            if(roots.Length!=1)throw new IOException("New parent requires exactly one matching root resource: "+parentName);
            string rootName=roots[0];
            var identities=expected.Keys.ToDictionary(name=>name,
                name=>HeadlessToolkit.Identity(Path.Combine(source,name)),Cmp);
            foreach(var group in expected.Keys.GroupBy(name=>identities[name].Id).Where(g=>g.Count()>1).ToArray()) {
                var rows=group.Select(name=>(name,identity:identities[name],sha:expected[name])).ToArray();
                if(rows.Select(x=>x.identity.Type).Distinct().Count()!=1)
                    throw new IOException("Source folder has conflicting types for one nested resource ID: "+group.Key);
                var xml=rows.FirstOrDefault(x=>Path.GetExtension(x.name).Equals(".xml",StringComparison.OrdinalIgnoreCase));
                var compiled=compileXml&&xml.name!=null;
                var selected=compiled?xml:rows.FirstOrDefault(x=>!Path.GetExtension(x.name).Equals(".xml",StringComparison.OrdinalIgnoreCase));
                if(selected.name==null)selected=rows[0];
                var binaryVariants=rows.Where(x=>!Path.GetExtension(x.name).Equals(".xml",StringComparison.OrdinalIgnoreCase)).ToArray();
                if(!compiled&&binaryVariants.Select(x=>x.sha).Distinct(StringComparer.OrdinalIgnoreCase).Count()>1)
                    throw new IOException("Source folder has differing binary payloads for one nested resource ID: "+group.Key);
                foreach(var row in rows)if(!row.name.Equals(selected.name,StringComparison.OrdinalIgnoreCase))expected.Remove(row.name);
            }
            sourceFingerprint=string.Join("\n",inputs.OrderBy(Path.GetFileName,Cmp).Select(f=>Path.GetFileName(f)+"="+Hash(f)));
            packInput=Path.Combine(Path.GetDirectoryName(report)!,"pack_input_"+Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(packInput);
            foreach(string name in expected.Keys)File.Copy(Path.Combine(source,name),Path.Combine(packInput,name));
            HeadlessToolkit.InitializeHashes();
            foreach(string name in expected.Keys) {
                string stem=Path.GetFileNameWithoutExtension(name);
                int split=stem.IndexOf("_-_",StringComparison.Ordinal);
                if(split>0&&stem[..split].All(char.IsDigit))stem=stem[(split+3)..];
                HeadlessToolkit.RegisterModName(stem);
                string extension=Path.GetExtension(name).TrimStart('.');
                if(extension.Length>0&&!extension.All(char.IsDigit))HeadlessToolkit.RegisterModName(extension);
            }
            var data=(DataFile)(Activator.CreateInstance(typeof(DataFile),new[]{game})
                ??throw new IOException("DataFile(Game) constructor returned null"));
            SetOption(data,"Batch",true);SetOption(data,"Cache",false);SetOption(data,"FileListCache",false);
            Directory.CreateDirectory(Path.GetDirectoryName(output)!);
            (bool failed,string message) write;
            using(var destination=new FileStream(output,FileMode.CreateNew,FileAccess.Write,FileShare.None))
                write=data.Serialize(destination,packInput,null!,false);
            if(write.failed)throw new IOException("AnvilToolkit could not pack new .data: "+write.message);
            if(!File.Exists(output)||new FileInfo(output).Length<=0||new FileInfo(output).Length>int.MaxValue)
                throw new IOException("AnvilToolkit produced an empty or oversized .data file");
            scratch=Path.Combine(Path.GetDirectoryName(report)!,"pack_readback_"+Guid.NewGuid().ToString("N"));
            var read=NestedTrial.UnpackData(typeof(DataFile),game,output,scratch);
            if(read.failed)throw new IOException("AnvilToolkit could not read back the synthesized .data: "+read.message);
            var actual=Index(scratch);
            var intended=expected.ToDictionary(kv=>HeadlessToolkit.Identity(Path.Combine(source,kv.Key)).Id,
                kv=>(kv.Key,kv.Value,HeadlessToolkit.Identity(Path.Combine(source,kv.Key)).Type));
            var recovered=actual.ToDictionary(kv=>HeadlessToolkit.Identity(Path.Combine(scratch,kv.Key)).Id,
                kv=>(kv.Key,kv.Value,HeadlessToolkit.Identity(Path.Combine(scratch,kv.Key)).Type));
            string[] missing=intended.Keys.Where(k=>!recovered.ContainsKey(k)).Select(k=>k.ToString("X16")).Take(20).ToArray();
            string[] unexpected=recovered.Keys.Where(k=>!intended.ContainsKey(k)).Select(k=>k.ToString("X16")).Take(20).ToArray();
            string[] changed=intended.Where(kv=>recovered.TryGetValue(kv.Key,out var item)&&
                (item.Type!=kv.Value.Type||!item.Value.Equals(kv.Value.Value,StringComparison.OrdinalIgnoreCase))&&
                !Path.GetExtension(kv.Value.Key).Equals(".xml",StringComparison.OrdinalIgnoreCase))
                .Select(kv=>kv.Key.ToString("X16")).Take(20).ToArray();
            if(recovered.Count!=intended.Count||missing.Length>0||unexpected.Length>0||changed.Length>0)
                throw new IOException($"New .data readback differs: expected {intended.Count}, got {recovered.Count}; missing=[{string.Join(",",missing)}]; unexpected=[{string.Join(",",unexpected)}]; changed=[{string.Join(",",changed)}]");
            var rootIdentity=HeadlessToolkit.Identity(Path.Combine(source,rootName));
            var packedIdentity=DataFile.CreateForgeEntry(output,0,Game.GhostReconBreakpoint);
            if(packedIdentity.ID!=rootIdentity.Id||packedIdentity.Extension!=rootIdentity.Type)
                throw new IOException("Synthesized parent identity does not match its named root; refusing a partial/misidentified container");
            string afterFingerprint=string.Join("\n",inputs.OrderBy(Path.GetFileName,Cmp).Select(f=>Path.GetFileName(f)+"="+Hash(f)));
            if(!sourceFingerprint.Equals(afterFingerprint,StringComparison.Ordinal))throw new IOException("Source mod payload changed during packing");
            File.WriteAllText(report,JsonSerializer.Serialize(new {
                verified=true,mode="PRIVATE_NEW_DATA_PACK_AND_READBACK",gameModified=false,
                sourceDirectory=source,sourceFileCount=inputs.Length,serializedFileCount=expected.Count,
                compileXml,output,outputSha256=Hash(output),readbackFileCount=actual.Count,
                rootIdentityVerified=true,rootResource=rootName,rootId=rootIdentity.Id,rootType=rootIdentity.Type,
                sourceUnchanged=true,warning="Packed container is structurally verified only. Forge insertion and gameplay compatibility require separate checks."
            },new JsonSerializerOptions{WriteIndented=true}));
            Console.WriteLine("New .data pack/readback verified: "+expected.Count+" resources.");
            return 0;
        } catch(Exception ex) {
            Console.Error.WriteLine(ex);
            if(output!=null)try{File.Delete(output);}catch{}
            if(report!=null){Directory.CreateDirectory(Path.GetDirectoryName(report)!);File.WriteAllText(report,JsonSerializer.Serialize(new {
                verified=false,mode="PRIVATE_NEW_DATA_PACK_FAILED",gameModified=false,error=ex.ToString()
            },new JsonSerializerOptions{WriteIndented=true}));}
            return 1;
        } finally {
            if(scratch!=null&&Directory.Exists(scratch))try{Directory.Delete(scratch,true);}catch{}
            if(packInput!=null&&Directory.Exists(packInput))try{Directory.Delete(packInput,true);}catch{}
        }
    }

    private static void SetOption(object instance,string name,object value) {
        var property=instance.GetType().GetProperty(name,System.Reflection.BindingFlags.Instance|
            System.Reflection.BindingFlags.Public|System.Reflection.BindingFlags.NonPublic)
            ??throw new MissingMemberException(instance.GetType().FullName,name);
        var setter=property.GetSetMethod(true);
        if(setter!=null){setter.Invoke(instance,new[]{value});return;}
        var field=instance.GetType().GetField("<"+name+">k__BackingField",
            System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic)
            ??throw new MissingFieldException(instance.GetType().FullName,"<"+name+">k__BackingField");
        field.SetValue(instance,value);
    }
}
