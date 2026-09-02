import { HttpErrorResponse } from '@angular/common/http';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { of, throwError } from 'rxjs';
import { vi } from 'vitest';

import { Register } from './register';
import { AuthService } from '../../services/auth';

describe('Register', () => {
  let fixture: ComponentFixture<Register>;
  const register = vi.fn();

  beforeEach(async () => {
    register.mockReset();
    await TestBed.configureTestingModule({
      imports: [Register],
      providers: [provideRouter([]), { provide: AuthService, useValue: { register } }]
    }).compileComponents();
    fixture = TestBed.createComponent(Register);
    fixture.detectChanges();
  });

  it('renders the registration controls', () => {
    expect(fixture.nativeElement.textContent).toContain('Create your account');
    expect(fixture.nativeElement.querySelectorAll('input').length).toBe(4);
  });

  it('submits a valid registration and schedules login navigation', () => {
    vi.useFakeTimers();
    register.mockReturnValue(of({ id: 1, fullname: 'Rajesh', email: 'r@example.com', email_sent: true }));
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate');
    Object.assign(fixture.componentInstance, { fullname: ' Rajesh ', email: ' r@example.com ', password: 'secret1', confirmPassword: 'secret1' });
    fixture.componentInstance.submit();
    expect(register).toHaveBeenCalledWith({ fullname: 'Rajesh', email: 'r@example.com', password: 'secret1' });
    vi.advanceTimersByTime(2500);
    expect(navigate).toHaveBeenCalledWith(['/login']);
    vi.useRealTimers();
  });

  it('rejects mismatched passwords without calling the API', () => {
    Object.assign(fixture.componentInstance, { fullname: 'Rajesh', email: 'r@example.com', password: 'secret1', confirmPassword: 'secret2' });
    fixture.componentInstance.submit();
    expect(register).not.toHaveBeenCalled();
    expect(fixture.componentInstance.errorMessage()).toBe('Passwords do not match.');
  });

  it('shows the duplicate-account message for a 400 response', () => {
    register.mockReturnValue(throwError(() => new HttpErrorResponse({ status: 400 })));
    Object.assign(fixture.componentInstance, { fullname: 'Rajesh', email: 'r@example.com', password: 'secret1', confirmPassword: 'secret1' });
    fixture.componentInstance.submit();
    expect(fixture.componentInstance.errorMessage()).toContain('already exists');
  });
});
