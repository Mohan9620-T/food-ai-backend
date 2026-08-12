import { computed, inject, Injectable, PLATFORM_ID, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { isPlatformBrowser } from '@angular/common';
import { Observable, tap } from 'rxjs';
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
  token_type: string;
}

@Injectable({
  providedIn: 'root'
})
export class AuthService {

  private readonly apiUrl = `${environment.apiUrl}/users`;
  private readonly tokenKey = 'food-ai-access-token';
  private readonly rememberedEmailKey = 'food-ai-remembered-email';

  private readonly http = inject(HttpClient);
  private readonly isBrowser = isPlatformBrowser(inject(PLATFORM_ID));

  private readonly tokenState = signal<string | null>(this.readToken());

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
      tap((res) => {
        this.saveRememberedEmail(credentials.email, rememberMe);
        this.setToken(res.access_token, rememberMe);
      })
    );
  }

  getRememberedEmail(): string {
    if (!this.isBrowser) return '';
    return localStorage.getItem(this.rememberedEmailKey) ?? '';
  }

  register(data: { fullname: string; email: string; password: string }): Observable<RegisterResponse> {
    return this.http.post<RegisterResponse>(`${this.apiUrl}/`, data);
  }

  logout(): void {
    if (this.isBrowser) {
      localStorage.removeItem(this.tokenKey);
      sessionStorage.removeItem(this.tokenKey);
    }
    this.tokenState.set(null);
    this.isLoggedIn.set(false);
  }

  getToken(): string | null {
    return this.tokenState();
  }

  private setToken(token: string, rememberMe: boolean): void {
    if (this.isBrowser) {
      localStorage.removeItem(this.tokenKey);
      sessionStorage.removeItem(this.tokenKey);
      const storage = rememberMe ? localStorage : sessionStorage;
      storage.setItem(this.tokenKey, token);
    }
    this.tokenState.set(token);
    this.isLoggedIn.set(true);
  }

  private saveRememberedEmail(email: string, rememberMe: boolean): void {
    if (!this.isBrowser) return;

    if (rememberMe) {
      localStorage.setItem(this.rememberedEmailKey, email);
    } else {
      localStorage.removeItem(this.rememberedEmailKey);
    }
  }

  private readToken(): string | null {
    if (!this.isBrowser) return null;
    return localStorage.getItem(this.tokenKey) ?? sessionStorage.getItem(this.tokenKey);
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
