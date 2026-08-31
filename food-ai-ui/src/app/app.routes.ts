import { Routes } from '@angular/router';
import { Home } from './pages/home/home';
import { Login } from './pages/login/login';
import { Register } from './pages/register/register';
import { Welcome } from './pages/welcome/welcome';
import { authGuard } from './services/auth-guard';
import { MealLogPage } from './pages/meal-log/meal-log';
import { ProfilePage } from './pages/profile/profile';
import { DietPlanPage } from './pages/diet-plan/diet-plan';

export const routes: Routes = [
  { path: 'profile', component: ProfilePage, canActivate: [authGuard] },
  { path: 'diet-plan', component: DietPlanPage, canActivate: [authGuard] },
  {
    path: 'meals',
    component: MealLogPage,
    canActivate: [authGuard]
  },
  {
    path: 'welcome',
    component: Welcome
  },
  {
    path: 'login',
    component: Login
  },
  {
    path: 'register',
    component: Register
  },
  {
    path: '',
    component: Home,
    canActivate: [authGuard]
  }
];
