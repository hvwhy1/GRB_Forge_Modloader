"""Exercise the production C# reader without requiring the proprietary toolkit."""
import shutil,subprocess,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HARNESS=r'''
using System.Text;
using System.Diagnostics;
using System.Text.Json;
internal static class Check {
 static List<ForgeStream.Entry> Legacy(Stream stream) {
  // Original per-entry table reader, retained as an independent comparator.
  using var r=new BinaryReader(stream,Encoding.UTF8,true);
  stream.Position=13;long header=r.ReadInt64();stream.Position=header;
  int total=r.ReadInt32();stream.Position=header+32;int dirs=r.ReadInt32();long next=r.ReadInt64();
  var entries=new List<ForgeStream.Entry>();
  for(int d=0;d<dirs;d++) {
   stream.Position=next;int count=r.ReadInt32();r.ReadInt32();long table=r.ReadInt64();
   next=r.ReadInt64();r.ReadInt32();r.ReadInt32();long info=r.ReadInt64();
   for(int i=0;i<count;i++) {
    long t=table+i*20L,m=info+i*192L;stream.Position=t;long offset=r.ReadInt64();ulong id=r.ReadUInt64();int size=r.ReadInt32();
    stream.Position=m;r.ReadInt32();r.ReadUInt64();r.ReadInt32();uint type=r.ReadUInt32();stream.Position=m+44;
    byte[] bytes=r.ReadBytes(128);int end=Array.IndexOf(bytes,(byte)0);
    entries.Add(new(entries.Count,id,type,Encoding.UTF8.GetString(bytes,0,end<0?128:end),offset,size,t,m));
   }
  }
  if(entries.Count!=total)throw new Exception("Bad fixture");return entries;
 }
 static byte[] Fixture(int count) {
  long table=128,info=table+count*20L;
  using var s=new MemoryStream(new byte[checked((int)(info+count*192L+8))]);using var w=new BinaryWriter(s,Encoding.UTF8,true);
  w.Write(Encoding.ASCII.GetBytes("scimitar\0"));w.Write(27);w.Write(32L);
  s.Position=32;w.Write(count);s.Position=64;w.Write(1);w.Write(80L);
  s.Position=80;w.Write(count);w.Write(0);w.Write(table);w.Write(-1L);w.Write(0);w.Write(0);w.Write(info);w.Write(0L);
  for(int i=0;i<count;i++) {
   s.Position=table+i*20;w.Write(s.Length-8);w.Write((ulong)(100+i));w.Write(8);
   s.Position=info+i*192;w.Write(8);s.Position=info+i*192+16;w.Write((uint)(300+i));
   s.Position=info+i*192+44;w.Write(Encoding.UTF8.GetBytes(i==0?new string('x',128):"scope_é_"+i));
  }
  return s.ToArray();
 }
 static void Reject(byte[] bytes) {
  try {ForgeStream.Read(new MemoryStream(bytes));}catch(InvalidDataException){return;}catch(IOException){return;}
  throw new Exception("Corrupt index accepted");
 }
 static void Main(string[] args) {
  if(args.Length>0) {
   foreach(string path in args) {
    using var f=File.OpenRead(path);var clock=Stopwatch.StartNew();var old=Legacy(f);double before=clock.Elapsed.TotalSeconds;
    clock.Restart();var actual=ForgeStream.Read(f);double after=clock.Elapsed.TotalSeconds;
    if(!old.SequenceEqual(actual))throw new Exception("Real index differs");
    var queries=actual.Where((e,i)=>i%Math.Max(1,actual.Count/1000)==0).Take(1000).ToArray();
    clock.Restart();var scanned=queries.Select(q=>actual.Where(e=>e.Id==q.Id&&e.Type==q.Type).ToArray()).ToArray();double scan=clock.Elapsed.TotalSeconds;
    clock.Restart();var lookup=new ForgeIdentityIndex(new(){[path]=actual});var found=queries.Select(q=>lookup.Local(path,q.Id,q.Type)).ToArray();double indexed=clock.Elapsed.TotalSeconds;
    for(int i=0;i<queries.Length;i++)if(!scanned[i].SequenceEqual(found[i]))throw new Exception("Real lookup differs");
    Console.WriteLine(JsonSerializer.Serialize(new{file=Path.GetFileName(path),entries=actual.Count,legacySeconds=before,blockSeconds=after,queries=queries.Length,scanSeconds=scan,indexBuildAndLookupSeconds=indexed,equal=true}));
   }
   return;
  }
  foreach(int count in new[]{1,1023,1024,1025,4099}) {
   byte[] bytes=Fixture(count);using var stream=new MemoryStream(bytes);
   if(!Legacy(stream).SequenceEqual(ForgeStream.Read(stream)))throw new Exception("Index differs at count "+count);
   // Metadata size disagreement, out-of-range/negative payload, and truncation.
   byte[] bad=(byte[])bytes.Clone();BitConverter.GetBytes(9).CopyTo(bad,128+count*20);Reject(bad);
   bad=(byte[])bytes.Clone();BitConverter.GetBytes(long.MaxValue).CopyTo(bad,128);Reject(bad);
   bad=(byte[])bytes.Clone();BitConverter.GetBytes(-1).CopyTo(bad,144);Reject(bad);
   Reject(bytes[..(bytes.Length-200)]);
   bad=(byte[])bytes.Clone();BitConverter.GetBytes(80L).CopyTo(bad,96);Reject(bad);
  }
  var a=new ForgeStream.Entry(0,123,456,"a",0,8,0,0);
  var b=a with {Name="b"};var other=a with {Type=789};
  var sources=new Dictionary<string,List<ForgeStream.Entry>>{
   ["local"]=new(){a,b,other},["base"]=new(){b,a},["empty"]=new()};
  var index=new ForgeIdentityIndex(sources);
  if(!index.Find("local",123,456).SequenceEqual(new[]{("local",a),("local",b)}))throw new Exception("Local duplicates or precedence lost");
  if(!index.Find("empty",123,456).SequenceEqual(new[]{("local",a),("local",b),("base",b),("base",a)}))throw new Exception("Global ambiguity/order lost");
  if(index.Find("base",123,789).Single()!=("local",other)||index.Find("empty",999,456).Length!=0)throw new Exception("Identity/type routing changed");
  Console.WriteLine("Boundary, UTF-8, full-length names, corruption, identity routing and legacy equivalence checks passed");
 }
}
'''

