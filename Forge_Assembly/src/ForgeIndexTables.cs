using System.Buffers.Binary;
using System.IO;
using System.Text;

// Read the two index tables sequentially in bounded chunks. A per-entry seek
// alternates between distant tables, defeating FileStream's read buffer.
internal static class ForgeIndexTables
{
    internal static void Append(Stream stream, int count, long table, long info,
                                List<ForgeStream.Entry> entries)
    {
        const int chunkSize = 1024;
        byte[] offsets = new byte[Math.Min(count, chunkSize) * 20];
        byte[] metadata = new byte[Math.Min(count, chunkSize) * 192];
        for (int first = 0; first < count; first += chunkSize)
        {
            int n = Math.Min(chunkSize, count - first);
            stream.Position = table + first * 20L;
            stream.ReadExactly(offsets.AsSpan(0, n * 20));
            stream.Position = info + first * 192L;
            stream.ReadExactly(metadata.AsSpan(0, n * 192));
            for (int i = 0; i < n; i++)
            {
                var t = offsets.AsSpan(i * 20, 20);
                var m = metadata.AsSpan(i * 192, 192);
                long offset = BinaryPrimitives.ReadInt64LittleEndian(t);
                ulong id = BinaryPrimitives.ReadUInt64LittleEndian(t[8..]);
                int size = BinaryPrimitives.ReadInt32LittleEndian(t[16..]);
                if (offset < 0 || size < 0 || offset > stream.Length || size > stream.Length - offset)
                    throw new InvalidDataException("Forge range outside file");
                if (BinaryPrimitives.ReadInt32LittleEndian(m) != size)
                    throw new InvalidDataException("Inconsistent entry sizes");
                uint type = BinaryPrimitives.ReadUInt32LittleEndian(m[16..]);
                var name = m.Slice(44, 128);
                int end = name.IndexOf((byte)0);
                entries.Add(new(entries.Count, id, type,
                    Encoding.UTF8.GetString(end < 0 ? name : name[..end]), offset, size,
                    table + (first + i) * 20L, info + (first + i) * 192L));
            }
        }
    }
}
