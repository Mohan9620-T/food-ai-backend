import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AuthService } from '../../services/auth';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [FormsModule, RouterLink],
  templateUrl: './login.html',
  styleUrl: './login.css'
})
export class Login {

  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);

  email = this.authService.getRememberedEmail();
  password = '';
  rememberMe = !!this.email;
  readonly loading = signal(false);
  readonly errorMessage = signal('');

  submit(): void {
    if (!this.email.trim() || !this.password.trim()) {
      this.errorMessage.set('Please enter email and password.');
      return;
    }

    this.loading.set(true);
    this.errorMessage.set('');

    this.authService.login(
      { email: this.email.trim(), password: this.password, remember_me: this.rememberMe },
      this.rememberMe
    ).subscribe({
      next: () => {
        this.loading.set(false);
        this.router.navigate(['/']);
      },
      error: () => {
        this.loading.set(false);
        this.errorMessage.set('Invalid email or password.');
      }
    });
  }
}