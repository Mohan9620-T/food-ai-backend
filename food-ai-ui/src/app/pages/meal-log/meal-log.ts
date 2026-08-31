import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { DecimalPipe } from '@angular/common';

import { Sidebar } from '../../components/sidebar/sidebar';
import { MealsService } from '../../services/meals';

@Component({
  selector: 'app-meal-log',
  standalone: true,
  imports: [FormsModule, Sidebar, DecimalPipe],
  templateUrl: './meal-log.html',
  styleUrl: './meal-log.css'
})
export class MealLogPage {
  private readonly mealsService = inject(MealsService);
  readonly meals = this.mealsService.meals;
  readonly totals = this.mealsService.totals;
  readonly loading = this.mealsService.loading;
  readonly saving = this.mealsService.saving;
  readonly error = this.mealsService.error;
  description = '';
  selectedDate = this.today();

  constructor() {
    this.mealsService.loadDate(this.selectedDate);
  }

  changeDate(): void {
    this.mealsService.loadDate(this.selectedDate);
  }

  submit(): void {
    const description = this.description.trim();
    if (!description || this.saving()) return;
    const loggedAt = this.loggedAtForSelectedDate();
    this.mealsService.create(description, loggedAt).subscribe({
      next: () => {
        this.description = '';
        this.mealsService.loadDate(this.selectedDate);
      },
      error: (error: HttpErrorResponse) => {
        const detail = typeof error.error?.detail === 'string' ? error.error.detail : null;
        this.mealsService.setError(detail ?? 'The meal could not be logged. Please try again.');
      }
    });
  }

  uploadPhoto(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file || this.saving()) return;
    if (!['image/jpeg', 'image/png', 'image/webp', 'image/gif'].includes(file.type)) {
      this.mealsService.setError('Unsupported file type. Upload a JPEG, PNG, WebP, or GIF image.');
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      this.mealsService.setError('Image is too large. Maximum size is 8 MB.');
      return;
    }
    this.mealsService.createFromImage(file, this.loggedAtForSelectedDate()).subscribe({
      next: () => this.mealsService.loadDate(this.selectedDate),
      error: (error: HttpErrorResponse) => {
        const detail = typeof error.error?.detail === 'string' ? error.error.detail : null;
        this.mealsService.setError(detail ?? 'The image could not be processed. Please try again.');
      }
    });
  }

  deleteMeal(mealId: number): void {
    this.mealsService.delete(mealId).subscribe({
      next: () => this.mealsService.loadDate(this.selectedDate),
      error: () => this.mealsService.setError('The meal could not be deleted.')
    });
  }

  formatTime(value: string): string {
    return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })
      .format(new Date(value));
  }

  private today(): string {
    const date = new Date();
    const offset = date.getTimezoneOffset() * 60_000;
    return new Date(date.getTime() - offset).toISOString().slice(0, 10);
  }

  private loggedAtForSelectedDate(): string {
    return this.selectedDate === this.today()
      ? new Date().toISOString()
      : `${this.selectedDate}T12:00:00.000Z`;
  }
}
