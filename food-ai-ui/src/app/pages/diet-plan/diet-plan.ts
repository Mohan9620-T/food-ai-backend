import { DatePipe, DecimalPipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Sidebar } from '../../components/sidebar/sidebar';
import { DietPlan, DietPlanMeal, DietPlanService, PlanTotals } from '../../services/diet-plan';
import { ProfileService } from '../../services/profile';

@Component({selector:'app-diet-plan-page',standalone:true,imports:[Sidebar,DecimalPipe,DatePipe,RouterLink],templateUrl:'./diet-plan.html',styleUrl:'./diet-plan.css'})
export class DietPlanPage {
  private readonly service=inject(DietPlanService); private readonly profiles=inject(ProfileService);
  readonly active=this.service.active;readonly plans=this.service.plans;readonly loading=this.service.loading;readonly profile=this.profiles.profile;
  readonly days=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];error='';
  constructor(){this.service.load();this.profiles.load().subscribe({error:(e:HttpErrorResponse)=>{if(e.status===404)this.profiles.clear();}});}
  generate():void{if(!this.profile()||this.loading())return;this.error='';this.service.generate().subscribe({error:(e:HttpErrorResponse)=>this.error=typeof e.error?.detail==='string'?e.error.detail:'Plan could not be generated.'});}
  meals(plan:DietPlan,day:number):DietPlanMeal[]{return plan.meals.filter(m=>m.day_of_week===day);}
  totals(plan:DietPlan,day:number):PlanTotals|undefined{return plan.daily_totals.find(t=>t.day_of_week===day);}
  select(plan:DietPlan):void{this.service.select(plan);} delete(id:number):void{this.service.delete(id).subscribe();}
}
