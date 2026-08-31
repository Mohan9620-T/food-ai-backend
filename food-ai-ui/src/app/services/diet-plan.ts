import { HttpClient } from '@angular/common/http';
import { inject, Injectable, signal } from '@angular/core';
import { finalize, Observable, tap } from 'rxjs';
import { environment } from '../../environments/environment';

export interface DietPlanItem { id:number; food_name:string; quantity:number; unit:string; fdc_id:number|null; calories:number|null; protein_g:number|null; carbs_g:number|null; fat_g:number|null; }
export interface DietPlanMeal { id:number; day_of_week:number; meal_slot:'breakfast'|'lunch'|'dinner'|'snack'; description:string; items:DietPlanItem[]; }
export interface PlanTotals { day_of_week:number; calories:number; protein_g:number; carbs_g:number; fat_g:number; matched_items:number; unmatched_items:number; }
export interface DietPlan { id:number; created_at:string; target_calories:number|null; target_protein_g:number|null; target_carbs_g:number|null; target_fat_g:number|null; meals:DietPlanMeal[]; daily_totals:PlanTotals[]; }

@Injectable({ providedIn: 'root' })
export class DietPlanService {
  private readonly http = inject(HttpClient);
  private readonly url = `${environment.apiUrl}/diet-plans`;
  private readonly plansState = signal<DietPlan[]>([]);
  private readonly activeState = signal<DietPlan|null>(null);
  private readonly loadingState = signal(false);
  readonly plans = this.plansState.asReadonly(); readonly active = this.activeState.asReadonly(); readonly loading = this.loadingState.asReadonly();
  load(): void { this.loadingState.set(true); this.http.get<DietPlan[]>(`${this.url}/`).pipe(finalize(()=>this.loadingState.set(false))).subscribe({next:p=>{this.plansState.set(p);this.activeState.set(p[0]??null);}}); }
  generate(): Observable<DietPlan> { this.loadingState.set(true); return this.http.post<DietPlan>(`${this.url}/generate`,{}).pipe(tap(p=>{this.activeState.set(p);this.plansState.update(v=>[p,...v]);}),finalize(()=>this.loadingState.set(false))); }
  select(plan:DietPlan): void { this.activeState.set(plan); }
  delete(id:number): Observable<unknown> { return this.http.delete(`${this.url}/${id}`).pipe(tap(()=>{const p=this.plansState().filter(v=>v.id!==id);this.plansState.set(p);this.activeState.set(p[0]??null);})); }
}
