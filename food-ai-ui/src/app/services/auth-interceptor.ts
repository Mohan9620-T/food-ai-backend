import { HttpContextToken, HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, switchMap, throwError } from 'rxjs';
import { AuthService } from './auth';

const AUTH_RETRY_ATTEMPTED = new HttpContextToken(() => false);

export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const authService = inject(AuthService);
  const accessToken = authService.getToken();
  const authenticatedRequest = accessToken
    ? request.clone({ setHeaders: { Authorization: `Bearer ${accessToken}` } })
    : request;

  return next(authenticatedRequest).pipe(
    catchError((error: HttpErrorResponse) => {
      const isRefreshRequest = request.url.endsWith('/users/refresh');
      const alreadyRetried = request.context.get(AUTH_RETRY_ATTEMPTED);
      if (error.status !== 401 || isRefreshRequest || alreadyRetried || !authService.getRefreshToken()) {
        return throwError(() => error);
      }

      return authService.refreshAccessToken().pipe(
        switchMap((tokens) => next(request.clone({
          context: request.context.set(AUTH_RETRY_ATTEMPTED, true),
          setHeaders: { Authorization: `Bearer ${tokens.access_token}` }
        }))),
        catchError(() => {
          authService.clearSession();
          return throwError(() => error);
        })
      );
    })
  );
};
