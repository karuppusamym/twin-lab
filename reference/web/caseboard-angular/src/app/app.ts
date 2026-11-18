import { Component, HostListener, inject, signal } from '@angular/core';
import { Router, RouterLink, RouterOutlet } from '@angular/router';

import { Auth } from './auth';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink],
  template: `
    <header id="appHeader">
      <span id="brand">Caseboard</span>
      @if (auth.user(); as user) {
        <a id="navCases" routerLink="/cases">Cases</a>
        <span class="spacer"></span>
        <span id="currentUser">Signed in as {{ user }}</span>
        <button id="btnSignOut" type="button" (click)="asking.set(true)">Sign out</button>
      }
    </header>
    <main>
      <router-outlet />
    </main>
    @if (asking()) {
      <div class="backdrop" id="signOutBackdrop">
        <div
          class="dialog"
          id="signOutDialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="signOutTitle"
        >
          <h2 id="signOutTitle">Sign out?</h2>
          <p id="signOutText">Changes that were not saved are lost.</p>
          <div class="actions">
            <button id="btnSignOutCancel" type="button" (click)="asking.set(false)">
              Stay signed in
            </button>
            <button id="btnSignOutConfirm" type="button" class="primary" (click)="signOut()">
              Sign out now
            </button>
          </div>
        </div>
      </div>
    }
  `,
  styles: `
    header {
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 10px 20px;
      background: #16324f;
      color: #fff;
    }
    #brand {
      font-weight: bold;
      font-size: 18px;
    }
    a {
      color: #cfe3fb;
    }
    .spacer {
      flex: 1;
    }
    .backdrop {
      position: fixed;
      inset: 0;
      background: rgba(10, 20, 30, 0.45);
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .dialog {
      background: #fff;
      padding: 20px 24px;
      border-radius: 6px;
      min-width: 320px;
    }
    .dialog h2 {
      margin: 0 0 8px;
      font-size: 18px;
    }
    .actions {
      display: flex;
      justify-content: flex-end;
      gap: 10px;
      margin-top: 16px;
    }
  `,
})
export class App {
  protected readonly auth = inject(Auth);
  private readonly router = inject(Router);
  protected readonly asking = signal(false);

  /** Escape closes the sign-out dialog, as Stay signed in does. */
  @HostListener('document:keydown.escape')
  protected closeDialog(): void {
    this.asking.set(false);
  }

  protected signOut(): void {
    this.asking.set(false);
    this.auth.signOut();
    void this.router.navigate(['/sign-in']);
  }
}
