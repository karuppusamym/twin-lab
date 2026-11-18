import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';

import { CaseStore, PRIORITIES, STATUSES } from './case-store';

@Component({
  selector: 'app-case-list',
  imports: [RouterLink],
  template: `
    <h1 id="casesHeading">Cases</h1>
    @if (saved(); as id) {
      <p id="savedMessage" class="notice" role="status">Case {{ id }} was saved.</p>
    }
    <div class="toolbar">
      <label for="search">Search</label>
      <input
        id="search"
        type="search"
        placeholder="Case number, title or owner"
        [value]="store.query()"
        (input)="store.search($any($event.target).value)"
      />
      <button
        id="btnClearSearch"
        type="button"
        [disabled]="!store.query()"
        (click)="store.search('')"
      >
        Clear
      </button>
      <span id="resultCount">{{ store.matching().length }} cases</span>
    </div>
    <table id="caseTable">
      <thead>
        <tr>
          <th>Case</th>
          <th>Title</th>
          <th>Owner</th>
          <th>Status</th>
          <th>Priority</th>
          <th>Due</th>
          <th>Urgent</th>
        </tr>
      </thead>
      <tbody>
        @for (c of store.visible(); track c.id) {
          <tr [id]="'case-' + c.id" [class.saved]="c.id === saved()">
            <td class="case-no">
              <a [id]="'open-' + c.id" [routerLink]="['/cases', c.id]">{{ c.id }}</a>
            </td>
            <td class="case-title">{{ c.title }}</td>
            <td class="case-owner">{{ c.owner }}</td>
            <td class="case-status">{{ statusLabel(c.status) }}</td>
            <td class="case-priority">{{ priorityLabel(c.priority) }}</td>
            <td class="case-due">{{ c.dueDate || '-' }}</td>
            <td class="case-urgent">{{ c.urgent ? 'Yes' : 'No' }}</td>
          </tr>
        } @empty {
          <tr id="noCases">
            <td colspan="7">No case matches the search.</td>
          </tr>
        }
      </tbody>
    </table>
    <div class="pager">
      <button id="btnPrevPage" type="button" [disabled]="page() <= 1" (click)="go(-1)">
        Previous
      </button>
      <span id="pageInfo">Page {{ page() }} of {{ store.pageCount() }}</span>
      <button
        id="btnNextPage"
        type="button"
        [disabled]="page() >= store.pageCount()"
        (click)="go(1)"
      >
        Next
      </button>
    </div>
  `,
  styles: `
    .toolbar,
    .pager {
      display: flex;
      align-items: center;
      gap: 10px;
      margin: 12px 0;
    }
    #search {
      width: 280px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      background: #fff;
    }
    th,
    td {
      text-align: left;
      padding: 7px 10px;
      border-bottom: 1px solid #dde3ea;
    }
    th {
      background: #e8edf3;
    }
    tr.saved td {
      background: #f1faf4;
    }
  `,
})
export class CaseList {
  protected readonly store = inject(CaseStore);
  protected readonly saved = this.store.savedId;

  protected page(): number {
    return Math.min(this.store.page(), this.store.pageCount());
  }

  protected go(by: number): void {
    this.store.page.set(Math.min(Math.max(1, this.page() + by), this.store.pageCount()));
  }

  protected statusLabel(value: string): string {
    return STATUSES.find((s) => s.value === value)?.label ?? value;
  }

  protected priorityLabel(value: string): string {
    return PRIORITIES.find((p) => p.value === value)?.label ?? value;
  }
}
