import { HttpErrorResponse } from '@angular/common/http';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Sidebar } from '../../components/sidebar/sidebar';
import { ProfileService, UserProfileInput } from '../../services/profile';

@Component({selector:'app-profile-page',standalone:true,imports:[FormsModule,Sidebar],templateUrl:'./profile.html',styleUrl:'./profile.css'})
export class ProfilePage {
  private readonly service=inject(ProfileService); readonly loading=this.service.loading;
  form:UserProfileInput={goal:'maintain',target_calories:null,target_protein_g:null,target_carbs_g:null,target_fat_g:null,allergies:[],dietary_restrictions:[],disliked_foods:[]};
  allergies=''; restrictions=''; dislikes=''; message=''; error='';
  constructor(){this.service.load().subscribe({next:p=>{this.form={...p};this.allergies=p.allergies.join(', ');this.restrictions=p.dietary_restrictions.join(', ');this.dislikes=p.disliked_foods.join(', ');},error:(e:HttpErrorResponse)=>{if(e.status!==404)this.error='Profile could not be loaded.';}});}
  save():void{this.message='';this.error='';this.form.allergies=this.tags(this.allergies);this.form.dietary_restrictions=this.tags(this.restrictions);this.form.disliked_foods=this.tags(this.dislikes);this.service.save(this.form).subscribe({next:()=>this.message='Profile saved.',error:()=>this.error='Profile could not be saved.'});}
  private tags(value:string):string[]{return [...new Set(value.split(',').map(v=>v.trim()).filter(Boolean))];}
}
