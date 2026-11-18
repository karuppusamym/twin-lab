import { Injectable, computed, signal } from '@angular/core';

export type Status = 'open' | 'in-progress' | 'waiting' | 'closed';
export type Priority = 'low' | 'normal' | 'high';

export interface CaseRecord {
  id: string;
  title: string;
  owner: string;
  status: Status;
  priority: Priority;
  urgent: boolean;
  dueDate: string; // yyyy-mm-dd, or empty
  notes: string;
}

export const STATUSES: { value: Status; label: string }[] = [
  { value: 'open', label: 'Open' },
  { value: 'in-progress', label: 'In progress' },
  { value: 'waiting', label: 'Waiting for customer' },
  { value: 'closed', label: 'Closed' },
];

export const PRIORITIES: { value: Priority; label: string }[] = [
  { value: 'low', label: 'Low' },
  { value: 'normal', label: 'Normal' },
  { value: 'high', label: 'High' },
];

export const PAGE_SIZE = 5;

type Row = [string, string, string, Status, Priority, boolean, string];

// Synthetic records: invented titles and first names, no real customer or person.
const ROWS: Row[] = [
  ['CB-1001', 'Pump seal leaking on line two', 'Avery', 'open', 'high', true, '2026-12-01'],
  ['CB-1002', 'Invoice layout misses the tax line', 'Blake', 'in-progress', 'normal', false, '2026-12-03'],
  ['CB-1003', 'Valve actuator slow to respond', 'Casey', 'open', 'normal', false, '2026-12-04'],
  ['CB-1004', 'Badge reader rejects new cards', 'Devon', 'waiting', 'low', false, '2026-12-08'],
  ['CB-1005', 'Conveyor belt tracking drifts left', 'Emery', 'open', 'high', true, '2026-12-09'],
  ['CB-1006', 'Label printer prints blank pages', 'Finley', 'closed', 'low', false, ''],
  ['CB-1007', 'Valve position sensor reads zero', 'Gray', 'open', 'normal', false, '2026-12-11'],
  ['CB-1008', 'Night shift report arrives late', 'Harper', 'in-progress', 'normal', false, '2026-12-12'],
  ['CB-1009', 'Forklift charger trips the breaker', 'Indigo', 'waiting', 'high', true, '2026-12-15'],
  ['CB-1010', 'Warehouse scanner loses its pairing', 'Jules', 'open', 'low', false, ''],
  ['CB-1011', 'Cooling fan vibrates at full speed', 'Kai', 'open', 'normal', false, '2026-12-17'],
  ['CB-1012', 'Shipping manifest shows old address', 'Lane', 'closed', 'normal', false, '2026-12-19'],
];

const SEED: CaseRecord[] = ROWS.map(([id, title, owner, status, priority, urgent, dueDate]) => ({
  id,
  title,
  owner,
  status,
  priority,
  urgent,
  dueDate,
  notes: '',
}));

/** The cases, and where the list is (search text, page). In memory only: a reload starts over. */
@Injectable({ providedIn: 'root' })
export class CaseStore {
  readonly cases = signal<CaseRecord[]>(SEED.map((c) => ({ ...c })));
  readonly query = signal('');
  readonly page = signal(1);
  readonly savedId = signal<string | null>(null);

  readonly matching = computed(() => {
    const q = this.query().trim().toLowerCase();
    if (!q) return this.cases();
    return this.cases().filter((c) =>
      [c.id, c.title, c.owner].some((text) => text.toLowerCase().includes(q)),
    );
  });
  readonly pageCount = computed(() => Math.max(1, Math.ceil(this.matching().length / PAGE_SIZE)));
  readonly visible = computed(() => {
    const start = (Math.min(this.page(), this.pageCount()) - 1) * PAGE_SIZE;
    return this.matching().slice(start, start + PAGE_SIZE);
  });

  search(text: string): void {
    this.query.set(text);
    this.page.set(1);
  }

  find(id: string): CaseRecord | undefined {
    return this.cases().find((c) => c.id === id);
  }

  save(record: CaseRecord): void {
    this.cases.update((all) => all.map((c) => (c.id === record.id ? { ...record } : c)));
    this.savedId.set(record.id);
  }
}
