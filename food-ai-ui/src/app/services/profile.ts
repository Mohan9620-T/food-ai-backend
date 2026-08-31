import { HttpClient } from '@angular/common/http';
import { inject, Injectable, signal } from '@angular/core';
import { finalize, Observable, tap } from 'rxjs';
import { environment } from '../../environments/environment';

export type DietGoal = 'lose_weight' | 'maintain' | 'gain_weight';
export interface UserProfileInput {
  goal: DietGoal;
  target_calories: number | null;
  target_protein_g: number | null;
  target_carbs_g: number | null;
  target_fat_g: number | null;
  allergies: string[];
  dietary_restrictions: string[];
  disliked_foods: string[];
}
export interface UserProfile extends UserProfileInput { id: number; user_id: number; updated_at: string; }

@Injectable({ providedIn: 'root' })
export class ProfileService {
  private readonly http = inject(HttpClient);
  private readonly url = `${environment.apiUrl}/profile/`;
  private readonly profileState = signal<UserProfile | null>(null);
  private readonly loadingState = signal(false);
  readonly profile = this.profileState.asReadonly();
  readonly loading = this.loadingState.asReadonly();

  load(): Observable<UserProfile> {
    this.loadingState.set(true);
    return this.http.get<UserProfile>(this.url).pipe(tap((profile) => this.profileState.set(profile)), finalize(() => this.loadingState.set(false)));
  }
  save(profile: UserProfileInput): Observable<UserProfile> {
    this.loadingState.set(true);
    return this.http.put<UserProfile>(this.url, profile).pipe(tap((saved) => this.profileState.set(saved)), finalize(() => this.loadingState.set(false)));
  }
  clear(): void { this.profileState.set(null); }
}
