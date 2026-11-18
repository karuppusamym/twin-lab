import { Routes } from '@angular/router';

import { authGuard } from './auth.guard';
import { CaseList } from './case-list';
import { SignIn } from './sign-in';

export const routes: Routes = [
  { path: 'sign-in', component: SignIn, title: 'Sign in - Caseboard' },
  { path: 'cases', component: CaseList, canActivate: [authGuard], title: 'Cases - Caseboard' },
  {
    // Lazy: the edit form is a chunk of its own, loaded when a case is first opened.
    path: 'cases/:id',
    loadComponent: () => import('./case-edit').then((m) => m.CaseEdit),
    canActivate: [authGuard],
    title: 'Edit case - Caseboard',
  },
  { path: '', pathMatch: 'full', redirectTo: 'cases' },
  { path: '**', redirectTo: 'cases' },
];
