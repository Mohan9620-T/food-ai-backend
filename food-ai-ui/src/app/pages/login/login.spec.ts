import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { of, throwError } from 'rxjs';
import { vi } from 'vitest';

import { Login } from './login';
import { AuthService } from '../../services/auth';

describe('Login', () => {
  let fixture: ComponentFixture<Login>;
  const login = vi.fn();

  beforeEach(async () => {
    login.mockReset();
    await TestBed.configureTestingModule({
      imports: [Login],
      providers: [provideRouter([]), { provide: AuthService, useValue: { getRememberedEmail: () => '', login } }]
    }).compileComponents();
    fixture = TestBed.createComponent(Login);
    fixture.detectChanges();
  });

  it('renders the login form', () => {
    expect(fixture.nativeElement.querySelector('h1')?.textContent).toContain('Food AI Assistant');
    expect(fixture.nativeElement.querySelector('button[type="submit"]')?.textContent).toContain('Login');
  });

  it('submits trimmed credentials and navigates home', () => {
    login.mockReturnValue(of({ access_token: 'a', refresh_token: 'r', token_type: 'bearer' }));
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate');
    Object.assign(fixture.componentInstance, { email: ' user@example.com ', password: 'secret', rememberMe: true });
    fixture.componentInstance.submit();
    expect(login).toHaveBeenCalledWith({ email: 'user@example.com', password: 'secret', remember_me: true }, true);
    expect(navigate).toHaveBeenCalledWith(['/']);
  });

  it('shows an error and skips the API when fields are empty', () => {
    fixture.componentInstance.submit();
    expect(login).not.toHaveBeenCalled();
    expect(fixture.componentInstance.errorMessage()).toBe('Please enter email and password.');
  });

  it('shows an authentication error when login fails', () => {
    login.mockReturnValue(throwError(() => new Error('unauthorized')));
    Object.assign(fixture.componentInstance, { email: 'user@example.com', password: 'wrong' });
    fixture.componentInstance.submit();
    expect(fixture.componentInstance.errorMessage()).toBe('Invalid email or password.');
  });
});