def build_harness(root):
    # Compile the actual header/index parser, excluding unrelated toolkit writers.
    source=(ROOT/'src/ForgeStream.cs').read_text().split('    internal static void CopyRange')[0]+'}\n'
    source='\n'.join(line for line in source.splitlines() if not line.startswith('using AnvilToolkit.'))
    (root/'ForgeStream.cs').write_text(source)
    shutil.copyfile(ROOT/'src/ForgeIndexTables.cs',root/'ForgeIndexTables.cs')
    shutil.copyfile(ROOT/'src/ForgeIdentityIndex.cs',root/'ForgeIdentityIndex.cs')
    (root/'Program.cs').write_text(HARNESS)
    (root/'IndexTest.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net9.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings></PropertyGroup></Project>')
    result=subprocess.run(['dotnet','build',str(root/'IndexTest.csproj'),'-c','Release','--nologo'],capture_output=True,text=True,timeout=120)
    if result.returncode:raise AssertionError(result.stdout+result.stderr)
    return root/'bin/Release/net9.0/IndexTest.dll'

class ForgeIndexTests(unittest.TestCase):
 @unittest.skipUnless(shutil.which('dotnet'),'Requires .NET SDK')
 def test_production_index_parser_matches_legacy_and_rejects_corruption(self):
  with tempfile.TemporaryDirectory() as folder:
   dll=build_harness(Path(folder))
   result=subprocess.run(['dotnet',str(dll)],capture_output=True,text=True,timeout=60)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
