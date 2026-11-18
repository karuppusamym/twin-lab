import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { Auth } from './auth';

/** A signed-out user is sent to the sign-in page. */
export const authGuard: CanActivateFn = () => {
  const auth = inject(Auth);
  return auth.user() ? true : inject(Router).createUrlTree(['/sign-in']);
};
