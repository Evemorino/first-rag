import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { buildEntriesQuery } from "../api/params";
import type { Filters } from "../store/ui";

export function useTypes() {
  return useQuery({ queryKey: ["types"], queryFn: api.types, staleTime: Infinity });
}

export function useEntries(filters: Filters, page: number) {
  const query = buildEntriesQuery(filters, page);
  return useQuery({ queryKey: ["entries", query], queryFn: () => api.entries(query) });
}

export function useEntry(id: string | null) {
  return useQuery({
    queryKey: ["entry", id],
    queryFn: () => api.entry(id ?? ""),
    enabled: id !== null,
  });
}
