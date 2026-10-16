using System.IO;
using AnvilToolkit.Utils;

internal static class SchemaExport {
    internal static int Run(string[] args) {
        if(args.Length!=4)throw new IOException("schema-export <toolkit> <source resource> <new private XML>");
        string source=Path.GetFullPath(args[2]),output=Path.GetFullPath(args[3]);
        if(File.Exists(output)||Trial.Inside(Path.GetFullPath(args[1]),output)||!output.EndsWith(".xml",StringComparison.OrdinalIgnoreCase))throw new IOException("Unsafe XML export path");
        string original=Trial.Hash(source);HeadlessToolkit.InitializeHashes();DataStorage.LoadSchema();
        var schema=DataStorage.ActiveSchema??throw new IOException("GRB schema unavailable");
        var obj=schema.ReadFile(source);Directory.CreateDirectory(Path.GetDirectoryName(output)!);
        if(!schema.WriteToXml(obj,output)||Trial.Hash(source)!=original)throw new IOException("Schema export failed or source changed");
        Console.WriteLine("Read-only schema export: "+output);return 0;
    }
}
