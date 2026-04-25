import { describe, it, expect } from 'vitest'
import type { ExtractionLog } from '@/types'
import {
  aggregateByEmail,
  summarizeEmails,
  OUTCOME_PRIORITY,
} from '../scanAggregation'

const makeLog = (overrides: Partial<ExtractionLog> = {}): ExtractionLog => ({
  id: 1,
  email_uid: 'uid-1',
  email_subject: 'Invoice from Acme',
  attachment_filename: 'invoice.pdf',
  outcome: 'saved',
  classification_tier: 1,
  parse_method: 'deterministic',
  parse_format: 'pdf',
  download_outcome: null,
  invoice_no: '12345',
  confidence: 0.95,
  error_detail: null,
  created_at: '2026-04-20T10:00:00Z',
  ...overrides,
})

describe('aggregateByEmail', () => {
  it('returns empty array for empty input', () => {
    expect(aggregateByEmail([])).toEqual([])
  })

  it('single email with single extraction → one aggregate, pass-through fields', () => {
    const log = makeLog({
      id: 1,
      email_uid: 'uid-A',
      email_subject: 'Test Email',
      outcome: 'saved',
      invoice_no: 'INV-001',
      classification_tier: 2,
    })
    const result = aggregateByEmail([log])
    expect(result).toHaveLength(1)
    expect(result[0]).toEqual({
      email_uid: 'uid-A',
      email_subject: 'Test Email',
      extractions: [log],
      best_outcome: 'saved',
      invoice_no: 'INV-001',
      highest_tier: 2,
    })
  })

  it('single email with 3 attachments (Sam\'s Club shape) → one aggregate with 3 extractions', () => {
    // A typical Chinese e-invoice email arrives as pdf + xml + ofd
    // attachments. Only ONE gets saved (highest precedence); the
    // other two end up low_confidence or parse_failed because their
    // formats don't match the same extractor.
    const logs: ExtractionLog[] = [
      makeLog({ id: 1, email_uid: 'uid-sams', outcome: 'saved',          parse_format: 'pdf', invoice_no: '25442000000123456789', classification_tier: 1 }),
      makeLog({ id: 2, email_uid: 'uid-sams', outcome: 'low_confidence', parse_format: 'ofd', invoice_no: null,                   classification_tier: 1 }),
      makeLog({ id: 3, email_uid: 'uid-sams', outcome: 'low_confidence', parse_format: 'xml', invoice_no: null,                   classification_tier: 1 }),
    ]
    const result = aggregateByEmail(logs)
    expect(result).toHaveLength(1)
    expect(result[0].email_uid).toBe('uid-sams')
    expect(result[0].extractions).toHaveLength(3)
    expect(result[0].best_outcome).toBe('saved')
    expect(result[0].invoice_no).toBe('25442000000123456789')
  })

  it('two emails with duplicate invoice_no (actual Sam\'s Club 2026-04-20 scenario) → 2 cards, saved + duplicate', () => {
    // Sam's Club retransmitted the same e-invoice 247s apart.
    // First email: pdf+ofd+xml, all saved/low_confidence (new invoice).
    // Second email: pdf+ofd+xml, all duplicate (invoice_no collision).
    // Before Fix 8 this rendered as 6 rows of confusion. After Fix 8,
    // it's clearly 2 email cards: 1 saved, 1 duplicate.
    const logs: ExtractionLog[] = [
      makeLog({ id: 1, email_uid: 'uid-first',  outcome: 'saved',          parse_format: 'pdf', invoice_no: 'INV-SAMS' }),
      makeLog({ id: 2, email_uid: 'uid-first',  outcome: 'low_confidence', parse_format: 'ofd' }),
      makeLog({ id: 3, email_uid: 'uid-first',  outcome: 'low_confidence', parse_format: 'xml' }),
      makeLog({ id: 4, email_uid: 'uid-second', outcome: 'duplicate',      parse_format: 'pdf', invoice_no: 'INV-SAMS' }),
      makeLog({ id: 5, email_uid: 'uid-second', outcome: 'duplicate',      parse_format: 'ofd', invoice_no: 'INV-SAMS' }),
      makeLog({ id: 6, email_uid: 'uid-second', outcome: 'duplicate',      parse_format: 'xml', invoice_no: 'INV-SAMS' }),
    ]
    const result = aggregateByEmail(logs)
    expect(result).toHaveLength(2)
    const [first, second] = result
    expect(first.email_uid).toBe('uid-first')
    expect(first.best_outcome).toBe('saved')
    expect(first.invoice_no).toBe('INV-SAMS')
    expect(second.email_uid).toBe('uid-second')
    expect(second.best_outcome).toBe('duplicate')
    expect(second.invoice_no).toBe('INV-SAMS')
  })

  it('manual uploads (missing email_uid) → each extraction gets its own bucket', () => {
    // Backend uses email_uid="manual:{scan_log.id}" for manual uploads,
    // but defensively the aggregator also handles the empty-string /
    // null case by minting a synthetic __no_uid_{id} key so manual
    // uploads never collapse into one giant card.
    const logs: ExtractionLog[] = [
      makeLog({ id: 10, email_uid: '', email_subject: 'Upload 1', outcome: 'saved'    }),
      makeLog({ id: 11, email_uid: '', email_subject: 'Upload 2', outcome: 'duplicate' }),
      makeLog({ id: 12, email_uid: '', email_subject: 'Upload 3', outcome: 'saved'    }),
    ]
    const result = aggregateByEmail(logs)
    expect(result).toHaveLength(3)
    expect(result.map((a) => a.extractions[0].id)).toEqual([10, 11, 12])
  })

  it('mixed valid + empty email_uid → valid emails group, empty ones stay separate', () => {
    const logs: ExtractionLog[] = [
      makeLog({ id: 1, email_uid: 'uid-A', outcome: 'saved'      }),
      makeLog({ id: 2, email_uid: 'uid-A', outcome: 'duplicate'  }),
      makeLog({ id: 3, email_uid: '',      outcome: 'saved'      }),
      makeLog({ id: 4, email_uid: '',      outcome: 'saved'      }),
    ]
    const result = aggregateByEmail(logs)
    expect(result).toHaveLength(3)
    expect(result[0].email_uid).toBe('uid-A')
    expect(result[0].extractions).toHaveLength(2)
    expect(result[1].email_uid).toBe('')
    expect(result[1].extractions).toHaveLength(1)
    expect(result[2].email_uid).toBe('')
    expect(result[2].extractions).toHaveLength(1)
  })

  it('picks highest-priority outcome regardless of iteration order (duplicate before saved)', () => {
    const logs: ExtractionLog[] = [
      makeLog({ id: 1, email_uid: 'uid-X', outcome: 'duplicate' }),
      makeLog({ id: 2, email_uid: 'uid-X', outcome: 'saved'     }),
      makeLog({ id: 3, email_uid: 'uid-X', outcome: 'error'     }),
    ]
    expect(aggregateByEmail(logs)[0].best_outcome).toBe('saved')
  })

  it('picks highest-priority outcome (error before saved too)', () => {
    const logs: ExtractionLog[] = [
      makeLog({ id: 1, email_uid: 'uid-X', outcome: 'error' }),
      makeLog({ id: 2, email_uid: 'uid-X', outcome: 'saved' }),
    ]
    expect(aggregateByEmail(logs)[0].best_outcome).toBe('saved')
  })

  it('captures first non-null invoice_no (iteration order)', () => {
    const logs: ExtractionLog[] = [
      makeLog({ id: 1, email_uid: 'uid-X', outcome: 'low_confidence', invoice_no: null     }),
      makeLog({ id: 2, email_uid: 'uid-X', outcome: 'saved',          invoice_no: 'FIRST'  }),
      makeLog({ id: 3, email_uid: 'uid-X', outcome: 'duplicate',      invoice_no: 'SECOND' }),
    ]
    expect(aggregateByEmail(logs)[0].invoice_no).toBe('FIRST')
  })

  it('highest_tier tracks the max, null-safe', () => {
    const logs: ExtractionLog[] = [
      makeLog({ id: 1, email_uid: 'uid-X', classification_tier: 1    }),
      makeLog({ id: 2, email_uid: 'uid-X', classification_tier: 3    }),
      makeLog({ id: 3, email_uid: 'uid-X', classification_tier: null }),
      makeLog({ id: 4, email_uid: 'uid-X', classification_tier: 2    }),
    ]
    expect(aggregateByEmail(logs)[0].highest_tier).toBe(3)
  })

  it('highest_tier stays null when all extractions have null tier', () => {
    const logs: ExtractionLog[] = [
      makeLog({ id: 1, email_uid: 'uid-X', classification_tier: null }),
      makeLog({ id: 2, email_uid: 'uid-X', classification_tier: null }),
    ]
    expect(aggregateByEmail(logs)[0].highest_tier).toBe(null)
  })

  it('unknown outcomes fall through OUTCOME_PRIORITY with rank 0', () => {
    // An unknown outcome coming second after a known one of higher
    // priority must NOT overwrite. This guards against upstream
    // backend outcome-name drift silently inverting precedence.
    const logs: ExtractionLog[] = [
      makeLog({ id: 1, email_uid: 'uid-X', outcome: 'saved'              }),
      makeLog({ id: 2, email_uid: 'uid-X', outcome: 'mystery_new_outcome' }),
    ]
    expect(aggregateByEmail(logs)[0].best_outcome).toBe('saved')
  })

  it('two unknown outcomes → first-seen wins (both rank 0, newRank not > currentRank)', () => {
    const logs: ExtractionLog[] = [
      makeLog({ id: 1, email_uid: 'uid-X', outcome: 'mystery_one' }),
      makeLog({ id: 2, email_uid: 'uid-X', outcome: 'mystery_two' }),
    ]
    expect(aggregateByEmail(logs)[0].best_outcome).toBe('mystery_one')
  })
})

