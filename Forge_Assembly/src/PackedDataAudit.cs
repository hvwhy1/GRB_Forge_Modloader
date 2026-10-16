using System.IO;

// ATK's DataBlock reader skips the stored checksum. Check it independently
// before any private Forge writer can report a replacement as verified.
internal static class PackedDataAudit {
    const ulong Magic=1154322941026740787UL;
    static uint Adler(byte[] data) {
        uint a=0,b=0;int position=0;
        while(position<data.Length) {
            int end=Math.Min(position+5552,data.Length);
            for(;position<end;position++){a+=data[position];b+=a;}
            a%=65521;b%=65521;
        }
        return (b<<16)|a;
    }
    internal static void VerifyFile(string file) {using var stream=File.OpenRead(file);Verify(stream);}
    internal static List<(long Offset,uint Previous,uint Correct)> RepairChecksums(string file) {
        using var stream=new FileStream(file,FileMode.Open,FileAccess.ReadWrite,FileShare.None);
        var corrections=new List<(long Offset,uint Previous,uint Correct)>();
        // Validate the entire structure before writing even a single checksum.
        Verify(stream,corrections);
        using var writer=new BinaryWriter(stream,System.Text.Encoding.UTF8,true);
        foreach(var change in corrections){stream.Position=change.Offset;writer.Write(change.Correct);}
        writer.Flush();stream.Flush(true);stream.Position=0;Verify(stream);
        return corrections;
    }
    internal static void Verify(Stream stream,List<(long Offset,uint Previous,uint Correct)>? corrections=null) {
        using var reader=new BinaryReader(stream,System.Text.Encoding.UTF8,true);
        void Range(long count){if(count<0||count>stream.Length-stream.Position)throw new InvalidDataException("Packed block outside file");}
        void Check(int raw,int packed,bool checksum) {
            if(raw<=0||packed<=0||raw>16*1024*1024||packed>16*1024*1024)throw new InvalidDataException("Invalid packed block length");
            Range(packed+(checksum?4L:0L));long checksumOffset=stream.Position;uint stored=checksum?reader.ReadUInt32():0;
            byte[] bytes=reader.ReadBytes(packed);
            if(checksum&&stored!=Adler(bytes)) {
                if(corrections==null)throw new InvalidDataException("Packed .data Adler-32 checksum mismatch");
                corrections.Add((checksumOffset,stored,Adler(bytes)));
            }
        }
        for(int section=0;section<2;section++) {
            Range(15);if(reader.ReadUInt64()!=Magic)throw new InvalidDataException("Packed section magic mismatch");
            short version=reader.ReadInt16();byte algorithm=reader.ReadByte();uint flags=reader.ReadUInt32();
            int blockSize=(int)(flags&0x7fffffff);
            if(version<0||version>3||algorithm>4||blockSize<=0||blockSize>16*1024*1024)throw new InvalidDataException("Unsupported GRB packed header");
            if((flags>>31)==1) {
                Range(4);uint count=reader.ReadUInt32();int width=version==1?4:8;
                if(count==0||count>200000)throw new InvalidDataException("Invalid packed block count");
                Range(count*(long)width);var table=new List<(int raw,int packed)>();
                for(int i=0;i<count;i++)table.Add(version==1?(reader.ReadUInt16(),reader.ReadUInt16()):(reader.ReadInt32(),reader.ReadInt32()));
                foreach(var block in table){if(block.raw>blockSize)throw new InvalidDataException("Block exceeds declared block size");Check(block.raw,block.packed,true);}
            } else {
                int count=0;
                while(stream.Position<stream.Length) {
                    long position=stream.Position;byte flag=reader.ReadByte();
                    if(flag>1){stream.Position=position;break;}
                    Range(flag==1?8:4);int packed=reader.ReadInt32();int raw=flag==1?reader.ReadInt32():packed;
                    if(raw>blockSize)throw new InvalidDataException("Default-mode block exceeds block size");
                    Check(raw,packed,flag==1);if(++count>200000)throw new InvalidDataException("Too many packed blocks");
                }
                if(count==0)throw new InvalidDataException("Empty default-mode section");
            }
        }
        if(stream.Position!=stream.Length)throw new InvalidDataException("Trailing bytes outside packed sections");
    }
}
