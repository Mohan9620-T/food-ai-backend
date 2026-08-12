import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { AuthService } from '../../services/auth';

@Component({
  selector: 'app-register',
  standalone: true,
  imports: [FormsModule, RouterLink],
  templateUrl: './register.html',
  styleUrl: './register.css'
})
export class Register {

  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);

  fullname = '';
  email = '';
  password = '';
  confirmPassword = '';

  readonly loading = signal(false);
  readonly errorMessage = signal('');
  readonly successMessage = signal('');

  submit(): void {
    this.errorMessage.set('');
    this.successMessage.set('');

    if (!this.fullname.trim() || !this.email.trim() || !this.password.trim()) {
      this.errorMessage.set('Please fill in all fields.');
      return;
    }

    if (this.password.length < 6) {
      this.errorMessage.set('Password must be at least 6 characters.');
      return;
    }

    if (this.password !== this.confirmPassword) {
      this.errorMessage.set('Passwords do not match.');
      return;
    }

    this.loading.set(true);

    this.authService.register({
      fullname: this.fullname.trim(),
      email: this.email.trim(),
      password: this.password
    }).subscribe({
      next: (response) => {
        this.loading.set(false);
        this.successMessage.set(response.email_sent
          ? 'Account created and login details emailed! Redirecting to login...'
          : 'Account created, but email was not sent because SMTP is not configured. Redirecting to login...'
        );
        setTimeout(() => this.router.navigate(['/login']), 2500);
      },
      error: (err: HttpErrorResponse) => {
        this.loading.set(false);
        this.errorMessage.set(
          err.status === 400 ? 'An account with this email already exists.' : 'Something went wrong. Please try again.'
        );
      }
    });
  }
}
