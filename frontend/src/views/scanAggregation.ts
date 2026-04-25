import type { ExtractionLog } from '@/types'

export interface EmailAggregate {
  email_uid: string | null
  email_subject: string
  extractions: ExtractionLog[]
  best_outcome: string
  invoice_no: string | null
  highest_tier: number | null
}

export interface EmailCountBreakdown {
  saved: number
  duplicates: number
  skipped: number
  not_invoice: number
  errors: number
  total_emails: number
}

// Non-obvious ordering choices (derived from 2026-04-20 investigation):
//   duplicate(80) > skipped_seen(70): duplicate carries the
//     "deduped by invoice_no" signal users need to see.
//   parse_failed(40) > error(30): parse_failed is specific.
//   not_vat_invoice / scam_detected tied at 55: both are
//     classifier-rejected for legitimate reasons.
// Unknown outcomes fall through to 0 at the call site (?? 0).
export const OUTCOME_PRIORITY: Record<string, number> = {
  saved: 100,
  success: 100,
  duplicate: 80,
  skipped_seen: 70,
  low_confidence: 60,
  not_vat_invoice: 55,
  scam_detected: 55,
  parse_failed: 40,
  error: 30,
  failed: 30,
  not_invoice: 10,
}

export const aggregateByEmail = (logs: ExtractionLog[]): EmailAggregate[] => {
  const buckets = new Map<string, EmailAggregate>()
  for (const log of logs) {
    // Manual uploads have no email_uid; bucket each by log.id so they
    // don't collapse into one synthetic "manual uploads" card.
    const key = log.email_uid || `__no_uid_${log.id}`
    let bucket = buckets.get(key)
    if (!bucket) {
      bucket = {
        email_uid: log.email_uid,
        email_subject: log.email_subject,
        extractions: [],
        best_outcome: log.outcome,
        invoice_no: null,
        highest_tier: null,
      }
      buckets.set(key, bucket)
    }
    bucket.extractions.push(log)
    const currentRank = OUTCOME_PRIORITY[bucket.best_outcome] ?? 0
    const newRank = OUTCOME_PRIORITY[log.outcome] ?? 0
    if (newRank > currentRank) bucket.best_outcome = log.outcome
    if (log.invoice_no && !bucket.invoice_no) bucket.invoice_no = log.invoice_no
    if (log.classification_tier != null) {
      bucket.highest_tier = Math.max(bucket.highest_tier ?? 0, log.classification_tier)
    }
  }
  return Array.from(buckets.values())
}

export const summarizeEmails = (aggregates: EmailAggregate[]): EmailCountBreakdown => {
  const result: EmailCountBreakdown = {
    saved: 0,
    duplicates: 0,
    skipped: 0,
    not_invoice: 0,
    errors: 0,
    total_emails: aggregates.length,
  }
  for (const agg of aggregates) {
    const o = agg.best_outcome
    if (o === 'saved' || o === 'success') result.saved += 1
    else if (o === 'duplicate') result.duplicates += 1
    else if (o === 'skipped_seen') result.skipped += 1
    else if (o === 'error' || o === 'failed' || o === 'parse_failed') result.errors += 1
    // Deliberate UX bucket: low_confidence / not_vat_invoice / scam_detected /
    // not_invoice / unknown all collapse here. Per-email badges below still
    // distinguish them; the banner aggregates them as "not an actionable
    // invoice" per the 2026-04-20 investigation.
    else result.not_invoice += 1
  }
  return result
}
