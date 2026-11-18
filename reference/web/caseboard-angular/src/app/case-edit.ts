import { Component, inject, signal } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { CaseStore, PRIORITIES, Priority, STATUSES, Status } from './case-store';

@Component({
  selector: 'app-case-edit',
  imports: [ReactiveFormsModule, RouterLink],
  template: `
    <h1 id="editHeading">
      Edit case <span id="caseId">{{ id }}</span>
    </h1>
    <form id="caseForm" [formGroup]="form" (ngSubmit)="save()" novalidate>
      <div class="field">
        <label for="title">Title</label>
        <input id="title" type="text" formControlName="title" maxlength="80" />
        @if (shows('title')) {
          <p id="titleError" class="error" role="alert">A title is required.</p>
        }
      </div>
      <div class="field">
        <label for="owner">Owner</label>
        <input id="owner" type="text" formControlName="owner" maxlength="40" />
        @if (shows('owner')) {
          <p id="ownerError" class="error" role="alert">An owner is required.</p>
        }
      </div>
      <div class="field">
        <label for="status">Status</label>
        <select id="status" formControlName="status">
          @for (s of statuses; track s.value) {
            <option [value]="s.value">{{ s.label }}</option>
          }
        </select>
      </div>
      <fieldset id="priorityGroup">
        <legend>Priority</legend>
        @for (p of priorities; track p.value) {
          <label [for]="'priority-' + p.value">
            <input
              type="radio"
              [id]="'priority-' + p.value"
              name="priority"
              formControlName="priority"
              [value]="p.value"
            />
            {{ p.label }}
          </label>
        }
      </fieldset>
      <div class="field inline">
        <input id="urgent" type="checkbox" formControlName="urgent" />
        <label for="urgent">Urgent</label>
      </div>
      <div class="field">
        <label for="dueDate">Due date</label>
        <input id="dueDate" type="date" formControlName="dueDate" />
      </div>
      <div class="field">
        <label for="notes">Notes</label>
        <textarea id="notes" rows="3" formControlName="notes"></textarea>
      </div>
      @if (submitted() && form.invalid) {
        <p id="formError" class="error" role="alert">
          The case was not saved: see the messages above.
        </p>
      }
      <div class="actions">
        <button id="btnSave" type="submit" class="primary">Save</button>
        <a id="btnCancel" routerLink="/cases">Cancel</a>
      </div>
    </form>
  `,
  styles: `
    form {
      display: grid;
      gap: 12px;
      max-width: 520px;
      background: #fff;
      padding: 20px;
      border-radius: 6px;
    }
    .field {
      display: grid;
      gap: 4px;
    }
    .field.inline {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    fieldset {
      display: flex;
      gap: 16px;
      border: 1px solid #dde3ea;
      border-radius: 4px;
    }
    .error {
      margin: 0;
    }
    .actions {
      display: flex;
      align-items: center;
      gap: 16px;
    }
    input.ng-invalid.ng-touched {
      border-color: #a61b1b;
    }
  `,
})
export class CaseEdit {
  private readonly store = inject(CaseStore);
  private readonly router = inject(Router);
  protected readonly id = inject(ActivatedRoute).snapshot.paramMap.get('id') ?? '';
  protected readonly statuses = STATUSES;
  protected readonly priorities = PRIORITIES;
  protected readonly submitted = signal(false);
  protected readonly form = new FormGroup({
    title: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    owner: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    status: new FormControl<Status>('open', { nonNullable: true }),
    priority: new FormControl<Priority>('normal', { nonNullable: true }),
    urgent: new FormControl(false, { nonNullable: true }),
    dueDate: new FormControl('', { nonNullable: true }),
    notes: new FormControl('', { nonNullable: true }),
  });

  constructor() {
    const record = this.store.find(this.id);
    if (record) {
      this.form.setValue({
        title: record.title,
        owner: record.owner,
        status: record.status,
        priority: record.priority,
        urgent: record.urgent,
        dueDate: record.dueDate,
        notes: record.notes,
      });
    } else {
      void this.router.navigate(['/cases']);
    }
  }

  /** A required field's message shows once the field was left, or Save was pressed. */
  protected shows(name: 'title' | 'owner'): boolean {
    const control = this.form.controls[name];
    return control.invalid && (control.touched || this.submitted());
  }

  protected save(): void {
    this.submitted.set(true);
    if (this.form.invalid) return;
    const values = this.form.getRawValue();
    this.store.save({
      id: this.id,
      ...values,
      title: values.title.trim(),
      owner: values.owner.trim(),
    });
    void this.router.navigate(['/cases']);
  }
}
