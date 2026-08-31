import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable, signal } from '@angular/core';
import { finalize, forkJoin, Observable, tap } from 'rxjs';

import { environment } from '../../environments/environment';

export interface MealItem {
  id: number;
  food_name: string;
  quantity: number;
  unit: string;
  fdc_id: number | null;
  calories: number | null;
  protein_g: number | null;
  carbs_g: number | null;
  fat_g: number | null;
}

export interface MealLog {
  id: number;
  raw_description: string;
  source: 'text' | 'image';
  logged_at: string;
  created_at: string;
  items: MealItem[];
}

export interface DailyTotals {
  date: string;
  calories: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
  matched_items: number;
  unmatched_items: number;
}

@Injectable({ providedIn: 'root' })
export class MealsService {
  private readonly url = `${environment.apiUrl}/meals`;
  private readonly http = inject(HttpClient);
  private readonly mealsState = signal<MealLog[]>([]);
  private readonly totalsState = signal<DailyTotals | null>(null);
  private readonly loadingState = signal(false);
  private readonly savingState = signal(false);
  private readonly errorState = signal<string | null>(null);

  readonly meals = this.mealsState.asReadonly();
  readonly totals = this.totalsState.asReadonly();
  readonly loading = this.loadingState.asReadonly();
  readonly saving = this.savingState.asReadonly();
  readonly error = this.errorState.asReadonly();

  loadDate(date: string): void {
    this.loadingState.set(true);
    this.errorState.set(null);
    const params = new HttpParams().set('start_date', date).set('end_date', date);
    forkJoin({
      meals: this.http.get<MealLog[]>(`${this.url}/`, { params }),
      totals: this.http.get<DailyTotals>(`${this.url}/totals`, {
        params: new HttpParams().set('date', date)
      })
    }).pipe(finalize(() => this.loadingState.set(false))).subscribe({
      next: ({ meals, totals }) => {
        this.mealsState.set(meals);
        this.totalsState.set(totals);
      },
      error: () => this.errorState.set('Meals could not be loaded. Please try again.')
    });
  }

  create(description: string, loggedAt: string): Observable<MealLog> {
    this.savingState.set(true);
    this.errorState.set(null);
    return this.http.post<MealLog>(`${this.url}/`, {
      description,
      logged_at: loggedAt
    }).pipe(
      tap((meal) => this.mealsState.update((meals) => [meal, ...meals])),
      finalize(() => this.savingState.set(false))
    );
  }

  createFromImage(file: File, loggedAt: string): Observable<MealLog> {
    this.savingState.set(true);
    this.errorState.set(null);
    const form = new FormData();
    form.append('image', file, file.name);
    form.append('logged_at', loggedAt);
    return this.http.post<MealLog>(`${this.url}/from-image`, form).pipe(
      tap((meal) => this.mealsState.update((meals) => [meal, ...meals])),
      finalize(() => this.savingState.set(false))
    );
  }

  delete(mealId: number): Observable<unknown> {
    return this.http.delete(`${this.url}/${mealId}`).pipe(
      tap(() => this.mealsState.update((meals) => meals.filter((meal) => meal.id !== mealId)))
    );
  }

  setError(message: string | null): void {
    this.errorState.set(message);
  }
}
