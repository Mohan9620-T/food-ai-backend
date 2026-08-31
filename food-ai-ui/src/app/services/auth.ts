import { computed, inject, Injectable, PLATFORM_ID, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { isPlatformBrowser } from '@angular/common';
import { finalize, Observable, shareReplay, tap, throwError } from 'rxjs';
import { environment } from '../../environments/environment';

export interface LoginRequest {
  email: string;
  password: string;
  remember_me: boolean;
}

export interface RegisterResponse {
  id: number;
  fullname: string;
  email: string;
  email_sent: boolean;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly apiUrl = `${environment.apiUrl}/users`;
  private readonly accessTokenKey = 'food-ai-access-token';
  private readonly refreshTokenKey = 'food-ai-refresh-token';
  private readonly rememberedEmailKey = 'food-ai-remembered-email';

  private readonly http = inject(HttpClient);
  private readonly isBrowser = isPlatformBrowser(inject(PLATFORM_ID));
  private readonly tokenState = signal<string | null>(this.readStoredValue(this.accessTokenKey));
  private refreshRequest: Observable<TokenResponse> | null = null;

  readonly isLoggedIn = signal(!!this.tokenState());
  private readonly currentUser = computed(() => this.readUser(this.tokenState()));
  readonly currentUserId = computed(() => this.currentUser()?.sub ?? null);
  readonly currentUserEmail = computed(() => this.currentUser()?.email ?? '');
  readonly currentUserName = computed(() => {
    const user = this.currentUser();
    return user?.fullname || user?.email.split('@')[0] || 'User';
  });
  readonly currentUserInitials = computed(() => this.currentUserName()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join(''));

  login(credentials: LoginRequest, rememberMe = false): Observable<TokenResponse> {
    return this.http.post<TokenResponse>(`${this.apiUrl}/login`, {
      ...credentials,
      remember_me: rememberMe
    }).pipe(
      tap((response) => {
        this.saveRememberedEmail(credentials.email, rememberMe);
        this.setTokens(response, rememberMe);
      })
    );
  }

  refreshAccessToken(): Observable<TokenResponse> {
    if (this.refreshRequest) return this.refreshRequest;
    const refreshToken = this.getRefreshToken();
    if (!refreshToken) return throwError(() => new Error('No refresh token is available'));

    const rememberMe = this.isBrowser && localStorage.getItem(this.refreshTokenKey) !== null;
    this.refreshRequest = this.http.post<TokenResponse>(`${this.apiUrl}/refresh`, {
      refresh_token: refreshToken
    }).pipe(
      tap((response) => this.setTokens(response, rememberMe)),
      finalize(() => this.refreshRequest = null),
      shareReplay({ bufferSize: 1, refCount: false })
    );
    return this.refreshRequest;
  }

  getRememberedEmail(): string {
    if (!this.isBrowser) return '';
    return localStorage.getItem(this.rememberedEmailKey) ?? '';
  }

  register(data: { fullname: string; email: string; password: string }): Observable<RegisterResponse> {
    return this.http.post<RegisterResponse>(`${this.apiUrl}/`, data);
  }

  logout(): void {
    const refreshToken = this.getRefreshToken();
    if (!refreshToken) {
      this.clearSession();
      return;
    }

    this.http.post(`${this.apiUrl}/logout`, { refresh_token: refreshToken }).pipe(
      finalize(() => this.clearSession())
    ).subscribe({ error: () => undefined });
  }

  clearSession(): void {
    if (this.isBrowser) {
      for (const storage of [localStorage, sessionStorage]) {
        storage.removeItem(this.accessTokenKey);
        storage.removeItem(this.refreshTokenKey);
      }
    }
    this.tokenState.set(null);
    this.isLoggedIn.set(false);
  }

  getToken(): string | null {
    return this.tokenState();
  }

  getRefreshToken(): string | null {
    return this.readStoredValue(this.refreshTokenKey);
  }

  private setTokens(tokens: TokenResponse, rememberMe: boolean): void {
    if (this.isBrowser) {
      for (const storage of [localStorage, sessionStorage]) {
        storage.removeItem(this.accessTokenKey);
        storage.removeItem(this.refreshTokenKey);
      }
      const storage = rememberMe ? localStorage : sessionStorage;
      storage.setItem(this.accessTokenKey, tokens.access_token);
      storage.setItem(this.refreshTokenKey, tokens.refresh_token);
    }
    this.tokenState.set(tokens.access_token);
    this.isLoggedIn.set(true);
  }

  private saveRememberedEmail(email: string, rememberMe: boolean): void {
    if (!this.isBrowser) return;
    if (rememberMe) localStorage.setItem(this.rememberedEmailKey, email);
    else localStorage.removeItem(this.rememberedEmailKey);
  }

  private readStoredValue(key: string): string | null {
    if (!this.isBrowser) return null;
    return localStorage.getItem(key) ?? sessionStorage.getItem(key);
  }

  private readUser(token: string | null): { sub: string; email: string; fullname?: string } | null {
    if (!this.isBrowser || !token) return null;
    try {
      const payloadPart = token.split('.')[1];
      if (!payloadPart) return null;
      const base64 = payloadPart.replace(/-/g, '+').replace(/_/g, '/');
      const payload = JSON.parse(atob(base64)) as {
        sub?: unknown;
        email?: unknown;
        fullname?: unknown;
      };
      if (typeof payload.sub !== 'string') return null;
      return {
        sub: payload.sub,
        email: typeof payload.email === 'string' ? payload.email : '',
        fullname: typeof payload.fullname === 'string' ? payload.fullname : undefined
      };
    } catch {
      return null;
    }
  }
}
