import { Routes } from '@angular/router';
import { Home } from './pages/home/home';
import { Login } from './pages/login/login';
import { Register } from './pages/register/register';
import { Welcome } from './pages/welcome/welcome';
import { authGuard } from './services/auth-guard';

export const routes: Routes = [
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
  },
  { path: '**', redirectTo: '' }
];
