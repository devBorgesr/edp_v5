// Hooks TanStack Query sobre o cliente REST tipado.
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { dashboard, memory, lineage, flags } from '../api/rest'

// ── Dashboard ────────────────────────────────────────────────────────────────
export function useDashboard() {
  return useQuery({
    queryKey: ['dashboard'],
    queryFn: () => dashboard.state(),
    refetchInterval: 5000,
  })
}

// ── Memory ───────────────────────────────────────────────────────────────────
export interface MemoryFilters {
  layer?: 'episodic' | 'semantic' | 'all'
  filter_status?: string
  filter_source?: string
  search?: string
  limit?: number
  offset?: number
}

export function useMemoryList(filters: MemoryFilters) {
  return useQuery({
    queryKey: ['memory', 'list', filters],
    queryFn: () => memory.list(filters),
  })
}

export function useMemoryStats() {
  return useQuery({ queryKey: ['memory', 'stats'], queryFn: () => memory.stats() })
}

export function useMemoryDominance() {
  return useQuery({ queryKey: ['memory', 'dominance'], queryFn: () => memory.dominance() })
}

export function useDeleteMemory() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => memory.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['memory'] })
    },
  })
}

// ── Lineage (histórico da câmara / proveniência) ─────────────────────────────
export function useLineage(lastN = 30) {
  return useQuery({
    queryKey: ['lineage', lastN],
    queryFn: () => lineage.list(lastN),
  })
}

// ── Flags / contradições ─────────────────────────────────────────────────────
export function useFlags(filter_feedback?: string) {
  return useQuery({
    queryKey: ['flags', 'list', filter_feedback],
    queryFn: () => flags.list({ limit: 100, filter_feedback }),
  })
}

export function useFlagsAggregates() {
  return useQuery({ queryKey: ['flags', 'aggregates'], queryFn: () => flags.aggregates() })
}

export function useFlagFeedback() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, feedback, note }: { id: string; feedback: string; note?: string }) =>
      flags.feedback(id, feedback, note ?? ''),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['flags'] })
    },
  })
}
