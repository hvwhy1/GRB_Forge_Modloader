using System.IO;
using System.Text;
using AnvilToolkit.FileTypes.AnvilNext.Containers;

internal static class V20FormatTests {
    internal static int Run() {
        var entry=new ForgeEntry {LengthOnDisk=123,UMACHash=456,EngineVersion=7,Extension=8,
            RevisionNumberData=9,RevisionNumberAttributes=10,Parent=11,TimeStamp=12,
            SCCStatusData=13,MetaFileKey=14,SCCStatusAttributes=15,IsHidden=16};
        var record=ForgeOuterAddTrial.MetadataRecord(entry,"TEST_ROOT",2);
        using var reader=new BinaryReader(new MemoryStream(record),Encoding.UTF8);
        if(record.Length!=192||reader.ReadInt32()!=123||reader.ReadUInt64()!=456||reader.ReadInt32()!=7||
            reader.ReadUInt32()!=8||reader.ReadInt32()!=9||reader.ReadInt32()!=10||reader.ReadInt32()!=3||
            reader.ReadInt32()!=1||reader.ReadInt32()!=11||reader.ReadUInt32()!=12)
            throw new IOException("V27 metadata header offsets differ");
        var name=reader.ReadBytes(128);
        if(Encoding.UTF8.GetString(name).TrimEnd('\0')!="TEST_ROOT"||reader.ReadInt32()!=13||
            reader.ReadUInt64()!=14||reader.ReadInt32()!=15||reader.ReadInt32()!=16||reader.BaseStream.Position!=192)
            throw new IOException("V27 metadata tail offsets differ");
        bool rejected=false;
        try{ForgeOuterAddTrial.MetadataRecord(entry,new string('é',64),0);}catch(IOException){rejected=true;}
        if(!rejected)throw new IOException("UTF8 metadata name limit was not enforced");
        byte[] Packet(bool toc) {
            using var stream=new MemoryStream();using var writer=new BinaryWriter(stream);
            for(int section=0;section<2;section++) {
                writer.Write(1154322941026740787UL);writer.Write((short)3);writer.Write((byte)3);writer.Write(toc?0x80008000U:32768U);
                if(toc){writer.Write(1);writer.Write(4);writer.Write(4);writer.Write(0x045901C0U);}else{writer.Write(false);writer.Write(4);}
                writer.Write(Encoding.ASCII.GetBytes("test"));
            }
            return stream.ToArray();
        }
        foreach(bool toc in new[]{true,false})using(var packet=new MemoryStream(Packet(toc)))PackedDataAudit.Verify(packet);
        byte[] damaged=Packet(true);damaged[27]^=1;rejected=false;
        try{using var packet=new MemoryStream(damaged);PackedDataAudit.Verify(packet);}catch(InvalidDataException){rejected=true;}
        if(!rejected)throw new IOException("Packed checksum corruption was accepted");
        string repairFile=Path.Combine(Path.GetTempPath(),"deef-checksum-test-"+Guid.NewGuid().ToString("N")+".data");
        try {
            File.WriteAllBytes(repairFile,damaged);
            var repaired=PackedDataAudit.RepairChecksums(repairFile);
            if(repaired.Count!=1||!File.ReadAllBytes(repairFile).SequenceEqual(Packet(true)))throw new IOException("Checksum repair changed non-checksum bytes");
            if(PackedDataAudit.RepairChecksums(repairFile).Count!=0)throw new IOException("Checksum repair was not idempotent");
            byte[] truncated=damaged[..^1];File.WriteAllBytes(repairFile,truncated);rejected=false;
            try{PackedDataAudit.RepairChecksums(repairFile);}catch(InvalidDataException){rejected=true;}
            if(!rejected||!File.ReadAllBytes(repairFile).SequenceEqual(truncated))throw new IOException("Invalid packet was modified during checksum repair");
        } finally{File.Delete(repairFile);}
        using(var fixture=new MemoryStream(new byte[2048],true)) {
            using var w=new BinaryWriter(fixture,Encoding.UTF8,true);
            w.Write(Encoding.ASCII.GetBytes("scimitar\0"));w.Write(27);w.Write(64L);
            fixture.Position=64;w.Write(3);fixture.Position=96;w.Write(3);w.Write(112L);
            void Directory(long address,int count,long table,long next,int first,int last,long info) {
                fixture.Position=address;w.Write(count);w.Write(0);w.Write(table);w.Write(next);w.Write(first);w.Write(last);w.Write(info);w.Write(0L);
            }
            Directory(112,2,256,160,0,1,512);
            Directory(160,0,0,208,2,3,0);
            Directory(208,1,296,-1,10000,14999,896);
            for(int i=0;i<3;i++) {
                fixture.Position=i<2?256+i*20:296;w.Write(1800L+i*4);w.Write((ulong)(i+1));w.Write(4);
                fixture.Position=i<2?512+i*192:896;
                w.Write(ForgeOuterAddTrial.MetadataRecord(new ForgeEntry{LengthOnDisk=4,Extension=8},"ITEM"+i,i));
                fixture.Position=1800+i*4;w.Write(0x12345678+i);
            }
            w.Flush();var before=ForgeStream.Read(fixture);byte[] original=fixture.ToArray();
            ForgeDirectoryRepair.Normalize(fixture);var after=ForgeStream.Read(fixture);
            if(!before.SequenceEqual(after)||!original.AsSpan(1800,12).SequenceEqual(fixture.ToArray().AsSpan(1800,12)))throw new IOException("Range repair changed payloads or entry records");
            fixture.Position=96;using var r=new BinaryReader(fixture,Encoding.UTF8,true);
            if(r.ReadInt32()!=2)throw new IOException("Empty set remained in active directory chain");
            fixture.Position=232;if(r.ReadInt32()!=2)throw new IOException("Populated set global index remains outside total count");
            if(ForgeDirectoryRepair.Normalize(fixture).Count!=0)throw new IOException("Directory normalization is not idempotent");
        }
        Console.WriteLine("V20 format tests passed: metadata fields, offsets, links and UTF8 limits.");
        return 0;
    }
}