describe('summarizeEmails', () => {
  it('empty input → all zeros', () => {
    expect(summarizeEmails([])).toEqual({
      saved: 0,
      duplicates: 0,
      skipped: 0,
      not_invoice: 0,
      errors: 0,
      total_emails: 0,
    })
  })

  it('buckets each best_outcome into the right counter', () => {
    const logs: ExtractionLog[] = [
      makeLog({ id: 1,  email_uid: 'e1', outcome: 'saved'          }),
      makeLog({ id: 2,  email_uid: 'e2', outcome: 'success'        }),
      makeLog({ id: 3,  email_uid: 'e3', outcome: 'duplicate'      }),
      makeLog({ id: 4,  email_uid: 'e4', outcome: 'skipped_seen'   }),
      makeLog({ id: 5,  email_uid: 'e5', outcome: 'error'          }),
      makeLog({ id: 6,  email_uid: 'e6', outcome: 'failed'         }),
      makeLog({ id: 7,  email_uid: 'e7', outcome: 'parse_failed'   }),
      makeLog({ id: 8,  email_uid: 'e8', outcome: 'low_confidence' }),
      makeLog({ id: 9,  email_uid: 'e9', outcome: 'not_vat_invoice'}),
      makeLog({ id: 10, email_uid: 'e10', outcome: 'scam_detected' }),
      makeLog({ id: 11, email_uid: 'e11', outcome: 'not_invoice'   }),
      makeLog({ id: 12, email_uid: 'e12', outcome: 'mystery'       }),
    ]
    const result = summarizeEmails(aggregateByEmail(logs))
    expect(result).toEqual({
      saved: 2,
      duplicates: 1,
      skipped: 1,
      errors: 3,
      // low_confidence + not_vat_invoice + scam_detected + not_invoice + mystery
      // = 5 in the "not actionable" bucket.
      not_invoice: 5,
      total_emails: 12,
    })
  })

  it('buckets total always equals total_emails (invariant)', () => {
    const logs: ExtractionLog[] = [
      makeLog({ id: 1, email_uid: 'uid-A', outcome: 'saved'          }),
      makeLog({ id: 2, email_uid: 'uid-B', outcome: 'duplicate'      }),
      makeLog({ id: 3, email_uid: 'uid-C', outcome: 'low_confidence' }),
    ]
    const result = summarizeEmails(aggregateByEmail(logs))
    const sum = result.saved + result.duplicates + result.skipped + result.errors + result.not_invoice
    expect(sum).toBe(result.total_emails)
    expect(sum).toBe(3)
  })

  it('Sam\'s Club 6-row scenario → "2 emails · 1 saved · 1 duplicate"', () => {
    // The EXACT user-complaint shape from the 2026-04-20 investigation.
    // Pre-Fix 8: flat list of 6 rows. Post-Fix 8: summary banner says
    // 2 emails, 1 saved, 1 duplicate. This is the canonical
    // regression test.
    const logs: ExtractionLog[] = [
      makeLog({ id: 1, email_uid: 'uid-first',  outcome: 'saved',          invoice_no: 'INV-SAMS' }),
      makeLog({ id: 2, email_uid: 'uid-first',  outcome: 'low_confidence' }),
      makeLog({ id: 3, email_uid: 'uid-first',  outcome: 'low_confidence' }),
      makeLog({ id: 4, email_uid: 'uid-second', outcome: 'duplicate',      invoice_no: 'INV-SAMS' }),
      makeLog({ id: 5, email_uid: 'uid-second', outcome: 'duplicate',      invoice_no: 'INV-SAMS' }),
      makeLog({ id: 6, email_uid: 'uid-second', outcome: 'duplicate',      invoice_no: 'INV-SAMS' }),
    ]
    expect(summarizeEmails(aggregateByEmail(logs))).toEqual({
      saved: 1,
      duplicates: 1,
      skipped: 0,
      not_invoice: 0,
      errors: 0,
      total_emails: 2,
    })
  })
})

