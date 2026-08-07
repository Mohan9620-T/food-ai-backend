import { inject, Injectable, PLATFORM_ID, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { isPlatformBrowser } from '@angular/common';
import { Observable, tap } from 'rxjs';
import { environment } from '../../environments/environment';

export interface LoginRequest {
  email: string;
  password: string;
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

  private readonly http = inject(HttpClient);
  private readonly isBrowser = isPlatformBrowser(inject(PLATFORM_ID));

  private readonly tokenState = signal<string | null>(this.readToken());

  readonly isLoggedIn = signal(!!this.tokenState());

  login(credentials: LoginRequest): Observable<TokenResponse> {
    return this.http.post<TokenResponse>(`${this.apiUrl}/login`, credentials).pipe(
      tap((res) => this.setToken(res.access_token))
    );
  }

  register(data: { fullname: string; email: string; password: string }): Observable<unknown> {
    return this.http.post(`${this.apiUrl}/`, data);
  }

  logout(): void {
    if (this.isBrowser) localStorage.removeItem(this.tokenKey);
    this.tokenState.set(null);
    this.isLoggedIn.set(false);
  }

  getToken(): string | null {
    return this.tokenState();
  }

  private setToken(token: string): void {
    if (this.isBrowser) localStorage.setItem(this.tokenKey, token);
    this.tokenState.set(token);
    this.isLoggedIn.set(true);
  }

  private readToken(): string | null {
    if (!this.isBrowser) return null;
    return localStorage.getItem(this.tokenKey);
  }
}