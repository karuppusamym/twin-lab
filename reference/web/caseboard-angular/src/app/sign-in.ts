import { Component, inject, signal } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { Auth } from './auth';

@Component({
  selector: 'app-sign-in',
  imports: [ReactiveFormsModule],
  template: `
    <h1 id="signInHeading">Sign in</h1>
    <form id="signInForm" [formGroup]="form" (ngSubmit)="submit()" novalidate>
      <label for="username">User name</label>
      <input id="username" type="text" formControlName="username" autocomplete="off" />
      <label for="password">Password</label>
      <input id="password" type="password" formControlName="password" autocomplete="off" />
      @if (failed()) {
        <p id="signInError" class="error" role="alert">The user name or password is wrong.</p>
      }
      <button id="btnSignIn" type="submit" class="primary">Sign in</button>
    </form>
  `,
  styles: `
    form {
      display: grid;
      gap: 8px;
      max-width: 320px;
      background: #fff;
      padding: 20px;
      border-radius: 6px;
    }
    button {
      justify-self: start;
      margin-top: 8px;
    }
  `,
})
export class SignIn {
  private readonly auth = inject(Auth);
  private readonly router = inject(Router);
  protected readonly failed = signal(false);
  protected readonly form = new FormGroup({
    username: new FormControl('', { nonNullable: true }),
    password: new FormControl('', { nonNullable: true }),
  });

  protected submit(): void {
    const { username, password } = this.form.getRawValue();
    if (this.auth.signIn(username, password)) {
      this.failed.set(false);
      void this.router.navigate(['/cases']);
    } else {
      this.failed.set(true);
    }
  }
}