describe('OUTCOME_PRIORITY', () => {
  it('saved and success are tied at the top', () => {
    expect(OUTCOME_PRIORITY.saved).toBe(OUTCOME_PRIORITY.success)
    expect(OUTCOME_PRIORITY.saved).toBe(100)
  })

  it('duplicate > skipped_seen (the 2026-04-20 investigation fix)', () => {
    expect(OUTCOME_PRIORITY.duplicate).toBeGreaterThan(OUTCOME_PRIORITY.skipped_seen)
  })

  it('parse_failed > error (specificity)', () => {
    expect(OUTCOME_PRIORITY.parse_failed).toBeGreaterThan(OUTCOME_PRIORITY.error)
  })

  it('error and failed are tied (both are generic unhappy-path signals)', () => {
    expect(OUTCOME_PRIORITY.error).toBe(OUTCOME_PRIORITY.failed)
  })

  it('not_vat_invoice and scam_detected are tied (both classifier rejections)', () => {
    expect(OUTCOME_PRIORITY.not_vat_invoice).toBe(OUTCOME_PRIORITY.scam_detected)
  })

  it('precedence order (fully specified): saved=success > duplicate > skipped_seen > low_confidence > not_vat_invoice=scam_detected > parse_failed > error=failed > not_invoice', () => {
    expect(OUTCOME_PRIORITY.saved).toBeGreaterThan(OUTCOME_PRIORITY.duplicate)
    expect(OUTCOME_PRIORITY.duplicate).toBeGreaterThan(OUTCOME_PRIORITY.skipped_seen)
    expect(OUTCOME_PRIORITY.skipped_seen).toBeGreaterThan(OUTCOME_PRIORITY.low_confidence)
    expect(OUTCOME_PRIORITY.low_confidence).toBeGreaterThan(OUTCOME_PRIORITY.not_vat_invoice)
    expect(OUTCOME_PRIORITY.not_vat_invoice).toBeGreaterThan(OUTCOME_PRIORITY.parse_failed)
    expect(OUTCOME_PRIORITY.parse_failed).toBeGreaterThan(OUTCOME_PRIORITY.error)
    expect(OUTCOME_PRIORITY.error).toBeGreaterThan(OUTCOME_PRIORITY.not_invoice)
  })
})
