using System;
using System.Collections.Generic;

// Reflection-free canonical evidence predicate for anonymous ATK audit output.
// ONLY for PRIVATE no-export staging. Never apply to the normal install gate.
internal static class PrivatePrefetchCanonicalization
{
    internal static bool IsProven(Dictionary<string, object?> audit)
    {
        if (!audit.TryGetValue("status", out var status) ||
            !string.Equals(status?.ToString(), "PREFETCH_BYTES_DIFFER_UNVERIFIED", StringComparison.Ordinal))
            return false;
        if (!audit.TryGetValue("byteIdentical", out var identical) || identical is not false)
            return false;
        if (!audit.TryGetValue("originalSha256", out var original) ||
            !audit.TryGetValue("rebuiltSha256", out var rebuilt) ||
            string.Equals(original?.ToString(), rebuilt?.ToString(), StringComparison.OrdinalIgnoreCase))
            return false;
        if (!audit.TryGetValue("toolkitReserialization", out var serialization) || serialization == null)
            return false;
        var type = serialization.GetType();
        object? Prop(string name) => type.GetProperty(name)?.GetValue(serialization);
        return Prop("attempted") is true &&
            Prop("originalParsesAndReserializesToRebuiltBytes") is true &&
            Prop("rebuiltPrefetchIsStableUnderReserialization") is true &&
            string.Equals(Prop("originalParsedReserializedSha256")?.ToString(), rebuilt?.ToString(), StringComparison.OrdinalIgnoreCase) &&
            string.Equals(Prop("rebuiltParsedReserializedSha256")?.ToString(), rebuilt?.ToString(), StringComparison.OrdinalIgnoreCase) &&
            Equals(Convert.ToInt64(Prop("originalParsedReserializedBytes")), Convert.ToInt64(audit["rebuiltBytes"])) &&
            Equals(Convert.ToInt64(Prop("rebuiltParsedReserializedBytes")), Convert.ToInt64(audit["rebuiltBytes"]));
    }
}
