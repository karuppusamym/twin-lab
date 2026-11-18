import { Injectable, signal } from '@angular/core';

const KEY = 'caseboard.user';
const PASSWORD = 'demo'; // synthetic: the only password of this reference application

function stored(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

/** Who is signed in. Kept in memory, and in sessionStorage so a reload keeps the session. */
@Injectable({ providedIn: 'root' })
export class Auth {
  readonly user = signal<string | null>(stored());

  signIn(userName: string, password: string): boolean {
    const name = userName.trim();
    if (!name || password !== PASSWORD) return false;
    this.user.set(name);
    try {
      sessionStorage.setItem(KEY, name);
    } catch {
      /* storage refused: the session lives in memory only */
    }
    return true;
  }

  signOut(): void {
    this.user.set(null);
    try {
      sessionStorage.removeItem(KEY);
    } catch {
      /* nothing stored */
    }
  }
}
